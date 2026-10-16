"""Config defaults, merging and font path resolution."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import settings
from settings import DEFAULTS, ROOT, font_path, merge_config


class SettingsChecks(unittest.TestCase):
    def test_example_config_documents_the_defaults(self):
        example = json.loads((ROOT / "project.example.json").read_text(encoding="utf-8"))
        self.assertEqual(example, DEFAULTS)

    def test_nested_settings_merge_into_defaults(self):
        config = merge_config({"subtitle": {"max_cps": 18}, "encode": {"preset": "medium"}})
        self.assertEqual((config["subtitle"]["max_cps"], config["subtitle"]["target_cps"]), (18, 15))
        self.assertEqual((config["encode"]["preset"], config["encode"]["crf"]), ("medium", 27))
        self.assertEqual(DEFAULTS["subtitle"]["max_cps"], 20)

    def test_unknown_keys_are_rejected(self):
        for data, name in [({"max_line_char": 30}, "max_line_char"), ({"subtitle": {"max_line_char": 30}}, "subtitle.max_line_char")]:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, f"unknown keys: {name}\\."):
                merge_config(data)

    def test_invalid_values_name_the_setting(self):
        for data, message in [({"subtitle": {"max_lines": 3}}, "subtitle.max_lines"), ({"encode": {"crf": 60}}, "encode.crf"), ({"music_ranges": [[5, 2]]}, "music_ranges")]:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                merge_config(data)

    def test_relative_font_paths_start_at_the_repository_root(self):
        self.assertEqual(font_path("fonts/Bold.ttf"), ROOT / "fonts" / "Bold.ttf")
        absolute = Path(settings.__file__).resolve()
        self.assertEqual(font_path(str(absolute)), absolute)


if __name__ == "__main__":
    unittest.main()
