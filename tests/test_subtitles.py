"""Checks for subtitle word preservation, readable wrapping, cue timing, QA and exports."""
import contextlib
import csv
import io
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import subtitle as p
from settings import merge_config
from subtitle import split_text, wrap_lines, timestamp, ass_timestamp, check_ids

CONFIG = merge_config({})
OPTIONS = CONFIG["subtitle"]
SCREEN = p.screen_for(CONFIG, 640)


def make_project(root):
    return p.Project(root / "project.json", merge_config({}), root, root / "subbed")


class SubtitleChecks(unittest.TestCase):
    def test_split_preserves_words(self):
        text = "Aku kudu nglawan wong sing nggunakake ilmu kanggo tumindak ala. Aja nganti padha bisa nguwasani jagad iki."
        parts = split_text(text, 60)
        self.assertEqual(" ".join(parts), text)
        self.assertTrue(all(len(part) <= 60 for part in parts))

    def test_unicode_wrap_preserves_words(self):
        text = "Nggih, Pak. Kula badhé nglawan gerombolan Sigma."
        lines = wrap_lines(text, SCREEN)
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


class CueTimingChecks(unittest.TestCase):
    def item(self, start, end, jv):
        return {"id": 7, "start": start, "end": end, "jv": jv}

    def test_cue_leads_speech_and_holds_after_it(self):
        start, end = p.cue_window(self.item(1.0, 2.0, "x" * 15), 10.0, OPTIONS, 20.0)
        self.assertAlmostEqual(start, 0.96)
        self.assertAlmostEqual(end, 2.18)

    def test_next_line_limits_the_cue(self):
        self.assertAlmostEqual(p.cue_window(self.item(1.0, 2.0, "x" * 15), 1.5, OPTIONS, 20.0)[1], 1.5)

    def test_short_speech_extends_to_minimum_duration(self):
        self.assertAlmostEqual(p.cue_window(self.item(1.0, 1.1, "Ya"), 10.0, OPTIONS, 20.0)[1], 0.96 + OPTIONS["min_duration"])

    def test_cue_never_runs_past_the_source(self):
        self.assertAlmostEqual(p.cue_window(self.item(1.0, 2.0, "x" * 15), 10.0, OPTIONS, 1.5)[1], 1.5)

    def test_overlapping_speech_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Utterance 7 overlaps the next utterance"):
            p.cue_window(self.item(1.0, 2.0, "x"), 0.9, OPTIONS, 20.0)

    def test_long_lasting_text_splits_in_two(self):
        text = "Aku kudu nglawan wong sing nggunakake ilmu kanggo ala."
        parts, end = p.screen_parts(self.item(0.0, 10.0, text), 0.0, 10.0, OPTIONS, SCREEN)
        self.assertEqual(len(parts), 2)
        self.assertEqual(" ".join(parts), text)
        self.assertEqual(end, 10.0)

    def test_short_long_lasting_text_is_clamped(self):
        parts, end = p.screen_parts(self.item(0.0, 10.0, "Ayo mangkat."), 0.0, 10.0, OPTIONS, SCREEN)
        self.assertEqual(parts, ["Ayo mangkat."])
        self.assertEqual(end, OPTIONS["max_duration"])


class OverrideChecks(unittest.TestCase):
    def items(self, edits):
        units = [{"id": i, "start": float(i), "end": i + 0.5, "ja": f"台詞{i}", "kind": "omit" if i == 4 else "dialogue", "speaker_role": "hero"} for i in range(1, 5)]
        translations = [{"id": i, "jv": f"Ukara {i}", "english_gloss": "", "change_reason": "", "uncertainty": ""} for i in range(1, 5)]
        with tempfile.TemporaryDirectory() as folder:
            project = make_project(Path(folder))
            p.write_json(project.config_path.parent / "subtitle-overrides.json", edits)
            return p.subtitle_items(project, units, translations)[0]

    def test_merge_group_joins_consecutive_units(self):
        items = self.items({"merge_groups": [[1, 2], {"ids": [3], "jv": "Ukara katelu."}]})
        self.assertEqual([(item["source_ids"], item["jv"], item["end"]) for item in items], [([1, 2], "Ukara 1 Ukara 2", 2.5), ([3], "Ukara katelu.", 3.5)])

    def test_omitted_units_get_no_subtitle(self):
        self.assertEqual([item["id"] for item in self.items({})], [1, 2, 3])

    def test_merge_group_must_be_consecutive(self):
        with self.assertRaisesRegex(ValueError, "consecutive"):
            self.items({"merge_groups": [[1, 3]]})

    def test_overrides_for_another_episode_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "belongs to another episode"):
            self.items({"episode": "other-episode"})


