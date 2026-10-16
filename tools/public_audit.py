"""Audit the Git index; never copy private working files into a release."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ROOT_FILES = {"README.md", "AGENTS.md", ".gitignore", ".gitattributes", ".env.example", "project.example.json", "requirements.txt", "requirements.lock.txt"}
DIRECTORIES = {"tools", "tests", "prompts", "docs", "examples", ".agents", ".github"}
TEXT_EXTENSIONS = {".py", ".md", ".txt", ".json", ".yaml", ".yml"}
PRIVATE_NAMES = {"project.json", "source-overrides.json", "subtitle-overrides.json"}
PUBLIC_PREVIEWS = {"docs/assets/zaborger-javanese.jpg"}


def public_path(name):
    relative = Path(name)
    allowed = name in ROOT_FILES or name in PUBLIC_PREVIEWS or (len(relative.parts) > 1 and relative.parts[0] in DIRECTORIES and relative.suffix in TEXT_EXTENSIONS)
    return allowed and relative.name not in PRIVATE_NAMES and "__pycache__" not in relative.parts and ".." not in relative.parts


def check_preview(content):
    """The approved README image is the only binary publication exception."""
    with Image.open(io.BytesIO(content)) as preview:
        if preview.format != "JPEG" or preview.size != (1280, 600):
            raise ValueError("unexpected preview format or dimensions")
        if preview.getexif() or set(preview.info) - {"jfif", "jfif_version", "jfif_unit", "jfif_density"}:
            raise ValueError("preview contains unexpected metadata")
        preview.verify()


def main():
    if subprocess.run(["git", "diff", "--quiet", "--exit-code"], cwd=ROOT).returncode:
        raise ValueError("Tracked working files differ from the index; stage the reviewed version before auditing.")
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode("utf-8").split("\0")
    paths = sorted(name for name in names if name)
    if not paths:
        raise ValueError("No tracked files to audit; stage the explicit intended include set first.")
    findings, manifest = [], {}
    secrets = {name: value for name, value in os.environ.items() if re.search(r"KEY|TOKEN|SECRET|PASSWORD", name, re.I) and len(value) >= 16}
    patterns = {
        "provider token": re.compile(r"(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
        "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        "personal home path": re.compile(r"(?:[A-Za-z]:[\\/]Users[\\/]|/Users/|/home/)[A-Za-z0-9_.-]+[\\/]"),
    }
    for name in paths:
        if not public_path(name):
            findings.append({"file": name, "reason": "outside public include set"})
            continue
        staged = subprocess.check_output(["git", "show", ":" + name], cwd=ROOT)
        if len(staged) > 300000 or (name not in PUBLIC_PREVIEWS and b"\0" in staged):
            findings.append({"file": name, "reason": "unexpected large or binary file"})
            continue
        for key, value in secrets.items():
            if value.encode("utf-8") in staged:
                findings.append({"file": name, "reason": "matches environment credential " + key})
        if name in PUBLIC_PREVIEWS:
            try:
                check_preview(staged)
            except (ValueError, OSError) as error:
                findings.append({"file": name, "reason": str(error)})
            manifest[name] = {"bytes": len(staged), "sha256": hashlib.sha256(staged).hexdigest()}
            continue
        try:
            content = staged.decode("utf-8-sig")
        except UnicodeDecodeError:
            findings.append({"file": name, "reason": "public text file is not UTF-8"})
            continue
        for label, pattern in patterns.items():
            if pattern.search(content):
                findings.append({"file": name, "reason": label})
        manifest[name] = {"bytes": len(staged), "sha256": hashlib.sha256(staged).hexdigest()}
    report = {"passed": not findings, "tracked_files": len(paths), "total_bytes": sum(v["bytes"] for v in manifest.values()), "findings": findings, "files": manifest}
    folder = ROOT / "work/publication"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "tracked-audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ["passed", "tracked_files", "total_bytes", "findings"]}, indent=2))
    if findings:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
