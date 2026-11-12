#!/usr/bin/env python3
# Copyright (c) 2026 PantingEYEs. All rights reserved.
# No project license has been selected. Third-party inputs retain their licenses.
from pathlib import Path
import hashlib
import json
import os
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.work'
LOCK = json.loads((ROOT / 'sources.lock.json').read_text())

def git(*args):
    return subprocess.check_output(['git', *map(str, args)], text=True).strip()

def prepare_git(target, origin, revision):
    if not (target / '.git').exists():
        target.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git', 'init', str(target)], check=True, stdout=subprocess.DEVNULL)
        git('-C', target, 'remote', 'add', 'origin', origin)
        git('-C', target, 'fetch', '--depth', '1', 'origin', revision)
        git('-C', target, 'checkout', '--detach', revision)
    if git('-C', target, 'rev-parse', 'HEAD') != revision:
        raise SystemExit('Wrong source revision: ' + str(target))
    if git('-C', target, 'diff', '--name-only', 'HEAD', '--'):
        raise SystemExit('Modified official source: ' + str(target))
    if git('-C', target, 'ls-files', '--others'):
        raise SystemExit('Unexpected untracked source: ' + str(target))

def prepare_boost():
    spec = LOCK['dependencies']['boost']
    cache = ROOT / '.cache' / 'boost_1_89_0.tar.bz2'
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        temporary = cache.with_suffix('.download')
        urllib.request.urlretrieve(spec['url'], temporary)
        temporary.replace(cache)
    if hashlib.file_digest(cache.open('rb'), 'sha256').hexdigest() != spec['sha256']:
        raise SystemExit('Boost archive digest mismatch')
    destination = Path(os.environ.get('RIME_BOOST_DIR', str(WORK / 'boost')))
    expected = set()
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(cache, 'r:bz2') as archive:
        prefix = 'boost_1_89_0/'
        for member in archive:
            if not member.isfile() or not member.name.startswith(prefix):
                continue
            relative = member.name[len(prefix):]
            if not (relative.startswith('boost/') or relative == 'LICENSE_1_0.txt'):
                continue
            target = (destination / relative).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise SystemExit('Unsafe archive path')
            data = archive.extractfile(member).read()
            expected.add(target)
            if target.exists():
                if target.read_bytes() != data:
                    raise SystemExit('Modified Boost header: ' + relative)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
    if {p.resolve() for p in destination.rglob('*') if p.is_file()} != expected:
        raise SystemExit('Unexpected Boost source files')

def main():
    WORK.mkdir(parents=True, exist_ok=True)
    prepare_git(WORK / 'librime', LOCK['upstream']['repository'], LOCK['upstream']['commit'])
    for name in ['leveldb', 'yaml-cpp']:
        spec = LOCK['dependencies'][name]
        prepare_git(WORK / 'librime' / 'deps' / name, spec['repository'], spec['commit'])
    prepare_boost()
    print('Pinned official librime, LevelDB, yaml-cpp and Boost inputs verified.')

if __name__ == '__main__':
    main()
