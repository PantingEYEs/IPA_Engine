"""Verify complete lexical retention, rank addressing, and binary round trips."""
import contextlib
import io
import json
import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path

from graph.compiler import HEADER, compile_dictionary, convert_indices, ensure_builder


class ReferenceDictionary:
    """Independent small test reader: no dependency on the native runtime."""
    def __init__(self, path):
        self.raw = Path(path).read_bytes()
        header = HEADER.unpack_from(self.raw)
        self.magic, self.version, self.nodes, self.arcs, self.keys, self.words, alpha, self.root = header[:8]
        self.offsets = header[8:16]
        self.end = header[16]
        self.width = 3 if self.version == 2 else 4
        self.final_flag = 1 << (self.width * 8 - 1)
        self.labels = {chr(struct.unpack_from('<I', self.raw, self.offsets[0] + i * 4)[0]): i + 1
                       for i in range(alpha)}

    def u24(self, section, index):
        at = self.offsets[section] + index * self.width
        return int.from_bytes(self.raw[at:at + self.width], 'little')

    def arc(self, index):
        at = self.offsets[3] + index * (self.width + 1)
        return self.raw[at], int.from_bytes(self.raw[at + 1:at + 1 + self.width], 'little')

    def rank(self, key):
        node, rank = self.root, 0
        for character in key:
            if character not in self.labels:
                return None
            label = self.labels[character]
            count = self.u24(2, node)
            rank += bool(count & self.final_flag)
            found = None
            for at in range(self.u24(1, node), self.u24(1, node + 1)):
                arc_label, target = self.arc(at)
                if arc_label < label:
                    rank += self.u24(2, target) & (self.final_flag - 1)
                elif arc_label == label:
                    found = target
                    break
                else:
                    break
            if found is None:
                return None
            node = found
        return rank if self.u24(2, node) & self.final_flag else None

    def varint(self, at):
        value, shift = 0, 0
        while True:
            byte = self.raw[at]
            at += 1
            value |= (byte & 127) << shift
            if not byte & 128:
                return value, at
            shift += 7

    def text(self, word):
        at = self.offsets[7] + self.u24(6, word // 16)
        previous = b''
        for unused in range(word % 16 + 1):
            shared, at = self.varint(at)
            length, at = self.varint(at)
            previous = previous[:shared] + self.raw[at:at + length]
            at += length
        return previous.decode('utf-8')

    def postings(self, key):
        rank = self.rank(key)
        if rank is None:
            return []
        at = self.offsets[5] + self.u24(4, rank)
        count, at = self.varint(at)
        previous_word, results = 0, []
        for unused in range(count):
            code, at = self.varint(at)
            previous_word += code >> 4
            score = self.raw[at]
            at += 1
            results.append((self.text(previous_word), code & 7, bool(code & 8), score))
        assert at == self.offsets[5] + self.u24(4, rank + 1)
        return results


def create_fixture(path):
    connection = sqlite3.connect(path)
    connection.executescript('''
        CREATE TABLE pronunciations(id INTEGER PRIMARY KEY,word TEXT,lookup_word TEXT,
            language TEXT,region TEXT,kind TEXT,ipa TEXT,simplified TEXT,traditional TEXT,
            method TEXT,source TEXT,source_line INTEGER,normalized_file TEXT,
            normalized_line INTEGER,review_status TEXT);
        CREATE TABLE frequencies(lookup_word TEXT,language TEXT,region TEXT,kind TEXT,
            zipf REAL,per_million REAL,source TEXT);
        CREATE TABLE sources(id TEXT,url TEXT,path TEXT,license TEXT,note TEXT);
        INSERT INTO sources VALUES('direct','https://example.org/direct','raw/direct','CC0','fixture');
        INSERT INTO sources VALUES('derived','https://example.org/derived','raw/derived','CC0','fixture');
        INSERT INTO sources VALUES('frequency','https://example.org/frequency','raw/frequency','CC0','fixture');
    ''')
    rows = [
        ('Alpha', 'a', 'en-US', False), ('Alpha', 'a', 'en-GB', False),
        ('Alphabeta', 'ab', 'en-US', False), ('Betaalpha', 'ba', 'en-US', False),
        ('Betabeta', 'bb', 'en-US', False), ('APPLE', 'a', 'en-US', False),
        ('一', 'a', 'zh-CN', True), ('一', 'a', 'zh-CN', False),
        ('丁', 'a', 'zh-CN', False), ('台湾', 'tai', 'zh-TW', False),
        ('学校', 'ɡa', 'ja-JP', False), ('x' * 40, 'ɛ', 'en-US', False),
        ('3月', 's', 'ja-JP', False), ('NFC', 'e\u0301', 'en-US', False),
    ]
    for index, (word, ipa, region, derived) in enumerate(rows, 1):
        lang = 'en' if region.startswith('en') else 'zh' if region.startswith('zh') else 'ja'
        review = 'derived_mapping_requires_phonological_review' if derived else 'automatic_provenance_and_character_screen'
        connection.execute('INSERT INTO pronunciations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (index, word, word.casefold(), lang, region, 'word', ipa,
             word if lang == 'zh' else None, '臺灣' if word == '台湾' else word if lang == 'zh' else None,
             'derived_moe_reading_via_kfcd_syllable_mapping' if derived else 'source_ipa',
             'derived' if derived else 'direct', index, 'data/normalized/source.jsonl.gz', index, review))
    connection.execute("INSERT INTO pronunciations VALUES(99,'bad','bad','en','en-US','word','bad',NULL,NULL,'source_ipa','direct',99,'data/normalized/foo.quarantine.jsonl.gz',99,'automatic_provenance_and_character_screen')")
    connection.execute("INSERT INTO pronunciations VALUES(100,'affix','affix','en','en-US','prefix','pa',NULL,NULL,'source_ipa','direct',100,'data/normalized/source.jsonl.gz',100,'automatic_provenance_and_character_screen')")
    connection.executemany('INSERT INTO frequencies VALUES(?,?,?,?,?,?,?)', [
        ('alpha', 'en', 'en-US', 'word', 4.0, None, 'frequency'),
        ('alpha', 'en', 'mixed', 'word', None, 100.0, 'frequency'),
        ('alpha', 'en', 'en-GB', 'word', 6.0, None, 'frequency'),
        ('alpha', 'en', 'en-US', 'character', 9.0, None, 'frequency'),
        ('apple', 'en', 'en-US', 'word', 4.125, None, 'frequency'),
        ('一', 'zh', 'mixed', 'word', 7.0, None, 'frequency'),
        ('学校', 'ja', 'ja-JP', 'word', None, 10.0, 'frequency'),
    ])
    connection.commit()
    connection.close()


class GraphCompilerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_builder()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.database = self.directory / 'source.sqlite'
        create_fixture(self.database)
        self.output = self.directory / 'lexicon.ipad'

    def tearDown(self):
        self.temporary.cleanup()

    def compile(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return compile_dictionary(self.database, self.output, scratch_root=self.directory, **kwargs)

    def test_full_round_trip_rank_regions_frequency_and_direct_preference(self):
        before = self.database.read_bytes()
        report = self.compile(provenance_output=self.directory / 'provenance.jsonl')
        lexicon = ReferenceDictionary(self.output)
        self.assertEqual(lexicon.magic, b'IPADWG01')
        self.assertEqual(lexicon.version, 2)
        self.assertEqual(HEADER.size, 108)
        self.assertEqual(lexicon.end, len(lexicon.raw))
        self.assertTrue(report['complete'])
        self.assertEqual(report['pronunciation_postings'], 13)
        self.assertEqual(report['rejected']['quarantined'], 1)
        self.assertEqual(report['rejected']['nonlexical_kind'], 1)
        self.assertEqual(lexicon.postings('a'), [
            ('APPLE', 0, False, 83), ('Alpha', 0, False, 100), ('Alpha', 1, False, 120),
            ('一', 2, False, 140), ('丁', 2, False, 0)])
        self.assertEqual(lexicon.postings('tai'), [('臺灣', 3, False, 0)])
        self.assertEqual(lexicon.postings('ɡa'), [('学校', 4, False, 80)])
        self.assertEqual(lexicon.postings('ɛ'), [('x' * 40, 0, False, 0)])
        self.assertEqual(lexicon.postings('s'), [('3月', 4, False, 0)])
        self.assertEqual(lexicon.postings('é'), [('NFC', 0, False, 0)])
        keys = sorted(('a', 'ab', 'ba', 'bb', 'tai', 'ɡa', 'ɛ', 's', 'é'))
        self.assertEqual([lexicon.rank(key) for key in keys], list(range(len(keys))))
        self.assertIsNone(lexicon.rank('b'))
        self.assertIsNone(lexicon.rank('abc'))
        self.assertEqual(self.database.read_bytes(), before)
        self.assertLess(report['graph']['nodes'], report['graph']['trie_nodes'])
        for node in range(lexicon.nodes):
            labels = []
            for arc in range(lexicon.u24(1, node), lexicon.u24(1, node + 1)):
                label, target = lexicon.arc(arc)
                self.assertLess(target, node)
                labels.append(label)
            self.assertEqual(labels, sorted(labels))
        traces = [json.loads(line) for line in (self.directory / 'provenance.jsonl').read_text().splitlines()]
        self.assertEqual(len(traces), 13)
        self.assertEqual(next(row for row in traces if row['text'] == '一')['source'], 'direct')

    def test_determinism_explicit_smoke_flag_and_affix_opt_in(self):
        first = self.compile()
        complete = self.output.read_bytes()
        second = self.compile()
        self.assertEqual(first['sha256'], second['sha256'])
        self.assertEqual(self.output.read_bytes(), complete)
        smoke = self.compile(limit=2)
        self.assertFalse(smoke['complete'])
        self.assertEqual(smoke['explicit_source_row_limit'], 2)
        self.assertEqual(smoke['status'], 'explicit_limited_smoke_test')
        self.compile(include_affixes=True)
        self.assertEqual(ReferenceDictionary(self.output).postings('pa'), [('affix', 0, False, 0)])

    def test_size_gate_preserves_existing_artifact_and_reports_full_size(self):
        full = self.compile()
        original = self.output.read_bytes()
        with self.assertRaisesRegex(ValueError, 'no entries were silently dropped'):
            self.compile(max_bytes=full['bytes'] - 1)
        self.assertEqual(self.output.read_bytes(), original)
        report = json.loads(self.output.with_name('lexicon.rejected.manifest.json').read_text())
        self.assertEqual(report['status'], 'rejected_size_limit')
        self.assertEqual(report['pronunciation_postings'], full['pronunciation_postings'])
        self.assertEqual(report['bytes'], full['bytes'])
        current = json.loads(self.output.with_name('lexicon.manifest.json').read_text())
        self.assertEqual(current['sha256'], full['sha256'])

    def test_front_code_block_boundaries_and_utf8_byte_prefixes(self):
        connection = sqlite3.connect(self.database)
        extra = ['字' + chr(0x4e00 + i) for i in range(40)]
        for index, word in enumerate(extra, 200):
            connection.execute('INSERT INTO pronunciations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (index, word, word, 'zh', 'zh-CN', 'word', 'a', word, word,
                 'source_ipa', 'direct', index, 'data/normalized/source.jsonl.gz', index,
                 'automatic_provenance_and_character_screen'))
        connection.commit()
        connection.close()
        self.compile()
        lexicon = ReferenceDictionary(self.output)
        texts = [lexicon.text(word) for word in range(lexicon.words)]
        self.assertEqual(texts, sorted(set(texts)))
        self.assertTrue(set(extra).issubset(texts))
        for block in range((lexicon.words + 15) // 16):
            start = lexicon.offsets[7] + lexicon.u24(6, block)
            prefix, unused = lexicon.varint(start)
            self.assertEqual(prefix, 0)

    def test_full_32bit_compile_conversion_and_exact_24bit_round_trip(self):
        original = self.compile()
        original_bytes = self.output.read_bytes()
        wide_path = self.directory / 'lexicon-32.ipad'
        wide = convert_indices(self.output, wide_path, 32)
        self.assertEqual(wide['version'], 3)
        self.assertEqual(wide['index_bits'], 32)
        expected_growth = original['u24_v2_saved_bytes'] + original['graph']['arcs']
        self.assertEqual(wide['bytes'], original['bytes'] + expected_growth)
        narrow_reader, wide_reader = ReferenceDictionary(self.output), ReferenceDictionary(wide_path)
        for key in ('a', 'ab', 'ba', 'bb', 'tai', 'ɡa', 'ɛ', 's', 'é'):
            self.assertEqual(narrow_reader.rank(key), wide_reader.rank(key))
            self.assertEqual(narrow_reader.postings(key), wide_reader.postings(key))
        for word in range(narrow_reader.words):
            self.assertEqual(narrow_reader.text(word), wide_reader.text(word))
        restored_path = self.directory / 'lexicon-restored.ipad'
        restored = convert_indices(wide_path, restored_path, 24)
        self.assertEqual(restored['sha256'], original['sha256'])
        self.assertEqual(restored_path.read_bytes(), original_bytes)
        independently_compiled = self.compile(index_bits=32)
        self.assertEqual(independently_compiled['sha256'], wide['sha256'])
        self.assertEqual(self.output.read_bytes(), wide_path.read_bytes())
        self.assertEqual(restored['post_blob_sha256'], wide['post_blob_sha256'])
        self.assertEqual(restored['word_blob_sha256'], wide['word_blob_sha256'])
        with self.assertRaisesRegex(ValueError, 'exceeding strict limit'):
            convert_indices(restored_path, self.directory / 'too-small.ipad', 32,
                            max_bytes=wide['bytes'] - 1)
        with self.assertRaisesRegex(ValueError, 'must differ'):
            convert_indices(restored_path, restored_path, 24)


if __name__ == '__main__':
    unittest.main()
