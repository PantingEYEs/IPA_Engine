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

from graph.lexicon_reader import CompiledLexicon
from graph.tests.make_fixture import build
from scripts.engine_cli import display_reverse, random_test

ROOT = Path(__file__).resolve().parents[1]


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

    def test_reverse_subcommand_does_not_need_native_binary_or_source_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.ipad'
            path.write_bytes(build())
            command = [sys.executable, str(ROOT / 'scripts/engine_cli.py'), '--dictionary', str(path),
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
        with patch('scripts.engine_cli.invoke', return_value=result) as invoke:
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
    exact = raw in ('a', 'ab')
    print(json.dumps(dict(raw=raw, status='ok', exact_found=exact, candidates=[dict(text='about')] if exact else [], argv=sys.argv[1:])), flush=True)
if sys.argv[1] == 'query':
    reply(sys.argv[3])
else:
    for line in sys.stdin:
        reply(line.rstrip('\\n'))
''')
            native.chmod(0o755)
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/engine_cli.py'),
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


if __name__ == '__main__':
    unittest.main()
