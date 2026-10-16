# Production runbook

## Episode setup

Read `AGENTS.md` and the local skill. Install `requirements.lock.txt`, provide FFmpeg/ffprobe and supply the two provider keys through the environment. No script automatically loads `.env`.

```sh
python tools/subtitle.py init --slug show-01 --series "Show name" --episode 01 --source "input/episode.mkv"
python tools/subtitle.py doctor --check-models
python tools/subtitle.py status
```

Init never overwrites a config. Add confirmed Japanese `keyterms`, a `glossary` of objects with `ja`, `latin`, `jv`, `meaning`, and concise `series_context` to ignored `project.json`. `music_ranges` is a list such as `[[12,58],[1454,1494]]` in source-video seconds, measured for this episode. Empty ranges are allowed.

For several episodes, use `init --project local/show-02/project.json` and pass that `--project` to every command, including helpers. `SUBBER_PROJECT` is an alternative environment setting. Source paths are relative to the repository root; absolute paths are accepted but remain local. After extraction, the remembered source is usable without repeating `--source`.

Each slug owns `work/<slug>/`. Source path, size and modification time bind that cache to its media. A changed source requires a new slug. Overrides live beside the selected config and include its exact `episode` slug.

## Transcribe and review Japanese

```sh
python tools/subtitle.py prepare
python tools/subtitle.py transcribe
python tools/subtitle.py segment
python tools/subtitle.py crosscheck
python tools/subtitle.py source-review
```

Preparation extracts mono 16 kHz lossless audio and evenly sampled frames. Scribe preserves words, timestamps, anonymous speakers and audio events. Independent ASR is a second opinion. Sampled scenes are not continuous video understanding.

Read `japanese-transcript.txt`, `source-reviewed.json` and raw ASR. Look for names, implausible timing, split syllables, negation, competing readings and doubtful speaker identity. Do not discard real dialogue merely because it is difficult.

```sh
python tools/check_audio.py 120 128
python tools/check_audio.py 120 128 --model scribe_v2
python tools/check_audio.py 120 128 --model whisper-1
python tools/align_audio.py 120 128 "reviewed Japanese passage"
```

`gpt-transcribe` is the default bounded ASR; Whisper supplies timestamps. `--model gpt-audio-1.5` gives a listening opinion and gloss. Claimed confidence is not ground truth. Sparse action, songs and silence can cause hallucinations; prompt text or remembered plot must not be mistaken for recovered speech.

Copy `examples/source-overrides.example.json` beside the config and set its slug. Each `units` entry has an existing utterance `id` and only corrected fields: `ja`, `start`, `end`, `kind`, `speaker_role`, `timing_suspect` or `uncertainty`. Keep evidence in the note. Meaningful utterances require positive, ordered timings. Preserve raw transcripts.

## Translate and review

```sh
python tools/subtitle.py translate
python tools/subtitle.py review-translation
```

Each draft batch receives full episode text, context, glossary and register. The full draft is reviewed against Japanese. Source/prompt/model changes may create a legitimately new paid request, so inspect existing results before rerunning.

Use ngoko with polite speech where warranted. Avoid Indonesian grammar with scattered Javanese words. Maintain tone and social relationships across ASR label changes. Check Japanese and scene context as well as English glosses.

## Layout and corrections

```sh
python tools/subtitle.py layout
```

Inspect `subtitle-qa.json` and the exported review TSV. Structural checks and reading-speed limits gate encoding. Proportional subdivision of long utterances is a timing starting point; inspect phrase boundaries and synchronization.

Copy `examples/subtitle-overrides.example.json` beside the config. Correct `jv` by utterance ID. `merge_groups` accepts consecutive visible IDs such as `[6,7]`, or an object with `ids` and a reviewed joined `jv` string. Join only dialogue that belongs together; do not span unrelated action. TSV rows follow actual final cues, including edits and splits.

After Japanese meaning changes, rerun translation and review before layout. After Javanese wording/layout changes, rerun layout and downstream checks. A changed prompt does not require discarding raw ASR.

## Encode and prove the result

```sh
python tools/subtitle.py encode
python tools/subtitle.py verify
python tools/check_exports.py
python tools/render_samples.py
```

Defaults: original display aspect at 480 pixels high, original frame rate, 8-bit H.264 CRF 27/slow, 64 kb/s Japanese AAC and fast-start MP4. Inherited chapter references are removed to avoid broken QuickTime chapter tracks. Font measurement and ASS use the same available family. Song subtitles use the top of the frame.

Outputs: `subbed/<slug>.jv.480p.mp4`, `.jv.srt`, `.jv.vtt`, `.jv.ass` and `.review.tsv`. `verification.json` records complete decode and geometry/audio/timing checks; `deliverable-manifest.json` records export parity and hashes. `visual-qa/` contains actual burned frames. Open them and check clipping, contrast, names, longest lines, dialogue and songs. Inspect problem passages in local video/audio tools when timing remains doubtful.

All runs the sequence and emits evidence but does not replace editorial or visual inspection. Fix failed gates rather than disabling them. Report remaining dialogue doubts honestly.

## Resume paid jobs safely

Start with status. Transcripts and content-hashed results are reused. An OpenAI background receipt with a response ID resumes through retrieval, rather than creating another job. Completed result caches need no provider call.

A submitting receipt without a response ID has an unknown outcome. Inspect provider history and local evidence before resubmitting. A socket timeout does not prove failure. Do not delete or silently overwrite that receipt. After confirming a definite rejection, archive its receipt under ignored work, correct the cause and rerun. Keep the record for provenance. Atomic receipts prevent two processes claiming the same job.

Background review can take several minutes. Keep response IDs and receipts so a restart resumes the job. Stop on terminal errors, malformed output, missing/duplicate IDs or refusal and inspect diagnostics.
