# Working in this repository

For Japanese-to-Javanese subtitling, audio/source corrections, subtitle editing, encoding or pipeline work, read [.agents/skills/jp-jv-subber/SKILL.md](.agents/skills/jp-jv-subber/SKILL.md) first. The skill is the project-local workflow. It does not need an earlier chat or a user-level skill installation.

- Run `python tools/subtitle.py status` against the selected local config before you submit API jobs. Reuse transcripts and saved request IDs.
- Default to everyday ngoko in Latin script. Use polite speech if relationships warrant it. Preserve the Japanese audio. Export a compact 480p MP4 in `subbed/`.
- For existing ASS/SRT/VTT subtitles, use `python tools/subtitle.py burn --source VIDEO --subtitles SUBTITLES`. This command creates and verifies a 480p hard-sub MP4. It does not need API calls or a config. Use `--output` to set a destination. Use `--overwrite` only if you intend to replace an export.
- Keep media, transcripts, outputs, receipts, personal paths and credentials out of Git. The explicitly approved `docs/assets/zaborger-javanese.jpg` README preview is the sole media exception. Git ignores local episode settings and overrides.
- Before you change code, inspect the relevant stage and its callers. Run `python tools/verify_project.py` afterward. Synthetic tests prove mechanics. They do not prove translation quality.
- For a requested public push, run `python tools/public_audit.py`. Then inspect the tracked diff. Then run a redacted secret scan as [docs/release.md](docs/release.md) describes. This document does not authorize publication on its own.
