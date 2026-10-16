"""The README sample exception must not admit other episode media."""
import io
from pathlib import Path
import sys
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import public_audit as audit


class PublicIncludeChecks(unittest.TestCase):
    def test_only_the_approved_preview_path_accepts_images(self):
        self.assertTrue(audit.public_path("docs/assets/zaborger-javanese.jpg"))
        for name in ["docs/assets/episode.jpg", "work/frame.jpg", "subbed/episode.mp4", "docs/project.json", "docs/../project.json"]:
            with self.subTest(name=name):
                self.assertFalse(audit.public_path(name))

    def test_preview_requires_expected_size_format_and_no_metadata(self):
        for format, size, options, allowed in [
            ("JPEG", (1280, 600), {}, True),
            ("PNG", (1280, 600), {}, False),
            ("JPEG", (640, 480), {}, False),
            ("JPEG", (1280, 600), {"comment": b"private note"}, False),
        ]:
            with self.subTest(format=format, size=size, options=options):
                data = io.BytesIO()
                Image.new("RGB", size, "navy").save(data, format, **options)
                if allowed:
                    audit.check_preview(data.getvalue())
                else:
                    with self.assertRaises(ValueError):
                        audit.check_preview(data.getvalue())


if __name__ == "__main__":
    unittest.main()
