"""Check that a release subtree export is safe to push to the fixed public branch."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.sync_public_sources import permitted_source

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_BRANCH = 'codex/remote-v0.0.1-dev'
REMOTE_REF = 'refs/remotes/public/codex/ipa-engine-v0.0.1-dev'
ROOT_FILES = {'README.md', 'VERSION', 'CONTRIBUTING.md', 'board-update.json'}
BATCH_FILES = {
    'README.md', 'SHA256SUMS', 'THIRD_PARTY_NOTICES.json', 'client.json',
    'classes.dex', 'libipa_graph_android.so', 'lexicon.ipad',
    'lexicon.manifest.json', 'release.json', 'build.json',
}


def git(*args):
    return subprocess.check_output(['git', '-c', 'core.quotepath=false', *args], cwd=ROOT, text=True).strip()


def permitted(path):
    if path.startswith('source/'):
        return permitted_source(path[len('source/'):])
    if path in ROOT_FILES or path == 'pre-release/README.md':
        return True
    parts = path.split('/')
    if len(parts) != 3 or parts[0] != 'pre-release':
        return False
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', parts[1]) or parts[1] in ('.', '..'):
        return False
    return parts[2] in BATCH_FILES or bool(re.fullmatch(
        r'ipa-engine-v\d+\.\d+\.\d+-dev-android-arm64-v8a\.aar', parts[2]))


def verify(branch=PUBLIC_BRANCH, remote_ref=REMOTE_REF, expected_main=None):
    if not branch.startswith('codex/'):
        raise ValueError('export must use a codex development branch')
    if git('status', '--porcelain'):
        raise ValueError('commit local changes before verifying the export')
    public_commit = git('rev-parse', '--verify', f'{branch}^{{commit}}')
    remote_commit = git('rev-parse', '--verify', f'{remote_ref}^{{commit}}')
    if git('rev-parse', f'{branch}^{{tree}}') != git('rev-parse', 'HEAD:remote'):
        raise ValueError('export tree differs from the local remote/ directory')
    result = subprocess.run(['git', 'merge-base', '--is-ancestor', remote_commit,
                             public_commit], cwd=ROOT, capture_output=True)
    if result.returncode:
        raise ValueError('export does not retain the fetched public history; integrate that history and re-export before pushing')
    commits = git('rev-list', public_commit).splitlines()
    for commit in commits:
        for entry in git('ls-tree', '-r', commit).splitlines():
            description, path = entry.split('\t', 1)
            mode, kind, _ = description.split()
            if kind != 'blob' or mode not in ('100644', '100755') or not permitted(path):
                raise ValueError(f'non-release file in public history: {commit}: {path}')
    main = git('rev-parse', '--verify', 'main')
    if expected_main and main != expected_main:
        raise ValueError('local main changed unexpectedly')
    return {
        'status': 'passed', 'public_commit': public_commit,
        'fetched_public_commit': remote_commit, 'public_commits_checked': len(commits),
        'tree_matches_remote_directory': True, 'retains_public_history': True,
        'merge_parents': git('show', '-s', '--format=%P', public_commit).split(),
        'local_main': main, 'working_tree_clean': True,
        'note': 'Local check against the fetched public ref; fetch first, then verify the remote SHA after push.',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--branch', default=PUBLIC_BRANCH)
    parser.add_argument('--remote-ref', default=REMOTE_REF)
    parser.add_argument('--expected-main')
    args = parser.parse_args()
    try:
        result = verify(args.branch, args.remote_ref, args.expected_main)
    except (ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Public export verification failed: {error}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
