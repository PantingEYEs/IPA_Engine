"""Compare Rust production code with the previous runtime and binary builder."""
import json
from pathlib import Path
import random
import shutil
import struct
import subprocess
import tempfile
import unittest

from graph.tests.make_fixture import build
from scripts.build_rust_core import build as build_rust
from tests.test_graph_omissions import ENTRIES

ROOT = Path(__file__).resolve().parents[1]


class RustMigrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='ipa-rust-parity-')
        cls.directory = Path(cls.temporary.name)
        output = build_rust(bins=('ipa_graph', 'build_dawg'))
        cls.rust, cls.rust_builder = output / 'ipa_graph', output / 'build_dawg'
        cls.cpp, cls.cpp_builder = cls.directory / 'reference_cli', cls.directory / 'reference_builder'
        compiler = shutil.which('clang++') or shutil.which('g++')
        if not compiler:
            raise unittest.SkipTest('C++ compiler required for migration reference checks')
        subprocess.run([compiler, '-std=c++17', '-O3', '-I', str(ROOT / 'graph/include'),
                        str(ROOT / 'graph/src/ipa_graph.cpp'), str(ROOT / 'graph/src/ipa_graph_cli.cpp'),
                        '-o', str(cls.cpp)], check=True)
        subprocess.run([compiler, '-std=c++17', '-O3', str(ROOT / 'graph/build_dawg.cpp'),
                        '-o', str(cls.cpp_builder)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_query_contract_in_both_formats(self):
        entries = {**ENTRIES, 'ɕoː': [('学校', 4, 100, False)],
                   'a': [('A"B\\C', 0, 100, False), ('𠀀', 2, 90, False),
                         ('cross-region', 0, 80, False), ('cross-region', 1, 81, True)]}
        queries = ['a', 'ab', 'ba', 'b', 'maɪ', 'mɪ', 'ˈmaɪ', 'nixɑʊ',
                   'ni35 xɑʊ̯21', 'ni53 xɑʊ̯21', 'ɕoː', 'é', 'e\u0301', 'e',
                   'ni0', '🙂', '', ' ', 'a' * 65, 'abbbb']
        configurations = [[], ['--strict'], ['--prefix', '--prefix-budget', '1'],
                          ['--omission-budget', '1'], ['--region', 'zh-CN'],
                          ['--region', 'en-US', '--region', 'en-GB'], ['--limit', '0']]
        for bits in (24, 32):
            dictionary = self.directory / f'queries-{bits}.ipad'
            dictionary.write_bytes(build(bits, entries))
            for options in configurations:
                outputs = []
                for cli in (self.cpp, self.rust):
                    process = subprocess.run([str(cli), 'repl', str(dictionary), '--limit', '180', *options],
                                             input='\n'.join(queries) + '\n', text=True,
                                             capture_output=True, check=True)
                    rows = [json.loads(line) for line in process.stdout.splitlines()]
                    for row in rows:
                        row.pop('elapsed_ms', None)
                    outputs.append(rows)
                with self.subTest(bits=bits, options=options):
                    self.assertEqual(outputs[0], outputs[1])

    def test_rejects_corrupt_or_truncated_dictionary(self):
        original = build(24)
        malformed = [original[:length] for length in (0, 7, 107, len(original) - 1)]
        for offset, value in ((0, b'BADMAGIC'), (8, struct.pack('<I', 99)),
                              (12, struct.pack('<I', 0)), (32, struct.pack('<I', 0xffffffff)),
                              (36, struct.pack('<Q', 0)), (100, struct.pack('<Q', len(original) + 1))):
            altered = bytearray(original)
            altered[offset:offset + len(value)] = value
            malformed.append(altered)
        for number, value in enumerate(malformed):
            path = self.directory / f'corrupt-{number}.ipad'
            path.write_bytes(value)
            for cli in (self.cpp, self.rust):
                with self.subTest(number=number, runtime=cli.name):
                    result = subprocess.run([str(cli), 'info', str(path)], capture_output=True)
                    self.assertNotEqual(result.returncode, 0)

    def test_minimizer_emits_identical_sections(self):
        randomizer = random.Random(192)
        keys = sorted({bytes(randomizer.choice([1, 2, 127, 128, 255]) for _ in range(randomizer.randint(1, 12)))
                       for _ in range(5000)})
        stream = self.directory / 'keys.bin'
        stream.write_bytes(b''.join(struct.pack('<H', len(key)) + key for key in keys))
        for bits in (24, 32):
            outputs, metrics = [], []
            for builder in (self.cpp_builder, self.rust_builder):
                output = self.directory / f'{builder.name}-{bits}.bin'
                result = subprocess.run([str(builder), str(stream), str(output), str(bits)],
                                        check=True, text=True, capture_output=True)
                outputs.append(output.read_bytes())
                metrics.append(json.loads(result.stdout))
            self.assertEqual(outputs[0], outputs[1])
            self.assertEqual(metrics[0], metrics[1])
        for invalid in (b'\x01', b'\0\0', b'\x01\0\0', b'\x01\0\x02\x01\0\x01'):
            stream.write_bytes(invalid)
            result = subprocess.run([str(self.rust_builder), str(stream), str(self.directory / 'bad.bin')],
                                    capture_output=True)
            self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
