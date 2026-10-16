---
name: jp-jv-subber
description: Produce and revise Javanese subtitles for Japanese tokusatsu, or burn existing ASS/SRT/VTT subtitles into a verified 480p video. Use for this repository's episode processing, source corrections, subtitle edits, hardsubs, encoding and pipeline maintenance.
---

# Japanese to Javanese subtitling

Work from the repository root containing `tools/subtitle.py` and `project.example.json`. The scripts and [production runbook](../../../docs/workflow.md) are the workflow; read the runbook when processing or repairing an episode. No prior chat, personal directories or global skill is needed.

## Begin or resume

- For an existing subtitle file that only needs hardsubs, use `python tools/subtitle.py burn --source "input/episode.mkv" --subtitles "captions.jv.ass"`. No config or provider keys are required. Optional `--output` selects the MP4; an existing destination requires `--overwrite`. This mode uses supplied wording/timing as-is and does not establish editorial approval. Read the runbook's burn section for settings and evidence, then inspect actual burned frames.
- Identify the source and local config. Preserve an existing episode's slug, settings, overrides and caches. For a new episode, use `init` with a unique slug. Several episode configs can live under ignored `local/` directories.
- Run `doctor` and `status` with that config. Doctor is offline unless `--check-models` is requested. Keys come from environment variables; never print them.
- Resume the smallest necessary stage. Do not run `all` merely to refresh a finished episode. Check raw artifacts and API receipts before any new paid request.

## Editorial workflow

1. Prepare audio and sampled frames. Use Scribe v2 for Japanese words, timestamps and anonymous speaker IDs, then obtain independent ASR and source review.
2. Inspect the Japanese transcript, source review, glossary and sampled scenes. Labels can merge several characters; identify people from local evidence, not a fixed speaker-label mapping. Recheck impossible durations, syllables split across long action gaps, names and attack calls with bounded audio checks. Alignment measures timing of supplied text; it does not prove that text was spoken. Preserve unresolved doubts instead of inventing dialogue.
3. Add corrections to the episode's `source-overrides.json`, keeping raw ASR intact. Update local `series_context`, confirmed `glossary`, `keyterms` and measured `music_ranges` as evidence warrants. Never reuse another series's names or assumed song timings.
4. Translate directly from Japanese with full episode context, then review the translation. Default to everyday ngoko in Latin script and appropriate polite speech. Use Javanese grammar and core vocabulary; familiar technology loanwords are fine. Preserve agency, negation, threats, meaningful commands, song meaning and consistent names. English glosses stay in the review table.
5. Run layout and inspect `subtitle-qa.json`. Correct text, safe timing or explicit phrase groups before encoding. Prefer natural clauses, at most two 38-character lines, about 15 chars/s and no more than 20 chars/s. Do not remove key plot information just to pass a metric.

## Deliver and verify

Encode the reviewed ASS into a 480p H.264/AAC MP4, preserving display aspect ratio, frame rate and Japanese audio. Encoding stops on subtitle QA problems. Verify the complete output, run export-parity/fast-start checks, render samples and actually inspect those images. Generating previews is not human visual acceptance.

Save MP4, SRT/VTT/ASS and bilingual TSV in `subbed/`; keep provenance in `work/<slug>/`. Report the actual file, size, verification and remaining material doubts. Distinguish model-assisted review from native-speaker approval.

## Recovery and maintenance

- Saved OpenAI response IDs and content-hashed results are resumable. If a submission has no known outcome, inspect its receipt/provider history before anything that could submit it again. Do not delete a receipt to bypass the guard. Atomic receipt creation prevents simultaneous duplicate submissions.
- After confirming a definite rejection, correct the cause and archive its receipt locally before retrying. A timed-out upload remains uncertain until checked. Keys must be validated before a new submission is claimed.
- Source changes require downstream source/translation review. Javanese wording/layout edits usually need only layout, encode, verify, export checks and rendered inspection.
- Account model access varies. Check configured IDs against current official docs/access rather than inventing replacements. Record an agreed change in the local config.
- For code changes, run `python tools/verify_project.py`. Its generated tones/backgrounds and hand-authored captions test mechanics without credentials or copyright media; they do not establish ASR or linguistic accuracy.
- Publish only within an authorized request. Follow [release checks](../../../docs/release.md). Local configs, episode overrides, media, transcripts, outputs, frames, API receipts and credentials stay outside the tracked include set. The explicitly approved `docs/assets/zaborger-javanese.jpg` README preview is the sole media exception; this does not authorize sharing further episode material.
