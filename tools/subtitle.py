"""Make reviewed Javanese subtitles and a 480p MP4 from a Japanese episode. The tool reads API keys from the environment."""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import csv
from dataclasses import dataclass
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import NamedTuple

import requests
from PIL import Image, ImageDraw
from settings import ROOT, load_font, merge_config
from burn_subtitles import dimensions, encode_video, verify_video

# Cue timing, in seconds. Each value changes every exported cue.
LEAD_IN = 0.04  # A cue appears this long before its first spoken word.
HOLD = 0.18  # A cue stays this long after its last spoken word.
READING_EXTENSION = 0.8  # Reading time can extend a cue this far past its speech.
READING_FLOOR = 0.9  # The shortest reading allowance before the next cue takes over.
PART_GAP = 0.04  # The pause between the parts of one split utterance.
MIN_READABLE = 0.65  # QA flags shorter cues; layout aims for subtitle.min_duration.
# Cue splitting, in characters, and line fitting, in pixels.
LONG_TEXT = 30  # A cue that outlasts max_duration splits in two above this length.
LONG_PART_MIN = 25
FALLBACK_PART_MIN = 24
SIDE_MARGINS = 66  # ASS MarginL and MarginR plus a safety allowance.
BATCH_SIZE = 55  # Utterances per translation request.

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Dialogue,{family},26,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,1.8,0.5,2,28,28,22,1
Style: Song,{family},25,&H00E1F3FF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,1.8,0.5,8,28,28,18,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


@dataclass(frozen=True)
class Project:
    """One episode: its local config, cache folder and export folder."""
    config_path: Path
    config: dict
    work: Path
    out: Path

    @property
    def slug(self):
        return self.config["slug"]

    def export(self, suffix):
        return self.out / f"{self.slug}{suffix}"


class Screen(NamedTuple):
    """The subtitle font and line limits for one output width."""
    font: object
    max_chars: int
    max_width: float


def configure(path=None, required=True, out=None):
    """Select an episode config. Without a config file, the repository defaults apply."""
    config_path = Path(path or os.environ.get("SUBBER_PROJECT", ROOT / "project.json")).expanduser().resolve()
    if required and not config_path.is_file():
        raise ValueError(f"The tool found no local project config at {config_path}. Create one with: python tools/subtitle.py init --slug show-01 --series 'Show' --episode 01")
    config = merge_config(read_json(config_path) if config_path.is_file() else {})
    return Project(config_path, config, ROOT / "work" / config["slug"], Path(out) if out else ROOT / "subbed")


def ensure_workspace(project):
    project.work.mkdir(parents=True, exist_ok=True)
    project.out.mkdir(parents=True, exist_ok=True)


def output_dimensions(project, info=None):
    if info is None:
        probe = project.work / "probe.json"
        info = read_json(probe) if probe.exists() else {"streams": [{"codec_type": "video", "width": 640, "height": 480}]}
    return dimensions(info, project.config)


def screen_for(config, width):
    return Screen(load_font(config, 26, bold=True), config["subtitle"]["max_line_chars"], width - SIDE_MARGINS)


def write_json(path, data):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


def api_key(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Set {name} in the environment. The tool did not send a request.")
    return value


# Paid requests. Receipt file names differ by stage; they stay stable so old receipts keep blocking resubmission.

def claim_request(path, data):
    """Claim a paid submission atomically, including across separate agent processes."""
    try:
        with Path(path).open("x", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
    except FileExistsError as error:
        raise RuntimeError(f"{Path(path).name} already exists. Inspect that request before you retry.") from error


def ensure_unclaimed(receipt, label):
    """Stop before a paid request if an earlier attempt left a receipt."""
    if not receipt.exists():
        return
    state = read_json(receipt)
    if state.get("status") == "failed":
        raise RuntimeError(f"The earlier {label} request failed with HTTP {state.get('http_status')}. Read {receipt.name}. Archive that receipt before you retry.")
    if state.get("status") == "submitting":
        raise RuntimeError(f"The earlier {label} request has an unknown outcome. Check the provider history before you archive {receipt.name}.")
    raise RuntimeError(f"{receipt.name} already exists. Inspect the earlier {label} request before you retry.")


def submit_once(receipt, label, send, **details):
    """Claim the receipt, send one paid request and record a definite HTTP failure."""
    claim_request(receipt, {"status": "submitting", "started_at": time.time(), **details})
    response = send()
    if response.status_code != 200:
        write_json(receipt, {"status": "failed", "http_status": response.status_code, "detail": response.text[:2000]})
        raise RuntimeError(f"The {label} request failed with HTTP {response.status_code}. Read {receipt.name} for the provider response.")
    return response


def extract_clip(source, start, length, target, bitrate="64k"):
    """Cut mono 16 kHz MP3 audio for one bounded provider request."""
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", start, "-i", source, "-t", length,
         "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", bitrate, target])


