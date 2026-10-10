"""Export a bounded, reproducible training corpus from the observation index.

This is an offline training tool. It opens the 2 GB index read-only and never
copies dictionary rows into the inference package. Deduplication is disk-backed;
frequency evidence is scanned once per language, rather than queried per row.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import sqlite3
import sys
import tempfile
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.normalize import language_ipa_reason, strict_ipa

REGIONS = ('en-US', 'en-GB', 'zh-CN', 'zh-TW', 'ja-JP')
LANGUAGES = {'en-US': 'en', 'en-GB': 'en', 'zh-CN': 'zh',
             'zh-TW': 'zh', 'ja-JP': 'ja'}
DIRECT_REVIEWS = {'automatic_provenance_and_character_screen',
                  'source_ipa_with_regional_uncertainty_screened',
                  'source_UK_US_label_exact_requested_headword'}


def utf16_length(text):
    return len(text.encode('utf-16-le', errors='surrogatepass')) // 2


def normalize_word(word, language):
    result = unicodedata.normalize('NFC', word or '')
    return result.casefold() if language == 'en' else result


def valid_output(text):
    # One decoder slot is reserved for EOS. Keep complete words, never truncate.
    return (isinstance(text, str) and bool(text.strip()) and len(text) <= 31
            and utf16_length(text) <= 1000
            and any(unicodedata.category(c).startswith('L') for c in text)
            and all(unicodedata.category(c)[0] in ('L', 'M')
                    or c in " -'’" for c in text))


def frequency_score(zipf, per_million):
    """Maximum comparable written-word evidence; raw counts are not combined."""
    values = []
    if zipf is not None and math.isfinite(zipf):
        values.append(zipf)
    if per_million is not None and math.isfinite(per_million) and per_million > 0:
        values.append(math.log10(per_million) + 3)
    return max(0.0, min(9.0, max(values))) if values else 0.0


def split_for(group_id, seed):
    value = int.from_bytes(hashlib.sha256(f'{seed}:{group_id}'.encode()).digest()[:8], 'big') % 100
    return 'train' if value < 90 else 'valid' if value < 95 else 'test'


def assign_groups(rows, seed=42, group_homophones=False):
    """Group all readings/regions/scripts of a canonical written word together.

    Optional homophone components are deliberately not the default: polyphonic
    Chinese characters can link very large components and unbalance the split.
    """
    parent = {}

    def find(key):
        parent.setdefault(key, key)
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(left, right):
        left, right = find(left), find(right)
        if left != right:
            parent[max(left, right)] = min(left, right)

    ipa_groups = {}
    for row in rows:
        key = row['language'] + ':' + row['canonical_word']
        find(key)
        if group_homophones:
            ipa_key = (row['language'], row['ipa'])
            if ipa_key in ipa_groups:
                union(key, ipa_groups[ipa_key])
            ipa_groups[ipa_key] = key
    sizes = collections.Counter()
    for row in rows:
        key = find(row['language'] + ':' + row['canonical_word'])
        group_id = hashlib.sha256(key.encode()).hexdigest()[:24]
        row['group_id'] = group_id
        row['split'] = split_for(group_id, seed)
        sizes[group_id] += 1
    return {'groups': len(sizes), 'largest_group_rows': max(sizes.values(), default=0),
            'largest_group_rows_top10': sorted(sizes.values(), reverse=True)[:10]}


def prepare_corpus(database, output_dir, max_per_region=12000, seed=42,
                   min_frequency=0.0, include_derived=False, group_homophones=False):
    if max_per_region < 1:
        raise ValueError('max_per_region must be positive')
    if not math.isfinite(min_frequency) or min_frequency < 0:
        raise ValueError('min_frequency must be a finite nonnegative Zipf score')
    database, output_dir = Path(database).resolve(), Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    source_db = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    source_db.execute('PRAGMA query_only=ON')
    rejected, scanned, available = collections.Counter(), collections.Counter(), {}
    selected = []
    frequency_sources = set()
    try:
        with tempfile.TemporaryDirectory(prefix='ipa-model-data-') as scratch_dir:
            scratch = sqlite3.connect(Path(scratch_dir) / 'candidates.sqlite')
            scratch.executescript('''
                PRAGMA journal_mode=OFF;
                PRAGMA synchronous=OFF;
                PRAGMA cache_size=-16384;
                CREATE TABLE candidates(
                    id INTEGER PRIMARY KEY, region TEXT, ipa TEXT, text TEXT,
                    language TEXT, word TEXT, lookup_word TEXT, simplified TEXT,
                    traditional TEXT, canonical_word TEXT, source TEXT, method TEXT,
                    source_line INTEGER, normalized_file TEXT, normalized_line INTEGER,
                    provenance_key TEXT, frequency REAL DEFAULT 0,
                    UNIQUE(region,ipa,text));
            ''')
            insertion = '''INSERT INTO candidates(region,ipa,text,language,word,
                lookup_word,simplified,traditional,canonical_word,source,method,
                source_line,normalized_file,normalized_line,provenance_key)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(region,ipa,text) DO UPDATE SET
                    word=excluded.word, lookup_word=excluded.lookup_word,
                    simplified=excluded.simplified, traditional=excluded.traditional,
                    canonical_word=excluded.canonical_word, source=excluded.source,
                    method=excluded.method, source_line=excluded.source_line,
                    normalized_file=excluded.normalized_file,
                    normalized_line=excluded.normalized_line,
                    provenance_key=excluded.provenance_key
                WHERE excluded.provenance_key < candidates.provenance_key'''
            for language in ('en', 'zh', 'ja'):
                regions = tuple(r for r in REGIONS if LANGUAGES[r] == language)
                print(f'prepare pronunciation rows: {language}', flush=True)
                query = '''SELECT word,lookup_word,region,kind,ipa,simplified,traditional,
                    method,source,source_line,normalized_file,normalized_line,review_status
                    FROM pronunciations WHERE language=? AND region IN ('''
                query += ','.join('?' for _ in regions) + ')'
                batch = []
                for (word, lookup, region, kind, raw_ipa, simplified, traditional,
                     method, source, source_line, normalized_file, normalized_line,
                     review) in source_db.execute(query, (language, *regions)):
                    scanned[region] += 1
                    derived = (method or '').startswith('derived')
                    if kind not in ('word', 'character'):
                        rejected['nonlexical_kind'] += 1
                        continue
                    if derived and not include_derived:
                        rejected['derived'] += 1
                        continue
                    if not derived and (review not in DIRECT_REVIEWS or not method
                        or not method.startswith(('source_', 'chao_digits_to_ipa_tone_letters'))):
                        rejected['unverified_provenance'] += 1
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
                    canonical = normalize_word((simplified or lookup or word) if language == 'zh'
                                               else (lookup or word), language)
                    provenance = json.dumps([int(derived), source or '', method or '',
                                             normalized_file or '', normalized_line or 0],
                                            ensure_ascii=False, separators=(',', ':'))
                    batch.append((region, ipa, text, language, word, lookup, simplified,
                                  traditional, canonical, source, method, source_line,
                                  normalized_file, normalized_line, provenance))
                    if len(batch) >= 5000:
                        scratch.executemany(insertion, batch)
                        batch.clear()
                if batch:
                    scratch.executemany(insertion, batch)
                scratch.commit()
                # Only retain frequency evidence for actual usable written forms.
                needed = set()
                for forms in scratch.execute('SELECT word,lookup_word,simplified,traditional,text '
                                             'FROM candidates WHERE language=?', (language,)):
                    needed.update(normalize_word(w, language) for w in forms if w)
                scores = {}
                query = '''SELECT lookup_word,region,zipf,per_million,source
                    FROM frequencies WHERE language=? AND kind='word'
                    AND region IN (''' + ','.join('?' for _ in regions) + ", 'mixed')"
                print(f'scan written-word frequencies: {language} ({len(needed)} usable forms)', flush=True)
                for word, region, zipf, per_million, source in source_db.execute(query, (language, *regions)):
                    key = normalize_word(word, language)
                    if key not in needed:
                        continue
                    score = frequency_score(zipf, per_million)
                    if score > 0:
                        frequency_sources.add(source)
                        score_key = (region, key)
                        scores[score_key] = max(scores.get(score_key, 0.0), score)
                batch = []
                for id_, region, *forms in scratch.execute('SELECT id,region,word,lookup_word,simplified,traditional,text '
                                                         'FROM candidates WHERE language=?', (language,)):
                    score = max((scores.get((r, normalize_word(w, language)), 0.0)
                                 for w in forms if w for r in (region, 'mixed')), default=0.0)
                    batch.append((score, id_))
                    if len(batch) >= 5000:
                        scratch.executemany('UPDATE candidates SET frequency=? WHERE id=?', batch)
                        batch.clear()
                if batch:
                    scratch.executemany('UPDATE candidates SET frequency=? WHERE id=?', batch)
                scratch.commit()
                scores.clear()
                needed.clear()
                scratch.row_factory = sqlite3.Row
                for region in regions:
                    available[region] = scratch.execute('SELECT COUNT(*) FROM candidates '
                        'WHERE region=? AND frequency>=?', (region, min_frequency)).fetchone()[0]
                    for record in scratch.execute('SELECT * FROM candidates WHERE region=? AND frequency>=? '
                            'ORDER BY frequency DESC,text,ipa,source,method LIMIT ?',
                            (region, min_frequency, max_per_region)):
                        row = {key: record[key] for key in ('ipa', 'text', 'region', 'language',
                            'word', 'canonical_word', 'source', 'method', 'source_line',
                            'normalized_file', 'normalized_line')}
                        row['frequency'] = round(record['frequency'], 6)
                        row['weight'] = round(max(0.5, min(2.0, 0.5 + record['frequency'] / 4)), 6)
                        selected.append(row)
                scratch.row_factory = None
            scratch.close()
        groups = assign_groups(selected, seed, group_homophones)
        selected.sort(key=lambda r: (REGIONS.index(r['region']), -r['frequency'],
                                     r['text'], r['ipa'], r['source'], r['method']))
        digest = hashlib.sha256()
        corpus_path = output_dir / 'corpus.jsonl'
        with corpus_path.with_suffix('.jsonl.part').open('wb') as target:
            for row in selected:
                line = (json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n').encode()
                target.write(line)
                digest.update(line)
        corpus_path.with_suffix('.jsonl.part').replace(corpus_path)
        counts = {region: dict(collections.Counter(row['split'] for row in selected if row['region'] == region))
                  for region in REGIONS}
        used_sources = {row['source'] for row in selected} | frequency_sources
        sources = [dict(zip(('id', 'url', 'path', 'license', 'note'), row))
                   for row in source_db.execute('SELECT id,url,path,license,note FROM sources ORDER BY id')
                   if row[0] in used_sources]
        stats = database.stat()
        metadata = {
            'format': 'ipa-model-training-corpus-v1', 'seed': seed,
            'database': {'path': str(database), 'size_bytes': stats.st_size,
                         'mtime_ns': stats.st_mtime_ns, 'access': 'read-only'},
            'corpus_sha256': digest.hexdigest(), 'rows': len(selected),
            'parameters': {'max_per_region': max_per_region, 'min_frequency_zipf': min_frequency,
                           'include_derived': include_derived, 'group_homophones': group_homophones,
                           'max_input_utf16_units': 64, 'max_output_unicode_scalars': 31},
            'scanned_by_region': dict(scanned), 'usable_deduplicated_by_region': available,
            'counts_by_region_and_split': counts, 'rejected': dict(rejected),
            'split_groups': groups,
            'split_method': 'SHA-256(seed:group_id), 90/5/5 nominal train/valid/test; canonical language+written-word '
                            'groups share all pronunciations, regional variants, source duplicates and simplified/traditional forms.'
                            + (' Same-language exact IPA homophones are also joined into connected components.'
                               if group_homophones else ' Homophones are not joined; evaluation must report seen/unseen IPA separately.'),
            'sampling': 'Highest maximum written-word Zipf evidence per region; ties text, IPA, source, method. '
                        'Region and mixed observations may contribute; character/symbol frequencies and raw-only counts excluded. '
                        'Independent corpora are never added. Frequency does not measure a pronunciation reading.',
            'weight': 'clip(0.5 + max_written_word_zipf / 4, 0.5, 2.0)',
            'provenance': 'Representative direct source preferred when region/IPA/output duplicates exist. '
                          'This metadata and corpus are training-only; inference assets contain neither source rows nor dictionary.',
            'sources': sources,
            'frequency_sources_considered': sorted(s for s in frequency_sources if s),
        }
        (output_dir / 'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'rows': len(selected), 'counts': counts, 'usable': available}, ensure_ascii=False), flush=True)
        return metadata
    finally:
        source_db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=ROOT / 'data/ipa.sqlite')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'data/model')
    parser.add_argument('--max-per-region', type=int, default=12000)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--min-frequency', type=float, default=0.0,
                        help='Minimum comparable written-word Zipf evidence, not raw corpus count')
    parser.add_argument('--include-derived', action='store_true')
    parser.add_argument('--group-homophones', action='store_true')
    args = parser.parse_args()
    prepare_corpus(**vars(args))


if __name__ == '__main__':
    main()
