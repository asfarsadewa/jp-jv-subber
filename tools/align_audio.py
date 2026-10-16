"""Align a reviewed Japanese passage to a bounded audio clip using ElevenLabs."""
import argparse
import hashlib
import json
import time

import requests

import subtitle as pipeline
from subtitle import api_key, ensure_unclaimed, extract_clip, read_json, run_cli, source_file, submit_once, write_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("start", type=float)
    parser.add_argument("end", type=float)
    parser.add_argument("text")
    parser.add_argument("--project", help="Path to the local project config. The default is SUBBER_PROJECT or project.json.")
    parser.add_argument("--source", help="Source video. Relative paths start at the repository root.")
    args = parser.parse_args(argv)
    if args.end <= args.start:
        parser.error("Set end to a time after start.")
    project = pipeline.configure(args.project)
    source = source_file(project, args.source)
    pipeline.inspect(project, source)
    folder = project.work / "alignment"
    folder.mkdir(exist_ok=True)
    digest = hashlib.sha256(f"{args.start}|{args.end}|{args.text}".encode()).hexdigest()[:12]
    target = folder / f"{digest}.json"
    if not target.exists():
        audio = folder / f"{digest}.mp3"
        extract_clip(source, args.start, args.end - args.start, audio)
        receipt = folder / f"{digest}.request.json"
        ensure_unclaimed(receipt, "alignment")
        key = api_key("ELEVENLABS_API_KEY")
        with audio.open("rb") as f:
            r = submit_once(receipt, "alignment", lambda: requests.post(
                "https://api.elevenlabs.io/v1/forced-alignment", headers={"xi-api-key": key}, data={"text": args.text},
                files={"file": (audio.name, f, "audio/mpeg")}, timeout=(30, 300)), start=args.start, end=args.end, text=args.text)
        data = r.json()
        for kind in ["words", "characters"]:
            for item in data.get(kind, []):
                item["start"] = round(item["start"] + args.start, 2)
                item["end"] = round(item["end"] + args.start, 2)
        data.update({"clip_start": args.start, "clip_end": args.end, "text": args.text})
        write_json(target, data)
        write_json(receipt, {"status": "completed", "completed_at": time.time()})
    data = read_json(target)
    print(json.dumps({"loss": data.get("loss"), "words": data.get("words")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run_cli(main)
