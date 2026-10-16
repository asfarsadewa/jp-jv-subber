"""Japanese -> Javanese subtitles. Run with --help; keys stay in the environment."""
from __future__ import annotations

import argparse
import base64
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
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction

import requests
from PIL import Image, ImageDraw, ImageFont
from settings import DEFAULTS, ROOT, load_font, merge_config

CONFIG_PATH = Path(os.environ.get("SUBBER_PROJECT", ROOT / "project.json")).resolve()
CONFIG = merge_config(json.loads(CONFIG_PATH.read_text(encoding="utf-8"))) if CONFIG_PATH.exists() else merge_config({})
WORK = ROOT / "work" / CONFIG["slug"]
OUT = ROOT / "subbed"


def configure(path=None, required=True):
    global CONFIG_PATH, CONFIG, WORK
    CONFIG_PATH = Path(path or os.environ.get("SUBBER_PROJECT", ROOT / "project.json")).expanduser().resolve()
    if required and not CONFIG_PATH.is_file():
        raise ValueError("No local project config. Run: python tools/subtitle.py init --slug show-01 --series 'Show' --episode 01")
    CONFIG = merge_config(read_json(CONFIG_PATH) if CONFIG_PATH.is_file() else {})
    WORK = ROOT / "work" / CONFIG["slug"]


def ensure_workspace():
    WORK.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(exist_ok=True)


def output_dimensions(info=None):
    if info is None:
        info = read_json(WORK / "probe.json") if (WORK / "probe.json").exists() else {"streams": [{"codec_type": "video", "width": 640, "height": 480}]}
    stream = next(s for s in info["streams"] if s["codec_type"] == "video")
    sar = stream.get("sample_aspect_ratio", "1:1")
    sar = Fraction(sar.replace(":", "/")) if sar not in {"0:1", "N/A"} else Fraction(1)
    ratio = Fraction(stream["width"], stream["height"]) * sar
    height = CONFIG["encode"]["height"]
    width = max(2, round(float(ratio) * height / 2) * 2)
    return width, height


