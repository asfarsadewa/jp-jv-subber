"""Cache isolation, source identity and paid-request recovery invariants."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import subtitle as p
from settings import merge_config


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
        self.assertEqual(p.output_dimensions(probe), (854, 480))

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
            source = work / "source.mkv"
            source.write_bytes(b"synthetic")
            p.write_json(work / "source.json", {"path": str(source), "size": 1, "mtime_ns": 0})
            p.write_json(work / "probe.json", {"unchanged": True})
            with patch.object(p, "WORK", work), patch.object(p, "OUT", work / "subbed"), patch.object(p, "run") as command:
                with self.assertRaises(ValueError):
                    p.inspect(source)
                command.assert_not_called()
            self.assertEqual(p.read_json(work / "probe.json"), {"unchanged": True})

    def test_timed_out_submission_is_not_automatically_repeated(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(p, "WORK", Path(folder)), patch.dict(os.environ, {"OPENAI_API_KEY": "unit-test-only"}), patch.object(p.requests, "post", side_effect=p.requests.Timeout) as submit:
                with self.assertRaises(p.requests.Timeout):
                    p.llm("test", "model-for-fixture", "fixture", {}, p.obj({"ok": p.BOOL}))
                with self.assertRaises(RuntimeError):
                    p.llm("test", "model-for-fixture", "fixture", {}, p.obj({"ok": p.BOOL}))
                self.assertEqual(submit.call_count, 1)

    def test_background_job_resumes_by_id_and_cache_needs_no_key(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(p, "WORK", root), patch.dict(os.environ, {"OPENAI_API_KEY": "unit-test-only"}), patch.object(p.requests, "post", side_effect=p.requests.Timeout):
                with self.assertRaises(p.requests.Timeout):
                    p.llm("test", "model-for-fixture", "fixture", {}, p.obj({"ok": p.BOOL}))
            receipt = next((root / "api").glob("*.receipt.json"))
            p.write_json(receipt, {"id": "resp_fixture", "status": "in_progress"})
            response = Mock()
            response.json.return_value = {"id": "resp_fixture", "status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"ok": true}'}]}]}
            with patch.object(p, "WORK", root), patch.dict(os.environ, {"OPENAI_API_KEY": "unit-test-only"}), patch.object(p.requests, "post") as submit, patch.object(p.requests, "get", return_value=response) as retrieve:
                self.assertEqual(p.llm("test", "model-for-fixture", "fixture", {}, p.obj({"ok": p.BOOL})), {"ok": True})
                submit.assert_not_called()
                retrieve.assert_called_once()
            with patch.object(p, "WORK", root), patch.dict(os.environ, {}, clear=True), patch.object(p.requests, "get") as retrieve, patch.object(p.requests, "post") as submit:
                self.assertEqual(p.llm("test", "model-for-fixture", "fixture", {}, p.obj({"ok": p.BOOL})), {"ok": True})
                retrieve.assert_not_called()
                submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
