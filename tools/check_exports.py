"""Verify all subtitle exports agree and the MP4 is ready for progressive playback."""
import csv
import argparse
import hashlib
import html
import json

import subtitle as pipeline
from subtitle import read_json, write_json, timestamp, ass_timestamp, run_cli
from burn_subtitles import mp4_boxes


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project")
    args = parser.parse_args(argv)
    pipeline.configure(args.project)
    CONFIG, OUT, WORK = pipeline.CONFIG, pipeline.OUT, pipeline.WORK
    stem = CONFIG["slug"]
    cues = read_json(WORK / "cues.json")
    srt = (OUT / f"{stem}.jv.srt").read_text(encoding="utf-8").strip().split("\n\n")
    vtt = (OUT / f"{stem}.jv.vtt").read_text(encoding="utf-8").strip().split("\n\n")[1:]
    ass = [line for line in (OUT / f"{stem}.jv.ass").read_text(encoding="utf-8-sig").splitlines() if line.startswith("Dialogue:")]
    with (OUT / f"{stem}.review.tsv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    require(len(cues) == len(srt) == len(vtt) == len(ass) == len(rows), "Subtitle export cue counts differ")
    for cue, s, v, a, row in zip(cues, srt, vtt, ass, rows):
        expected = "\n".join(cue["lines"])
        error = f"Subtitle export mismatch at cue {cue['id']}"
        require(s.splitlines()[2:] == cue["lines"], error)
        require(html.unescape("\n".join(v.splitlines()[2:])) == expected, error)
        require(a.split(",", 9)[9].replace("\\N", "\n") == expected, error)
        require(row["Final Javanese subtitle"] == expected, error)
        require(row["start"] == timestamp(cue["start"]) and row["end"] == timestamp(cue["end"]), error)
        require(s.splitlines()[1] == f"{timestamp(cue['start'])} --> {timestamp(cue['end'])}", error)
        require(v.splitlines()[1] == f"{timestamp(cue['start'], '.')} --> {timestamp(cue['end'], '.')}", error)
        require(a.split(",")[1:3] == [ass_timestamp(cue["start"]), ass_timestamp(cue["end"])], error)

    video = OUT / f"{stem}.jv.480p.mp4"
    boxes = mp4_boxes(video)
    require("moov" in boxes and "mdat" in boxes and boxes.index("moov") < boxes.index("mdat"), "MP4 must be fast-start")
    manifest = {"subtitle_text_and_timing_parity": True, "cue_count": len(cues), "mp4_faststart": True, "mp4_boxes": boxes, "files": {}}
    for path in sorted(OUT.glob(f"{stem}.*")):
        if "partial" not in path.name:
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            manifest["files"][path.name] = {"bytes": path.stat().st_size, "sha256": digest}
    write_json(WORK / "deliverable-manifest.json", manifest)
    print(json.dumps({"subtitle_export_parity": True, "cue_count": len(cues), "faststart": True, "mp4_boxes": boxes}, indent=2))


if __name__ == "__main__":
    run_cli(main)
