"""Publish an AAR with one command: client batch, cleanup, pinned manifest, remote subtree."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import package_client_release as client
from scripts.package_pre_release import AAR_CONTENTS
from scripts.verify_public_export import permitted
from scripts.sync_public_sources import sync_sources

ROOT = Path(__file__).resolve().parents[1]
EXPORT = 'codex/remote-v0.0.1-dev'
TARGET = 'refs/heads/' + client.BRANCH
TRACKING = 'refs/remotes/public/' + client.BRANCH


def system_proxy():
    if sys.platform != 'darwin':
        return None
    result = subprocess.run(['scutil', '--proxy'], capture_output=True, text=True)
    if result.returncode:
        return None
    values = dict(re.findall(r'^\s*(\w+)\s*:\s*(\S+)\s*$', result.stdout, re.M))
    if values.get('HTTPSEnable') == '1':
        host, port = values.get('HTTPSProxy'), values.get('HTTPSPort')
        if host and port and port.isdigit():
            return f'http://{host}:{port}'
    return None


def build_bundle(destination, aar, version, batch, dictionary_version, sdk, d8, sources=None):
    """Build once in a disposable tree, before changing any published working files."""
    source_dir = destination / 'pre-release' / version
    source_dir.mkdir(parents=True)
    filename = f'ipa-engine-{version}-android-arm64-v8a.aar'
    source = source_dir / filename
    shutil.copyfile(aar, source)
    with zipfile.ZipFile(source) as archive:
        if set(archive.namelist()) != AAR_CONTENTS:
            raise ValueError('AAR must contain only the compiled engine and dictionary assets')
        metadata = client.load_json(archive.read('assets/ipa/lexicon.manifest.json'))
        dictionary = archive.read('assets/ipa/lexicon.ipad')
        if metadata.get('complete') is not True:
            raise ValueError('release requires a complete compiled dictionary')
        client.validate_dictionary(dictionary, metadata['ipa_keys'])
        if len(dictionary) != metadata['bytes'] or client.digest(dictionary) != metadata['sha256']:
            raise ValueError('AAR dictionary disagrees with its compilation manifest')
    build_report = aar.parent / 'build-report.json'
    provenance = client.load_json(build_report.read_bytes()) if build_report.is_file() else {}
    if provenance.get('implementation') == 'rust':
        if provenance.get('aar_sha256') != client.digest(source.read_bytes()):
            raise ValueError('Rust build report does not describe the supplied AAR')
        if not sources or provenance.get('rust_source_sha256') != sources['runtimeSourceSha256']:
            raise ValueError('AAR Rust source digest differs from the public source snapshot; rebuild first')
    client.save_json(source_dir / 'release.json', {
        'version': version, 'artifact': filename, 'artifact_bytes': source.stat().st_size,
        'artifact_sha256': client.digest(source.read_bytes()), 'min_sdk': 26,
        'abi': 'arm64-v8a', 'entry_point': client.ENTRY, 'includes_sources': False,
        'dictionary': {'bytes': len(dictionary), 'sha256': metadata['sha256'],
                       'ipa_keys': metadata['ipa_keys'], 'format_version': 2, 'index_bits': 24},
        'note': 'Packaging checks only; runtime/device validation is maintained separately.',
        'source': ({'path': 'source/', 'sha256': sources['sourceSha256'],
                    'runtime_sha256': sources['runtimeSourceSha256']} if sources else None),
        'implementation': provenance.get('implementation', 'unrecorded'),
        'validation': {
            'aarch64_elf_verified': provenance.get('aarch64_elf_verified') is True,
            'jni_exports_verified': provenance.get('jni_exports_verified') is True,
            'android_device_execution': provenance.get('aar_wrapper_device_verified') is True,
        },
    })
    client.save_json(source_dir / 'THIRD_PARTY_NOTICES.json', {
        'version': version, 'sources': metadata['sources'],
        'scope': 'Source-specific notices; no blanket data license is assigned.',
    })
    (source_dir / 'SHA256SUMS').write_text(''.join(
        f'{client.digest(p.read_bytes())}  {p.name}\n' for p in sorted(source_dir.iterdir())), encoding='utf-8')
    return client.prepare(destination, source, batch, version, dictionary_version, sdk, d8)


class Publisher:
    def __init__(self, args, root=ROOT):
        self.args, self.root = args, Path(root).resolve()
        self.public = self.root / 'remote'
        self.proxy = args.git_proxy
        if self.proxy is None and not self.git('config', '--get', 'http.proxy', optional=True):
            self.proxy = system_proxy()
        state_path = Path(self.git('rev-parse', '--git-path', 'ipa-publish.json'))
        self.state_path = state_path if state_path.is_absolute() else self.root / state_path

    def git(self, *args, optional=False):
        command = ['git', '-C', str(self.root), '-c', 'core.quotepath=false']
        if getattr(self, 'proxy', None) is not None:
            command += ['-c', 'http.proxy=' + self.proxy]
        result = subprocess.run([*command, *args], capture_output=True, text=True)
        if result.returncode and not optional:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'Git command failed')
        return result.stdout.rstrip('\n')

    def save(self, state):
        temporary = self.state_path.with_suffix('.tmp')
        client.save_json(temporary, state)
        temporary.replace(self.state_path)

    def split(self):
        self.git('subtree', 'split', '--prefix=remote', '--rejoin', '--branch=' + EXPORT)
        return self.git('rev-parse', EXPORT)

    def is_ancestor(self, ancestor, descendant):
        result = subprocess.run(['git', '-C', str(self.root), 'merge-base', '--is-ancestor',
                                 ancestor, descendant], capture_output=True, text=True)
        if result.returncode not in (0, 1):
            raise RuntimeError(result.stderr.strip())
        return result.returncode == 0

    def audit(self):
        if self.git('rev-parse', EXPORT + '^{tree}') != self.git('rev-parse', 'HEAD:remote'):
            raise ValueError('public export differs from remote/')
        self.git('merge-base', '--is-ancestor', TRACKING, EXPORT)
        for commit in self.git('rev-list', EXPORT).splitlines():
            for entry in self.git('ls-tree', '-r', commit).splitlines():
                info, path = entry.split('\t', 1)
                mode, kind, _ = info.split()
                if kind != 'blob' or mode not in ('100644', '100755') or not permitted(path):
                    raise ValueError('non-release file in public history: ' + path)

    def push(self, commit):
        if self.git('rev-parse', EXPORT) != commit:
            raise ValueError('export branch changed during publication; inspect the pending release')
        self.audit()
        self.git('push', '--quiet', self.args.remote, 'refs/heads/' + EXPORT + ':' + TARGET)
        actual = self.git('ls-remote', self.args.remote, TARGET).split()
        if not actual or actual[0] != commit:
            raise ValueError('remote publication SHA does not match the export')

    def commit_remote(self, message):
        self.git('add', '-A', '--', 'remote')
        staged = [path for path in self.git('diff', '--cached', '--name-only', '-z').split('\0') if path]
        if any(not path.startswith('remote/') for path in staged):
            raise ValueError('unrelated source files are staged; publisher commits only remote/')
        if staged:
            self.git('commit', '-m', message)

    def run(self):
        args = self.args
        version = args.version
        if not re.fullmatch(r'v\d+\.\d+\.\d+-dev', version):
            raise ValueError('version must have the form v0.0.3-dev')
        batch = args.batch or version + '-client.1'
        client.batch_path(self.public, batch)
        if batch == version:
            raise ValueError('client batch and AAR directory must be different')
        branch = self.git('branch', '--show-current')
        if not branch.startswith('codex/') or branch == EXPORT:
            raise ValueError('run from the full project on a codex development branch')
        if not args.message.strip():
            raise ValueError('commit message cannot be empty')
        main = self.git('rev-parse', 'main')
        aar = args.aar.resolve()
        state = json.loads(self.state_path.read_text()) if self.state_path.exists() else None
        resuming = state is not None
        identity = {'version': version, 'batch': batch, 'message': args.message,
                    'branch': branch, 'main': main, 'remote': args.remote}
        if state:
            if any(state.get(key) != value for key, value in identity.items()):
                raise ValueError('another release is pending; rerun its original command first')
            if not aar.exists():
                aar = self.public / state['aar_path']
            if client.digest(aar.read_bytes()) != state['aar_sha256']:
                raise ValueError('pending release AAR changed; restore it before retrying')
            for line in self.git('status', '--porcelain').splitlines():
                if not line[3:].startswith('remote/'):
                    raise ValueError('commit unrelated source changes before resuming the release')
        else:
            if self.git('status', '--porcelain'):
                raise ValueError('commit source/document changes first; the publisher commits only remote/')
            print('Synchronizing public branch...', flush=True)
            self.git('fetch', '--quiet', args.remote, TARGET + ':' + TRACKING)
            self.split()
            if not self.is_ancestor(TRACKING, EXPORT):
                try:
                    self.git('subtree', 'merge', '--prefix=remote', '-m', 'Merge public updates without fast-forward', TRACKING)
                except RuntimeError:
                    self.git('merge', '--abort', optional=True)
                    raise ValueError('remote changes conflict with the project; resolve them before publishing')
                self.split()
            with zipfile.ZipFile(aar) as archive:
                metadata = client.load_json(archive.read('assets/ipa/lexicon.manifest.json'))
            old = client.load_json((self.public / 'board-update.json').read_bytes())
            dictionary_version = args.dictionary_version or (
                old['dictionaryVersion'] if old['dictionarySha256'] == metadata['sha256'] else version + '-lexicon')
            state = {**identity, 'aar_sha256': client.digest(aar.read_bytes()),
                     'aar_path': f'pre-release/{version}/ipa-engine-{version}-android-arm64-v8a.aar',
                     'dictionary_version': dictionary_version, 'phase': 'prepare'}
        if state['phase'] == 'prepare':
            print('Building client batch and removing old artifacts...', flush=True)
            with tempfile.TemporaryDirectory(prefix='ipa-publish-') as temporary:
                stage = Path(temporary)
                sources = sync_sources(self.root, stage)
                build_bundle(stage, aar, version, batch, state['dictionary_version'], args.sdk, args.d8, sources)
                existing = client.batch_path(self.public, batch)
                generated = client.batch_path(stage, batch)
                if not resuming and existing.exists() and (
                        {p.name for p in existing.iterdir()} != {p.name for p in generated.iterdir()}
                        or any((existing / p.name).read_bytes() != p.read_bytes() for p in generated.iterdir())):
                    raise ValueError('existing client batch has different contents; use --batch with client.2')
                self.save(state)
                releases = self.public / 'pre-release'
                for path in list(releases.iterdir()):
                    if path.is_dir() and not path.name.startswith('.'):
                        shutil.rmtree(path)
                for path in (stage / 'pre-release').iterdir():
                    shutil.copytree(path, releases / path.name)
                sources = self.public / 'source'
                if sources.exists():
                    shutil.rmtree(sources)
                shutil.copytree(stage / 'source', sources)
            (self.public / 'VERSION').write_text(version + '\n', encoding='utf-8')
            (self.public / 'pre-release/README.md').write_text(
                f'# Current release: {version}\n\n'
                f'- [Client binaries]({batch}/README.md): DEX, matching ARM64 JNI, raw 24-bit dictionary.\n'
                f'- [AAR]({version}/ipa-engine-{version}-android-arm64-v8a.aar) for Gradle integration.\n'
                '- [Update entry](../board-update.json) is the client discovery endpoint.\n\n'
                'Only current artifacts remain in this tree. Previous releases are recoverable from Git history '
                'and remain downloadable using their pinned commit.\n', encoding='utf-8')
            client.verify(self.public, batch)
            self.commit_remote(args.message + ' [artifacts]')
            state['artifact_commit'] = self.split()
            state['phase'] = 'artifacts'
            self.save(state)
        if state['phase'] == 'artifacts':
            print('Publishing artifacts...', flush=True)
            self.push(state['artifact_commit'])
            state['phase'] = 'entry'
            self.save(state)
        if state['phase'] == 'entry':
            client.publish_manifest(self.public, batch, state['artifact_commit'], args.remote,
                                    root=self.root, git_proxy=self.proxy)
            self.commit_remote(args.message)
            state['manifest_commit'] = self.split()
            state['phase'] = 'manifest'
            self.save(state)
        print('Publishing update entry...', flush=True)
        self.push(state['manifest_commit'])
        if self.git('rev-parse', 'main') != main:
            raise ValueError('main changed unexpectedly')
        self.state_path.unlink()
        print(f"Published {version}: {state['manifest_commit']} ({client.BRANCH})", flush=True)
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--aar', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--message', required=True)
    parser.add_argument('--batch', help='default: VERSION-client.1')
    parser.add_argument('--dictionary-version', help='unchanged dictionary retains its current version')
    parser.add_argument('--remote', default='public')
    parser.add_argument('--sdk', type=Path, default=Path(os.environ.get('ANDROID_SDK_ROOT') or os.environ.get('ANDROID_HOME') or Path.home() / 'Library/Android/sdk'))
    parser.add_argument('--d8', type=Path)
    parser.add_argument('--git-proxy', help='default: existing Git proxy or macOS system HTTPS proxy')
    args = parser.parse_args()
    try:
        Publisher(args).run()
    except (ValueError, RuntimeError, OSError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        parser.exit(1, f'Publication stopped: {error}\nRerun the same command to resume after fixing the error.\n')


if __name__ == '__main__':
    main()
