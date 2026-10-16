"""Input protection and failed-export behavior for standalone burn-in."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import burn_subtitles as burn
from settings import merge_config


class BurnChecks(unittest.TestCase):
    def test_never_overwrites_inputs_even_with_overwrite_flag(self):
        with tempfile.TemporaryDirectory() as folder:
            source, subtitle = Path(folder) / "source.mp4", Path(folder) / "captions.ass"
            source.write_bytes(b"original video")
            subtitle.write_text("original subtitles", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "either input"):
                burn.validate_paths(source, subtitle, source, overwrite=True)
            self.assertEqual(source.read_bytes(), b"original video")

    def test_existing_output_requires_explicit_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            source, subtitle, output = [Path(folder) / name for name in ["source.mkv", "captions.ass", "output.mp4"]]
            for path in (source, subtitle, output):
                path.write_text("fixture", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "already exists"):
                burn.validate_paths(source, subtitle, output, overwrite=False)
            burn.validate_paths(source, subtitle, output, overwrite=True)

    def test_cues_outside_the_video_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, subtitle, output = [root / name for name in ["source.mkv", "captions.srt", "output.mp4"]]
            source.write_bytes(b"fixture")
            subtitle.write_text("1\n01:00:00,000 --> 01:00:02,000\nAyo!\n", encoding="utf-8")
            info = {"streams": [{"codec_type": "video"}, {"codec_type": "audio"}], "format": {"duration": "6"}}
            with patch.object(burn, "probe", return_value={"packets": [{"pts_time": "3600", "duration_time": "2"}]}), patch.object(burn.subprocess, "run") as command:
                with self.assertRaisesRegex(ValueError, "overlapping"):
                    burn.encode_video(source, subtitle, output, merge_config({}), root / "job", info)
                command.assert_not_called()
            self.assertFalse(output.exists())

    def test_failed_verification_keeps_existing_output_and_removes_partial(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, subtitle, output = [root / name for name in ["source.mkv", "captions.ass", "output.mp4"]]
            source.write_bytes(b"fixture")
            subtitle.write_text("fixture", encoding="utf-8")
            output.write_bytes(b"previous good deliverable")
            info = {"streams": [{"codec_type": "video", "width": 640, "height": 480}, {"codec_type": "audio"}], "format": {"duration": "6"}}

            def fake_encode(args, **kwargs):
                Path(args[-1]).write_bytes(b"bad candidate")

            with patch.object(burn, "probe", return_value={"packets": [{"pts_time": "1", "duration_time": "2"}]}), patch.object(burn.subprocess, "run", side_effect=fake_encode), patch.object(burn, "verify_video", side_effect=ValueError("verification failed")):
                with self.assertRaisesRegex(ValueError, "verification failed"):
                    burn.encode_video(source, subtitle, output, merge_config({}), root / "job", info, overwrite=True, verify_output=True)
            self.assertEqual(output.read_bytes(), b"previous good deliverable")
            self.assertEqual(list(root.glob("*.partial.mp4")), [])


if __name__ == "__main__":
    unittest.main()
