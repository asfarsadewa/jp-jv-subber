"""Verify a fresh checkout without media, credentials or paid requests."""
from __future__ import annotations

import ast
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
        raise ValueError("The project skill has invalid or unfinished metadata.")
    docs = [ROOT / "README.md", ROOT / "AGENTS.md", skill, *sorted((ROOT / "docs").glob("*.md"))]
    for doc in docs:
        for target in re.findall(r"\]\(([^)]+)\)", doc.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("#"):
                continue
            target = target.split("#", 1)[0]
            if not (doc.parent / target).resolve().exists():
                raise ValueError(f"{doc.relative_to(ROOT)} links to a missing local file: {target}")
    print("Skill metadata and doc links: passed", flush=True)


def unused_imports(path):
    """List imported names that a module never reads; a stdlib stand-in for a linter."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)) and getattr(node, "module", None) != "__future__":
            for alias in node.names:
                imported[(alias.asname or alias.name).split(".")[0]] = node.lineno
    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    return [f"{path.relative_to(ROOT)}:{line} imports {name} but never uses it." for name, line in imported.items() if name not in used]


def main():
    validate_instructions()
    if not compileall.compile_dir(ROOT / "tools", quiet=1):
        raise ValueError("Python compilation failed.")
    unused = [problem for folder in ("tools", "tests") for path in sorted((ROOT / folder).glob("*.py")) for problem in unused_imports(path)]
    if unused:
        raise ValueError("\n".join(unused))
    env = {key: value for key, value in os.environ.items() if key not in {"OPENAI_API_KEY", "ELEVENLABS_API_KEY", "SUBBER_PROJECT"}}
    subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=ROOT, env=env, check=True)
    subprocess.run([sys.executable, "tools/smoke_test.py"], cwd=ROOT, env=env, check=True)
    print("Offline verification: passed with no provider requests")


if __name__ == "__main__":
    main()
