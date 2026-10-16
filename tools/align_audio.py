"""Align a reviewed Japanese passage to a bounded audio clip using ElevenLabs."""
import argparse
import hashlib
import os
import sys
import time

import requests

import subtitle as pipeline
from subtitle import source_file, run, write_json, read_json, claim_request, api_key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("start", type=float)
    parser.add_argument("end", type=float)
    parser.add_argument("text")
    parser.add_argument("--project")
    parser.add_argument("--source")
    args = parser.parse_args()
    if args.end <= args.start:
        parser.error("end must be after start")
    pipeline.configure(args.project)
    source = source_file(args.source)
    pipeline.inspect(source)
    WORK = pipeline.WORK
    folder = WORK / "alignment"
    folder.mkdir(exist_ok=True)
    digest = hashlib.sha256(f"{args.start}|{args.end}|{args.text}".encode()).hexdigest()[:12]
    target = folder / f"{digest}.json"
    if not target.exists():
        audio = folder / f"{digest}.mp3"
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", args.start, "-i", source, "-t", args.end-args.start, "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "64k", audio])
        receipt = folder / f"{digest}.request.json"
        if receipt.exists():
            raise RuntimeError("Check the earlier alignment outcome before retrying.")
        key = api_key("ELEVENLABS_API_KEY")
        claim_request(receipt, {"status": "submitting", "start": args.start, "end": args.end, "text": args.text, "started_at": time.time()})
        with audio.open("rb") as f:
            r = requests.post("https://api.elevenlabs.io/v1/forced-alignment", headers={"xi-api-key": key}, data={"text": args.text}, files={"file": (audio.name, f, "audio/mpeg")}, timeout=(30, 300))
        if r.status_code != 200:
            write_json(receipt, {"status": "failed", "error": r.text[:1500]})
            raise RuntimeError(f"Alignment failed: {r.status_code}")
        data = r.json()
        for kind in ["words", "characters"]:
            for item in data.get(kind, []):
                item["start"] = round(item["start"] + args.start, 2)
                item["end"] = round(item["end"] + args.start, 2)
        data.update({"clip_start": args.start, "clip_end": args.end, "text": args.text})
        write_json(target, data)
        write_json(receipt, {"status": "completed"})
    data = read_json(target)
    print({"loss": data.get("loss"), "words": data.get("words")})


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
