"""Offline integration using generated tones/backgrounds, never an episode or API."""
from __future__ import annotations

import os
from pathlib import Path
import uuid
from unittest.mock import patch

from PIL import Image

import subtitle as p
from settings import merge_config


def one_case(anamorphic=False):
    slug = "offline-smoke-" + uuid.uuid4().hex[:10]
    folder = p.ROOT / "work" / slug
    folder.mkdir(parents=True)
    source = folder / "synthetic.mkv"
    metadata = folder / "chapters.ffmeta"
    metadata.write_text(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=6000\ntitle=Synthetic test\n", encoding="utf-8")
    size, sar = ("720x480", "32/27") if anamorphic else ("640x480", "1")
    p.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x183048:s={size}:r=15:d=6", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=6", "-i", metadata, "-map", "0:v", "-map", "1:a", "-map_metadata", "2", "-map_chapters", "2", "-vf", f"setsar={sar}", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-t", "6", source], capture_output=True)
    config = merge_config({"slug": slug, "series": "Synthetic test", "episode": "test", "source": str(source), "encode": {"preset": "ultrafast"}})
    config_path = folder / "project.json"
    p.write_json(config_path, config)
    p.configure(config_path)
    p.OUT = folder / "subbed"
    p.prepare(source)
    p.write_json(folder / "scribe.json", {"language_code": "jpn", "words": [
        {"text": "行こう。", "start": 0.5, "end": 1.7, "type": "word", "speaker_id": "speaker_0"},
        {"text": "[足音]", "start": 1.8, "end": 2.5, "type": "audio_event"},
        {"text": "ここで待って。", "start": 3.0, "end": 4.5, "type": "word", "speaker_id": "speaker_1"}
    ]})
    p.transcribe(source)  # A saved transcript must not submit a new API request.
    p.segment()
    raw = p.read_json(folder / "utterances.json")
    source_units = [{"id": u["id"], "ja": u["ja"], "speaker_role": "synthetic role", "kind": "dialogue", "uncertainty": "Synthetic fixture; there is no recorded speech.", "timing_suspect": False} for u in raw]
    p.write_json(folder / "source-reviewed.json", {"episode_context": "Offline synthetic fixture, not an AI-reviewed episode", "glossary": [], "speaker_caveat": "fixture", "units": source_units})
    p.write_json(folder / "translation-reviewed.json", {"overall_note": "Hand-authored synthetic captions; not a translation quality test", "units": [
        {"id": 1, "jv": "Ayo, padha mangkat!", "english_gloss": "Let's go", "change_reason": "", "uncertainty": "fixture"},
        {"id": 2, "jv": "Entenana ing kene.", "english_gloss": "Wait here", "change_reason": "", "uncertainty": "fixture"}
    ]})
    p.layout()
    p.encode(source)
    p.verify(source)
    from check_exports import main as check_exports
    from render_samples import main as render_samples
    check_exports(["--project", str(config_path)])
    render_samples(["--project", str(config_path)])
    with Image.open(folder / "visual-qa" / "cue-001.png") as image:
        bottom = image.convert("L").crop((0, image.height-100, image.width, image.height))
        if sum(bottom.histogram()[211:]) < 100:
            raise ValueError("Burned subtitle pixels were not found in the actual encoded frame.")
    p.write_json(folder / "offline-proof.json", {"network_disabled": True, "synthetic_only": True, "subtitle_pixels_detected": True, "anamorphic": anamorphic})
    print(f"Offline integration passed ({'anamorphic 16:9' if anamorphic else '4:3'}): {folder}")


def main():
    for name in ["OPENAI_API_KEY", "ELEVENLABS_API_KEY", "SUBBER_PROJECT"]:
        os.environ.pop(name, None)
    with patch("requests.sessions.Session.request", side_effect=RuntimeError("Network is forbidden in offline integration")):
        one_case(False)
        one_case(True)


if __name__ == "__main__":
    main()
