"""Check that the subtitle exports match. Check that the MP4 is fast-start for progressive playback."""
import argparse
import csv
import hashlib
import html
import json

import subtitle as pipeline
from subtitle import read_json, write_json, timestamp, ass_timestamp, run_cli
from burn_subtitles import is_faststart, mp4_boxes


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check(project):
    cues = read_json(project.work / "cues.json")
    srt = project.export(".jv.srt").read_text(encoding="utf-8").strip().split("\n\n")
    vtt = project.export(".jv.vtt").read_text(encoding="utf-8").strip().split("\n\n")[1:]
    ass = [line for line in project.export(".jv.ass").read_text(encoding="utf-8-sig").splitlines() if line.startswith("Dialogue:")]
    with project.export(".review.tsv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    require(len(cues) == len(srt) == len(vtt) == len(ass) == len(rows), "The subtitle exports have different cue counts.")
    for cue, s, v, a, row in zip(cues, srt, vtt, ass, rows):
        expected = "\n".join(cue["lines"])
        error = f"The subtitle exports disagree at cue {cue['id']}."
        require(s.splitlines()[2:] == cue["lines"], error)
        require(html.unescape("\n".join(v.splitlines()[2:])) == expected, error)
        require(a.split(",", 9)[9].replace("\\N", "\n") == expected, error)
        require(row["Final Javanese subtitle"] == expected, error)
        require(row["start"] == timestamp(cue["start"]) and row["end"] == timestamp(cue["end"]), error)
        require(s.splitlines()[1] == f"{timestamp(cue['start'])} --> {timestamp(cue['end'])}", error)
        require(v.splitlines()[1] == f"{timestamp(cue['start'], '.')} --> {timestamp(cue['end'], '.')}", error)
        require(a.split(",")[1:3] == [ass_timestamp(cue["start"]), ass_timestamp(cue["end"])], error)

    boxes = mp4_boxes(project.export(".jv.480p.mp4"))
    require(is_faststart(boxes), "The MP4 is not fast-start. Move the moov box before the mdat box.")
    manifest = {"subtitle_text_and_timing_parity": True, "cue_count": len(cues), "mp4_faststart": True, "mp4_boxes": boxes, "files": {}}
    for path in sorted(project.out.glob(f"{project.slug}.*")):
        if "partial" not in path.name:
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            manifest["files"][path.name] = {"bytes": path.stat().st_size, "sha256": digest}
    write_json(project.work / "deliverable-manifest.json", manifest)
    print(json.dumps({"subtitle_export_parity": True, "cue_count": len(cues), "faststart": True, "mp4_boxes": boxes}, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", help="Path to the local project config. The default is SUBBER_PROJECT or project.json.")
    args = parser.parse_args(argv)
    check(pipeline.configure(args.project))


if __name__ == "__main__":
    run_cli(main)
