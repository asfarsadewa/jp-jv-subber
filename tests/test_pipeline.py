"""Cache isolation, source identity and paid-request recovery invariants."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import burn_subtitles
import subtitle as p
from settings import merge_config


def make_project(root, **config):
    """A project whose config, cache and exports all live under one temporary folder."""
    return p.Project(root / "project.json", merge_config(config), root, root / "subbed")


def write_episode(work):
    """One reviewed, translated utterance with a six-second probe; no media."""
    p.write_json(work / "utterances.json", [{"id": 1, "start": 1.0, "end": 2.5, "speaker": "speaker_0", "ja": "行こう。", "word_indices": [0]}])
    p.write_json(work / "source-reviewed.json", {"episode_context": "fixture", "glossary": [], "speaker_caveat": "fixture", "units": [{"id": 1, "ja": "行こう。", "speaker_role": "hero", "kind": "dialogue", "uncertainty": "", "timing_suspect": False}]})
    p.write_json(work / "translation-reviewed.json", {"overall_note": "fixture", "units": [{"id": 1, "jv": "Ayo mangkat.", "english_gloss": "Let's go", "change_reason": "", "uncertainty": ""}]})
    p.write_json(work / "probe.json", {"streams": [{"codec_type": "video", "width": 640, "height": 480}, {"codec_type": "audio"}], "format": {"duration": "6"}})


def ask(project, label="test"):
    return p.llm(project, label, "model-for-fixture", "fixture", {}, p.obj({"ok": p.BOOL}))


class PipelineChecks(unittest.TestCase):
    def test_cli_initializes_a_cold_project_without_overwriting(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "project.json"
            command = [sys.executable, str(p.ROOT / "tools/subtitle.py"), "init", "--project", str(config), "--slug", "fixture-init", "--series", "Synthetic show", "--episode", "01"]
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            content = config.read_bytes()
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(second.returncode, 0)
            self.assertEqual(config.read_bytes(), content)
            status = subprocess.run([sys.executable, str(p.ROOT / "tools/subtitle.py"), "status", "--project", str(config)], capture_output=True, text=True)
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertEqual(json.loads(status.stdout)["slug"], "fixture-init")

    def test_reject_path_traversal_in_slug(self):
        for value in ["../other", "a/b", "", "A Name", "a\\b"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                merge_config({"slug": value})

    def test_anamorphic_dimensions_use_display_aspect(self):
        probe = {"streams": [{"codec_type": "video", "width": 720, "height": 480, "sample_aspect_ratio": "32:27"}]}
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(p.output_dimensions(make_project(Path(folder)), probe), (854, 480))

    def test_receipt_claim_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            receipt = Path(folder) / "request.json"
            p.claim_request(receipt, {"status": "submitting"})
            with self.assertRaises(RuntimeError):
                p.claim_request(receipt, {"status": "new"})
            self.assertEqual(p.read_json(receipt)["status"], "submitting")

    def test_changed_source_does_not_replace_cached_probe(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            project = make_project(work)
            source = work / "source.mkv"
            source.write_bytes(b"synthetic")
            p.write_json(work / "source.json", {"path": str(source), "size": 1, "mtime_ns": 0})
            p.write_json(work / "probe.json", {"unchanged": True})
            with patch.object(p, "run") as command:
                with self.assertRaises(ValueError):
                    p.inspect(project, source)
                command.assert_not_called()
            self.assertEqual(p.read_json(work / "probe.json"), {"unchanged": True})

    def test_timed_out_submission_is_not_automatically_repeated(self):
        with tempfile.TemporaryDirectory() as folder:
            project = make_project(Path(folder))
            with patch.dict(os.environ, {"OPENAI_API_KEY": "unit-test-only"}), patch.object(p.requests, "post", side_effect=p.requests.Timeout) as submit:
                with self.assertRaises(p.requests.Timeout):
                    ask(project)
                with self.assertRaisesRegex(RuntimeError, "unknown outcome"):
                    ask(project)
                self.assertEqual(submit.call_count, 1)

    def test_background_job_resumes_by_id_and_cache_needs_no_key(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = make_project(root)
            with patch.dict(os.environ, {"OPENAI_API_KEY": "unit-test-only"}), patch.object(p.requests, "post", side_effect=p.requests.Timeout):
                with self.assertRaises(p.requests.Timeout):
                    ask(project)
            receipt = next((root / "api").glob("*.receipt.json"))
            p.write_json(receipt, {"id": "resp_fixture", "status": "in_progress"})
            response = Mock()
            response.json.return_value = {"id": "resp_fixture", "status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"ok": true}'}]}]}
            with patch.dict(os.environ, {"OPENAI_API_KEY": "unit-test-only"}), patch.object(p.requests, "post") as submit, patch.object(p.requests, "get", return_value=response) as retrieve, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(ask(project), {"ok": True})
                submit.assert_not_called()
                retrieve.assert_called_once()
            with patch.dict(os.environ, {}, clear=True), patch.object(p.requests, "get") as retrieve, patch.object(p.requests, "post") as submit, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(ask(project), {"ok": True})
                retrieve.assert_not_called()
                submit.assert_not_called()

    def test_selected_project_wins_over_an_invalid_default_config(self):
        with tempfile.TemporaryDirectory() as folder:
            broken, chosen = Path(folder) / "broken.json", Path(folder) / "chosen.json"
            broken.write_text('{"slug": "Not A Slug"}', encoding="utf-8")
            p.write_json(chosen, merge_config({"slug": "fixture-chosen"}))
            env = {**os.environ, "SUBBER_PROJECT": str(broken)}
            script = str(p.ROOT / "tools/subtitle.py")
            selected = subprocess.run([sys.executable, script, "status", "--project", str(chosen)], env=env, capture_output=True, text=True)
            self.assertEqual(selected.returncode, 0, selected.stderr)
            self.assertEqual(json.loads(selected.stdout)["slug"], "fixture-chosen")
            default = subprocess.run([sys.executable, script, "status"], env=env, capture_output=True, text=True)
            self.assertEqual(default.returncode, 1)
            self.assertTrue(default.stderr.startswith("Error: Write the slug"), default.stderr)
            self.assertNotIn("Traceback", default.stderr)

    def test_encode_keeps_previous_deliverable_when_verification_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            project = make_project(work)
            project.out.mkdir()
            write_episode(work)
            source = work / "source.mkv"
            source.write_bytes(b"fixture")
            project.export(".jv.ass").write_text("fixture", encoding="utf-8")
            video = project.export(".jv.480p.mp4")
            video.write_bytes(b"previous good deliverable")
            p.write_json(work / "cues.json", [{"id": 1, "source_ids": [1], "start": 1.0, "end": 3.0, "lines": ["Ayo mangkat."], "kind": "dialogue", "speaker_role": "hero"}])
            commands = []

            def fake_encode(args, **kwargs):
                commands.append(args)
                Path(args[-1]).write_bytes(b"bad candidate")

            with patch.object(p, "inspect", return_value=p.read_json(work / "probe.json")), patch.object(burn_subtitles, "probe", return_value={"packets": [{"pts_time": "1", "duration_time": "2"}]}), patch.object(burn_subtitles.subprocess, "run", side_effect=fake_encode), patch.object(burn_subtitles, "verify_video", side_effect=ValueError("verification failed")), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(ValueError, "verification failed"):
                    p.encode(project, source)
            self.assertEqual(video.read_bytes(), b"previous good deliverable")
            self.assertEqual(list(project.out.glob("*.partial.mp4")), [])
            self.assertIn(f"title={project.config['series']} {project.config['episode']} - Javanese", commands[0])

    def test_subtitle_overrides_reject_unknown_ids(self):
        for edit, expected in [({"id": 99, "jv": "Typo target"}, None), ({"id": 1, "jv": "Ayo, mangkat saiki."}, ["Ayo, mangkat saiki."])]:
            with self.subTest(edit=edit), tempfile.TemporaryDirectory() as folder:
                work = Path(folder)
                project = make_project(work)
                project.out.mkdir()
                write_episode(work)
                p.write_json(work / "subtitle-overrides.json", {"units": [edit]})
                with contextlib.redirect_stdout(io.StringIO()):
                    if expected is None:
                        with self.assertRaisesRegex(ValueError, "unknown utterance IDs: 99"):
                            p.layout(project)
                    else:
                        p.layout(project)
                if expected is None:
                    self.assertFalse((work / "cues.json").exists())
                else:
                    self.assertEqual(p.read_json(work / "cues.json")[0]["lines"], expected)

    def test_translation_review_payload_omits_word_indices(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            project = make_project(work)
            write_episode(work)
            p.write_json(work / "translation-draft.json", [{"id": 1, "jv": "Ayo mangkat.", "english_gloss": "Let's go", "note": ""}])
            reviewed = {"overall_note": "fixture", "units": [{"id": 1, "jv": "Ayo mangkat.", "english_gloss": "Let's go", "change_reason": "", "uncertainty": ""}]}
            with patch.object(p, "llm", return_value=reviewed) as request:
                p.review_translation(project)
            unit = request.call_args.args[4]["units"][0]
            self.assertNotIn("word_indices", unit)
            self.assertEqual((unit["ja"], unit["speaker_role"], unit["draft"]["jv"]), ("行こう。", "hero", "Ayo mangkat."))

    def test_source_lookup_explains_missing_and_ambiguous_videos(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = make_project(root / "work")
            with patch.object(p, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "contain no source video"):
                    p.source_file(project)
                (root / "episode.mkv").write_bytes(b"fixture")
                self.assertEqual(p.source_file(project), root / "episode.mkv")
                (root / "input").mkdir()
                (root / "input" / "other.mp4").write_bytes(b"fixture")
                with self.assertRaisesRegex(ValueError, "more than one source video"):
                    p.source_file(project)

    def test_rejected_submission_reports_its_http_status_and_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as folder:
            project = make_project(Path(folder))
            rejected = Mock(status_code=400, text="invalid model")
            with patch.dict(os.environ, {"OPENAI_API_KEY": "unit-test-only"}), patch.object(p.requests, "post", return_value=rejected) as submit:
                with self.assertRaisesRegex(RuntimeError, "HTTP 400"):
                    ask(project)
                with self.assertRaisesRegex(RuntimeError, "failed with HTTP 400"):
                    ask(project)
                self.assertEqual(submit.call_count, 1)
            receipt = p.read_json(next((Path(folder) / "api").glob("*.receipt.json")))
            self.assertEqual((receipt["status"], receipt["http_status"], receipt["detail"]), ("failed", 400, "invalid model"))

    def test_refusal_or_invalid_json_points_to_the_saved_response(self):
        for content, message in [({"type": "refusal", "refusal": "I cannot help."}, "no output text"), ({"type": "output_text", "text": "not json"}, "invalid JSON")]:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as folder:
                project = make_project(Path(folder))
                accepted = Mock(status_code=200, headers={})
                accepted.json.return_value = {"id": "resp_fixture", "status": "completed", "output": [{"type": "message", "content": [content]}]}
                with patch.dict(os.environ, {"OPENAI_API_KEY": "unit-test-only"}), patch.object(p.requests, "post", return_value=accepted), contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(RuntimeError, message):
                        ask(project)
                self.assertEqual(len(list((Path(folder) / "api").glob("*.response.json"))), 1)

    def test_existing_receipts_block_paid_requests_with_a_specific_reason(self):
        with tempfile.TemporaryDirectory() as folder:
            receipt = Path(folder) / "scribe-request.json"
            p.ensure_unclaimed(receipt, "Scribe")
            for state, message in [({"status": "submitting"}, "unknown outcome"), ({"status": "failed", "http_status": 429}, "failed with HTTP 429"), ({"status": "completed"}, "already exists")]:
                with self.subTest(state=state):
                    p.write_json(receipt, state)
                    with self.assertRaisesRegex(RuntimeError, message):
                        p.ensure_unclaimed(receipt, "Scribe")

    def test_cli_reports_expected_failures_without_traceback(self):
        failures = [
            (subprocess.CalledProcessError(1, ["ffprobe", "clip.mkv"], stderr="clip.mkv: Invalid data found when processing input\n"), "Error: ffprobe stopped with exit status 1.\nclip.mkv: Invalid data found"),
            (p.requests.Timeout("Read timed out."), "Error: Read timed out."),
            (PermissionError("output.mp4 is open in another program"), "Error: output.mp4 is open"),
        ]
        for error, message in failures:
            with self.subTest(error=type(error).__name__):
                stderr = io.StringIO()
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as stop:
                    p.run_cli(Mock(side_effect=error))
                self.assertEqual(stop.exception.code, 1)
                self.assertTrue(stderr.getvalue().startswith(message), stderr.getvalue())
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(KeyError):
            p.run_cli(Mock(side_effect=KeyError("units")))

    def test_helper_scripts_report_config_errors_without_traceback(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = str(Path(folder) / "absent.json")
            for script, arguments in [("check_exports.py", []), ("render_samples.py", []), ("check_audio.py", ["1", "2"]), ("align_audio.py", ["1", "2", "fixture"])]:
                with self.subTest(script=script):
                    result = subprocess.run([sys.executable, str(p.ROOT / "tools" / script), *arguments, "--project", missing], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertTrue(result.stderr.startswith("Error: The tool found no local project config"), result.stderr)
                    self.assertNotIn("Traceback", result.stderr)


class SegmentChecks(unittest.TestCase):
    def segment(self, words, **config):
        with tempfile.TemporaryDirectory() as folder:
            project = make_project(Path(folder), **config)
            p.write_json(project.work / "scribe.json", {"words": words})
            with contextlib.redirect_stdout(io.StringIO()):
                p.segment(project)
            return p.read_json(project.work / "utterances.json"), p.read_json(project.work / "audio-events.json"), (project.work / "japanese-transcript.txt").read_text(encoding="utf-8")

    def test_splits_on_punctuation_speaker_change_event_and_long_gap(self):
        words = [
            {"text": "行こう。", "start": 0.5, "end": 1.0, "type": "word", "speaker_id": "speaker_0"},
            {"text": " ", "start": 1.0, "end": 1.5, "type": "spacing"},
            {"text": "ここ", "start": 1.5, "end": 1.8, "type": "word", "speaker_id": "speaker_0"},
            {"text": "で", "start": 1.85, "end": 1.9, "type": "word", "speaker_id": "speaker_0"},
            {"text": "待って", "start": 1.95, "end": 2.3, "type": "word", "speaker_id": "speaker_1"},
            {"text": "[足音]", "start": 2.4, "end": 2.6, "type": "audio_event"},
            {"text": "よし", "start": 4.0, "end": 4.3, "type": "word", "speaker_id": "speaker_1"},
            {"text": "行け", "start": 5.5, "end": 5.8, "type": "word", "speaker_id": "speaker_1"},
        ]
        utterances, events, transcript = self.segment(words)
        self.assertEqual([u["ja"] for u in utterances], ["行こう。", "ここで", "待って", "よし", "行け"])
        self.assertEqual([u["id"] for u in utterances], [1, 2, 3, 4, 5])
        self.assertEqual(utterances[1]["word_indices"], [2, 3])
        self.assertEqual((utterances[2]["speaker"], utterances[2]["start"], utterances[2]["end"]), ("speaker_1", 1.95, 2.3))
        self.assertEqual([e["text"] for e in events], ["[足音]"])
        self.assertEqual(transcript.splitlines()[0], "001 [0.50-1.00] speaker_0: 行こう。")

    def test_music_ranges_allow_longer_gaps_between_sung_syllables(self):
        words = [{"text": "よし", "start": 4.0, "end": 4.3, "type": "word", "speaker_id": "speaker_0"}, {"text": "行け", "start": 5.5, "end": 5.8, "type": "word", "speaker_id": "speaker_0"}]
        self.assertEqual([u["ja"] for u in self.segment(words)[0]], ["よし", "行け"])
        self.assertEqual([u["ja"] for u in self.segment(words, music_ranges=[[0, 10]])[0]], ["よし行け"])

    def test_long_continuous_speech_splits_after_ten_seconds(self):
        words = [{"text": f"語{i}", "start": round(0.9 * i, 2), "end": round(0.9 * i + 0.5, 2), "type": "word", "speaker_id": "speaker_0"} for i in range(12)]
        self.assertEqual([len(u["word_indices"]) for u in self.segment(words)[0]], [11, 1])


if __name__ == "__main__":
    unittest.main()