def source_file(project, value=None):
    value = value or project.config.get("source")
    if value:
        source = Path(value).expanduser()
        source = (ROOT / source).resolve() if not source.is_absolute() else source.resolve()
        if not source.is_file():
            raise ValueError(f"The source video does not exist: {source}")
        return source
    remembered = project.work / "source.json"
    if remembered.is_file():
        path = Path(read_json(remembered)["path"])
        if path.is_file():
            return path
    folders = [ROOT, ROOT / "input"]
    files = [p for folder in folders if folder.is_dir() for p in folder.iterdir() if p.suffix.lower() in {".mkv", ".mp4", ".avi", ".mov", ".webm"}]
    if not files:
        raise ValueError("The repository root and input/ contain no source video. Use --source to choose a video.")
    if len(files) > 1:
        raise ValueError("The repository root and input/ contain more than one source video. Use --source to choose one.")
    return files[0]


def inspect(project, source):
    ensure_workspace(project)
    stat = source.stat()
    identity = {"path": str(source), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    previous = project.work / "source.json"
    if previous.exists() and read_json(previous) != identity:
        raise ValueError("The source video has changed. Choose a new project slug to keep episode caches apart.")
    info = json.loads(run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", source], capture_output=True, encoding="utf-8").stdout)
    write_json(project.work / "probe.json", info)
    write_json(previous, identity)
    return info


def prepare(project, source):
    info = inspect(project, source)
    audio = project.work / "audio.flac"
    if not audio.exists():
        temp = project.work / "audio.partial.flac"
        run(["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y", "-i", source, "-map", "0:a:0", "-vn",
             "-af", "aresample=16000:async=1:first_pts=0", "-ac", "1", "-sample_fmt", "s16", "-c:a", "flac", temp])
        temp.replace(audio)
    sheet = project.work / "scene-overview.jpg"
    if not sheet.exists():
        duration = float(info["format"]["duration"])
        thumbs = []
        if duration <= 0:
            raise ValueError("The source video has no positive duration.")
        for t in [min(15, duration / 2)] + list(range(75, int(duration), 60)):
            t = float(t)
            frame = project.work / f"frame-{t:07.2f}.jpg"
            if not frame.exists():
                run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", t, "-i", source, "-frames:v", "1", "-vf", "scale=320:240,setsar=1", "-q:v", "3", frame])
            thumbs.append((t, frame))
        canvas = Image.new("RGB", (320 * 5, 266 * ((len(thumbs) + 4) // 5)), "#141414")
        draw = ImageDraw.Draw(canvas)
        font = load_font(project.config, 18)
        for i, (t, frame) in enumerate(thumbs):
            x, y = (i % 5) * 320, (i // 5) * 266
            with Image.open(frame) as im:
                canvas.paste(im, (x, y))
            draw.text((x + 8, y + 242), f"{int(t)//60:02d}:{int(t)%60:02d}", fill="white", font=font)
        canvas.save(sheet, quality=88)
    print(f"The tool prepared {float(info['format']['duration']):.2f} s of audio ({audio.stat().st_size / 1e6:.1f} MB).", flush=True)


def transcribe(project, source):
    prepare(project, source)
    target = project.work / "scribe.json"
    if target.exists():
        print("The tool reuses the saved Scribe transcript.", flush=True)
        return
    receipt = project.work / "scribe-request.json"
    ensure_unclaimed(receipt, "Scribe")
    model = project.config["transcription_model"]
    data = [("model_id", model), ("language_code", "ja"), ("diarize", "true"), ("timestamps_granularity", "word"), ("tag_audio_events", "true"), ("no_verbatim", "false")]
    data += [("keyterms", term) for term in project.config["keyterms"]]
    key = api_key("ELEVENLABS_API_KEY")
    print("The tool is uploading the Japanese audio to ElevenLabs Scribe.", flush=True)
    with (project.work / "audio.flac").open("rb") as f:
        response = submit_once(receipt, "Scribe", lambda: requests.post(
            "https://api.elevenlabs.io/v1/speech-to-text", headers={"xi-api-key": key}, data=data,
            files={"file": ("audio.flac", f, "audio/flac")}, timeout=(30, 1800)), audio="audio.flac", settings=data)
    result = response.json()
    write_json(target, result)
    write_json(receipt, {"status": "completed", "completed_at": time.time(), "model": model, "settings": data,
                         "request_id": response.headers.get("request-id"), "transcription_id": result.get("transcription_id")})
    print(f"The tool saved {len(result.get('words', []))} timed tokens.", flush=True)


def segment(project):
    words = read_json(project.work / "scribe.json")["words"]
    music_ranges = project.config.get("music_ranges", [])
    utterances, events, group = [], [], []

    def flush():
        if not group:
            return
        text = "".join(w["text"] for w in group).strip()
        spoken = [w for w in group if w.get("type") == "word"]
        if text and spoken:
            utterances.append({"id": len(utterances) + 1, "start": spoken[0]["start"], "end": spoken[-1]["end"],
                               "speaker": spoken[0].get("speaker_id") or "unknown", "ja": text, "word_indices": [w["_index"] for w in spoken]})
        group.clear()

    for i, original in enumerate(words):
        w = {**original, "_index": i}
        if w.get("type") == "audio_event":
            flush()
            events.append(original)
            continue
        if w.get("type") == "spacing":
            if group:
                group.append(w)
            continue
        if w.get("type") != "word":
            continue
        spoken = [x for x in group if x.get("type") == "word"]
        if spoken:
            prev = spoken[-1]
            # Musical phrases have long gaps between individual syllables.
            musical = any(start <= w["start"] <= end for start, end in music_ranges)
            gap = 1.8 if musical else 1.0
            max_span = 9 if musical else 10
            punctuation = re.search(r"[。！？!?]$", prev["text"]) and w["start"] - prev["end"] > 0.12
            if w.get("speaker_id") != prev.get("speaker_id") or w["start"] - prev["end"] > gap or w["end"] - spoken[0]["start"] > max_span or punctuation:
                flush()
        group.append(w)
    flush()
    write_json(project.work / "utterances.json", utterances)
    write_json(project.work / "audio-events.json", events)
    transcript = "\n".join(f"{u['id']:03d} [{u['start']:.2f}-{u['end']:.2f}] {u['speaker']}: {u['ja']}" for u in utterances)
    (project.work / "japanese-transcript.txt").write_text(transcript, encoding="utf-8")
    print(f"The tool segmented the transcript into {len(utterances)} utterances and {len(events)} audio events.", flush=True)


def crosscheck(project, source):
    """A second ASR reading is evidence for review, not a replacement timing track."""
    info = inspect(project, source)
    duration = float(info["format"]["duration"])
    folder = project.work / "crosscheck"
    folder.mkdir(exist_ok=True)
    config = project.config

    def one(start):
        path = folder / f"{start:04d}.json"
        if path.exists():
            return read_json(path)
        clip = folder / f"{start:04d}.mp3"
        begin = max(0, start - 3)
        length = min(306, duration - begin)
        if not clip.exists():
            extract_clip(source, begin, length, clip, "48k")
        receipt = folder / f"{start:04d}.request.json"
        label = f"crosscheck-{start:04d}"
        ensure_unclaimed(receipt, label)
        key = api_key("OPENAI_API_KEY")
        context = f"日本語の特撮番組『{config['series']}』。聞こえない台詞は補わない。" + "、".join(config.get("keyterms", []))
        with clip.open("rb") as f:
            r = submit_once(receipt, label, lambda: requests.post(
                "https://api.openai.com/v1/audio/transcriptions", headers={"Authorization": "Bearer " + key},
                data={"model": config["crosscheck_model"], "language": "ja", "response_format": "json", "prompt": context},
                files={"file": (clip.name, f, "audio/mpeg")}, timeout=(30, 600)))
        result = {"start": begin, "end": begin + length, "model": config["crosscheck_model"], **r.json()}
        write_json(path, result)
        write_json(receipt, {"status": "completed", "request_id": r.headers.get("x-request-id")})
        print(f"The tool checked {begin:.0f}-{begin + length:.0f} s with independent ASR.", flush=True)
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(one, range(0, int(duration), 300)))
    write_json(project.work / "crosscheck.json", results)


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def arr(schema):
    return {"type": "array", "items": schema}


STR = {"type": "string"}
INT = {"type": "integer"}
BOOL = {"type": "boolean"}


def fetch_response(response_id, headers):
    response = requests.get("https://api.openai.com/v1/responses/" + response_id, headers=headers, timeout=90)
    response.raise_for_status()
    return response.json()


def llm(project, label, model, instructions, payload, schema, images=(), effort=None):
    """Persist request IDs and content-hashed caches; resume instead of resubmitting."""
    content = [{"type": "input_text", "text": json.dumps(payload, ensure_ascii=False)}]
    for path in images:
        content.append({"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii"), "detail": "high"})
    body = {"model": model, "instructions": instructions, "input": [{"role": "user", "content": content}],
            "reasoning": {"effort": effort or project.config["reasoning_effort"]},
            "text": {"format": {"type": "json_schema", "name": label.replace("-", "_"), "strict": True, "schema": schema}},
            "max_output_tokens": 30000, "background": True, "store": True}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
    folder = project.work / "api"
    folder.mkdir(exist_ok=True)
    prefix = folder / f"{label}-{digest}"
    result_path = prefix.with_suffix(".result.json")
    if result_path.exists():
        print(f"The tool reuses the cached {label} result.", flush=True)
        return read_json(result_path)
    receipt = prefix.with_suffix(".receipt.json")
    headers = {"Authorization": "Bearer " + api_key("OPENAI_API_KEY")}
    state = read_json(receipt) if receipt.exists() else {}
    if state.get("id"):
        data = fetch_response(state["id"], headers)
    else:
        ensure_unclaimed(receipt, label)
        write_json(prefix.with_suffix(".request.json"), body)
        response = submit_once(receipt, label, lambda: requests.post("https://api.openai.com/v1/responses", headers=headers, json=body, timeout=(30, 300)), model=model)
        data = response.json()
        write_json(receipt, {"id": data["id"], "status": data["status"], "model": model, "started_at": time.time()})
    print(f"The {label} job on {model} is {data['status'].replace('_', ' ')}.", flush=True)
    last_status = data["status"]
    while data["status"] in {"queued", "in_progress"}:
        time.sleep(8)
        data = fetch_response(data["id"], headers)
        if data["status"] != last_status:
            print(f"The {label} job is {data['status'].replace('_', ' ')}.", flush=True)
            last_status = data["status"]
    response_path = prefix.with_suffix(".response.json")
    write_json(response_path, data)
    write_json(receipt, {"id": data["id"], "status": data["status"], "model": model, "finished_at": time.time()})
    if data["status"] != "completed":
        raise RuntimeError(f"The {label} job ended with status {data['status']}. Read {response_path.name} for diagnostics.")
    chunks = [part["text"] for item in data.get("output", []) if item.get("type") == "message" for part in item.get("content", []) if part.get("type") == "output_text"]
    if not chunks:
        raise RuntimeError(f"The model returned no output text for {label}. Read {response_path.name} for a refusal or other diagnostics.")
    try:
        parsed = json.loads("".join(chunks))
    except json.JSONDecodeError as error:
        raise RuntimeError(f"The model returned invalid JSON for {label}. Read {response_path.name}.") from error
    write_json(result_path, parsed)
    print(f"The tool saved the {label} result.", flush=True)
    tokens = data.get("usage", {}).get("total_tokens")
    if tokens is not None:
        print(f"The {label} job used {tokens} tokens.", flush=True)
    return parsed


def source_review(project):
    config = project.config
    utterances = read_json(project.work / "utterances.json")
    schema = obj({
        "episode_context": STR,
        "glossary": arr(obj({"ja": STR, "latin": STR, "jv": STR, "meaning": STR})),
        "speaker_caveat": STR,
        "units": arr(obj({"id": INT, "ja": STR, "speaker_role": STR, "kind": {"type": "string", "enum": ["dialogue", "narration", "song", "shout", "omit"]},
                          "uncertainty": STR, "timing_suspect": BOOL})),
    })
    prompt = (ROOT / "prompts" / "source-review.txt").read_text(encoding="utf-8")
    compact = [{k: v for k, v in u.items() if k != "word_indices"} for u in utterances]
    # The payload key order is part of the cache hash.
    payload = {
        "series": config["series"],
        "series_context": config["series_context"],
        "approved_glossary": config["glossary"],
        "timed_scribe_utterances": compact,
        "independent_asr": read_json(project.work / "crosscheck.json"),
        "visual_evidence": "Attached is an evenly sampled contact sheet, each frame labeled with its time. It provides only limited visual context; it does not prove who speaks at another time.",
    }
    result = llm(project, "source-review", config["review_model"], prompt, payload, schema, [project.work / "scene-overview.jpg"])
    check_ids(result["units"], utterances)
    write_json(project.work / "source-reviewed.json", result)


def check_ids(actual, expected):
    ids = [u["id"] for u in actual]
    expected_ids = [u["id"] for u in expected]
    if ids != expected_ids:
        raise ValueError(f"The result IDs do not match the input IDs. The input has IDs {expected_ids}. The result has IDs {ids}.")


def overrides(project, name):
    """Read an episode override file beside the config; it must name this episode's slug."""
    manual = project.config_path.parent / name
    changes = read_json(manual) if manual.exists() else {}
    if changes.get("episode", project.slug) != project.slug:
        raise ValueError(f"{name} belongs to another episode. Archive it before you change the slug.")
    return changes


def corrected_units(project):
    reviewed = read_json(project.work / "source-reviewed.json")
    source = read_json(project.work / "utterances.json")
    changes = {u["id"]: u for u in overrides(project, "source-overrides.json").get("units", [])}
    checked = {u["id"]: u for u in reviewed["units"]}
    check_ids(reviewed["units"], source)
    if set(changes) - set(checked):
        raise ValueError("Source overrides contain unknown utterance IDs.")
    units = [{**u, **checked[u["id"]], **changes.get(u["id"], {})} for u in source]
    previous = 0.0
    for unit in units:
        if unit["kind"] == "omit":
            continue
        if not unit["ja"].strip() or not math.isfinite(unit["start"] + unit["end"]) or unit["end"] <= unit["start"] or unit["start"] < previous:
            raise ValueError(f"Utterance {unit['id']} has empty text or invalid timing. Correct it before translation or layout.")
        previous = unit["start"]
    return units


def translate(project):
    config = project.config
    reviewed = read_json(project.work / "source-reviewed.json")
    units = corrected_units(project)
    schema = obj({"units": arr(obj({"id": INT, "jv": STR, "english_gloss": STR, "note": STR}))})
    prompt = (ROOT / "prompts" / "translate.txt").read_text(encoding="utf-8")
    whole_episode = [{"id": u["id"], "start": u["start"], "ja": u["ja"], "speaker_role": u["speaker_role"]} for u in units]

    # Each group gets the full source and story context; no isolated-line translation.
    def one(offset):
        batch = units[offset:offset + BATCH_SIZE]
        # The payload key order is part of the cache hash.
        payload = {
            "register": config["register"],
            "episode_context": reviewed["episode_context"],
            "glossary": reviewed["glossary"],
            "whole_episode_source": whole_episode,
            "translate_only_these_units": [{k: v for k, v in u.items() if k != "word_indices"} for u in batch],
            "subtitle_policy": config["subtitle"],
        }
        result = llm(project, f"translate-{offset:03d}", config["translation_model"], prompt, payload, schema)
        check_ids(result["units"], batch)
        return result["units"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        batches = list(pool.map(one, range(0, len(units), BATCH_SIZE)))
    write_json(project.work / "translation-draft.json", [u for batch in batches for u in batch])


def review_translation(project):
    config = project.config
    reviewed = read_json(project.work / "source-reviewed.json")
    units = corrected_units(project)
    drafts = read_json(project.work / "translation-draft.json")
    check_ids(drafts, units)
    schema = obj({"overall_note": STR, "units": arr(obj({"id": INT, "jv": STR, "english_gloss": STR, "change_reason": STR, "uncertainty": STR}))})
    prompt = (ROOT / "prompts" / "translation-review.txt").read_text(encoding="utf-8")
    # The payload key order is part of the cache hash.
    payload = {
        "register": config["register"],
        "episode_context": reviewed["episode_context"],
        "glossary": reviewed["glossary"],
        "units": [{**{k: v for k, v in u.items() if k != "word_indices"}, "draft": d} for u, d in zip(units, drafts)],
        "subtitle_policy": config["subtitle"],
    }
    result = llm(project, "translation-review", config["review_model"], prompt, payload, schema)
    check_ids(result["units"], units)
    write_json(project.work / "translation-reviewed.json", result)


def split_text(text, limit=76):
    """Split on word/phrase boundaries, preserving all translated words."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return [text]
    words = text.split()
    count = math.ceil(len(text) / limit)
    target = len(text) / count
    candidates = []
    for i in range(1, len(words)):
        left, right = " ".join(words[:i]), " ".join(words[i:])
        if len(left) <= limit and right:
            punctuation_bonus = 10 if re.search(r"[,;.!?]$", words[i-1]) else 0
            candidates.append((abs(len(left) - target) - punctuation_bonus, i))
    if not candidates:
        raise ValueError(f"A single word is too long for one subtitle: {text}")
    index = min(candidates)[1]
    return [" ".join(words[:index])] + split_text(" ".join(words[index:]), limit)


def wrap_lines(text, screen):
    """Break a subtitle into at most two lines that fit the character and pixel limits."""
    def fits(line):
        return len(line) <= screen.max_chars and screen.font.getlength(line) <= screen.max_width

    if fits(text):
        return [text]
    words = text.split()
    options = []
    for index in range(1, len(words)):
        left, right = " ".join(words[:index]), " ".join(words[index:])
        if fits(left) and fits(right):
            score = abs(screen.font.getlength(left) - screen.font.getlength(right))
            if re.search(r"[,;.!?]$", left):
                score -= 40
            options.append((score, [left, right]))
    if not options:
        raise ValueError(f"The text does not fit two subtitle lines: {text}")
    return min(options, key=lambda x: x[0])[1]


def timestamp(seconds, decimal=","):
    millis = round(seconds * 1000)
    seconds, millis = divmod(millis, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{decimal}{millis:03d}"


def ass_timestamp(seconds):
    centis = round(seconds * 100)
    seconds, centis = divmod(centis, 100)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centis:02d}"


def subtitle_items(project, units, translations):
    """Apply reviewed text edits and merge groups. Units marked omit get no subtitle."""
    edits = overrides(project, "subtitle-overrides.json")
    text_edits = {u["id"]: u for u in edits.get("units", [])}
    unknown = sorted(set(text_edits) - {t["id"] for t in translations}, key=str)
    if unknown:
        raise ValueError(f"Subtitle overrides contain unknown utterance IDs: {', '.join(map(str, unknown))}.")
    translations = [{**t, **text_edits.get(t["id"], {})} for t in translations]
    items = []
    for u, t in zip(units, translations):
        if u["kind"] == "omit":
            continue
        if not t["jv"].strip():
            raise ValueError(f"Utterance {u['id']} has no Javanese translation.")
        items.append({**u, "jv": t["jv"].strip(), "source_ids": [u["id"]]})
    # Explicit reviewed groups repair split words/phrases; timing never spans a long action gap by inference.
    for specification in edits.get("merge_groups", []):
        ids = specification["ids"] if isinstance(specification, dict) else specification
        indexes = [i for i, item in enumerate(items) if item["id"] in ids]
        if not indexes or len(indexes) != len(ids) or indexes != list(range(indexes[0], indexes[-1] + 1)):
            raise ValueError(f"Merge group {ids} must name consecutive visible utterance IDs.")
        group = items[indexes[0]:indexes[-1] + 1]
        merged = {**group[0], "source_ids": ids, "end": group[-1]["end"], "jv": " ".join(x["jv"] for x in group), "ja": "".join(x["ja"] for x in group)}
        if isinstance(specification, dict) and specification.get("jv"):
            merged["jv"] = specification["jv"]
        items[indexes[0]:indexes[-1] + 1] = [merged]
    return items, translations


def cue_window(item, next_start, options, source_duration):
    """Time one subtitle from its speech, its reading speed and the next line."""
    # Keep attacks/action with no speech clear; never lead the next spoken line.
    start = max(0, float(item["start"]) - LEAD_IN)
    natural_end = float(item["end"]) + HOLD
    needed = len(item["jv"]) / options["target_cps"]
    end = min(next_start, max(natural_end, start + min(needed, max(READING_FLOOR, item["end"] - start + READING_EXTENSION))))
    end = min(source_duration, max(end, min(next_start, start + options["min_duration"])))
    if end <= start:
        raise ValueError(f"Utterance {item['id']} overlaps the next utterance. Correct its timing.")
    return start, end


def screen_parts(item, start, end, options, screen):
    """Split text into parts that each fit two lines. A long-lasting cue splits in two."""
    parts = []
    for part in split_text(item["jv"], options["max_line_chars"] * options["max_lines"] - 2):
        try:
            wrap_lines(part, screen)
            parts.append(part)
        except ValueError:
            # A phrase may be shorter than 76 chars yet have no valid two-line word break.
            parts.extend(split_text(part, max(FALLBACK_PART_MIN, math.ceil(len(part) / 2))))
    # A long source statement should not linger as one subtitle for ten seconds.
    if len(parts) == 1 and end - start > options["max_duration"]:
        if len(item["jv"]) > LONG_TEXT:
            parts = split_text(item["jv"], max(LONG_PART_MIN, math.ceil(len(item["jv"]) / 2)))
        else:
            end = start + options["max_duration"]
    return parts, end


def layout(project):
    units = corrected_units(project)
    translations = read_json(project.work / "translation-reviewed.json")["units"]
    check_ids(translations, units)
    items, translations = subtitle_items(project, units, translations)
    options = project.config["subtitle"]
    screen = screen_for(project.config, output_dimensions(project)[0])
    source_duration = float(read_json(project.work / "probe.json")["format"]["duration"])
    cues = []
    for index, item in enumerate(items):
        next_start = float(items[index + 1]["start"]) - options["gap"] if index + 1 < len(items) else source_duration
        start, end = cue_window(item, next_start, options, source_duration)
        parts, end = screen_parts(item, start, end, options, screen)
        total = sum(len(part) for part in parts)
        cursor = start
        for n, part in enumerate(parts):
            last = n == len(parts) - 1
            # Long utterances share their time by text length; earlier parts end PART_GAP early.
            finish = end if last else cursor + (end - start) * len(part) / total
            cues.append({"id": len(cues) + 1, "source_ids": item["source_ids"], "start": round(cursor, 2), "end": round(finish if last else finish - PART_GAP, 2),
                         "lines": wrap_lines(part, screen), "kind": item["kind"], "speaker_role": item["speaker_role"]})
            cursor = finish
    write_json(project.work / "cues.json", cues)
    export_subtitles(project, cues, units, translations)
    validate_cues(project, cues)


def export_subtitles(project, cues, units, translations):
    srt = "\n\n".join(f"{cue['id']}\n{timestamp(cue['start'])} --> {timestamp(cue['end'])}\n" + "\n".join(cue["lines"]) for cue in cues) + "\n"
    project.export(".jv.srt").write_text(srt, encoding="utf-8")
    vtt = "WEBVTT\n\n" + "\n\n".join(f"{cue['id']}\n{timestamp(cue['start'], '.')} --> {timestamp(cue['end'], '.')}\n"
                                     + "\n".join(html.escape(line, quote=False) for line in cue["lines"]) for cue in cues) + "\n"
    project.export(".jv.vtt").write_text(vtt, encoding="utf-8")
    width, height = output_dimensions(project)
    family = load_font(project.config, 26, bold=True).getname()[0]
    if "," in family:
        raise ValueError(f"The font family name contains a comma: {family}. Choose another font in font_paths.")
    rows = []
    for cue in cues:
        text = "\\N".join(line.replace("{", "(").replace("}", ")").replace("\\", "/") for line in cue["lines"])
        style = "Song" if cue["kind"] == "song" else "Dialogue"
        rows.append(f"Dialogue: 0,{ass_timestamp(cue['start'])},{ass_timestamp(cue['end'])},{style},,0,0,0,,{text}")
    header = ASS_HEADER.format(width=width, height=height, family=family)
    project.export(".jv.ass").write_text(header + "\n".join(rows) + "\n", encoding="utf-8-sig")
    source_by_id = {u["id"]: u for u in units}
    translation_by_id = {t["id"]: t for t in translations}
    with project.export(".review.tsv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["cue", "source IDs", "start", "end", "role", "kind", "Japanese source", "Final Javanese subtitle",
                         "English gloss (review only)", "source review notes", "translation uncertainty", "review change"])
        for cue in cues:
            sources = [source_by_id[i] for i in cue["source_ids"]]
            texts = [translation_by_id[i] for i in cue["source_ids"]]
            writer.writerow([
                cue["id"], ",".join(str(i) for i in cue["source_ids"]), timestamp(cue["start"]), timestamp(cue["end"]), cue["speaker_role"], cue["kind"],
                " ".join(u["ja"] for u in sources), "\n".join(cue["lines"]), " ".join(t["english_gloss"] for t in texts),
                " | ".join(u["uncertainty"] for u in sources if u["uncertainty"]),
                " | ".join(t["uncertainty"] for t in texts if t["uncertainty"]),
                " | ".join(t["change_reason"] for t in texts if t["change_reason"]),
            ])
    print(f"The tool exported {len(cues)} subtitle cues.", flush=True)


def validate_cues(project, cues):
    options = project.config["subtitle"]
    issues = []
    duration = float(read_json(project.work / "probe.json")["format"]["duration"])
    previous = 0
    for cue in cues:
        span = cue["end"] - cue["start"]
        text = " ".join(cue["lines"])
        errors = []
        if cue["start"] < previous - 0.001:
            errors.append("overlap")
        if span <= 0 or cue["end"] > duration + 0.01:
            errors.append("invalid timing")
        if len(cue["lines"]) > options["max_lines"] or any(len(line) > options["max_line_chars"] for line in cue["lines"]):
            errors.append("line length")
        if any(c in text for c in "{}\\") or re.search(r"[぀-ヿ一-鿿]", text):
            errors.append("unexpected Japanese or markup")
        cps = len(text) / span if span > 0 else 999
        if cps > options["max_cps"] + 0.01:
            errors.append("reading speed")
        if span < MIN_READABLE:
            errors.append("very short duration")
        if span > options["max_duration"] + 0.05:
            errors.append("long duration")
        if errors:
            issues.append({"cue": cue["id"], "source_ids": cue["source_ids"], "cps": round(cps, 1), "duration": round(span, 2), "issues": errors, "text": text})
        previous = cue["end"]
    write_json(project.work / "subtitle-qa.json", {"cue_count": len(cues), "issues": issues})
    print(f"Subtitle QA found {len(issues)} cues that need attention. Read subtitle-qa.json for details.", flush=True)
    return issues


def encode(project, source):
    original = inspect(project, source)
    issues = validate_cues(project, read_json(project.work / "cues.json"))
    if issues:
        raise ValueError("Resolve the subtitle QA issues before you encode. Read subtitle-qa.json for details.")
    title = f"{project.config['series']} {project.config['episode']} - Javanese"
    # Verify the candidate first; a failed encode keeps the previous deliverable.
    encode_video(source, project.export(".jv.ass"), project.export(".jv.480p.mp4"), project.config, project.work, original,
                 overwrite=True, verify_output=True, title=title)


def verify(project, source):
    original = inspect(project, source)
    report = verify_video(source, project.export(".jv.480p.mp4"), project.config, project.work, original)
    report["subtitle_qa"] = read_json(project.work / "subtitle-qa.json")
    write_json(project.work / "verification.json", report)


def doctor(project, check_models=False):
    config = project.config
    checks = {"project_config": project.config_path.is_file(), "ffmpeg": bool(shutil.which("ffmpeg")), "ffprobe": bool(shutil.which("ffprobe"))}
    if checks["ffmpeg"]:
        filters = run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, encoding="utf-8").stdout
        encoders = run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, encoding="utf-8").stdout
        checks["libass"] = bool(re.search(r"\bsubtitles\s", filters))
        checks["encoders"] = all(re.search(r"\b" + name + r"\s", encoders) for name in ["libx264", "aac", "flac", "libmp3lame"])
    try:
        checks["fonts"] = bool(load_font(config, 18) and load_font(config, 26, bold=True))
    except (RuntimeError, OSError):
        checks["fonts"] = False
    keys = {name: bool(os.environ.get(name)) for name in ["ELEVENLABS_API_KEY", "OPENAI_API_KEY"]}
    result = {"checks": checks, "credentials_present": keys, "network_checked": check_models, "ready": all(checks.values()) and all(keys.values())}
    if check_models:
        if not keys["OPENAI_API_KEY"]:
            result["models"] = {"error": "Set OPENAI_API_KEY to check model access."}
            result["ready"] = False
        else:
            r = requests.get("https://api.openai.com/v1/models", headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]}, timeout=45)
            r.raise_for_status()
            available = {m["id"] for m in r.json()["data"]}
            result["models"] = {config[key]: config[key] in available for key in ["translation_model", "review_model", "crosscheck_model"]}
            result["ready"] = result["ready"] and all(result["models"].values())
    print(json.dumps(result, indent=2))
    return result


def status(project):
    names = ["source.json", "scribe.json", "utterances.json", "crosscheck.json", "source-reviewed.json", "translation-draft.json",
             "translation-reviewed.json", "cues.json", "subtitle-qa.json", "verification.json", "deliverable-manifest.json"]
    receipts = []
    work = project.work
    if work.exists():
        for path in sorted(work.rglob("*receipt.json")):
            data = read_json(path)
            completed_cache = path.with_name(path.name.replace(".receipt.json", ".result.json")).is_file()
            receipts.append({"file": path.relative_to(work).as_posix(), "status": "completed" if completed_cache else data.get("status"), "response_id": data.get("id")})
        # Stage receipts use -request.json, .request.json or .request. API request bodies have no status.
        for path in sorted(set(work.rglob("*request.json")) | set(work.rglob("*.request"))):
            if path.is_file():
                data = read_json(path)
                if "status" in data:
                    receipts.append({"file": path.relative_to(work).as_posix(), "status": data["status"]})
    report = {"slug": project.slug, "artifacts": {name: (work / name).is_file() for name in names}, "receipts": receipts}
    print(json.dumps(report, indent=2))
    return report


def run_all(project, source):
    transcribe(project, source)
    segment(project)
    crosscheck(project, source)
    source_review(project)
    translate(project)
    review_translation(project)
    layout(project)
    encode(project, source)
    verify(project, source)
    from check_exports import check
    from render_samples import render
    check(project)
    render(project)


# Stages that read the source video, and stages that work from saved evidence after a source identity check.
SOURCE_STAGES = {"prepare": prepare, "transcribe": transcribe, "crosscheck": crosscheck, "encode": encode, "verify": verify}
EVIDENCE_STAGES = {"segment": segment, "source-review": source_review, "translate": translate, "review-translation": review_translation, "layout": layout}
PIPELINE = ["prepare", "transcribe", "segment", "crosscheck", "source-review", "translate", "review-translation", "layout", "encode", "verify"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["init", "doctor", "status", "all", *PIPELINE, "burn"])
    parser.add_argument("--project", help="Path to the local project config. The default is SUBBER_PROJECT or project.json.")
    parser.add_argument("--source", help="Source video. Relative paths start at the repository root.")
    parser.add_argument("--slug", help="init: the episode slug, such as show-01")
    parser.add_argument("--series", help="init: the series name")
    parser.add_argument("--episode", help="init: the episode number")
    parser.add_argument("--subtitles", help="burn: the UTF-8 ASS, SRT or VTT file to burn in")
    parser.add_argument("--output", help="burn: the MP4 destination. The default is subbed/<subtitle-stem>.480p.mp4.")
    parser.add_argument("--overwrite", action="store_true", help="burn: replace an existing output file. The tool never replaces an input.")
    parser.add_argument("--check-models", action="store_true", help="doctor: check OpenAI model access with a read-only request")
    args = parser.parse_args()
    if args.stage != "burn" and (args.subtitles or args.output or args.overwrite):
        parser.error("Use --subtitles, --output and --overwrite only with the burn stage.")
    if args.stage == "burn":
        required = bool(args.project)
    else:
        required = args.stage not in {"init", "doctor"}
    project = configure(args.project, required=required)
    if args.stage == "burn":
        if not args.subtitles:
            parser.error("Give the burn stage an ASS, SRT or VTT file with --subtitles.")
        from burn_subtitles import burn
        burn(source_file(project, args.source), args.subtitles, project.config, args.output, args.overwrite)
        return
    if args.stage == "init":
        if project.config_path.exists():
            parser.error(f"The local config already exists at {project.config_path}. Edit that file. For another episode, choose another --project path.")
        settings = merge_config({**project.config, **{k: getattr(args, k) for k in ["slug", "series", "episode", "source"] if getattr(args, k) is not None}})
        project.config_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(project.config_path, settings)
        print(f"The tool created the local config at {project.config_path}.")
        return
    if args.stage == "doctor":
        if not doctor(project, args.check_models)["ready"]:
            raise SystemExit(1)
        return
    if args.stage == "status":
        status(project)
        return
    ensure_workspace(project)
    source = source_file(project, args.source)
    if args.stage == "all":
        run_all(project, source)
    elif args.stage in EVIDENCE_STAGES:
        inspect(project, source)
        EVIDENCE_STAGES[args.stage](project)
    else:
        SOURCE_STAGES[args.stage](project, source)


def describe_error(error):
    if isinstance(error, subprocess.CalledProcessError):
        detail = error.stderr.strip().splitlines()[-3:] if isinstance(error.stderr, str) else []
        return "\n".join([f"{Path(str(error.cmd[0])).name} stopped with exit status {error.returncode}.", *detail])
    return str(error)


def run_cli(entry, *args):
    """Report expected failures in one message; programming errors keep their traceback."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        entry(*args)
    except (ValueError, RuntimeError, OSError, requests.RequestException, subprocess.CalledProcessError) as error:
        print(f"Error: {describe_error(error)}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    run_cli(main)
