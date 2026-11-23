"""Small packed fixtures and CLI protocol checks; no phone or full-data benchmark."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from cli_tests.scripts.compiled_lexicon import CompiledLexicon
from cli_tests.tests.fixtures.packed_dictionary import build, build_language_pack_fixture
from cli_tests.scripts.engine_cli import display_reverse, random_test, ensure_cli

ROOT = Path(__file__).resolve().parents[2]


class ReverseLookupTest(unittest.TestCase):
    def test_retained_readings_regions_and_missing_words(self):
        with tempfile.TemporaryDirectory() as directory:
            for bits in (24, 32):
                path = Path(directory) / f'{bits}.ipad'
                path.write_bytes(build(bits))
                with CompiledLexicon(path) as lexicon:
                    self.assertEqual([(r['ipa'], r['region']) for r in lexicon.reverse('alpha')],
                                     [('a', 'en-US'), ('a', 'en-GB'), ('ba', 'en-US')])
                    self.assertEqual([r['ipa'] for r in lexicon.reverse('alpha', ['en-GB'])], ['a'])
                    self.assertEqual(lexicon.reverse('alpha', ['ja-JP']), [])
                    self.assertEqual(lexicon.reverse('not-in-dictionary'), [])
                    self.assertEqual(lexicon.reverse('Alpha'), [])
                    self.assertEqual([(r['ipa'], r['derived']) for r in lexicon.reverse('爱')],
                                     [('ac', True), ('cb', True), ('cb', False)])
                    self.assertEqual(lexicon.reverse('学校')[0]['ipa'], 'ɕoː')
                    # Exercise blocks beyond the first front-coded 16-word block.
                    self.assertEqual(lexicon.reverse('word-25')[0]['ipa'], 'xz')

    def test_invalid_header_is_rejected_and_missing_output_is_silent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'invalid.ipad'
            path.write_bytes(b'wrong!!!' + build()[8:])
            with self.assertRaises(ValueError):
                CompiledLexicon(path)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            display_reverse('missing', [])
            display_reverse('missing', [], as_json=True)
        self.assertEqual(output.getvalue(), '')

    def test_reverse_subcommand_builds_and_calls_native_core_without_source_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.ipad'
            path.write_bytes(build())
            command = [sys.executable, str(ROOT / 'cli_tests/scripts/engine_cli.py'), '--dictionary', str(path),
                       '--cli', str(Path(directory) / 'nonexistent-native'), 'reverse']
            result = subprocess.run(command + ['alpha', '--region', 'en-GB', '--json'],
                                    text=True, capture_output=True, check=True)
            self.assertEqual(json.loads(result.stdout)['pronunciations'][0]['ipa'], 'a')
            missing = subprocess.run(command + ['not-in-dictionary', '--json'],
                                     text=True, capture_output=True, check=True)
            self.assertEqual(missing.stdout, '')


class InteractiveCommandsTest(unittest.TestCase):
    def test_random_case_preserves_its_options_and_reports_failed_expectation(self):
        case = dict(id='edge', raw='a', regions=['en-GB'], limit=1, expected_all=['absent'])
        result = dict(status='ok', candidates=[dict(text='alpha')])
        output = io.StringIO()
        with patch('cli_tests.scripts.engine_cli.invoke', return_value=result) as invoke:
            with contextlib.redirect_stdout(output):
                random_test('native', 'dict', [case], as_json=True)
        invoke.assert_called_once_with('native', 'dict', case)
        record = json.loads(output.getvalue())
        self.assertEqual(record['input'], case)
        self.assertFalse(record['passed'])
        self.assertEqual(record['failures'], ['expected_all'])

    def test_repl_test_reverse_unknown_and_ipa_escape_without_native_retest(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            dictionary = folder / 'fixture.ipad'
            dictionary.write_bytes(build())
            cases = folder / 'cases.jsonl'
            cases.write_text(json.dumps(dict(id='random-only', raw='ab', regions=['en-GB'],
                                            limit=1, expected_any=['about'])) + '\n')
            # Simulate the existing native line protocol; only the new Python
            # routing is under test, not core performance or phone behavior.
            native = folder / 'native'
            native.write_text('#!' + sys.executable + '\n' + '''
import json, sys
def reply(raw):
    if raw.startswith(':query\\t'):
        raw = raw[len(':query\\t'):]
    if raw.startswith(':reverse\\t'):
        word = raw[len(':reverse\\t'):]
        readings = [dict(ipa='a', region='en-US', derived=False, frequency_zipf=5)] if word in ('alpha', 'about') else []
        print(json.dumps(dict(word=word, readings=readings, truncated=False)), flush=True)
        return
    exact = raw in ('a', 'ab')
    print(json.dumps(dict(raw=raw, status='ok', exact_found=exact, candidates=[dict(text='about')] if exact else [], argv=sys.argv[1:])), flush=True)
if sys.argv[1] == 'query':
    reply(sys.argv[3])
else:
    for line in sys.stdin:
        reply(line.rstrip('\\n'))
''')
            native.chmod(0o755)
            result = subprocess.run([sys.executable, str(ROOT / 'cli_tests/scripts/engine_cli.py'),
                                     '--dictionary', str(dictionary), '--cli', str(native),
                                     'repl', '--json', '--cases', str(cases)],
                                    input='test\nalpha\nnot-in-dictionary\nreverse about\na\nipa unknown\n',
                                    text=True, capture_output=True, check=True)
            records = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(len(records), 5)  # Missing word emitted no row.
            self.assertTrue(records[0]['passed'])
            self.assertEqual(records[0]['id'], 'random-only')
            self.assertIn('en-GB', records[0]['result']['argv'])
            self.assertEqual(records[1]['word'], 'alpha')
            self.assertEqual(records[2]['word'], 'about')
            self.assertEqual(records[3]['raw'], 'a')  # IPA exact key takes priority.
            self.assertEqual(records[4]['raw'], 'unknown')
            self.assertEqual(records[4]['candidates'], [])


class LanguagePackCliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cli = ensure_cli(ROOT / 'data/cli_tests/builds/ipa_graph')

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.dictionary = self.directory / 'packs.ipad'
        self.dictionary.write_bytes(build_language_pack_fixture())
        self.command = [sys.executable, str(ROOT / 'cli_tests/scripts/engine_cli.py'),
                        '--dictionary', str(self.dictionary), '--cli', str(self.cli)]

    def run_cli(self, *args, input=None):
        process = subprocess.run(self.command + list(args), input=input, text=True,
                                 capture_output=True, check=True)
        if not process.stdout.strip():return []
        try:return [json.loads(process.stdout)]
        except json.JSONDecodeError:pass
        return [json.loads(line) for line in process.stdout.splitlines()]

    def test_default_packs_regions_reverse_and_lazy_start(self):
        inventory = self.run_cli('packs', '--json')[0]['packs']
        self.assertEqual([p['id'] for p in inventory], ['core', 'ru', 'ar', 'ko'])
        self.assertTrue(all(p['loaded'] and p['mode'] == 'input' for p in inventory))
        self.assertEqual(self.run_cli('query', 'kot', '--strict', '--region', 'ru-RU', '--json')[0]
                         ['candidates'][0]['text'], 'кот')
        self.assertEqual(self.run_cli('reverse', '한국', '--json')[0]['pronunciations'][0]['region'], 'ko-KR')
        self.assertEqual(self.run_cli('reverse', 'كتاب', '--json')[0]['pronunciations'][0]['region'], 'ar-001')
        self.assertEqual(self.run_cli('reverse', 'кот', '--region', 'ko-KR', '--json'), [])
        self.assertEqual(self.run_cli('reverse', 'e\u0301', '--json')[0]['pronunciations'][0]['ipa'], 'á')
        lazy = self.run_cli('packs', '--no-default-packs', '--json')[0]['packs']
        self.assertTrue(all(not p['loaded'] and p['search_index_bytes'] == 0 for p in lazy))
        reverse = self.run_cli('packs', '--no-default-packs', '--pack-mode', 'ru:reverse', '--json')[0]['packs'][1]
        self.assertTrue(reverse['loaded'])
        self.assertEqual(reverse['mode'], 'reverse')
        self.assertEqual(reverse['search_index_bytes'], 0)

    def test_repl_modes_apply_immediately_to_both_query_paths(self):
        records = self.run_cli('repl', '--json', input=(
            'reverse кот\npack ru off\nreverse кот\nipa kot\n'
            'pack ru reverse\nreverse кот\nipa kot\npack ru input\nipa kot\npacks\n'))
        self.assertEqual(len(records), 9)
        self.assertEqual(records[0]['word'], 'кот')
        self.assertFalse(records[1]['packs'][1]['loaded'])
        self.assertEqual(records[2]['candidates'], [])
        self.assertEqual(records[3]['packs'][1]['mode'], 'reverse')
        self.assertEqual(records[4]['word'], 'кот')
        self.assertEqual(records[5]['candidates'], [])
        self.assertEqual(records[6]['packs'][1]['mode'], 'input')
        self.assertEqual(records[7]['candidates'][0]['text'], 'кот')
        self.assertTrue(records[8]['packs'][1]['loaded'])

    def test_independent_language_file_attachment(self):
        self.dictionary.write_bytes(build_language_pack_fixture(['core']))
        russian = self.directory / 'russian.ipad'
        russian.write_bytes(build_language_pack_fixture(['ru']))
        packs = self.run_cli('packs', '--extra-dictionary', str(russian), '--no-default-packs',
                             '--pack-mode', 'core:input', '--pack-mode', 'ru:reverse', '--json')[0]['packs']
        self.assertEqual([p['id'] for p in packs], ['core', 'ru'])
        self.assertEqual([p['mode'] for p in packs], ['input', 'reverse'])
        self.assertEqual(self.run_cli('reverse', 'кот', '--extra-dictionary', str(russian), '--json')[0]
                         ['pronunciations'][0]['ipa'], 'kot')


if __name__ == '__main__':
    unittest.main()
