"""Verify a fresh checkout without media, credentials or paid requests."""
from __future__ import annotations

import compileall
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def validate_instructions():
    skill = ROOT / ".agents/skills/jp-jv-subber/SKILL.md"
    text = skill.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "name: jp-jv-subber\n" not in text or not re.search(r"^description: .+", text, re.M) or "TODO" in text:
        raise ValueError("The project skill has invalid or unfinished metadata")
    docs = [ROOT / "README.md", ROOT / "AGENTS.md", skill, *sorted((ROOT / "docs").glob("*.md"))]
    for doc in docs:
        for target in re.findall(r"\]\(([^)]+)\)", doc.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("#"):
                continue
            target = target.split("#", 1)[0]
            if not (doc.parent / target).resolve().exists():
                raise ValueError(f"Broken local documentation link in {doc.relative_to(ROOT)}: {target}")
    print("Project skill metadata and local documentation links passed.", flush=True)


def main():
    validate_instructions()
    if not compileall.compile_dir(ROOT / "tools", quiet=1):
        raise ValueError("Python compilation failed")
    env = {key: value for key, value in os.environ.items() if key not in {"OPENAI_API_KEY", "ELEVENLABS_API_KEY", "SUBBER_PROJECT"}}
    subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=ROOT, env=env, check=True)
    subprocess.run([sys.executable, "tools/smoke_test.py"], cwd=ROOT, env=env, check=True)
    print("Offline project verification passed; no provider requests were made.")


if __name__ == "__main__":
    main()
