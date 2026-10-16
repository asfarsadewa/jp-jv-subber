"""Local episode settings and fonts. Importing this module writes no files."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import re

from PIL import ImageFont

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = json.loads((ROOT / "project.example.json").read_text(encoding="utf-8"))


def merge_config(data):
    result = copy.deepcopy(DEFAULTS)
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key].update(value)
        else:
            result[key] = value
    validate_config(result)
    return result


def validate_config(config):
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", config.get("slug", "")):
        raise ValueError("slug must contain only lowercase words/numbers joined by hyphens")
    if config.get("source_language") != "ja" or config.get("target_language") != "jv":
        raise ValueError("This pipeline supports Japanese (ja) to Javanese (jv).")
    if config["encode"]["height"] != 480 or config["encode"]["codec"] != "libx264":
        raise ValueError("This export pipeline requires 480p H.264 (libx264).")
    if not isinstance(config.get("glossary"), list) or not isinstance(config.get("keyterms"), list):
        raise ValueError("glossary and keyterms must be lists")
    if not 0 <= config["encode"]["crf"] <= 51:
        raise ValueError("encode.crf must be between 0 and 51")
    options = config["subtitle"]
    if not 0 < options["target_cps"] <= options["max_cps"] or options["max_lines"] != 2:
        raise ValueError("Require 0 < target_cps <= max_cps and two subtitle lines")
    if options["max_line_chars"] < 10 or options["gap"] < 0:
        raise ValueError("Invalid subtitle line length or cue gap")
    if not 0 < options["min_duration"] <= options["max_duration"]:
        raise ValueError("Invalid minimum/maximum subtitle duration")
    for start, end in config.get("music_ranges", []):
        if not math.isfinite(start + end) or start < 0 or end <= start:
            raise ValueError("music_ranges entries must be [start, end] in seconds")


def load_font(config, size, bold=False):
    custom = config.get("font_paths", {}).get("bold" if bold else "regular")
    if custom:
        return ImageFont.truetype(str(Path(custom).expanduser()), size)
    names = [
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for name in names:
        if Path(name).is_file():
            return ImageFont.truetype(name, size)
    raise RuntimeError("Install Arial, Liberation Sans or DejaVu Sans, or set font_paths in the local project config.")
