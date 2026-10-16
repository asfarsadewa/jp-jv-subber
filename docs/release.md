# Public release checks

Publishing requires an explicit request for the destination. Normal subtitling does not authorize a push or sharing media.

The repository tracks only tooling, prompts, local skill/instructions, docs, config examples, dependencies, CI and tests. It also tracks the explicitly approved `docs/assets/zaborger-javanese.jpg` README preview. That exact-path exception contains two actual burned frames from the completed local Zaborger export. The exception does not extend to full media, transcripts, subtitles or further frames. Local settings/overrides, local/input/work/subbed directories and credentials stay untracked. The Git index and commit tree are authoritative.

```sh
python tools/verify_project.py
python tools/public_audit.py
git diff --cached --stat
git diff --cached
```

Stage an explicit reviewed list. The audit checks allowed paths, tracked content, credential/personal-path patterns and matches to current environment secrets. Its sole binary exception must be a JPEG preview of bounded size. The JPEG must have no EXIF or comment metadata. Inspect that preview visually before publication. For failures, the audit prints paths or variable names. It never prints values.

Run [Gitleaks](https://github.com/gitleaks/gitleaks) with redaction against the intended public history:

```sh
gitleaks git --redact --no-banner --log-opts=--all
```

Use `gitleaks dir --redact --no-banner PATH_TO_TRACKED_SNAPSHOT` for a Git export before the first commit. Do not copy private work/media into that snapshot. If Gitleaks finds a leak, repair the unpublished history. Scan again before you push.

Check that a clean clone installs locked dependencies. Check that the clone runs `python tools/verify_project.py` without configs, provider keys or an episode. CI runs those no-credit checks and Gitleaks. Never supply provider keys to CI just to make fixtures pass.

After you push, verify public visibility, the exact remote default branch/head and CI. Check the remote tree. Do not rely only on local ignore rules.
