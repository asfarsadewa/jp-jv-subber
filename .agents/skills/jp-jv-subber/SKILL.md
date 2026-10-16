---
name: jp-jv-subber
description: This skill produces and revises Javanese subtitles for Japanese tokusatsu. It also burns existing ASS/SRT/VTT subtitles into a verified 480p video. Use it for this repository's episode processing, source corrections, subtitle edits, hardsubs, encoding and pipeline maintenance.
---

# Japanese to Javanese subtitling

Work from the repository root that contains `tools/subtitle.py` and `project.example.json`. The scripts and the [production runbook](../../../docs/workflow.md) are the workflow. Read the runbook when you process or repair an episode. You do not need a prior chat, personal directories or a global skill.

## Begin or resume

- If an existing subtitle file only needs hardsubs, use `python tools/subtitle.py burn --source "input/episode.mkv" --subtitles "captions.jv.ass"`. This mode needs no config or provider keys. Optional `--output` selects the MP4. An existing destination requires `--overwrite`. This mode uses supplied wording/timing as-is. It does not establish editorial approval. Read the runbook's burn section for settings and evidence. Then inspect actual burned frames.
- Identify the source and local config. Preserve an existing episode's slug, settings, overrides and caches. For a new episode, use `init` with a unique slug. Several episode configs can live under ignored `local/` directories.
- Run `doctor` and `status` with that config. Doctor runs offline if you do not request `--check-models`. Keys come from environment variables. Never print them.
- Resume the smallest necessary stage. Do not run `all` merely to refresh a finished episode. Check raw artifacts and API receipts before any new paid request.

## Editorial workflow

1. Prepare audio and sampled frames. Use Scribe v2 for Japanese words, timestamps and anonymous speaker IDs. Then obtain independent ASR and source review.
2. Inspect the Japanese transcript, source review, glossary and sampled scenes. Labels can merge several characters. Identify people from local evidence. Do not rely on a fixed speaker-label mapping. Recheck impossible durations, syllables split across long action gaps, names and attack calls with bounded audio checks. Alignment measures the timing of supplied text. It does not prove that someone spoke that text. Preserve unresolved doubts. Do not invent dialogue.
3. Add corrections to the episode's `source-overrides.json`. Keep raw ASR intact. If evidence warrants it, update local `series_context`, confirmed `glossary`, `keyterms` and measured `music_ranges`. Never reuse another series's names or assumed song timings.
4. Translate directly from Japanese with full episode context. Then review the translation. Default to everyday ngoko in Latin script. Use polite speech if it is appropriate. Use Javanese grammar and core vocabulary. Familiar technology loanwords are fine. Preserve agency, negation, threats, meaningful commands, song meaning and consistent names. English glosses stay in the review table.
5. Run layout. Inspect `subtitle-qa.json`. Correct text, safe timing or explicit phrase groups before encoding. Prefer natural clauses. Use at most two 38-character lines. Target about 15 chars/s, with a maximum of 20 chars/s. Do not remove key plot information just to pass a metric.

## Deliver and verify

Encode the reviewed ASS into a 480p H.264/AAC MP4. Preserve display aspect ratio, frame rate and Japanese audio. The encoder stops on subtitle QA problems. Verify the complete output. Run export-parity/fast-start checks. Render samples. Then open and inspect those images. Preview generation does not count as human visual acceptance.

Save the MP4, SRT/VTT/ASS and bilingual TSV in `subbed/`. Keep provenance in `work/<slug>/`. Report the actual file, size, verification and remaining material doubts. Distinguish model-assisted review from native-speaker approval.

## Recovery and maintenance

- Saved OpenAI response IDs and content-hashed results are resumable. If a submission has no known outcome, inspect its receipt/provider history before anything that could submit it again. Do not delete a receipt to bypass the guard. Atomic receipt creation prevents simultaneous duplicate submissions.
- After you confirm a definite rejection, correct the cause. Archive its receipt locally before you retry. A timed-out upload remains uncertain until you check it. Code must validate keys before it claims a new submission.
- Source changes require downstream source/translation review. Javanese wording/layout edits usually need only layout, encode, verify, export checks and rendered inspection.
- Account model access varies. Check configured IDs against current official docs/access. Do not invent replacements. Record an agreed change in the local config.
- For code changes, run `python tools/verify_project.py`. Its generated tones/backgrounds and hand-authored captions test mechanics without credentials or copyright media. They do not establish ASR or linguistic accuracy.
- Publish only within an authorized request. Follow the [release checks](../../../docs/release.md). Local configs, episode overrides, media, transcripts, outputs, frames, API receipts and credentials stay outside the tracked include set. The explicitly approved `docs/assets/zaborger-javanese.jpg` README preview is the sole media exception. This exception does not authorize sharing further episode material.
