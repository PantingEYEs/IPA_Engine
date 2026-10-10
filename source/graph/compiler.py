"""Lossless offline lexicon compiler for the mmap-friendly IPADWG01 format.

The SQLite observation archive remains read-only. Disk-backed deduplication,
one frequency scan per language, and a sorted incremental Rust minimizer keep
the full compiler practical without loading the observation archive into RAM.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import shutil
import sqlite3
import struct
import subprocess
import tempfile
import unicodedata
from pathlib import Path

from scripts.normalize import language_ipa_reason, strict_ipa

ROOT = Path(__file__).resolve().parents[1]
MAGIC = b'IPADWG01'
HEADER = struct.Struct('<8s7I9Q')
HEADER_SIZE = HEADER.size  # 108 bytes, no implicit alignment.
REGIONS = ('en-US', 'en-GB', 'zh-CN', 'zh-TW', 'ja-JP')
REGION_IDS = {region: index for index, region in enumerate(REGIONS)}
LANGUAGES = {'en-US': 'en', 'en-GB': 'en', 'zh-CN': 'zh', 'zh-TW': 'zh', 'ja-JP': 'ja'}
DIRECT_REVIEWS = {
    'automatic_provenance_and_character_screen',
    'source_ipa_with_regional_uncertainty_screened',
    'source_UK_US_label_exact_requested_headword',
}
DERIVED_REVIEWS = {
    'derived_mapping_requires_phonological_review',
    'derived_reading_aligned_lexical_pitch_not_acoustic_contour',
}
AFFIX_KINDS = {'prefix', 'suffix', 'infix', 'interfix', 'affix', 'root', 'combining-form', 'circumfix'}


def utf16_length(text):
    return len(text.encode('utf-16-le', errors='surrogatepass')) // 2


def valid_output(text):
    # A dictionary headword may contain digits or punctuation. Only enforce the
    # host's actual text contract, rather than the neural decoder's 31 slots.
    return (isinstance(text, str) and bool(text.strip())
            and utf16_length(text) <= 1000
            and not any(unicodedata.category(c) in {'Cc', 'Cs'} for c in text))


def normalized_form(text, language):
    text = unicodedata.normalize('NFC', text or '')
    return text.casefold() if language == 'en' else text


def frequency_score(zipf, per_million):
    values = []
    if zipf is not None and math.isfinite(zipf):
        values.append(zipf)
    if per_million is not None and math.isfinite(per_million) and per_million > 0:
        values.append(math.log10(per_million) + 3)
    return max(0.0, min(9.0, max(values))) if values else 0.0


def quantize_frequency(score):
    # Half-up, deterministic across Python/C++ and independent of banker's rounding.
    return math.floor(max(0.0, min(9.0, score)) * 20 + 0.5)


def encode_varint(value):
    if value < 0:
        raise ValueError('unsigned varint cannot encode a negative integer')
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def _write_u32(out, value):
    if not 0 <= value <= 0xffffffff:
        raise ValueError('section offset exceeds uint32')
    out.write(struct.pack('<I', value))


def _write_u24(out, value):
    if not 0 <= value < (1 << 24):
        raise ValueError('packed section offset exceeds uint24; format cannot be truncated')
    out.write(value.to_bytes(3, 'little'))


def _pack_graph_u24(source, target, offset, count, final_counts=False):
    with Path(source).open('rb') as incoming:
        incoming.seek(offset)
        while count:
            n = min(count, 65536)
            block = incoming.read(n * 4)
            if len(block) != n * 4:
                raise ValueError('truncated intermediate graph integer array')
            packed = bytearray()
            for (value,) in struct.iter_unpack('<I', block):
                if final_counts:
                    suffix_count = value & 0x7fffffff
                    if suffix_count >= (1 << 23):
                        raise ValueError('accepted suffix count exceeds 23 bits; format cannot be truncated')
                    value = suffix_count | (0x800000 if value & 0x80000000 else 0)
                if value >= (1 << 24):
                    raise ValueError('graph array value exceeds uint24; format cannot be truncated')
                packed.extend(value.to_bytes(3, 'little'))
            target.write(packed)
            count -= n


def _copy_section(source, target, offset=0, count=None):
    with Path(source).open('rb') as incoming:
        incoming.seek(offset)
        if count is None:
            shutil.copyfileobj(incoming, target, 1024 * 1024)
        else:
            while count:
                block = incoming.read(min(count, 1024 * 1024))
                if not block:
                    raise ValueError('truncated intermediate graph section')
                target.write(block)
                count -= len(block)


def ensure_builder(build_dir=None):
    build_dir = Path(build_dir or ROOT / '.tools' / 'graph').resolve()
    build_dir.mkdir(parents=True, exist_ok=True)
    sources = [ROOT / 'rust/ipa_graph/Cargo.toml', *list((ROOT / 'rust/ipa_graph/src').rglob('*.rs'))]
    executable = build_dir / 'build_dawg'
    if not executable.exists() or executable.stat().st_mtime_ns < max(p.stat().st_mtime_ns for p in sources):
        from scripts.build_rust_core import build
        temporary = executable.with_suffix('.part')
        shutil.copyfile(build(bins=('build_dawg',)) / 'build_dawg', temporary)
        temporary.chmod(0o755)
        temporary.replace(executable)
    return executable


def _prepare_candidates(source, scratch, limit, include_affixes):
    scratch.executescript('''
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        PRAGMA temp_store=FILE;
        PRAGMA cache_size=-32768;
        CREATE TABLE candidates(
            ipa TEXT, text TEXT, region INTEGER, language TEXT, derived INTEGER,
            word TEXT, lookup_word TEXT, simplified TEXT, traditional TEXT,
            source TEXT, method TEXT, source_line INTEGER, normalized_file TEXT,
            normalized_line INTEGER, provenance TEXT, frequency REAL DEFAULT 0,
            PRIMARY KEY(ipa,text,region)) WITHOUT ROWID;
        CREATE INDEX candidate_language ON candidates(language);
    ''')
    insertion = '''INSERT INTO candidates(ipa,text,region,language,derived,word,
        lookup_word,simplified,traditional,source,method,source_line,normalized_file,
        normalized_line,provenance) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(ipa,text,region) DO UPDATE SET
            language=excluded.language, derived=excluded.derived, word=excluded.word,
            lookup_word=excluded.lookup_word, simplified=excluded.simplified,
            traditional=excluded.traditional, source=excluded.source, method=excluded.method,
            source_line=excluded.source_line, normalized_file=excluded.normalized_file,
            normalized_line=excluded.normalized_line, provenance=excluded.provenance
        WHERE excluded.provenance < candidates.provenance'''
    scanned, rejected = collections.Counter(), collections.Counter()
    query = '''SELECT word,lookup_word,language,region,kind,ipa,simplified,traditional,
        method,source,source_line,normalized_file,normalized_line,review_status
        FROM pronunciations WHERE region IN (?,?,?,?,?) ORDER BY id'''
    if limit is not None:
        query += ' LIMIT ?'
    parameters = REGIONS if limit is None else (*REGIONS, limit)
    batch = []
    last_progress = 0
    for (word, lookup, language, region, kind, raw_ipa, simplified, traditional,
         method, source_id, line, normalized_file, normalized_line, review) in source.execute(query, parameters):
        scanned[region] += 1
        if language != LANGUAGES[region]:
            rejected['region_language_mismatch'] += 1
            continue
        if kind not in {'word', 'character'} and not (include_affixes and kind in AFFIX_KINDS):
            rejected['nonlexical_kind'] += 1
            continue
        if '.quarantine.' in (normalized_file or '') or 'quarantine' in (review or '').lower():
            rejected['quarantined'] += 1
            continue
        derived = bool((method or '').startswith('derived'))
        if ((derived and review not in DERIVED_REVIEWS)
            or (not derived and (review not in DIRECT_REVIEWS
                or not (method or '').startswith(('source_', 'chao_digits_to_ipa_tone_letters'))))):
            rejected['unscreened_provenance'] += 1
            continue
        ipa, reason = strict_ipa(raw_ipa)
        if reason or utf16_length(ipa) > 64 or language_ipa_reason(ipa, language):
            rejected['invalid_or_long_ipa'] += 1
            continue
        text = traditional if region == 'zh-TW' else simplified if region == 'zh-CN' else word
        text = unicodedata.normalize('NFC', text or word or '')
        if not valid_output(text):
            rejected['invalid_or_long_output'] += 1
            continue
        provenance = json.dumps([int(derived), source_id or '', method or '',
                                 normalized_file or '', normalized_line or 0, line or 0],
                                ensure_ascii=False, separators=(',', ':'))
        batch.append((ipa, text, REGION_IDS[region], language, int(derived), word, lookup,
                      simplified, traditional, source_id, method, line,
                      normalized_file, normalized_line, provenance))
        if len(batch) >= 10000:
            scratch.executemany(insertion, batch)
            scratch.commit()
            batch.clear()
            scanned_total = sum(scanned.values())
            if scanned_total - last_progress >= 100000:
                print(f'accepted/deduplicating rows: {scanned_total:,}', flush=True)
                last_progress = scanned_total
    if batch:
        scratch.executemany(insertion, batch)
    scratch.commit()
    return scanned, rejected


def _attach_frequencies(source, scratch):
    sources, scanned = set(), collections.Counter()
    for language in ('en', 'zh', 'ja'):
        regions = tuple(region for region in REGIONS if LANGUAGES[region] == language)
        needed = set()
        for forms in scratch.execute('SELECT word,lookup_word,simplified,traditional,text '
                                     'FROM candidates WHERE language=?', (language,)):
            needed.update(normalized_form(form, language) for form in forms if form)
        if not needed:
            scanned[language] = 0
            continue
        scores = {}
        query = '''SELECT lookup_word,region,zipf,per_million,source FROM frequencies
            WHERE language=? AND kind='word' AND region IN ('''
        query += ','.join('?' for _ in regions) + ", 'mixed')"
        print(f'scan comparable frequencies once: {language}, {len(needed):,} written forms', flush=True)
        for word, region, zipf, per_million, source_id in source.execute(query, (language, *regions)):
            scanned[language] += 1
            key = normalized_form(word, language)
            if key not in needed:
                continue
            score = frequency_score(zipf, per_million)
            if score > 0:
                sources.add(source_id)
                pair = (region, key)
                scores[pair] = max(scores.get(pair, 0.0), score)
        batch = []
        query = 'SELECT ipa,text,region,word,lookup_word,simplified,traditional FROM candidates WHERE language=?'
        for ipa, text, region_id, *forms in scratch.execute(query, (language,)):
            region = REGIONS[region_id]
            score = max((scores.get((r, normalized_form(form, language)), 0.0)
                         for form in (*forms, text) if form for r in (region, 'mixed')), default=0.0)
            batch.append((score, ipa, text, region_id))
            if len(batch) >= 10000:
                scratch.executemany('UPDATE candidates SET frequency=? WHERE ipa=? AND text=? AND region=?', batch)
                batch.clear()
        if batch:
            scratch.executemany('UPDATE candidates SET frequency=? WHERE ipa=? AND text=? AND region=?', batch)
        scratch.commit()
    return sources, scanned


def _write_pools(scratch, directory, index_bits=24):
    write_index = _write_u24 if index_bits == 24 else _write_u32
    scratch.executescript('''
        CREATE TABLE words(id INTEGER PRIMARY KEY, text TEXT UNIQUE);
        INSERT INTO words(text) SELECT DISTINCT text FROM candidates ORDER BY text;
        CREATE TABLE keys(id INTEGER PRIMARY KEY, ipa TEXT UNIQUE);
        INSERT INTO keys(ipa) SELECT DISTINCT ipa FROM candidates ORDER BY ipa;
    ''')
    scratch.commit()
    alphabet = sorted({ord(character) for (ipa,) in scratch.execute('SELECT ipa FROM keys') for character in ipa})
    if len(alphabet) > 255:
        raise ValueError(f'IPA alphabet has {len(alphabet)} scalar values; format supports at most 255')
    labels = {value: index + 1 for index, value in enumerate(alphabet)}
    with (directory / 'keys.bin').open('wb') as out:
        for (ipa,) in scratch.execute('SELECT ipa FROM keys ORDER BY id'):
            encoded = bytes(labels[ord(character)] for character in ipa)
            out.write(struct.pack('<H', len(encoded)))
            out.write(encoded)
    with (directory / 'word_offsets.bin').open('wb') as offsets, (directory / 'words.bin').open('wb') as out:
        previous = b''
        for id_, text in scratch.execute('SELECT id-1,text FROM words ORDER BY id'):
            current = text.encode('utf-8')
            if id_ % 16 == 0:
                write_index(offsets, out.tell())
                shared = 0
            else:
                shared = 0
                while shared < min(len(previous), len(current)) and previous[shared] == current[shared]:
                    shared += 1
            suffix = current[shared:]
            out.write(encode_varint(shared))
            out.write(encode_varint(len(suffix)))
            out.write(suffix)
            previous = current
        write_index(offsets, out.tell())
    with (directory / 'post_offsets.bin').open('wb') as offsets, (directory / 'posts.bin').open('wb') as out:
        # The primary key makes source pronunciation traversal disk-streaming;
        # the join only maps to the global sorted unique orthography IDs.
        current_key, records = None, []

        def flush():
            if current_key is None:
                return
            write_index(offsets, out.tell())
            records.sort(key=lambda record: (record[0], record[1]))
            out.write(encode_varint(len(records)))
            previous_word = 0
            for word_id, region, derived, score in records:
                code = ((word_id - previous_word) << 4) | region | (derived << 3)
                out.write(encode_varint(code))
                out.write(bytes((quantize_frequency(score),)))
                previous_word = word_id

        query = '''SELECT c.ipa,w.id-1,c.region,c.derived,c.frequency
                   FROM candidates c JOIN words w ON c.text=w.text ORDER BY c.ipa,c.text,c.region'''
        for ipa, word_id, region, derived, score in scratch.execute(query):
            if ipa != current_key:
                flush()
                current_key, records = ipa, []
            records.append((word_id, region, derived, score))
        flush()
        write_index(offsets, out.tell())
    return alphabet, scratch.execute('SELECT COUNT(*) FROM words').fetchone()[0]


def _write_trace(scratch, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch.row_factory = sqlite3.Row
    try:
        with path.open('w', encoding='utf-8') as out:
            for row in scratch.execute('''SELECT ipa,text,region,derived,frequency,source,method,
                source_line,normalized_file,normalized_line FROM candidates ORDER BY ipa,text,region'''):
                record = dict(row)
                record['region'] = REGIONS[record['region']]
                out.write(json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n')
    finally:
        scratch.row_factory = None


def compile_dictionary(database, output, max_bytes=None, limit=None,
                       include_affixes=False, provenance_output=None, scratch_root=None, index_bits=24):
    if index_bits not in (24, 32):
        raise ValueError('index_bits must be 24 or 32')
    if max_bytes is None:
        max_bytes = 30_000_000 if index_bits == 24 else 40_000_000
    if max_bytes <= 0:
        raise ValueError('max_bytes must be positive')
    if limit is not None and limit <= 0:
        raise ValueError('explicit smoke-test limit must be positive')
    database, output = Path(database).resolve(), Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    scratch_root = Path(scratch_root or ROOT / '.tools' / 'tmp').resolve()
    scratch_root.mkdir(parents=True, exist_ok=True)
    builder = ensure_builder()
    source = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    source.execute('PRAGMA query_only=ON')
    temporary_output = output.with_name(output.name + '.part')
    try:
        with tempfile.TemporaryDirectory(prefix='ipa-graph-', dir=scratch_root) as temporary:
            directory = Path(temporary)
            scratch = sqlite3.connect(directory / 'candidates.sqlite')
            try:
                scanned, rejected = _prepare_candidates(source, scratch, limit, include_affixes)
                print('pronunciation scan complete; frequency association next', flush=True)
                frequency_sources, frequency_scanned = _attach_frequencies(source, scratch)
                alphabet, word_count = _write_pools(scratch, directory, index_bits)
                built = subprocess.run([str(builder), str(directory / 'keys.bin'), str(directory / 'graph.bin'), str(index_bits)],
                                       check=True, capture_output=True, text=True)
                graph_stats = json.loads(built.stdout)
                nodes, arcs, keys, root = (graph_stats[name] for name in ('nodes', 'arcs', 'keys', 'root'))
                if index_bits == 24 and (arcs >= (1 << 24) or keys >= (1 << 23)):
                    raise ValueError('version 2 graph arc offsets or accepted key counts exceed packed limits; no truncation')
                section_names = ['alphabet', 'node_offsets', 'node_counts', 'arcs', 'post_offsets',
                                 'post_blob', 'word_block_offsets', 'word_blob']
                index_bytes = index_bits // 8
                version = 2 if index_bits == 24 else 3
                section_sizes = [len(alphabet) * 4, (nodes + 1) * index_bytes, nodes * index_bytes, arcs * (1 + index_bytes),
                                 (directory / 'post_offsets.bin').stat().st_size,
                                 (directory / 'posts.bin').stat().st_size,
                                 (directory / 'word_offsets.bin').stat().st_size,
                                 (directory / 'words.bin').stat().st_size]
                offsets, end = [], HEADER_SIZE
                for size in section_sizes:
                    offsets.append(end)
                    end += size
                rows = scratch.execute('SELECT COUNT(*) FROM candidates').fetchone()[0]
                counts = {REGIONS[region]: count for region, count in scratch.execute(
                    'SELECT region,COUNT(*) FROM candidates GROUP BY region')}
                derived_counts = {REGIONS[region]: count for region, count in scratch.execute(
                    'SELECT region,COUNT(*) FROM candidates WHERE derived=1 GROUP BY region')}
                used_sources = set(frequency_sources) | {row[0] for row in scratch.execute('SELECT DISTINCT source FROM candidates')}
                source_records = [dict(zip(('id', 'url', 'path', 'license', 'note'), row))
                                  for row in source.execute('SELECT id,url,path,license,note FROM sources ORDER BY id')
                                  if row[0] in used_sources]
                stats = database.stat()
                arrays_entries = (nodes + 1) + nodes + (keys + 1) + ((word_count + 15) // 16 + 1)
                v2_bytes = end if index_bits == 24 else end - arrays_entries - arcs
                manifest = {
                    'format': 'IPADWG01', 'version': version, 'index_bits': index_bits, 'complete': limit is None,
                    'explicit_source_row_limit': limit, 'include_affixes': include_affixes,
                    'byte_limit': max_bytes, 'bytes': end, 'header_bytes': HEADER_SIZE,
                    'source_database': {'path': str(database), 'bytes': stats.st_size, 'mtime_ns': stats.st_mtime_ns},
                    'source_scanned_by_region': dict(scanned), 'rejected': dict(rejected),
                    'frequency_observations_scanned_by_language': dict(frequency_scanned),
                    'pronunciation_postings': rows, 'postings_by_region': counts,
                    'derived_postings_by_region': derived_counts,
                    'ipa_keys': keys, 'unique_texts': word_count, 'alphabet_scalars': len(alphabet),
                    'graph': graph_stats,
                    'trie_same_fixed_records_estimated_bytes': (graph_stats['trie_nodes'] + 1) * 4
                        + graph_stats['trie_nodes'] * 4 + graph_stats['trie_arcs'] * 4,
                    'dafsa_graph_bytes': sum(section_sizes[1:4]),
                    'u32_v1_same_lexicon_projected_bytes': v2_bytes + arrays_entries,
                    'u32_v3_same_lexicon_projected_bytes': v2_bytes + arrays_entries + arcs,
                    'u24_v2_saved_bytes': arrays_entries,
                    'u24_v2_saved_vs_full_u32_v3_bytes': arrays_entries + arcs,
                    'section_bytes': dict(zip(section_names, section_sizes)),
                    'section_offsets': dict(zip(section_names, offsets)),
                    'normalization': 'source-screened strict IPA, NFC, scalar alphabet; no tone/stress removal',
                    'frequency': 'max comparable word Zipf or log10(per_million)+3, same region or mixed; missing=0; quantized to 0.05 Zipf',
                    'deduplication': '(IPA,text,region); direct provenance preferred over derived, stable source tie-break',
                    'sources': source_records,
                    'provenance_sidecar': str(Path(provenance_output).resolve()) if provenance_output else None,
                }
                # This gate applies to the complete lexical package, not only the
                # graph. Never shrink, downsample, or drop entries to pass it.
                if end > max_bytes:
                    manifest['status'] = 'rejected_size_limit'
                    failure = output.with_name(output.stem + '.rejected.manifest.json')
                    failure.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                    raise ValueError(f'full dictionary needs {end:,} bytes, exceeding strict limit {max_bytes:,}; '
                                     f'no entries were silently dropped; report: {failure}')
                with temporary_output.open('wb') as target:
                    target.write(HEADER.pack(MAGIC, version, nodes, arcs, keys, word_count,
                                             len(alphabet), root, *offsets, end))
                    for scalar in alphabet:
                        _write_u32(target, scalar)
                    if index_bits == 24:
                        _pack_graph_u24(directory / 'graph.bin', target, offset=24, count=nodes + 1)
                        _pack_graph_u24(directory / 'graph.bin', target, offset=24 + (nodes + 1) * 4,
                                         count=nodes, final_counts=True)
                    else:
                        _copy_section(directory / 'graph.bin', target, offset=24, count=(nodes + 1) * 4 + nodes * 4)
                    _copy_section(directory / 'graph.bin', target,
                                  offset=24 + (nodes + 1) * 4 + nodes * 4, count=arcs * (1 + index_bytes))
                    for filename in ('post_offsets.bin', 'posts.bin', 'word_offsets.bin', 'words.bin'):
                        _copy_section(directory / filename, target)
                if temporary_output.stat().st_size != end:
                    raise RuntimeError('internal dictionary section size mismatch')
                if provenance_output:
                    _write_trace(scratch, provenance_output)
                digest = hashlib.sha256()
                with temporary_output.open('rb') as incoming:
                    for block in iter(lambda: incoming.read(1024 * 1024), b''):
                        digest.update(block)
                manifest['sha256'] = digest.hexdigest()
                manifest['status'] = 'complete' if limit is None else 'explicit_limited_smoke_test'
                temporary_output.replace(output)
                manifest_path = output.with_name(output.stem + '.manifest.json')
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                print(f'compiled {rows:,} postings / {keys:,} IPA keys / {word_count:,} texts: {end:,} bytes', flush=True)
                return manifest
            finally:
                scratch.close()
    finally:
        source.close()
        if temporary_output.exists():
            temporary_output.unlink()


def convert_indices(source_path, output_path, index_bits, max_bytes=None):
    """Losslessly change v2/v3 index widths without rescanning the source archive.

    Node IDs, arc order, key ranks, postings, orthography IDs, and frequency
    bytes remain unchanged. A matching source manifest preserves provenance.
    """
    if index_bits not in (24, 32):
        raise ValueError('index_bits must be 24 or 32')
    if max_bytes is None:
        max_bytes = 30_000_000 if index_bits == 24 else 40_000_000
    if max_bytes <= 0:
        raise ValueError('max_bytes must be positive')
    source_path, output_path = Path(source_path).resolve(), Path(output_path).resolve()
    if source_path == output_path:
        raise ValueError('conversion output must differ from the source artifact')
    raw = source_path.read_bytes()
    if len(raw) < HEADER_SIZE:
        raise ValueError('source dictionary header is truncated')
    header = HEADER.unpack_from(raw)
    magic, source_version, nodes, arcs, keys, words, alphabet, root = header[:8]
    old_offsets = list(header[8:16])
    old_end = header[16]
    if magic != MAGIC or source_version not in (2, 3):
        raise ValueError('conversion requires an IPADWG01 version 2 or 3 dictionary')
    if old_end != len(raw) or old_offsets[0] != HEADER_SIZE or old_offsets != sorted(old_offsets):
        raise ValueError('source section offsets or end offset are invalid')
    if not nodes or root >= nodes or alphabet > 255 or old_offsets[-1] > old_end:
        raise ValueError('source dictionary dimensions are invalid')
    old_width = 3 if source_version == 2 else 4
    width = index_bits // 8
    version = 2 if index_bits == 24 else 3
    old_sizes = [right - left for left, right in zip(old_offsets, old_offsets[1:] + [old_end])]
    counts = {1: nodes + 1, 2: nodes, 4: keys + 1, 6: (words + 15) // 16 + 1}
    if old_sizes[0] != alphabet * 4 or old_sizes[3] != arcs * (old_width + 1):
        raise ValueError('source alphabet or arc section size is invalid')
    for section, count in counts.items():
        if old_sizes[section] != count * old_width:
            raise ValueError('source integer array section size is invalid')
    if index_bits == 24 and (nodes >= (1 << 24) or arcs >= (1 << 24) or keys >= (1 << 23)
                            or old_sizes[5] >= (1 << 24) or old_sizes[7] >= (1 << 24)):
        raise ValueError('source dictionary exceeds 24-bit representable limits; no truncation')
    manifest_path = source_path.with_name(source_path.stem + '.manifest.json')
    if not manifest_path.exists():
        raise ValueError(f'a matching provenance manifest is required: {manifest_path}')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    source_digest = hashlib.sha256(raw).hexdigest()
    if manifest.get('sha256') != source_digest or manifest.get('bytes') != len(raw):
        raise ValueError('source artifact SHA256 or size does not match its provenance manifest')
    sizes = old_sizes.copy()
    for section, count in counts.items():
        sizes[section] = count * width
    sizes[3] = arcs * (width + 1)
    offsets, end = [], HEADER_SIZE
    for size in sizes:
        offsets.append(end)
        end += size
    if end > max_bytes:
        raise ValueError(f'converted dictionary needs {end:,} bytes, exceeding strict limit {max_bytes:,}; '
                         'no entries were silently dropped')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.name + '.part')
    section_names = ['alphabet', 'node_offsets', 'node_counts', 'arcs', 'post_offsets',
                     'post_blob', 'word_block_offsets', 'word_blob']
    try:
        with temporary.open('wb') as out:
            out.write(HEADER.pack(MAGIC, version, nodes, arcs, keys, words, alphabet, root, *offsets, end))
            for section in range(8):
                start, size = old_offsets[section], old_sizes[section]
                if section in counts:
                    packed = bytearray()
                    for at in range(start, start + size, old_width):
                        value = int.from_bytes(raw[at:at + old_width], 'little')
                        if section == 2:
                            old_flag = 1 << (old_width * 8 - 1)
                            suffix_count = value & (old_flag - 1)
                            flag = bool(value & old_flag)
                            if suffix_count >= (1 << (index_bits - 1)):
                                raise ValueError('accepted suffix count exceeds requested index width; no truncation')
                            value = suffix_count | ((1 << (index_bits - 1)) if flag else 0)
                        if value >= (1 << index_bits):
                            raise ValueError('array value exceeds requested index width; no truncation')
                        packed.extend(value.to_bytes(width, 'little'))
                        if len(packed) >= 262144:
                            out.write(packed)
                            packed.clear()
                    out.write(packed)
                elif section == 3:
                    packed = bytearray()
                    for at in range(start, start + size, old_width + 1):
                        label = raw[at]
                        target = int.from_bytes(raw[at + 1:at + 1 + old_width], 'little')
                        if not 1 <= label <= alphabet or target >= nodes or target >= (1 << index_bits):
                            raise ValueError('arc label or target exceeds source/requested format limits')
                        packed.append(label)
                        packed.extend(target.to_bytes(width, 'little'))
                        if len(packed) >= 262144:
                            out.write(packed)
                            packed.clear()
                    out.write(packed)
                else:
                    out.write(raw[start:start + size])
        if temporary.stat().st_size != end:
            raise RuntimeError('converted section size mismatch')
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        arrays_entries = sum(counts.values())
        v2_bytes = end if index_bits == 24 else end - arrays_entries - arcs
        manifest.update({
            'version': version, 'index_bits': index_bits, 'bytes': end, 'byte_limit': max_bytes,
            'sha256': digest, 'section_bytes': dict(zip(section_names, sizes)),
            'section_offsets': dict(zip(section_names, offsets)), 'dafsa_graph_bytes': sum(sizes[1:4]),
            'u32_v1_same_lexicon_projected_bytes': v2_bytes + arrays_entries,
            'u32_v3_same_lexicon_projected_bytes': v2_bytes + arrays_entries + arcs,
            'u24_v2_saved_bytes': arrays_entries,
            'u24_v2_saved_vs_full_u32_v3_bytes': arrays_entries + arcs,
            'post_blob_sha256': hashlib.sha256(raw[old_offsets[5]:old_offsets[5] + old_sizes[5]]).hexdigest(),
            'word_blob_sha256': hashlib.sha256(raw[old_offsets[7]:old_offsets[7] + old_sizes[7]]).hexdigest(),
        })
        manifest.setdefault('index_conversion_history', []).append({
            'source': str(source_path), 'source_version': source_version,
            'source_sha256': source_digest, 'target_version': version,
            'lossless': True, 'source_database_rescanned': False,
        })
        temporary.replace(output_path)
        output_path.with_name(output_path.stem + '.manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return manifest
    finally:
        if temporary.exists():
            temporary.unlink()


def main():
    parser = argparse.ArgumentParser(description='Compile all usable pronunciations to a compact minimal DAFSA dictionary.')
    parser.add_argument('--database', type=Path, default=ROOT / 'data' / 'ipa.sqlite')
    parser.add_argument('--output', type=Path,
                        help='default lexicon.ipad for 24-bit or lexicon-32.ipad for 32-bit; comparison preserves 24-bit file')
    parser.add_argument('--max-bytes', type=int,
                        help='strict decimal ceiling; default 30000000 for 24-bit, 40000000 for 32-bit; never prunes')
    parser.add_argument('--index-bits', type=int, choices=(24, 32), default=24,
                        help='24-bit compact v2 (default) or full 32-bit v3 indices and targets')
    parser.add_argument('--limit', type=int, help='explicit source row limit for smoke tests only; marks manifest incomplete')
    parser.add_argument('--include-affixes', action='store_true')
    parser.add_argument('--provenance-output', type=Path,
                        help='optional training/audit JSONL sidecar; do not ship it to the phone')
    parser.add_argument('--scratch-root', type=Path)
    args = parser.parse_args()
    output = args.output or ROOT / 'data' / 'compiled' / ('lexicon.ipad' if args.index_bits == 24 else 'lexicon-32.ipad')
    manifest = compile_dictionary(args.database, output, args.max_bytes, args.limit,
                                  args.include_affixes, args.provenance_output, args.scratch_root, args.index_bits)
    print(json.dumps({key: manifest[key] for key in ('bytes', 'ipa_keys', 'unique_texts', 'sha256', 'complete')},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
