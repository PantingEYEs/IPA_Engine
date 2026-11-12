#!/usr/bin/env python3
# Copyright 2026 PantingEYEs. All rights reserved.
"""Fetch the exact official commit, then verify all selected source bytes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def verify_source(root=ROOT):
    lock = json.loads((root / 'sources.lock.json').read_text())
    source = root / '.cache/HeliBoard'
    actual = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != lock['upstream']['commit']:
        raise ValueError('HeliBoard source commit differs from lock')
    expected = lock['upstream']['files']
    for name, record in expected.items():
        path = source / name
        if not path.is_file() or path.is_symlink() or digest(path) != record['sha256'] or path.stat().st_size != record['sizeBytes']:
            raise ValueError(f'HeliBoard source mismatch: {name}')
    # Unknown files could shadow quoted includes; require exactly the locked native tree.
    native = source / 'app/src/main/jni/src'
    current = {p.relative_to(source).as_posix() for p in native.rglob('*') if p.is_file()}
    declared = {p for p in expected if p.startswith('app/src/main/jni/src/')}
    if current != declared:
        raise ValueError('Unexpected or missing native source files')
    return lock, source

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    lock = json.loads((ROOT / 'sources.lock.json').read_text())
    source = ROOT / '.cache/HeliBoard'
    if not (source / '.git').is_dir():
        if args.offline:
            raise SystemExit('No cached source checkout; run bootstrap once with network')
        source.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git', 'init', '-q', str(source)], check=True)
        subprocess.run(['git', '-C', str(source), 'remote', 'add', 'origin', lock['upstream']['repository']], check=True)
        subprocess.run(['git', '-C', str(source), 'fetch', '--depth', '1', 'origin', lock['upstream']['commit']], check=True)
        subprocess.run(['git', '-C', str(source), 'checkout', '--detach', 'FETCH_HEAD'], check=True)
    lock, _ = verify_source()
    print(f"Verified {len(lock['upstream']['files'])} pinned upstream files at {lock['upstream']['commit']}")

if __name__ == '__main__':
    main()
