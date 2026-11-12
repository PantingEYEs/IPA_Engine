#!/usr/bin/env python3
# Copyright 2026 PantingEYEs. All rights reserved.
"""Check published LatinIME provenance/notices without network or private build caches."""
from pathlib import Path
import argparse
import gzip
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def check(data, record, label):
    if len(data) != record['sizeBytes'] or digest(data) != record['sha256']:
        raise ValueError('Source or notice mismatch: ' + label)


def read(root, name):
    return json.loads((root / name).read_bytes())


def verify(root=ROOT, source_tree=None, ndk=None):
    root = Path(root)
    publication = read(root, 'source-inventory/published-files.json')
    expected = set(publication['files'])
    actual = {'dictionary/main_en-US.dict', 'dictionary-sources.lock.json'}
    actual.update(p.relative_to(root).as_posix() for folder in ('licenses', 'source-inventory')
                  for p in (root / folder).rglob('*') if p.is_file()
                  and '__pycache__' not in p.parts
                  and p.relative_to(root).as_posix() != 'source-inventory/published-files.json')
    if actual != expected:
        raise ValueError('Published source/notice inventory changed')
    for name, row in publication['files'].items():
        if name.startswith('/') or '\\' in name or '..' in Path(name).parts:
            raise ValueError('Unsafe published source path')
        check((root / name).read_bytes(), row, name)
    sources = read(root, 'sources.lock.json')
    inventory = read(root, 'source-inventory/native-files.json')
    if inventory['commit'] != sources['upstream']['commit']:
        raise ValueError('Native inventory source revision mismatch')
    selected = inventory['files']
    paths = [row['path'] for row in selected]
    if len(paths) != len(set(paths)):
        raise ValueError('Duplicate selected native source')
    prefix = 'app/src/main/jni/src/'
    compilation_units = {prefix + p for p in sources['upstream']['coreSources']}
    actual_units = {r['path'] for r in selected if r['role'] == 'compiled-core-unit'}
    if actual_units != compilation_units:
        raise ValueError('Native compilation selection differs from reviewed source inventory')
    for row in selected:
        pinned = sources['upstream']['files'][row['path']]
        if any(row[k] != pinned[k] for k in ('sha256', 'sizeBytes')):
            raise ValueError('Native inventory hash differs from build input: ' + row['path'])
        if row['declaredLicense'] != 'Apache-2.0' or 'Licensed under the Apache License, Version 2.0' not in row['noticeHeader']:
            raise ValueError('Selected source lacks reviewed Apache notice: ' + row['path'])
    bridge = read(root, 'source-inventory/bridge-api-review.json')
    for row in bridge['projectSources']:
        check((root / row['path']).read_bytes(), row, row['path'])
    native_by_path = {row['path']: row for row in selected}
    for row in bridge['nativeApiReferences']:
        if row['sha256'] != native_by_path[row['path']]['sha256']:
            raise ValueError('Bridge API reference differs from reviewed native source: ' + row['path'])
    if source_tree is not None:
        source_tree = Path(source_tree)
        for row in selected + inventory['referenceOnly'] + inventory['excludedTests']:
            data = (source_tree / row['path']).read_bytes()
            check(data, row, row['path'])
            if not data.startswith(row['noticeHeader'].encode('utf-8')):
                raise ValueError('Recorded notice is not the source file header: ' + row['path'])

    notices = read(root, 'licenses/sources.json')
    for row in notices['notices'] + notices['additionalEvidence']:
        check((root / row['path']).read_bytes(), row, row['path'])
    if ndk is not None:
        ndk = Path(ndk)
        check((ndk / 'source.properties').read_bytes(), notices['toolchainProperties'], 'NDK source.properties')
        for row in notices['notices']:
            if row.get('source', '').startswith('Android NDK'):
                check((ndk / row['sourcePath']).read_bytes(), row, 'NDK ' + row['sourcePath'])

    dictionary = read(root, 'dictionary-sources.lock.json')
    row = dictionary['artifact']
    data = (root / row['path']).read_bytes()
    check(data, row, row['path'])
    check(data, sources['dictionary'], 'build source dictionary')
    binary_format = int.from_bytes(data[4:6], 'big')
    header_size = int.from_bytes(data[8:12], 'big')
    if int.from_bytes(data[:4], 'big') != 0x9bc13afe or binary_format != 202 or header_size != 99:
        raise ValueError('Existing dictionary format changed')
    attributes = data[12:header_size].split(b'\x1f')
    if attributes[-1] or len(attributes) % 2 != 1:
        raise ValueError('Malformed dictionary header')
    attributes = {attributes[i].decode('ascii'): attributes[i + 1].decode('ascii')
                  for i in range(0, len(attributes) - 1, 2)}
    if attributes != dictionary['format']['attributes']:
        raise ValueError('Recorded dictionary metadata mismatch')
    if dictionary['format']['binaryFormat'] != binary_format or attributes['version'] != '54':
        raise ValueError('Dictionary version must not be confused with binary format')
    wordlist = dictionary['correspondingWordlist']
    compressed = (root / wordlist['path']).read_bytes()
    check(compressed, wordlist, wordlist['path'])
    unpacked = gzip.decompress(compressed)
    check(unpacked, wordlist['uncompressed'], 'Original corresponding wordlist')
    if unpacked.splitlines()[0].decode() != wordlist['header'] or wordlist['rebuiltFromThisWordlist']:
        raise ValueError('Corresponding-source evidence or prebuilt dictionary policy changed')
    if dictionary['policy']['recompile'] or dictionary['policy']['modify']:
        raise ValueError('Dictionary was requested to remain prebuilt and unchanged')
    return {'verified': True, 'nativeSourceFiles': len(selected), 'compiledUnits': len(compilation_units),
            'nativeSourceTreeChecked': source_tree is not None, 'ndkOriginalNoticesChecked': ndk is not None,
            'dictionarySha256': row['sha256'], 'dictionaryBinaryFormat': binary_format,
            'dictionaryVersion': attributes['version'],
            'dictionaryFileLicense': dictionary['licenseEvidence']['fileLevelLicense'],
            'projectLicenseMigrationClaimed': False, 'androidRuntimeExecuted': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-tree', type=Path)
    parser.add_argument('--ndk', type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(source_tree=args.source_tree, ndk=args.ndk), indent=2))
    except (ValueError, OSError, KeyError, UnicodeError) as error:
        sys.exit('LatinIME provenance verification failed: ' + str(error))
