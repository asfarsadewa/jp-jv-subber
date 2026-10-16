# Working in this repository

For Japanese-to-Javanese subtitling, audio/source corrections, subtitle editing, encoding or pipeline work, read [.agents/skills/jp-jv-subber/SKILL.md](.agents/skills/jp-jv-subber/SKILL.md) first. It is the project-local workflow and needs no earlier chat or user-level skill installation.

- Run `python tools/subtitle.py status` against the selected local config before submitting API jobs. Reuse transcripts and saved request IDs.
- Default to everyday ngoko in Latin script, with polite speech where relationships warrant it. Preserve Japanese audio and export a compact 480p MP4 in `subbed/`.
- For existing ASS/SRT/VTT subtitles, `python tools/subtitle.py burn --source VIDEO --subtitles SUBTITLES` creates and verifies a 480p hard-sub MP4 without API calls or a config. Use `--output` for a destination and `--overwrite` only when replacing an export is intended.
- Keep media, transcripts, outputs, receipts, personal paths and credentials out of Git. The explicitly approved `docs/assets/zaborger-javanese.jpg` README preview is the sole media exception. Local episode settings and overrides are ignored.
- Inspect the relevant stage and callers before changing code. Run `python tools/verify_project.py` afterward. Synthetic tests prove mechanics, not translation quality.
- For a requested public push, run `python tools/public_audit.py`, inspect the tracked diff and run a redacted secret scan as described in [docs/release.md](docs/release.md). This document does not authorize publication on its own.