class QualityAndExportChecks(unittest.TestCase):
    def cue(self, cue_id, start, end, *lines):
        return {"id": cue_id, "source_ids": [cue_id], "start": start, "end": end, "lines": list(lines), "kind": "dialogue", "speaker_role": "hero"}

    def test_validate_cues_names_each_problem(self):
        cues = [
            self.cue(1, 0.0, 2.0, "Ayo mangkat."),
            self.cue(2, 1.5, 3.0, "Tumpang tindih."),
            self.cue(3, 3.1, 3.5, "Cendhak."),
            self.cue(4, 4.0, 5.0, "x" * 39),
            self.cue(5, 5.1, 5.9, "Iki ukara dawa banget kanggo diwaca"),
            self.cue(6, 6.0, 14.0, "Suwe."),
            self.cue(7, 14.1, 16.0, "{markup} 日本"),
        ]
        with tempfile.TemporaryDirectory() as folder:
            project = make_project(Path(folder))
            p.write_json(project.work / "probe.json", {"format": {"duration": "20"}})
            with contextlib.redirect_stdout(io.StringIO()):
                issues = {issue["cue"]: issue["issues"] for issue in p.validate_cues(project, cues)}
        self.assertEqual(issues, {2: ["overlap"], 3: ["very short duration"], 4: ["line length", "reading speed"], 5: ["reading speed"], 6: ["long duration"], 7: ["unexpected Japanese or markup"]})

    def test_exports_escape_markup_per_format(self):
        cues = [self.cue(1, 1.0, 3.0, "Pak {Guru}", "a\\b <i>c</i> & d"), {**self.cue(2, 3.5, 5.0, "Tembang"), "kind": "song"}]
        units = [{"id": i, "ja": f"台詞{i}", "uncertainty": ""} for i in (1, 2)]
        translations = [{"id": i, "english_gloss": f"line {i}", "uncertainty": "", "change_reason": ""} for i in (1, 2)]
        with tempfile.TemporaryDirectory() as folder:
            project = make_project(Path(folder))
            project.out.mkdir()
            with contextlib.redirect_stdout(io.StringIO()):
                p.export_subtitles(project, cues, units, translations)
            srt = project.export(".jv.srt").read_text(encoding="utf-8")
            vtt = project.export(".jv.vtt").read_text(encoding="utf-8")
            ass = project.export(".jv.ass").read_text(encoding="utf-8-sig")
            with project.export(".review.tsv").open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream, delimiter="\t"))
        self.assertIn("Pak {Guru}\na\\b <i>c</i> & d", srt)
        self.assertIn("Pak {Guru}\na\\b &lt;i&gt;c&lt;/i&gt; &amp; d", vtt)
        self.assertIn("Dialogue: 0,0:00:01.00,0:00:03.00,Dialogue,,0,0,0,,Pak (Guru)\\Na/b <i>c</i> & d", ass)
        self.assertIn("Dialogue: 0,0:00:03.50,0:00:05.00,Song,,0,0,0,,Tembang", ass)
        self.assertIn("PlayResX: 640\nPlayResY: 480\n", ass)
        self.assertEqual([row["Final Javanese subtitle"] for row in rows], ["Pak {Guru}\na\\b <i>c</i> & d", "Tembang"])


if __name__ == "__main__":
    unittest.main()
