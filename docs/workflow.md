# Production runbook

## Episode setup

Read `AGENTS.md` and the local skill. Install `requirements.lock.txt`. Provide FFmpeg/ffprobe. Supply the two provider keys through the environment. No script loads `.env` automatically.

```sh
python tools/subtitle.py init --slug show-01 --series "Show name" --episode 01 --source "input/episode.mkv"
python tools/subtitle.py doctor --check-models
python tools/subtitle.py status
```

Init never overwrites a config. In the ignored `project.json`, add confirmed Japanese `keyterms`. Add a `glossary` of objects with `ja`, `latin`, `jv` and `meaning`. Add a concise `series_context`. `music_ranges` is a list in source-video seconds, such as `[[12,58],[1454,1494]]`. Measure the ranges for this episode. The tool accepts empty ranges. The tool rejects unknown config keys. Relative `font_paths` start at the repository root.

For several episodes, use `init --project local/show-02/project.json`. Pass that `--project` to every command, including helpers. `SUBBER_PROJECT` is an alternative environment setting. Source paths are relative to the repository root. The tools also accept absolute paths. Absolute paths remain local. After extraction, the tools remember the source. Later commands do not need `--source` again.

Each slug owns `work/<slug>/`. Source path, size and modification time bind that cache to its media. A changed source requires a new slug. Overrides live beside the selected config. Each override file includes the exact `episode` slug of that config.

## Transcribe and review Japanese

```sh
python tools/subtitle.py prepare
python tools/subtitle.py transcribe
python tools/subtitle.py segment
python tools/subtitle.py crosscheck
python tools/subtitle.py source-review
```

Preparation extracts mono 16 kHz lossless audio and evenly sampled frames. Scribe preserves words, timestamps, anonymous speakers and audio events. Independent ASR gives a second opinion. Sampled scenes do not give continuous video understanding.

Read `japanese-transcript.txt`, `source-reviewed.json` and raw ASR. Look for names, implausible timing, split syllables, negation, competing readings and doubtful speaker identity. Do not discard real dialogue only because it is difficult.

```sh
python tools/check_audio.py 120 128
python tools/check_audio.py 120 128 --model scribe_v2
python tools/check_audio.py 120 128 --model whisper-1
python tools/align_audio.py 120 128 "reviewed Japanese passage"
```

`gpt-transcribe` is the default bounded ASR. Whisper supplies timestamps. `--model gpt-audio-1.5` gives a listening opinion and gloss. Claimed confidence is not ground truth. Sparse action, songs and silence can cause hallucinations. Do not mistake prompt text or remembered plot for recovered speech.

Copy `examples/source-overrides.example.json` beside the config. Set its slug. Each `units` entry has an existing utterance `id`. Add only the corrected fields: `ja`, `start`, `end`, `kind`, `speaker_role`, `timing_suspect` or `uncertainty`. Keep evidence in the note. Meaningful utterances require positive, ordered timings. Preserve raw transcripts.

## Translate and review

```sh
python tools/subtitle.py translate
python tools/subtitle.py review-translation
```

Each draft batch receives the full episode text, context, glossary and register. The review checks the full draft against the Japanese. Source/prompt/model changes may create a legitimately new paid request. Inspect existing results before you rerun.

Use ngoko. Use polite speech if it is warranted. Avoid Indonesian grammar with scattered Javanese words. Maintain tone and social relationships across ASR label changes. Check the Japanese, the scene context and the English glosses.

## Layout and corrections

```sh
python tools/subtitle.py layout
```

Inspect `subtitle-qa.json` and the exported review TSV. Structural checks and reading-speed limits gate encoding. The layout stage subdivides long utterances proportionally. Treat that timing as a starting point. Inspect phrase boundaries and synchronization.

Copy `examples/subtitle-overrides.example.json` beside the config. Correct `jv` by utterance ID. `merge_groups` accepts consecutive visible IDs such as `[6,7]`. It also accepts an object with `ids` and a reviewed joined `jv` string. Join only dialogue that belongs together. Do not span unrelated action. TSV rows follow actual final cues, including edits and splits.

