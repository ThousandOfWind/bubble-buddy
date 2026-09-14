"""Offline integrity/acceptance checks; real ASR/Copilot runs are opt-in tools."""
import copy
import hashlib
import json
import unittest

from tools.audio_e2e import FIXTURES, assess, error_rate, load_fixtures, verify_fixture


class AudioFixtureTest(unittest.TestCase):
    def test_exactly_two_small_unmodified_licensed_samples(self):
        fixtures = load_fixtures()
        self.assertEqual(len(fixtures), 2)
        self.assertEqual({f["language"] for f in fixtures}, {"zh", "en"})
        self.assertLess(sum(f["bytes"] for f in fixtures), 500_000)
        manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
        for fixture in fixtures:
            with self.subTest(fixture=fixture["id"]):
                self.assertTrue(verify_fixture(fixture).is_file())
                self.assertLess(fixture["frames"] / fixture["sample_rate"], 15)
                self.assertEqual((fixture["sample_rate"], fixture["channels"], fixture["sample_width"]), (16000, 1, 2))
                self.assertIn(manifest["upstream_revision"], fixture["source_url"])
                self.assertIn(fixture["license"], {"Apache-2.0", "CC-BY-4.0"})
                self.assertTrue((FIXTURES / fixture["license_file"]).is_file())
                self.assertTrue(fixture["dataset_url"].startswith("https://www.openslr.org/"))

    def test_references_match_the_pinned_upstream_transcription(self):
        source = (FIXTURES / "upstream-transcripts.txt").read_bytes()
        self.assertEqual(hashlib.sha256(source).hexdigest(), "b49dc7eca7e5803a53f2b676130b63bc27aa19df2e250327fd410cfe95d79a53")
        references = {}
        for line in source.decode("utf-8").splitlines():
            path, text = line.split(" ", 1)
            references[path.rsplit("/", 1)[-1]] = text
        for fixture in load_fixtures():
            self.assertEqual(fixture["transcript"], references[fixture["file"].removesuffix(".wav")])
            self.assertTrue(assess(fixture, fixture["transcript"], "asr")["passed"])
            self.assertTrue(assess(fixture, fixture["transcript"], "polish")["passed"])

    def test_hash_and_path_checks_fail_closed(self):
        original = load_fixtures()[0]
        corrupt = copy.deepcopy(original)
        corrupt["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "integrity mismatch"):
            verify_fixture(corrupt)
        corrupt["file"] = "../outside.wav"
        with self.assertRaisesRegex(ValueError, "basename"):
            verify_fixture(corrupt)

    def test_metrics_ignore_punctuation_and_case_not_words(self):
        self.assertEqual(error_rate("HELLO WORLD", "Hello, world!", "wer"), 0)
        self.assertEqual(error_rate("one two", "one", "wer"), 0.5)
        self.assertEqual(error_rate("广州", "广州市", "cer"), 0.5)
        self.assertEqual(error_rate("广州", "广州。", "cer"), 0)
        with self.assertRaises(ValueError):
            error_rate("", "hello", "wer")

    def test_small_asr_typo_is_reported_but_must_be_repaired_in_final_output(self):
        fixture = next(f for f in load_fixtures() if f["language"] == "zh")
        typo = "广州市法地产中介协会分析"
        raw = assess(fixture, typo, "asr")
        final = assess(fixture, typo, "polish")
        self.assertTrue(raw["passed"])
        self.assertGreater(raw["error_rate"], 0)
        self.assertTrue(raw["missing_phrases"])
        self.assertFalse(final["passed"])
        self.assertEqual(raw["error_rate"], final["error_rate"])
        self.assertTrue(assess(fixture, fixture["transcript"], "polish")["passed"])

    def test_empty_output_and_lost_key_facts_fail_even_with_low_error_rate(self):
        fixtures = load_fixtures()
        for fixture in fixtures:
            self.assertFalse(assess(fixture, "", "asr")["passed"])
        english = next(f for f in fixtures if f["language"] == "en")
        result = assess(english, english["transcript"].replace("COTTON", ""), "polish")
        self.assertLess(result["error_rate"], result["limit"])
        self.assertFalse(result["passed"])
        self.assertIn(["cotton"], result["missing_phrases"])
        chinese = next(f for f in fixtures if f["language"] == "zh")
        self.assertTrue(assess(chinese, "廣州市房地產中介協會分析。", "asr")["passed"])


if __name__ == "__main__":
    unittest.main()
