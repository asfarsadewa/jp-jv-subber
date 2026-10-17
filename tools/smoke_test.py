"""Offline integration using generated tones/backgrounds, never an episode or API."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
from unittest.mock import patch

from PIL import Image

import subtitle as p
from burn_subtitles import probe
from check_exports import check
from render_samples import render
from settings import merge_config


def has_subtitle_pixels(frame):
    """Bright pixels in the bottom band show that subtitles reached the encoded frame."""
    with Image.open(frame) as image:
        bottom = image.convert("L").crop((0, image.height - 100, image.width, image.height))
        return sum(bottom.histogram()[211:]) >= 100


def one_case(anamorphic=False):
    slug = "offline-smoke-" + uuid.uuid4().hex[:10]
    folder = p.ROOT / "work" / slug
    folder.mkdir(parents=True)
    source = folder / "synthetic episode's, [日本語].mkv"
    metadata = folder / "chapters.ffmeta"
    metadata.write_text(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=6000\ntitle=Synthetic test\n", encoding="utf-8")
    size, sar = ("720x480", "32/27") if anamorphic else ("640x480", "1")
    p.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x183048:s={size}:r=15:d=6",
           "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=6", "-i", metadata,
           "-map", "0:v", "-map", "1:a", "-map_metadata", "2", "-map_chapters", "2", "-vf", f"setsar={sar}",
           "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-t", "6", source], capture_output=True)
    config = merge_config({"slug": slug, "series": "Synthetic test", "episode": "test", "source": str(source), "encode": {"preset": "ultrafast"}})
    config_path = folder / "project.json"
    p.write_json(config_path, config)
    project = p.configure(config_path, out=folder / "subbed")
    p.prepare(project, source)
    p.write_json(folder / "scribe.json", {"language_code": "jpn", "words": [
        {"text": "行こう。", "start": 0.5, "end": 1.7, "type": "word", "speaker_id": "speaker_0"},
        {"text": "[足音]", "start": 1.8, "end": 2.5, "type": "audio_event"},
        {"text": "ここで待って。", "start": 3.0, "end": 4.5, "type": "word", "speaker_id": "speaker_1"}
    ]})
    p.transcribe(project, source)  # A saved transcript must not submit a new API request.
    p.segment(project)
    raw = p.read_json(folder / "utterances.json")
    source_units = [{"id": u["id"], "ja": u["ja"], "speaker_role": "synthetic role", "kind": "dialogue",
                     "uncertainty": "Synthetic fixture; there is no recorded speech.", "timing_suspect": False} for u in raw]
    p.write_json(folder / "source-reviewed.json", {"episode_context": "Offline synthetic fixture, not an AI-reviewed episode", "glossary": [], "speaker_caveat": "fixture", "units": source_units})
    p.write_json(folder / "translation-reviewed.json", {"overall_note": "Hand-authored synthetic captions; not a translation quality test", "units": [
        {"id": 1, "jv": "Ayo, padha mangkat!", "english_gloss": "Let's go", "change_reason": "", "uncertainty": "fixture"},
        {"id": 2, "jv": "Entenana ing kene.", "english_gloss": "Wait here", "change_reason": "", "uncertainty": "fixture"}
    ]})
    p.layout(project)
    p.encode(project, source)
    p.verify(project, source)
    title = probe(project.export(".jv.480p.mp4"))["format"].get("tags", {}).get("title")
    if title != "Synthetic test test - Javanese":
        raise ValueError(f"The episode MP4 has an unexpected title: {title}")
    check(project)
    render(project)
    if not has_subtitle_pixels(folder / "visual-qa" / "cue-001.png"):
        raise ValueError("The encoded frame has no burned subtitle pixels.")
    # The public burn command must work without any episode config or API keys,
    # accept awkward input paths, and put real subtitle pixels into the MP4.
    burn_jobs = []
    for extension in ["ass", "srt", "vtt"]:
        subtitle = folder / f"captions 'ngoko', [日本語].{extension}"
        before = project.export(f".jv.{extension}").read_bytes()
        subtitle.write_bytes(before)
        output = project.out / f"standalone-{extension}.mp4"
        env = {**os.environ, "SUBBER_PROJECT": str(folder / "absent-project.json")}
        command = [sys.executable, str(p.ROOT / "tools/subtitle.py"), "burn", "--source", str(source), "--subtitles", str(subtitle), "--output", str(output)]
        result = subprocess.run(command, env=env, capture_output=True, encoding="utf-8")
        if result.returncode:
            raise ValueError(f"The standalone {extension} burn failed:\n{result.stdout}\n{result.stderr}")
        burn_jobs += [Path(line.removeprefix("Burn evidence: ")) for line in result.stdout.splitlines() if line.startswith("Burn evidence: ")]
        if subtitle.read_bytes() != before:
            raise ValueError("Burning changed the original subtitle file.")
        if "title" in probe(output)["format"].get("tags", {}):
            raise ValueError(f"The standalone {extension} burn wrote a title from the default config.")
        preview = folder / "visual-qa" / f"standalone-{extension}.png"
        p.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", "1", "-i", output, "-frames:v", "1", preview], capture_output=True)
        if not has_subtitle_pixels(preview):
            raise ValueError(f"The standalone {extension} burn has no visible subtitle pixels.")
        print(f"Standalone {extension.upper()} burn: decode and subtitle pixels passed")
    if len(burn_jobs) != 3:
        raise ValueError("The standalone burns did not report their evidence folders.")
    # A passing run leaves nothing behind. A failing run keeps its folder for diagnosis.
    for job in burn_jobs:
        if job.parent != p.ROOT / "work" / "burns":
            raise ValueError(f"The burn evidence folder is outside work/burns: {job}")
        shutil.rmtree(job)
    shutil.rmtree(folder)
    print(f"Offline integration: passed ({'anamorphic 16:9' if anamorphic else '4:3'})")


def main():
    for name in ["OPENAI_API_KEY", "ELEVENLABS_API_KEY", "SUBBER_PROJECT"]:
        os.environ.pop(name, None)
    with patch("requests.sessions.Session.request", side_effect=RuntimeError("Network is forbidden in offline integration")):
        one_case(False)
        one_case(True)


if __name__ == "__main__":
    main()
