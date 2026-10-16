"""Checks for subtitle word preservation, readable wrapping, and time rounding."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from subtitle import split_text, wrap_lines, timestamp, ass_timestamp, check_ids


class SubtitleChecks(unittest.TestCase):
    def test_split_preserves_words(self):
        text = "Aku kudu nglawan wong sing nggunakake ilmu kanggo tumindak ala. Aja nganti padha bisa nguwasani jagad iki."
        parts = split_text(text, 60)
        self.assertEqual(" ".join(parts), text)
        self.assertTrue(all(len(part) <= 60 for part in parts))

    def test_unicode_wrap_preserves_words(self):
        text = "Nggih, Pak. Kula badhé nglawan gerombolan Sigma."
        lines = wrap_lines(text)
        self.assertEqual(" ".join(lines), text)
        self.assertLessEqual(len(lines), 2)
        self.assertTrue(all(len(line) <= 38 for line in lines))

    def test_time_rounding_carries(self):
        self.assertEqual(timestamp(59.9996), "00:01:00,000")
        self.assertEqual(timestamp(3599.9996, "."), "01:00:00.000")
        self.assertEqual(ass_timestamp(59.999), "0:01:00.00")

    def test_reject_missing_or_duplicate_ids(self):
        with self.assertRaises(ValueError):
            check_ids([{"id": 1}, {"id": 1}], [{"id": 1}, {"id": 2}])


if __name__ == "__main__":
    unittest.main()
