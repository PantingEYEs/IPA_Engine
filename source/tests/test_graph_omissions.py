"""Ordered-subsequence retrieval against an independent small packed lexicon.

These are correctness tests, not phone or dictionary-width benchmarks. The
oracle uses Python's Unicode NFD data rather than the runtime's expansion table.
"""
import itertools
import json
from pathlib import Path
import subprocess
import tempfile
import unicodedata
import unittest

from graph.tests.make_fixture import build
from scripts.engine_cli import ensure_cli


ENTRIES = {
    "a": [("exact-a", 0, 0, False)],
    "ab": [("long-ab", 0, 180, False)],
    "aba": [("long-aba", 0, 150, False)],
    "aab": [("long-aab", 0, 130, False)],
    "bab": [("long-bab", 0, 120, False)],
    "b": [("exact-b", 0, 0, False)],
    "c": [("exact-c", 0, 0, False)],
    "cz": [("long-cz", 0, 0, False)],
    "é": [("accent-e", 0, 100, False)],
    "ˈmaɪ": [("my", 0, 120, False)],
    "mˈaɪ": [("my", 1, 120, False)],
    "ni˧˥ xɑʊ̯˨˩": [("你好", 2, 100, False)],
    "ni˨˩˦ xɑʊ̯˨˩˦": [("你好", 2, 110, False)],
    "ɕoː.ɡa": [("fixture-japanese", 4, 100, False)],
}
TONES = str.maketrans("12345", "˩˨˧˦˥")


def comparison(raw):
    return unicodedata.normalize("NFD", raw.translate(TONES))


def is_subsequence(raw, key):
    remaining = iter(comparison(key))
    return all(any(character == value for value in remaining)
               for character in comparison(raw))


class GraphOmissionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temporary.name)
        # A fresh temporary binary ensures every run tests the current Rust core.
        cls.cli = ensure_cli(cls.directory / "ipa_graph")
        cls.dictionary = cls.directory / "omissions.ipad"
        cls.dictionary.write_bytes(build(24, entries=ENTRIES))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def query(self, raw, *options):
        process = subprocess.run(
            [str(self.cli), "query", str(self.dictionary), raw,
             "--limit", "180", *options],
            text=True, capture_output=True, check=True)
        return json.loads(process.stdout)

    def assert_valid_candidates(self, raw, result):
        self.assertEqual(result["status"], "ok")
        for candidate in result["candidates"]:
            key = candidate["matched_ipa"]
            self.assertIn(key, ENTRIES)
            self.assertTrue(is_subsequence(raw, key), (raw, candidate))
            self.assertEqual(candidate["omitted_symbols"],
                             len(comparison(key)) - len(comparison(raw)))
            self.assertIn(candidate["text"], [entry[0] for entry in ENTRIES[key]])

    def test_requested_my_nihao_and_numeric_tones(self):
        for raw, word, region in (
                ("maɪ", "my", "en-US"),
                ("nixɑʊ", "你好", "zh-CN"),
                ("ni35 xɑʊ̯21", "你好", "zh-CN")):
            with self.subTest(raw=raw):
                result = self.query(raw, "--region", region)
                self.assert_valid_candidates(raw, result)
                self.assertIn(word, [candidate["text"] for candidate in result["candidates"]])
                self.assertEqual(result["exact_found"], raw == "ni35 xɑʊ̯21")
        numeric = self.query("ni35 xɑʊ̯21", "--region", "zh-CN")
        target = next(c for c in numeric["candidates"] if c["text"] == "你好")
        self.assertEqual(target["omitted_symbols"], 0)

    def test_sounds_annotations_and_boundaries_can_be_missing(self):
        for raw, word in (("mɪ", "my"), ("aɪ", "my"),
                          ("ni xɑʊ", "你好"), ("ni35xɑʊ21", "你好"),
                          ("ɕoɡa", "fixture-japanese"),
                          ("ɕo.ɡa", "fixture-japanese")):
            with self.subTest(raw=raw):
                result = self.query(raw)
                self.assert_valid_candidates(raw, result)
                self.assertIn(word, [c["text"] for c in result["candidates"]])

    def test_complete_input_cannot_be_replaced_dropped_or_reordered(self):
        for raw, forbidden in (("ɪam", "my"), ("maɪz", "my"),
                               ("zaɪ", "my"), ("xɑʊni", "你好"),
                               ("ni53 xɑʊ̯21", "你好"), ("ni35 xɑʊ̯12", "你好")):
            with self.subTest(raw=raw):
                result = self.query(raw)
                self.assert_valid_candidates(raw, result)
                self.assertNotIn(forbidden, [c["text"] for c in result["candidates"]])
        # The query is supported, but the extra final b must never be discarded.
        self.assertEqual(self.query("abbbb")["candidates"], [])

    def test_only_one_to_five_are_supported_tone_digits(self):
        for raw in ("ni0", "ni6", "ni7", "ni8", "ni9", "ni０", "ni３"):
            with self.subTest(raw=raw):
                result = self.query(raw)
                self.assertEqual(result["status"], "unsupported_input")
                self.assertEqual(result["candidates"], [])
        # Numeric aliases remain valid even when strict matching is selected.
        numeric = self.query("ni35 xɑʊ̯21", "--strict", "--region", "zh-CN")
        self.assertTrue(numeric["exact_found"])
        self.assertEqual([c["text"] for c in numeric["candidates"]], ["你好"])

    def test_unicode_decomposition_preserves_missing_diacritics(self):
        base = self.query("e")
        self.assert_valid_candidates("e", base)
        self.assertIn("accent-e", [c["text"] for c in base["candidates"]])
        accent = next(c for c in base["candidates"] if c["text"] == "accent-e")
        self.assertEqual(accent["omitted_symbols"], 1)
        self.assertFalse(base["exact_found"])
        for raw in ("é", "e\u0301"):
            with self.subTest(raw=raw):
                result = self.query(raw)
                self.assert_valid_candidates(raw, result)
                self.assertTrue(result["exact_found"])
                self.assertEqual(result["candidates"][0]["omitted_symbols"], 0)

    def test_exact_precedes_frequency_and_budget_retains_exact(self):
        for options in ((), ("--omission-budget", "1")):
            with self.subTest(options=options):
                result = self.query("a", *options)
                self.assertTrue(result["exact_found"])
                self.assertEqual(result["candidates"][0]["text"], "exact-a")
                self.assertEqual(result["candidates"][0]["omitted_symbols"], 0)
                if options:
                    self.assertTrue(result["truncated"])
                    self.assertLessEqual(result["explored_states"], 1)
        japanese = self.query("a", "--region", "ja-JP")
        self.assertTrue(japanese["exact_found"])
        self.assertNotIn("exact-a", [c["text"] for c in japanese["candidates"]])
        self.assertTrue(all(c["region"] == "ja-JP" for c in japanese["candidates"]))
        self.assertEqual(self.query("a", "--strict", "--region", "ja-JP")["candidates"], [])

    def test_strict_is_the_previous_exact_only_behavior(self):
        for raw in ("maɪ", "nixɑʊ", "e"):
            with self.subTest(raw=raw):
                result = self.query(raw, "--strict")
                self.assertEqual(result["candidates"], [])
                self.assertFalse(result["exact_found"])
                self.assertEqual(result["explored_states"], 0)
        full = self.query("ˈmaɪ", "--strict", "--region", "en-US")
        self.assertEqual([c["text"] for c in full["candidates"]], ["my"])
        exact = self.query("a", "--strict")
        self.assertEqual([c["text"] for c in exact["candidates"]], ["exact-a"])

    def test_omissions_cannot_bypass_literal_prefix_key_budget(self):
        options = ("--prefix", "--prefix-budget", "1")
        expanded = self.query("a", *options)
        strict = self.query("a", "--strict", *options)
        self.assertTrue(expanded["truncated"])
        completion_keys = {c["matched_ipa"] for c in expanded["candidates"]
                           if c["kind"] == "completion"}
        strict_completion_keys = {c["matched_ipa"] for c in strict["candidates"]
                                  if c["kind"] == "completion"}
        self.assertLessEqual(len(completion_keys), 1)
        self.assertEqual(completion_keys, strict_completion_keys)
        # Prefix budgeting does not suppress a different valid subsequence path.
        target = next(c for c in expanded["candidates"] if c["text"] == "long-bab")
        self.assertEqual(target["kind"], "conversion")
        self.assertEqual(target["matched_ipa"], "bab")
        self.assertFalse(target["matched_ipa"].startswith("a"))

    def test_exhaustive_small_inputs_equal_independent_subsequence_oracle(self):
        # Repeated letters and shared DAWG suffixes exercise rank addressing and
        # alternative deletion paths without mirroring the traversal algorithm.
        queries = ["".join(chars) for length in range(1, 5)
                   for chars in itertools.product("abc", repeat=length)]
        process = subprocess.run(
            [str(self.cli), "repl", str(self.dictionary), "--limit", "180"],
            input="\n".join(queries) + "\n", text=True, capture_output=True, check=True)
        results = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(len(results), len(queries))
        for raw, result in zip(queries, results):
            with self.subTest(raw=raw):
                self.assert_valid_candidates(raw, result)
                self.assertFalse(result["truncated"])
                expected = {entry[0] for key, entries in ENTRIES.items()
                            if is_subsequence(raw, key) for entry in entries}
                self.assertEqual({c["text"] for c in result["candidates"]}, expected)
        # Removing a symbol broadens the full mathematical match set.
        sets = {raw: {c["text"] for c in result["candidates"]}
                for raw, result in zip(queries, results)}
        self.assertLessEqual(sets["ab"], sets["a"])
        self.assertLess(len(sets["ab"]), len(sets["a"]))


if __name__ == "__main__":
    unittest.main()