If the Japanese meaning changes, rerun translation and review before layout. If Javanese wording/layout changes, rerun layout and downstream checks. A changed prompt does not require you to discard raw ASR.

## Encode and prove the result

`layout` produces editable subtitle files. `encode` burns the reviewed ASS into the final video:

```sh
python tools/subtitle.py encode
python tools/subtitle.py verify
python tools/check_exports.py
python tools/render_samples.py
```

`encode` verifies the new MP4 before it replaces an earlier export. If verification fails, the earlier export stays in `subbed/`.

The encoder uses these defaults:

- Original display aspect at 480 pixels high
- Original frame rate
- 8-bit H.264 CRF 27/slow
- 64 kb/s Japanese AAC
- Fast-start MP4

The encoder removes inherited chapter references. This prevents broken QuickTime chapter tracks. Font measurement and ASS use the same available family. Song subtitles use the top of the frame.

The outputs are `subbed/<slug>.jv.480p.mp4`, `.jv.srt`, `.jv.vtt`, `.jv.ass` and `.review.tsv`. `verification.json` records complete decode and geometry/audio/timing checks. `deliverable-manifest.json` records export parity and hashes. `visual-qa/` contains actual burned frames. Open these frames. Check clipping, contrast, names, longest lines, dialogue and songs. If timing remains doubtful, inspect problem passages in local video/audio tools.

`all` runs the sequence. It also emits evidence. It does not replace editorial or visual inspection. Fix failed gates. Do not disable them. Report remaining dialogue doubts honestly.

## Burn an existing subtitle file

For an existing ASS, SRT or VTT file, use the local-only `burn` stage. The stage also works in a fresh checkout with no `project.json` or provider credentials:

```sh
python tools/subtitle.py burn --source "input/episode.mkv" --subtitles "subbed/episode-01.jv.ass"
python tools/subtitle.py burn --source "input/episode.mkv" --subtitles "captions.jv.srt" --output "subbed/episode-01.jv.480p.mp4" --overwrite
```

The default output is `subbed/<subtitle-stem>.480p.mp4`. The tool resolves relative paths from the repository root. Optional `--project` selects encoding/font settings. If you do not pass `--project`, the tool uses an existing default config. If no default config exists, the tool uses the repository defaults. Use UTF-8 subtitle files. The tool keeps ASS styles and placement. SRT/VTT subtitles receive a white, outlined style. This stage does not alter or translate the supplied subtitles. It does not read transcription/review caches. The encoder rejects a subtitle file if no timed cue overlaps the video.

Existing outputs require `--overwrite`. Neither input can be the destination. A verified temporary MP4 replaces the destination only after full decoding, geometry, frame-rate, AAC and fast-start checks pass. Failed exports leave an earlier destination intact. The reviewed episode pipeline uses the same encoder. The tool stores evidence in `work/burns/<job-id>/`. This folder contains `encode.log`, progress, `verification.json`, subtitle staging and `burn-manifest.json`. `burn-manifest.json` records input identity and subtitle/output hashes. Burn-in verification proves media mechanics. Inspect actual frames and synchronization separately.

## Resume paid jobs safely

Start with status. The tools reuse transcripts and content-hashed results. If an OpenAI background receipt has a response ID, the tool resumes the job through retrieval. Retrieval does not create another job. Completed result caches need no provider call.

A submitting receipt without a response ID has an unknown outcome. Inspect provider history and local evidence before you resubmit. A socket timeout does not prove failure. Do not delete or silently overwrite that receipt. When you confirm a definite rejection, archive its receipt under ignored work. Then correct the cause. Then rerun. Keep the record for provenance. Atomic receipts prevent two processes from claiming the same job.

Background review can take several minutes. Keep response IDs and receipts so that a restart resumes the job. Stop on terminal errors, malformed output, missing/duplicate IDs or refusal. Then inspect the diagnostics.
