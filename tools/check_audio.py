"""Run targeted Japanese ASR with timestamps. Use the result as evidence for source corrections."""
import argparse
import base64
import json
from pathlib import Path
import time

import requests

import subtitle as pipeline
from settings import ROOT
from subtitle import api_key, ensure_unclaimed, extract_clip, read_json, run_cli, source_file, submit_once, write_json

MODELS = ["whisper-1", "gpt-transcribe", "scribe_v2", "gpt-audio-1.5"]


def send_check(model, audio, key, config):
    """Send one bounded clip to the chosen ASR or listening model."""
    with audio.open("rb") as f:
        if model == "gpt-audio-1.5":
            prompt = (ROOT / "prompts" / "listening-check.txt").read_text(encoding="utf-8").strip()
            system = f"{prompt} Series context: {config['series']}. Confirmed terms: {', '.join(config['keyterms'])}"
            question = "What exactly is said? Be conservative, distinguishing audible wording from inference."
            payload = {"model": model, "modalities": ["text"], "max_completion_tokens": 700, "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": [{"type": "text", "text": question}, {"type": "input_audio", "input_audio": {"data": base64.b64encode(f.read()).decode("ascii"), "format": "mp3"}}]},
            ]}
            return requests.post("https://api.openai.com/v1/chat/completions", headers={"Authorization": "Bearer " + key}, json=payload, timeout=(30, 300))
        if model == "scribe_v2":
            data = {"model_id": "scribe_v2", "language_code": "ja", "timestamps_granularity": "word", "tag_audio_events": "false", "diarize": "false"}
            return requests.post("https://api.elevenlabs.io/v1/speech-to-text", headers={"xi-api-key": key}, data=data, files={"file": (audio.name, f, "audio/mpeg")}, timeout=(30, 300))
        params = [("model", model)]
        if model == "whisper-1":
            params += [("language", "ja"), ("response_format", "verbose_json"), ("timestamp_granularities[]", "word"), ("timestamp_granularities[]", "segment")]
        elif model == "gpt-transcribe":
            params += [("languages[]", "ja")]
        return requests.post("https://api.openai.com/v1/audio/transcriptions", headers={"Authorization": "Bearer " + key}, data=params,
                             files={"file": (audio.name, f, "audio/mpeg")}, timeout=(30, 300))


def scribe_segments(words):
    """Group Scribe words into phrases at pauses longer than 0.7 seconds."""
    groups, group = [], []
    for w in words:
        if w.get("type") != "word":
            continue
        if group and w["start"] - group[-1]["end"] > 0.7:
            groups.append({"start": group[0]["start"], "end": group[-1]["end"], "text": "".join(x["text"] for x in group)})
            group = []
        group.append(w)
    if group:
        groups.append({"start": group[0]["start"], "end": group[-1]["end"], "text": "".join(x["text"] for x in group)})
    return groups


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("start", type=float)
    parser.add_argument("end", type=float)
    parser.add_argument("--source", help="Source video. Relative paths start at the repository root.")
    parser.add_argument("--project", help="Path to the local project config. The default is SUBBER_PROJECT or project.json.")
    parser.add_argument("--model", choices=MODELS, default="gpt-transcribe")
    args = parser.parse_args(argv)
    if args.end <= args.start:
        parser.error("Set end to a time after start.")
    project = pipeline.configure(args.project)
    source = source_file(project, args.source)
    pipeline.inspect(project, source)
    folder = project.work / "audio-checks"
    folder.mkdir(exist_ok=True)
    label = f"{args.start:08.2f}-{args.end:08.2f}".replace(".", "_")
    stem = folder / f"{label}-{args.model}"
    target = Path(str(stem) + ".json")
    if not target.exists():
        audio = Path(str(stem) + ".mp3")
        extract_clip(source, args.start, args.end - args.start, audio)
        receipt = Path(str(target) + ".request")
        ensure_unclaimed(receipt, "audio check")
        key = api_key("ELEVENLABS_API_KEY" if args.model == "scribe_v2" else "OPENAI_API_KEY")
        r = submit_once(receipt, "audio check", lambda: send_check(args.model, audio, key, project.config))
        data = r.json()
        if args.model == "gpt-audio-1.5":
            data["text"] = data["choices"][0]["message"]["content"]
        for kind in ["words", "segments"]:
            for item in data.get(kind, []):
                item["start"] = round(item["start"] + args.start, 2)
                item["end"] = round(item["end"] + args.start, 2)
        write_json(target, data)
        write_json(receipt, {"status": "completed", "completed_at": time.time()})
    data = read_json(target)
    result = {"text": data.get("text"), "segments": [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in data.get("segments", [])]}
    if args.model == "scribe_v2":
        result["segments"] = scribe_segments(data.get("words", []))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run_cli(main)