def write_json(path, data):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def claim_request(path, data):
    """Claim a paid submission atomically, including across separate agent processes."""
    try:
        with Path(path).open("x", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
    except FileExistsError as error:
        raise RuntimeError(f"Request already has a receipt: {Path(path).name}. Inspect/resume it before retrying.") from error


def api_key(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing {name}. Supply it in the environment, then retry; no request was submitted.")
    return value


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


def source_file(value=None):
    value = value or CONFIG.get("source")
    if value:
        source = Path(value).expanduser()
        source = (ROOT / source).resolve() if not source.is_absolute() else source.resolve()
        if not source.is_file():
            raise ValueError(f"Source not found: {source}")
        return source
    remembered = WORK / "source.json"
    if remembered.is_file() and Path(read_json(remembered)["path"]).is_file():
        return Path(read_json(remembered)["path"])
    folders = [ROOT, ROOT / "input"]
    files = [p for folder in folders if folder.is_dir() for p in folder.iterdir() if p.suffix.lower() in {".mkv", ".mp4", ".avi", ".mov", ".webm"}]
    if len(files) != 1:
        raise ValueError("Use --source when the project has more than one source video.")
    return files[0]


def inspect(source):
    ensure_workspace()
    stat = source.stat()
    identity = {"path": str(source), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    previous = WORK / "source.json"
    if previous.exists() and read_json(previous) != identity:
        raise ValueError("The source has changed. Choose a new project slug to avoid mixing episode caches.")
    info = json.loads(run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", source], capture_output=True, encoding="utf-8").stdout)
    write_json(WORK / "probe.json", info)
    write_json(previous, identity)
    return info


def prepare(source):
    info = inspect(source)
    audio = WORK / "audio.flac"
    if not audio.exists():
        temp = WORK / "audio.partial.flac"
        run(["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y", "-i", source, "-map", "0:a:0", "-vn", "-af", "aresample=16000:async=1:first_pts=0", "-ac", "1", "-sample_fmt", "s16", "-c:a", "flac", temp])
        temp.replace(audio)
    sheet = WORK / "scene-overview.jpg"
    if not sheet.exists():
        duration = float(info["format"]["duration"])
        thumbs = []
        if duration <= 0:
            raise ValueError("The source has no positive duration.")
        for t in [min(15, duration / 2)] + list(range(75, int(duration), 60)):
            t = float(t)
            frame = WORK / f"frame-{t:07.2f}.jpg"
            if not frame.exists():
                run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", t, "-i", source, "-frames:v", "1", "-vf", "scale=320:240,setsar=1", "-q:v", "3", frame])
            thumbs.append((t, frame))
        canvas = Image.new("RGB", (320 * 5, 266 * ((len(thumbs) + 4) // 5)), "#141414")
        draw = ImageDraw.Draw(canvas)
        font = load_font(CONFIG, 18)
        for i, (t, frame) in enumerate(thumbs):
            x, y = (i % 5) * 320, (i // 5) * 266
            with Image.open(frame) as im:
                canvas.paste(im, (x, y))
            draw.text((x + 8, y + 242), f"{int(t)//60:02d}:{int(t)%60:02d}", fill="white", font=font)
        canvas.save(sheet, quality=88)
    print(f"Prepared {float(info['format']['duration']):.2f}s; audio {audio.stat().st_size / 1e6:.1f} MB", flush=True)


def transcribe(source):
    prepare(source)
    target = WORK / "scribe.json"
    if target.exists():
        print("Using saved Scribe transcript.", flush=True)
        return
    receipt = WORK / "scribe-request.json"
    if receipt.exists() and read_json(receipt)["status"] == "submitting":
        raise RuntimeError("An earlier Scribe request has an unknown outcome. Check ElevenLabs history before retrying, then remove scribe-request.json if safe.")
    data = [("model_id", CONFIG["transcription_model"]), ("language_code", "ja"), ("diarize", "true"), ("timestamps_granularity", "word"), ("tag_audio_events", "true"), ("no_verbatim", "false")]
    data += [("keyterms", term) for term in CONFIG["keyterms"]]
    key = api_key("ELEVENLABS_API_KEY")
    claim_request(receipt, {"status": "submitting", "started_at": time.time(), "audio": "audio.flac", "settings": data})
    print("Uploading Japanese audio to ElevenLabs Scribe v2...", flush=True)
    with (WORK / "audio.flac").open("rb") as f:
        response = requests.post("https://api.elevenlabs.io/v1/speech-to-text", headers={"xi-api-key": key}, data=data, files={"file": ("audio.flac", f, "audio/flac")}, timeout=(30, 1800))
    if response.status_code != 200:
        write_json(receipt, {"status": "failed", "http_status": response.status_code, "detail": response.text[:2000]})
        raise RuntimeError(f"Scribe failed (HTTP {response.status_code}); see scribe-request.json.")
    result = response.json()
    write_json(target, result)
    write_json(receipt, {"status": "completed", "completed_at": time.time(), "model": CONFIG["transcription_model"], "settings": data, "request_id": response.headers.get("request-id"), "transcription_id": result.get("transcription_id")})
    print(f"Saved {len(result.get('words', []))} timed tokens.", flush=True)


def segment():
    raw = read_json(WORK / "scribe.json")
    words = raw["words"]
    utterances, events, group = [], [], []

    def flush():
        if not group:
            return
        text = "".join(w["text"] for w in group).strip()
        spoken = [w for w in group if w.get("type") == "word"]
        if text and spoken:
            utterances.append({"id": len(utterances) + 1, "start": spoken[0]["start"], "end": spoken[-1]["end"], "speaker": spoken[0].get("speaker_id") or "unknown", "ja": text, "word_indices": [w["_index"] for w in spoken]})
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
            musical = any(start <= w["start"] <= end for start, end in CONFIG.get("music_ranges", []))
            gap = 1.8 if musical else 1.0
            max_span = 9 if musical else 10
            punctuation = re.search(r"[。！？!?]$", prev["text"]) and w["start"] - prev["end"] > 0.12
            split = (w.get("speaker_id") != prev.get("speaker_id") or w["start"] - prev["end"] > gap or w["end"] - spoken[0]["start"] > max_span or punctuation)
            if split:
                flush()
        group.append(w)
    flush()
    write_json(WORK / "utterances.json", utterances)
    write_json(WORK / "audio-events.json", events)
    (WORK / "japanese-transcript.txt").write_text("\n".join(f"{u['id']:03d} [{u['start']:.2f}-{u['end']:.2f}] {u['speaker']}: {u['ja']}" for u in utterances), encoding="utf-8")
    print(f"Segmented {len(utterances)} utterances and {len(events)} audio events.", flush=True)


def crosscheck(source):
    """A second ASR reading is evidence for review, not a replacement timing track."""
    info = inspect(source)
    duration = float(info["format"]["duration"])
    folder = WORK / "crosscheck"
    folder.mkdir(exist_ok=True)

    def one(start):
        path = folder / f"{start:04d}.json"
        if path.exists():
            return read_json(path)
        clip = folder / f"{start:04d}.mp3"
        begin = max(0, start - 3)
        length = min(306, duration - begin)
        if not clip.exists():
            run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", begin, "-i", source, "-t", length, "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "48k", clip])
        receipt = folder / f"{start:04d}.request.json"
        if receipt.exists() and read_json(receipt).get("status") == "submitting":
            raise RuntimeError(f"Unknown ASR outcome at {start}; inspect receipt before retrying.")
        key = api_key("OPENAI_API_KEY")
        claim_request(receipt, {"status": "submitting", "started_at": time.time()})
        with clip.open("rb") as f:
            context = f"日本語の特撮番組『{CONFIG['series']}』。聞こえない台詞は補わない。" + "、".join(CONFIG.get("keyterms", []))
            r = requests.post("https://api.openai.com/v1/audio/transcriptions", headers={"Authorization": "Bearer " + key}, data={"model": CONFIG["crosscheck_model"], "language": "ja", "response_format": "json", "prompt": context}, files={"file": (clip.name, f, "audio/mpeg")}, timeout=(30, 600))
        if r.status_code != 200:
            write_json(receipt, {"status": "failed", "http_status": r.status_code, "detail": r.text[:1500]})
            raise RuntimeError(f"Crosscheck failed: HTTP {r.status_code}")
        result = {"start": begin, "end": begin + length, "model": CONFIG["crosscheck_model"], **r.json()}
        write_json(path, result)
        write_json(receipt, {"status": "completed", "request_id": r.headers.get("x-request-id")})
        print(f"Crosschecked {begin:.0f}-{begin+length:.0f}s", flush=True)
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(one, range(0, int(duration), 300)))
    write_json(WORK / "crosscheck.json", results)


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def arr(schema):
    return {"type": "array", "items": schema}


STR = {"type": "string"}
INT = {"type": "integer"}
BOOL = {"type": "boolean"}


def llm(label, model, instructions, payload, schema, images=(), effort=None):
    """Persist request IDs and content-hashed caches; resume instead of resubmitting."""
    content = [{"type": "input_text", "text": json.dumps(payload, ensure_ascii=False)}]
    for path in images:
        content.append({"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii"), "detail": "high"})
    body = {"model": model, "instructions": instructions, "input": [{"role": "user", "content": content}], "reasoning": {"effort": effort or CONFIG["reasoning_effort"]}, "text": {"format": {"type": "json_schema", "name": label.replace("-", "_"), "strict": True, "schema": schema}}, "max_output_tokens": 30000, "background": True, "store": True}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
    folder = WORK / "api"
    folder.mkdir(exist_ok=True)
    prefix = folder / f"{label}-{digest}"
    result_path = prefix.with_suffix(".result.json")
    if result_path.exists():
        print(f"Using cached {label}.", flush=True)
        return read_json(result_path)
    receipt = prefix.with_suffix(".receipt.json")
    headers = {"Authorization": "Bearer " + api_key("OPENAI_API_KEY")}
    if receipt.exists():
        state = read_json(receipt)
        if not state.get("id"):
            raise RuntimeError(f"Unknown outcome for {label}; inspect {receipt.name} before retrying.")
        response = requests.get("https://api.openai.com/v1/responses/" + state["id"], headers=headers, timeout=90)
        response.raise_for_status()
        data = response.json()
    else:
        write_json(prefix.with_suffix(".request.json"), body)
        claim_request(receipt, {"status": "submitting", "model": model, "started_at": time.time()})
        response = requests.post("https://api.openai.com/v1/responses", headers=headers, json=body, timeout=(30, 300))
        if response.status_code != 200:
            write_json(receipt, {"status": "failed", "http_status": response.status_code, "error": response.text[:3000]})
            raise RuntimeError(f"{label} HTTP {response.status_code}: {response.text[:1000]}")
        data = response.json()
        write_json(receipt, {"id": data["id"], "status": data["status"], "model": model, "started_at": time.time()})
    print(f"{label}: {model} ({data['status']})", flush=True)
    last_status = data["status"]
    while data["status"] in {"queued", "in_progress"}:
        time.sleep(8)
        response = requests.get("https://api.openai.com/v1/responses/" + data["id"], headers=headers, timeout=90)
        response.raise_for_status()
        data = response.json()
        if data["status"] != last_status:
            print(f"{label}: {data['status']}", flush=True)
            last_status = data["status"]
    write_json(prefix.with_suffix(".response.json"), data)
    write_json(receipt, {"id": data["id"], "status": data["status"], "model": model, "finished_at": time.time()})
    if data["status"] != "completed":
        raise RuntimeError(f"{label} ended {data['status']}; saved response contains diagnostics.")
    chunks = [part["text"] for item in data.get("output", []) if item.get("type") == "message" for part in item.get("content", []) if part.get("type") == "output_text"]
    parsed = json.loads("".join(chunks))
    write_json(result_path, parsed)
    print(f"{label}: saved; usage {data.get('usage', {}).get('total_tokens', '?')} tokens", flush=True)
    return parsed


def source_review():
    utterances = read_json(WORK / "utterances.json")
    schema = obj({
        "episode_context": STR,
        "glossary": arr(obj({"ja": STR, "latin": STR, "jv": STR, "meaning": STR})),
        "speaker_caveat": STR,
        "units": arr(obj({"id": INT, "ja": STR, "speaker_role": STR, "kind": {"type": "string", "enum": ["dialogue", "narration", "song", "shout", "omit"]}, "uncertainty": STR, "timing_suspect": BOOL})),
    })
    prompt = (ROOT / "prompts" / "source-review.txt").read_text(encoding="utf-8")
    compact = [{k: v for k, v in u.items() if k != "word_indices"} for u in utterances]
    result = llm("source-review", CONFIG["review_model"], prompt, {"series": CONFIG["series"], "series_context": CONFIG["series_context"], "approved_glossary": CONFIG["glossary"], "timed_scribe_utterances": compact, "independent_asr": read_json(WORK / "crosscheck.json"), "visual_evidence": "Attached is an evenly sampled contact sheet, each frame labeled with its time. It provides only limited visual context; it does not prove who speaks at another time."}, schema, [WORK / "scene-overview.jpg"])
    check_ids(result["units"], utterances)
    write_json(WORK / "source-reviewed.json", result)


def check_ids(actual, expected):
    ids = [u["id"] for u in actual]
    expected_ids = [u["id"] for u in expected]
    if ids != expected_ids:
        raise ValueError(f"Missing, duplicate, or reordered IDs. Got {ids}; expected {expected_ids}.")


def corrected_units():
    reviewed = read_json(WORK / "source-reviewed.json")
    source = read_json(WORK / "utterances.json")
    manual = CONFIG_PATH.parent / "source-overrides.json"
    changes = read_json(manual) if manual.exists() else {}
    if changes.get("episode", CONFIG["slug"]) != CONFIG["slug"]:
        raise ValueError("Source overrides belong to a different episode; archive them before changing the slug.")
    overrides = {u["id"]: u for u in changes.get("units", [])}
    checked = {u["id"]: u for u in reviewed["units"]}
    check_ids(reviewed["units"], source)
    if set(overrides) - set(checked):
        raise ValueError("Source overrides contain unknown utterance IDs.")
    units = [{**u, **checked[u["id"]], **overrides.get(u["id"], {})} for u in source]
    previous = 0.0
    for unit in units:
        if unit["kind"] == "omit":
            continue
        if not unit["ja"].strip() or not math.isfinite(unit["start"] + unit["end"]) or unit["end"] <= unit["start"] or unit["start"] < previous:
            raise ValueError(f"Review source text/timing for ID {unit['id']} before translation or layout.")
        previous = unit["start"]
    return units


def translate():
    reviewed = read_json(WORK / "source-reviewed.json")
    units = corrected_units()
    schema = obj({"units": arr(obj({"id": INT, "jv": STR, "english_gloss": STR, "note": STR}))})
    prompt = (ROOT / "prompts" / "translate.txt").read_text(encoding="utf-8")
    # Each group gets the full source and story context; no isolated-line translation.
    def one(offset):
        batch = units[offset:offset + 55]
        payload = {"register": CONFIG["register"], "episode_context": reviewed["episode_context"], "glossary": reviewed["glossary"], "whole_episode_source": [{"id": u["id"], "start": u["start"], "ja": u["ja"], "speaker_role": u["speaker_role"]} for u in units], "translate_only_these_units": [{k: v for k, v in u.items() if k != "word_indices"} for u in batch], "subtitle_policy": CONFIG["subtitle"]}
        result = llm(f"translate-{offset:03d}", CONFIG["translation_model"], prompt, payload, schema)
        check_ids(result["units"], batch)
        return result["units"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        batches = list(pool.map(one, range(0, len(units), 55)))
    all_results = [u for batch in batches for u in batch]
    write_json(WORK / "translation-draft.json", all_results)


def review_translation():
    reviewed = read_json(WORK / "source-reviewed.json")
    units = corrected_units()
    drafts = read_json(WORK / "translation-draft.json")
    check_ids(drafts, units)
    schema = obj({"overall_note": STR, "units": arr(obj({"id": INT, "jv": STR, "english_gloss": STR, "change_reason": STR, "uncertainty": STR}))})
    prompt = (ROOT / "prompts" / "translation-review.txt").read_text(encoding="utf-8")
    payload = {"register": CONFIG["register"], "episode_context": reviewed["episode_context"], "glossary": reviewed["glossary"], "units": [{**u, "draft": d} for u, d in zip(units, drafts)], "subtitle_policy": CONFIG["subtitle"]}
    result = llm("translation-review", CONFIG["review_model"], prompt, payload, schema)
    check_ids(result["units"], units)
    write_json(WORK / "translation-reviewed.json", result)


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
        raise ValueError(f"Cannot split long subtitle word: {text}")
    index = min(candidates)[1]
    return [" ".join(words[:index])] + split_text(" ".join(words[index:]), limit)


def wrap_lines(text):
    maximum = CONFIG["subtitle"]["max_line_chars"]
    font = load_font(CONFIG, 26, bold=True)
    fits = lambda line: len(line) <= maximum and font.getlength(line) <= output_dimensions()[0] - 66
    if fits(text):
        return [text]
    words = text.split()
    options = []
    for index in range(1, len(words)):
        left, right = " ".join(words[:index]), " ".join(words[index:])
        if fits(left) and fits(right):
            score = abs(font.getlength(left) - font.getlength(right))
            if re.search(r"[,;.!?]$", left):
                score -= 40
            options.append((score, [left, right]))
    if not options:
        raise ValueError(f"Text does not fit two subtitle lines: {text}")
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


def layout():
    units = corrected_units()
    translations = read_json(WORK / "translation-reviewed.json")["units"]
    check_ids(translations, units)
    options = CONFIG["subtitle"]
    manual = CONFIG_PATH.parent / "subtitle-overrides.json"
    edits = read_json(manual) if manual.exists() else {}
    if edits.get("episode", CONFIG["slug"]) != CONFIG["slug"]:
        raise ValueError("Subtitle overrides belong to a different episode; archive them before changing the slug.")
    text_edits = {u["id"]: u for u in edits.get("units", [])}
    items = []
    translations = [{**t, **text_edits.get(t["id"], {})} for t in translations]
    for u, t in zip(units, translations):
        if u["kind"] == "omit":
            continue
        if not t["jv"].strip():
            raise ValueError(f"Missing translation for meaningful source ID {u['id']}")
        items.append({**u, "jv": t["jv"].strip(), "source_ids": [u["id"]]})
    # Explicit reviewed groups repair split words/phrases; timing never spans a long action gap by inference.
    for specification in edits.get("merge_groups", []):
        ids = specification["ids"] if isinstance(specification, dict) else specification
        indexes = [i for i, item in enumerate(items) if item["id"] in ids]
        if not indexes or len(indexes) != len(ids) or indexes != list(range(indexes[0], indexes[-1]+1)):
            raise ValueError(f"Invalid nonconsecutive merge group {ids}")
        group = items[indexes[0]:indexes[-1]+1]
        merged = {**group[0], "source_ids": ids, "end": group[-1]["end"], "jv": " ".join(x["jv"] for x in group), "ja": "".join(x["ja"] for x in group)}
        if isinstance(specification, dict) and specification.get("jv"):
            merged["jv"] = specification["jv"]
        items[indexes[0]:indexes[-1]+1] = [merged]

    cues = []
    source_duration = float(read_json(WORK / "probe.json")["format"]["duration"])
    for index, item in enumerate(items):
        # Keep attacks/action with no speech clear; never lead the next spoken line.
        start = max(0, float(item["start"]) - 0.04)
        next_start = float(items[index+1]["start"]) - options["gap"] if index+1 < len(items) else source_duration
        natural_end = float(item["end"]) + 0.18
        needed = len(item["jv"]) / options["target_cps"]
        end = min(next_start, max(natural_end, start + min(needed, max(0.9, item["end"] - start + 0.8))))
        end = min(source_duration, max(end, min(next_start, start + options["min_duration"])))
        if end <= start:
            raise ValueError(f"Invalid/overlapping source timing for ID {item['id']}")
        parts = split_text(item["jv"], options["max_line_chars"] * options["max_lines"] - 2)
        fitted = []
        for part in parts:
            try:
                wrap_lines(part)
                fitted.append(part)
            except ValueError:
                # A phrase may be shorter than 76 chars yet have no valid two-line word break.
                fitted.extend(split_text(part, max(24, math.ceil(len(part)/2))))
        parts = fitted
        # A long source statement should not linger as one subtitle for ten seconds.
        if len(parts) == 1 and end-start > options["max_duration"]:
            if len(item["jv"]) > 30:
                parts = split_text(item["jv"], max(25, math.ceil(len(item["jv"])/2)))
            else:
                end = start + options["max_duration"]
        total = sum(len(part) for part in parts)
        cursor = start
        for n, part in enumerate(parts):
            finish = end if n == len(parts)-1 else cursor + (end-start) * len(part)/total
            lines = wrap_lines(part)
            cues.append({"id": len(cues)+1, "source_ids": item["source_ids"], "start": round(cursor, 2), "end": round(finish if n == len(parts)-1 else finish-0.04, 2), "lines": lines, "kind": item["kind"], "speaker_role": item["speaker_role"]})
            cursor = finish
    write_json(WORK / "cues.json", cues)
    export_subtitles(cues, units, translations)
    validate_cues(cues)


def export_subtitles(cues, units, translations):
    base = OUT / (CONFIG["slug"] + ".jv")
    srt = "\n\n".join(f"{cue['id']}\n{timestamp(cue['start'])} --> {timestamp(cue['end'])}\n" + "\n".join(cue["lines"]) for cue in cues) + "\n"
    base.with_suffix(".jv.srt").write_text(srt, encoding="utf-8")
    vtt = "WEBVTT\n\n" + "\n\n".join(f"{cue['id']}\n{timestamp(cue['start'], '.')} --> {timestamp(cue['end'], '.')}\n" + "\n".join(html.escape(line, quote=False) for line in cue["lines"]) for cue in cues) + "\n"
    base.with_suffix(".jv.vtt").write_text(vtt, encoding="utf-8")
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 640
PlayResY: 480
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Dialogue,Arial,26,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,1.8,0.5,2,28,28,22,1
Style: Song,Arial,25,&H00E1F3FF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,1.8,0.5,8,28,28,18,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    width, height = output_dimensions()
    family = load_font(CONFIG, 26, bold=True).getname()[0]
    header = header.replace("PlayResX: 640", f"PlayResX: {width}").replace("PlayResY: 480", f"PlayResY: {height}").replace(",Arial,", f",{family},")
    rows = []
    for cue in cues:
        text = "\\N".join(line.replace("{", "(").replace("}", ")").replace("\\", "/") for line in cue["lines"])
        style = "Song" if cue["kind"] == "song" else "Dialogue"
        rows.append(f"Dialogue: 0,{ass_timestamp(cue['start'])},{ass_timestamp(cue['end'])},{style},,0,0,0,,{text}")
    base.with_suffix(".jv.ass").write_text(header + "\n".join(rows) + "\n", encoding="utf-8-sig")
    import csv
    with (OUT / (CONFIG["slug"] + ".review.tsv")).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["cue", "source IDs", "start", "end", "role", "kind", "Japanese source", "Final Javanese subtitle", "English gloss (review only)", "source review notes", "translation uncertainty", "review change"])
        source_by_id = {u["id"]: u for u in units}
        translation_by_id = {t["id"]: t for t in translations}
        for cue in cues:
            sources = [source_by_id[i] for i in cue["source_ids"]]
            texts = [translation_by_id[i] for i in cue["source_ids"]]
            writer.writerow([cue["id"], ",".join(str(i) for i in cue["source_ids"]), timestamp(cue["start"]), timestamp(cue["end"]), cue["speaker_role"], cue["kind"], " ".join(u["ja"] for u in sources), "\n".join(cue["lines"]), " ".join(t["english_gloss"] for t in texts), " | ".join(u["uncertainty"] for u in sources if u["uncertainty"]), " | ".join(t["uncertainty"] for t in texts if t["uncertainty"]), " | ".join(t["change_reason"] for t in texts if t["change_reason"])])
    print(f"Exported {len(cues)} subtitle cues.", flush=True)


def validate_cues(cues):
    issues = []
    duration = float(read_json(WORK / "probe.json")["format"]["duration"])
    previous = 0
    for cue in cues:
        span = cue["end"] - cue["start"]
        text = " ".join(cue["lines"])
        errors = []
        if cue["start"] < previous - 0.001:
            errors.append("overlap")
        if span <= 0 or cue["end"] > duration + 0.01:
            errors.append("invalid timing")
        if len(cue["lines"]) > CONFIG["subtitle"]["max_lines"] or any(len(line)>CONFIG["subtitle"]["max_line_chars"] for line in cue["lines"]):
            errors.append("line length")
        if any(c in text for c in "{}\\") or re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", text):
            errors.append("unexpected Japanese or markup")
        cps = len(text)/span if span > 0 else 999
        if cps > CONFIG["subtitle"]["max_cps"] + 0.01:
            errors.append("reading speed")
        if span < 0.65:
            errors.append("very short duration")
        if span > CONFIG["subtitle"]["max_duration"] + 0.05:
            errors.append("long duration")
        if errors:
            issues.append({"cue": cue["id"], "source_ids": cue["source_ids"], "cps": round(cps, 1), "duration": round(span, 2), "issues": errors, "text": text})
        previous = cue["end"]
    write_json(WORK / "subtitle-qa.json", {"cue_count": len(cues), "issues": issues})
    print(f"Subtitle QA: {len(issues)} cues need attention; see subtitle-qa.json.", flush=True)
    return issues


def encode(source):
    inspect(source)
    cues = read_json(WORK / "cues.json")
    issues = validate_cues(cues)
    if issues:
        raise ValueError("Resolve subtitle QA issues before encoding; see subtitle-qa.json.")
    subtitle = OUT / (CONFIG["slug"] + ".jv.ass")
    video = OUT / (CONFIG["slug"] + ".jv.480p.mp4")
    partial = video.with_suffix(".partial.mp4")
    log = WORK / "encode.log"
    options = CONFIG["encode"]
    # The path is built from a validated slug; source paths never enter filter syntax.
    relative = subtitle.relative_to(ROOT).as_posix()
    width, height = output_dimensions()
    vf = f"scale={width}:{height}:flags=lanczos,setsar=1,format=yuv420p,ass=filename='{relative}'"
    args = ["ffmpeg", "-hide_banner", "-y", "-i", source, "-map", "0:v:0", "-map", "0:a:0", "-vf", vf, "-c:v", options["codec"], "-preset", options["preset"], "-crf", options["crf"], "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", options["audio_bitrate"], "-ac", "2", "-movflags", "+faststart", "-map_metadata", "-1", "-map_chapters", "-1", "-metadata", f"title={CONFIG['series']} {CONFIG['episode']} - Javanese", "-metadata:s:a:0", "language=jpn", "-progress", str(WORK / "encode-progress.txt"), "-stats_period", "10", "-nostats", partial]
    print(f"Encoding {video.name}...", flush=True)
    with log.open("w", encoding="utf-8") as f:
        run(args, cwd=ROOT, stderr=f)
    partial.replace(video)
    print(f"Saved {video.name}: {video.stat().st_size / 1e6:.1f} MB", flush=True)


def verify(source):
    original = inspect(source)
    video = OUT / (CONFIG["slug"] + ".jv.480p.mp4")
    info = json.loads(run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", video], capture_output=True, encoding="utf-8").stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    original_video = next(s for s in original["streams"] if s["codec_type"] == "video")
    expected_width, expected_height = output_dimensions(original)
    checks = {"height_480": v["height"] == expected_height == 480, "h264_8bit": v["codec_name"] == "h264" and v["pix_fmt"] == "yuv420p", "display_aspect_preserved": v["width"] == expected_width and v.get("sample_aspect_ratio") == "1:1", "frame_rate_preserved": Fraction(v["r_frame_rate"]) == Fraction(original_video["r_frame_rate"]), "audio_present": a["codec_name"] == "aac", "duration_preserved": abs(float(info["format"]["duration"]) - float(original["format"]["duration"])) < 0.5}
    # Decode the complete deliverable, not just its header.
    decode = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"], capture_output=True, encoding="utf-8")
    checks["full_decode"] = decode.returncode == 0 and not decode.stderr.strip()
    if decode.stderr:
        (WORK / "decode-errors.log").write_text(decode.stderr, encoding="utf-8")
    else:
        (WORK / "decode-errors.log").unlink(missing_ok=True)
    report = {"checks": checks, "size_bytes": video.stat().st_size, "source_bytes": source.stat().st_size, "duration": info["format"]["duration"], "output_probe": info, "subtitle_qa": read_json(WORK / "subtitle-qa.json")}
    write_json(WORK / "verification.json", report)
    print(json.dumps({"checks": checks, "size_MB": round(video.stat().st_size/1e6, 1)}, indent=2), flush=True)
    if not all(checks.values()):
        raise ValueError("Output verification failed.")


def doctor(check_models=False):
    checks = {"project_config": CONFIG_PATH.is_file(), "ffmpeg": bool(shutil.which("ffmpeg")), "ffprobe": bool(shutil.which("ffprobe"))}
    if checks["ffmpeg"]:
        filters = run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, encoding="utf-8").stdout
        encoders = run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, encoding="utf-8").stdout
        checks["libass"] = bool(re.search(r"\bass\s", filters))
        checks["encoders"] = all(re.search(r"\b"+name+r"\s", encoders) for name in ["libx264", "aac", "flac", "libmp3lame"])
    try:
        checks["fonts"] = bool(load_font(CONFIG, 18) and load_font(CONFIG, 26, bold=True))
    except (RuntimeError, OSError):
        checks["fonts"] = False
    keys = {name: bool(os.environ.get(name)) for name in ["ELEVENLABS_API_KEY", "OPENAI_API_KEY"]}
    result = {"checks": checks, "credentials_present": keys, "network_checked": check_models, "ready": all(checks.values()) and all(keys.values())}
    if check_models:
        if not keys["OPENAI_API_KEY"]:
            result["models"] = {"error": "OPENAI_API_KEY is missing"}
            result["ready"] = False
        else:
            r = requests.get("https://api.openai.com/v1/models", headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]}, timeout=45)
            r.raise_for_status()
            available = {m["id"] for m in r.json()["data"]}
            result["models"] = {CONFIG[key]: CONFIG[key] in available for key in ["translation_model", "review_model", "crosscheck_model"]}
            result["ready"] = result["ready"] and all(result["models"].values())
    print(json.dumps(result, indent=2))
    return result


def status():
    names = ["source.json", "scribe.json", "utterances.json", "crosscheck.json", "source-reviewed.json", "translation-draft.json", "translation-reviewed.json", "cues.json", "subtitle-qa.json", "verification.json", "deliverable-manifest.json"]
    receipts = []
    if WORK.exists():
        for path in sorted(WORK.rglob("*receipt.json")):
            data = read_json(path)
            completed_cache = path.with_name(path.name.replace(".receipt.json", ".result.json")).is_file()
            receipts.append({"file": path.relative_to(WORK).as_posix(), "status": "completed" if completed_cache else data.get("status"), "response_id": data.get("id")})
        for path in sorted(set(WORK.rglob("*request.json")) | set(WORK.rglob("*.request"))):
            if path.is_file():
                data = read_json(path)
                if "status" in data:
                    receipts.append({"file": path.relative_to(WORK).as_posix(), "status": data["status"]})
    report = {"slug": CONFIG["slug"], "artifacts": {name: (WORK / name).is_file() for name in names}, "receipts": receipts}
    print(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["init", "doctor", "status", "all", "prepare", "transcribe", "segment", "crosscheck", "source-review", "translate", "review-translation", "layout", "encode", "verify"])
    parser.add_argument("--project", help="Local config path; defaults to project.json or SUBBER_PROJECT")
    parser.add_argument("--source")
    parser.add_argument("--slug")
    parser.add_argument("--series")
    parser.add_argument("--episode")
    parser.add_argument("--check-models", action="store_true", help="doctor: read-only OpenAI model access check")
    args = parser.parse_args()
    configure(args.project, required=args.stage not in {"init", "doctor"})
    if args.stage == "init":
        if CONFIG_PATH.exists():
            parser.error("The local config already exists; edit it in place or choose another --project path.")
        settings = merge_config({**CONFIG, **{k: getattr(args, k) for k in ["slug", "series", "episode", "source"] if getattr(args, k) is not None}})
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        write_json(CONFIG_PATH, settings)
        print(f"Created local config: {CONFIG_PATH}")
        return
    if args.stage == "doctor":
        if not doctor(args.check_models)["ready"]:
            raise SystemExit(1)
        return
    if args.stage == "status":
        status()
        return
    ensure_workspace()
    if args.stage in {"segment", "source-review", "translate", "review-translation", "layout"}:
        inspect(source_file(args.source))
    if args.stage == "all":
        source = source_file(args.source)
        transcribe(source)
        segment()
        crosscheck(source)
        source_review()
        translate()
        review_translation()
        layout()
        encode(source)
        verify(source)
        from check_exports import main as check_exports
        from render_samples import main as render_samples
        check_exports(["--project", str(CONFIG_PATH)])
        render_samples(["--project", str(CONFIG_PATH)])
    elif args.stage in {"segment", "source-review", "translate", "review-translation", "layout"}:
        globals()[args.stage.replace("-", "_")]()
    else:
        globals()[args.stage](source_file(args.source))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        main()
    except (ValueError, RuntimeError, FileNotFoundError, KeyError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
