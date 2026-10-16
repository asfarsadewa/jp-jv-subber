"""Local episode settings and fonts. Importing this module writes no files."""
from __future__ import annotations

import copy
import math
from pathlib import Path
import re

from PIL import ImageFont

ROOT = Path(__file__).resolve().parent.parent
# project.example.json documents these defaults; a test keeps the two identical.
DEFAULTS = {
    "series": "My tokusatsu series",
    "episode": "01",
    "slug": "episode-01",
    "source": "",
    "source_language": "ja",
    "target_language": "jv",
    "register": "Everyday standard Javanese ngoko in Latin script, with polite speech where the relationship warrants it. Avoid strong regional slang and unnecessary Indonesian vocabulary.",
    "series_context": "",
    "glossary": [],
    "keyterms": [],
    "music_ranges": [],
    "transcription_model": "scribe_v2",
    "crosscheck_model": "gpt-4o-transcribe",
    "translation_model": "gpt-6-luna",
    "review_model": "gpt-6.1-sol",
    "reasoning_effort": "high",
    "font_paths": {"regular": "", "bold": ""},
    "subtitle": {"max_line_chars": 38, "max_lines": 2, "target_cps": 15, "max_cps": 20, "min_duration": 0.9, "max_duration": 6.5, "gap": 0.08},
    "encode": {"height": 480, "codec": "libx264", "crf": 27, "preset": "slow", "audio_bitrate": "64k"},
}


def merge_config(data):
    unknown = sorted(set(data) - set(DEFAULTS))
    unknown += sorted(f"{key}.{name}" for key, value in data.items() if isinstance(value, dict) and isinstance(DEFAULTS.get(key), dict) for name in set(value) - set(DEFAULTS[key]))
    if unknown:
        raise ValueError(f"The config contains unknown keys: {', '.join(unknown)}. Check their spelling.")
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
        raise ValueError("Write the slug in lowercase letters and numbers, with single hyphens between words.")
    if config.get("source_language") != "ja" or config.get("target_language") != "jv":
        raise ValueError("Set source_language to ja and target_language to jv. The pipeline supports only this pair.")
    if config["encode"]["height"] != 480 or config["encode"]["codec"] != "libx264":
        raise ValueError("Set encode.height to 480 and encode.codec to libx264.")
    if not isinstance(config.get("glossary"), list) or not isinstance(config.get("keyterms"), list):
        raise ValueError("Write glossary and keyterms as JSON lists.")
    if not 0 <= config["encode"]["crf"] <= 51:
        raise ValueError("Set encode.crf to a value from 0 to 51.")
    options = config["subtitle"]
    if not 0 < options["target_cps"] <= options["max_cps"]:
        raise ValueError("Set subtitle.target_cps above 0 and no higher than subtitle.max_cps.")
    if options["max_lines"] != 2:
        raise ValueError("Set subtitle.max_lines to 2.")
    if options["max_line_chars"] < 10:
        raise ValueError("Set subtitle.max_line_chars to 10 or more.")
    if options["gap"] < 0:
        raise ValueError("Set subtitle.gap to 0 or more.")
    if not 0 < options["min_duration"] <= options["max_duration"]:
        raise ValueError("Set subtitle.min_duration above 0 and no higher than subtitle.max_duration.")
    for start, end in config.get("music_ranges", []):
        if not math.isfinite(start + end) or start < 0 or end <= start:
            raise ValueError("Write each music_ranges entry as [start, end] in seconds, with end after start.")


def font_path(value):
    """Resolve a configured font file. Relative paths start at the repository root."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def load_font(config, size, bold=False):
    custom = config.get("font_paths", {}).get("bold" if bold else "regular")
    if custom:
        return ImageFont.truetype(str(font_path(custom)), size)
    names = [
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for name in names:
        if Path(name).is_file():
            return ImageFont.truetype(name, size)
    raise RuntimeError("Install Arial, Liberation Sans or DejaVu Sans. You can also set font_paths in the local project config.")
