# Public release checks

Publishing requires an explicit request for the destination. Normal subtitling does not authorize a push or sharing media.

Tracked files are limited to tooling, prompts, local skill/instructions, docs, config examples, dependencies, CI and tests. Local settings/overrides, local/input/work/subbed directories, media, subtitles and credentials stay untracked. The Git index and commit tree are authoritative.

```sh
python tools/verify_project.py
python tools/public_audit.py
git diff --cached --stat
git diff --cached
```

Stage an explicit reviewed list. The audit checks allowed paths, tracked content, credential/personal-path patterns and matches to current environment secrets. Failures print paths or variable names, never values.

Run [Gitleaks](https://github.com/gitleaks/gitleaks) with redaction against the intended public history:

```sh
gitleaks git --redact --no-banner --log-opts=--all
```

Use `gitleaks dir --redact --no-banner PATH_TO_TRACKED_SNAPSHOT` for a Git export before the first commit. Do not copy private work/media into that snapshot. If a leak is found, repair unpublished history and scan again before pushing.

Check that a clean clone installs locked dependencies and runs `python tools/verify_project.py` without configs, provider keys or an episode. CI runs those no-credit checks and Gitleaks. Never supply provider keys to CI just to make fixtures pass.

After pushing, verify public visibility, exact remote default branch/head and CI. Check the remote tree, not merely local ignore rules.
