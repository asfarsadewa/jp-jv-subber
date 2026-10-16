"""Local subtitle burn-in and media verification; never calls a provider API."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import uuid
from fractions import Fraction

from settings import ROOT, load_font


def probe(path, packets=False):
    args = ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json"]
    if packets:
        args += ["-show_packets", "-show_entries", "packet=pts_time,duration_time:stream=codec_type"]
    result = subprocess.run([*args, str(path)], check=True, capture_output=True, encoding="utf-8")
    return json.loads(result.stdout)


def dimensions(info, config):
    stream = next(s for s in info["streams"] if s["codec_type"] == "video")
    sar = stream.get("sample_aspect_ratio", "1:1")
    sar = Fraction(sar.replace(":", "/")) if sar not in {"0:1", "N/A"} else Fraction(1)
    ratio = Fraction(stream["width"], stream["height"]) * sar
    height = config["encode"]["height"]
    return max(2, round(float(ratio) * height / 2) * 2), height


def mp4_boxes(video):
    boxes, offset = [], 0
    length = video.stat().st_size
    with video.open("rb") as stream:
        while offset < length:
            stream.seek(offset)
            header = stream.read(8)
            if len(header) != 8:
                raise ValueError("Truncated MP4 box header")
            size = int.from_bytes(header[:4], "big")
            boxes.append(header[4:8].decode("ascii"))
            header_size = 8
            if size == 1:
                extended = stream.read(8)
                if len(extended) != 8:
                    raise ValueError("Truncated extended MP4 box header")
                size, header_size = int.from_bytes(extended, "big"), 16
            if size == 0:
                size = length - offset
            if size < header_size or offset + size > length:
                raise ValueError("Invalid MP4 box size")
            offset += size
    return boxes


def verify_video(source, video, config, folder, original=None):
    original = original or probe(source)
    info = probe(video)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    original_video = next(s for s in original["streams"] if s["codec_type"] == "video")
    width, height = dimensions(original, config)
    boxes = mp4_boxes(video)
    checks = {
        "height_480": v["height"] == height == 480,
        "h264_8bit": v["codec_name"] == "h264" and v["pix_fmt"] == "yuv420p",
        "display_aspect_preserved": v["width"] == width and v.get("sample_aspect_ratio") == "1:1",
        "frame_rate_preserved": Fraction(v["r_frame_rate"]) == Fraction(original_video["r_frame_rate"]),
        "audio_present": a["codec_name"] == "aac",
        "duration_preserved": abs(float(info["format"]["duration"]) - float(original["format"]["duration"])) < 0.5,
        "video_and_audio_only": len(info["streams"]) == 2,
        "faststart": "moov" in boxes and "mdat" in boxes and boxes.index("moov") < boxes.index("mdat"),
    }
    decode = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"], capture_output=True, encoding="utf-8")
    checks["full_decode"] = decode.returncode == 0 and not decode.stderr.strip()
    errors = folder / "decode-errors.log"
    if decode.stderr:
        errors.write_text(decode.stderr, encoding="utf-8")
    else:
        errors.unlink(missing_ok=True)
    report = {"checks": checks, "size_bytes": video.stat().st_size, "source_bytes": source.stat().st_size, "duration": info["format"]["duration"], "output_probe": info}
    (folder / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"checks": checks, "size_MB": round(video.stat().st_size / 1e6, 1)}, indent=2), flush=True)
    if not all(checks.values()):
        raise ValueError(f"Output verification failed; inspect {folder / 'verification.json'}")
    return report


def validate_paths(source, subtitle, video, overwrite):
    if not source.is_file() or not subtitle.is_file():
        raise ValueError("The source video and subtitle file must both exist.")
    if subtitle.suffix.lower() not in {".ass", ".srt", ".vtt"}:
        raise ValueError("Use an ASS, SRT or VTT subtitle file encoded as UTF-8.")
    if video.suffix.lower() != ".mp4":
        raise ValueError("The output filename must end in .mp4.")
    for original in (source, subtitle):
        if video == original or (video.exists() and video.samefile(original)):
            raise ValueError("The output must not replace either input file.")
    if video.exists() and not overwrite:
        raise ValueError("Output already exists. Choose --output or use --overwrite to replace it.")


def encode_video(source, subtitle, video, config, folder, original=None, overwrite=False, verify_output=False, title=None):
    source, subtitle, video = (Path(p).expanduser().resolve() for p in (source, subtitle, video))
    validate_paths(source, subtitle, video, overwrite)
    original = original or probe(source)
    if not all(any(s["codec_type"] == kind for s in original["streams"]) for kind in ("video", "audio")):
        raise ValueError("The source must contain both video and Japanese audio.")
    folder.mkdir(parents=True, exist_ok=True)
    # Only a generated ASCII filename enters FFmpeg filter syntax. Original paths
    # may contain Windows drive letters, Unicode, quotes, commas or spaces.
    try:
        text = subtitle.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Save the subtitle file as UTF-8 before burning it.") from error
    staged = folder / ("captions" + subtitle.suffix.lower())
    staged.write_text(text, encoding="utf-8")
    try:
        packets = probe(staged, packets=True).get("packets", [])
    except subprocess.CalledProcessError as error:
        raise ValueError("FFprobe could not read the subtitle file; check its format and timestamps.") from error
    duration = float(original["format"]["duration"])
    spans = [(float(p.get("pts_time", "nan")), float(p.get("duration_time", 0))) for p in packets]
    if not any(math.isfinite(start) and math.isfinite(span) and span > 0 and max(0, start) < min(duration, start + span) for start, span in spans):
        raise ValueError("The subtitle file has no timed cues overlapping the source video.")
    width, height = dimensions(original, config)
    # Run from the job directory, keeping filter paths independent of its name.
    vf = f"scale={width}:{height}:flags=lanczos,setsar=1,format=yuv420p,subtitles=filename='{staged.name}'"
    if staged.suffix != ".ass":
        font = load_font(config, 26, bold=True).getname()[0]
        if any(char in font for char in "',:=\\"):
            raise ValueError("The subtitle font family contains unsupported filter characters.")
        vf += f":force_style='FontName={font},FontSize=16,Bold=-1,Outline=1.1,Shadow=0,MarginV=13'"
    fonts = folder / "fonts"
    for value in config.get("font_paths", {}).values():
        if value:
            fonts.mkdir(exist_ok=True)
            path = Path(value).expanduser().resolve()
            shutil.copyfile(path, fonts / (hashlib.sha256(str(path).encode()).hexdigest()[:12] + path.suffix))
    if fonts.is_dir():
        vf += ":fontsdir=fonts"
    video.parent.mkdir(parents=True, exist_ok=True)
    partial = video.with_name(video.stem + "." + uuid.uuid4().hex[:10] + ".partial.mp4")
    options = config["encode"]
    # Only the reviewed episode pipeline knows the real series; standalone burns get no title.
    metadata = ["-metadata", f"title={title}"] if title else []
    args = ["ffmpeg", "-hide_banner", "-y", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0", "-vf", vf, "-c:v", options["codec"], "-preset", options["preset"], "-crf", str(options["crf"]), "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", options["audio_bitrate"], "-ac", "2", "-movflags", "+faststart", "-map_metadata", "-1", "-map_chapters", "-1", *metadata, "-metadata:s:a:0", "language=jpn", "-progress", str(folder / "encode-progress.txt"), "-stats_period", "10", "-nostats", str(partial)]
    log = folder / "encode.log"
    print(f"Burning {subtitle.name} into {video.name}...", flush=True)
    try:
        with log.open("w", encoding="utf-8") as stream:
            subprocess.run(args, cwd=folder, stderr=stream, check=True)
        if verify_output:
            verify_video(source, partial, config, folder, original)
        if video.exists() and not overwrite:
            raise ValueError("Output was created by another run; choose another --output.")
        partial.replace(video)
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"FFmpeg failed; inspect {log}") from error
    finally:
        partial.unlink(missing_ok=True)
    print(f"Saved {video}: {video.stat().st_size / 1e6:.1f} MB", flush=True)
    return video


def burn(source, subtitle, config, output=None, overwrite=False):
    source = Path(source).expanduser().resolve()
    subtitle = Path(subtitle).expanduser()
    subtitle = (ROOT / subtitle).resolve()
    output = Path(output).expanduser() if output else ROOT / "subbed" / (subtitle.stem + ".480p.mp4")
    output = (ROOT / output).resolve()
    validate_paths(source, subtitle, output, overwrite)
    stat = source.stat()
    identity = {"path": str(source), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    digest = hashlib.sha256(subtitle.read_bytes()).hexdigest()
    job = hashlib.sha256(json.dumps([identity, digest, str(output), config], sort_keys=True).encode()).hexdigest()[:16]
    folder = ROOT / "work" / "burns" / job
    video = encode_video(source, subtitle, output, config, folder, overwrite=overwrite, verify_output=True)
    with video.open("rb") as stream:
        output_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    manifest = {"source": identity, "subtitle": {"path": str(subtitle), "sha256": digest}, "output": {"path": str(video), "bytes": video.stat().st_size, "sha256": output_digest}, "verification": "verification.json", "editorial_review": "Supplied subtitles used as-is; burn-in does not perform translation or linguistic review."}
    (folder / "burn-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Burn-in evidence: {folder}", flush=True)
    return video
