"""Extract representative burned subtitle frames for visual review."""
import argparse

from PIL import Image, ImageDraw, ImageOps

import subtitle as pipeline
from settings import load_font
from subtitle import read_json, run, timestamp


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project")
    args = parser.parse_args(argv)
    pipeline.configure(args.project)
    WORK, OUT, CONFIG = pipeline.WORK, pipeline.OUT, pipeline.CONFIG
    cues = read_json(WORK / "cues.json")
    video = OUT / (CONFIG["slug"] + ".jv.480p.mp4")
    folder = WORK / "visual-qa"
    folder.mkdir(exist_ok=True)
    if not cues:
        raise ValueError("No subtitle cues to preview.")
    candidates = [cues[0], cues[-1], max(cues, key=lambda c: max(len(s) for s in c["lines"]))]
    for kind in ["song", "narration", "shout"]:
        candidates += [c for c in cues if c["kind"] == kind][:1]
    candidates += [c for c in cues if len(c["lines"]) == 2][:1]
    candidates += [cues[len(cues)//3], cues[2*len(cues)//3]]
    samples = list({c["id"]: c for c in candidates}.values())[:8]
    font = load_font(CONFIG, 18)
    for sheet_number, offset in enumerate(range(0, len(samples), 4), 1):
        subset = samples[offset:offset+4]
        sheet = Image.new("RGB", (1280, 1012), "#161616")
        draw = ImageDraw.Draw(sheet)
        for j, cue in enumerate(subset):
            t = (cue["start"] + cue["end"]) / 2
            frame = folder / f"cue-{cue['id']:03d}.png"
            run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", t, "-i", video, "-frames:v", "1", frame])
            x, y = (j % 2) * 640, (j // 2) * 506
            with Image.open(frame) as im:
                thumb = ImageOps.contain(im, (640, 480))
                sheet.paste(thumb, (x+(640-thumb.width)//2, y+(480-thumb.height)//2))
            draw.text((x+8, y+482), f"Cue {cue['id']} / {timestamp(t)}", font=font, fill="white")
        target = folder / f"burned-samples-{sheet_number}.jpg"
        sheet.save(target, quality=92)
        print(target)


if __name__ == "__main__":
    main()
