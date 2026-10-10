"""Prepare immutable IPA Board binaries, then advertise an already pushed commit.

No Git state is changed by this tool. The publication boundary is deliberate:
prepare -> commit/export/push artifacts -> publish-manifest -> commit/export/push.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / 'remote'
BRANCH = 'codex/ipa-engine-v0.0.1-dev'
ENTRY = 'com.ipaengine.graph.GraphEngine'
MAX_JSON = 1_048_576
FILES = {'dex': ('classes.dex', 4_000_000),
         'native': ('libipa_graph_android.so', 8_000_000),
         'dictionary': ('lexicon.ipad', 40_000_000)}
NATIVE_METHODS = ('nativeOpen', 'nativeConfigure', 'nativeAcceptsInput',
                  'nativeSegmentPattern', 'nativeQuery', 'nativeClose')
HASH = re.compile(r'[0-9a-f]{64}')
COMMIT = re.compile(r'[0-9a-f]{40}')
PATH = re.compile(r'[A-Za-z0-9_.\-/]+')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def load_json(data):
    if len(data) > MAX_JSON:
        raise ValueError('JSON exceeds the client limit')
    result = json.loads(data.decode('utf-8'))
    if not isinstance(result, dict):
        raise ValueError('JSON must be an object')
    return result


def safe_path(value):
    if (not isinstance(value, str) or not PATH.fullmatch(value)
            or value.startswith('/') or any(p in ('', '.', '..') for p in value.split('/'))):
        raise ValueError('unsafe repository-relative artifact path')
    return value


def integer(value, lower, upper, name):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f'invalid {name}: expected integer {lower}..{upper}')


def validate_manifest(manifest, require_commit=False):
    for key, expected in {'schemaVersion': 1, 'entryPoint': ENTRY,
                          'abi': 'arm64-v8a', 'dictionaryFormat': 2}.items():
        if type(manifest.get(key)) is not type(expected) or manifest[key] != expected:
            raise ValueError(f'invalid {key}')
    integer(manifest.get('minSdk'), 26, 36, 'minSdk')
    integer(manifest.get('dictionaryKeys'), 1, 2_000_000, 'dictionaryKeys')
    for field in ('engineVersion', 'dictionaryVersion'):
        if not isinstance(manifest.get(field), str) or not 1 <= len(manifest[field]) <= 100:
            raise ValueError(f'invalid {field}')
    for prefix, (_, limit) in FILES.items():
        safe_path(manifest.get(prefix + 'Path'))
        integer(manifest.get(prefix + 'Bytes'), 1, limit, prefix + 'Bytes')
        value = manifest.get(prefix + 'Sha256')
        if not isinstance(value, str) or not HASH.fullmatch(value):
            raise ValueError(f'invalid {prefix}Sha256')
    if require_commit or 'artifactCommit' in manifest:
        value = manifest.get('artifactCommit')
        if not isinstance(value, str) or not COMMIT.fullmatch(value):
            raise ValueError('artifactCommit must be a full lowercase SHA-1')


def validate_dex(data):
    if (len(data) < 112 or not re.fullmatch(b'dex\n0[0-9]{2}\x00', data[:8])
            or struct.unpack_from('<I', data, 32)[0] != len(data)
            or struct.unpack_from('<I', data, 36)[0] != 112
            or struct.unpack_from('<I', data, 40)[0] != 0x12345678
            or data[12:32] != hashlib.sha1(data[32:]).digest()):
        raise ValueError('invalid standalone DEX header/signature')
    import zlib
    if struct.unpack_from('<I', data, 8)[0] != zlib.adler32(data[12:]):
        raise ValueError('invalid DEX checksum')
    if b'Lcom/ipaengine/graph/GraphEngine;\x00' not in data:
        raise ValueError('DEX does not contain the GraphEngine entry point')
    if any(name.encode() + b'\x00' not in data for name in NATIVE_METHODS):
        raise ValueError('DEX does not contain all expected JNI methods')


def validate_native(data):
    if (len(data) < 64 or data[:6] != b'\x7fELF\x02\x01'
            or struct.unpack_from('<H', data, 16)[0] != 3
            or struct.unpack_from('<H', data, 18)[0] != 183):
        raise ValueError('expected an ARM64 little-endian ELF shared library')
    # Check actual defined dynamic exports rather than arbitrary bytes in the file.
    table = struct.unpack_from('<Q', data, 40)[0]
    stride, count = struct.unpack_from('<HH', data, 58)
    if stride != 64 or table + count * stride > len(data):
        raise ValueError('invalid ELF section table')
    sections = [struct.unpack_from('<IIQQQQIIQQ', data, table + i * stride) for i in range(count)]
    symbols = set()
    for section in sections:
        if section[1] != 11:  # SHT_DYNSYM
            continue
        offset, size, link, item_size = section[4], section[5], section[6], section[9]
        if link >= count or item_size != 24 or offset + size > len(data):
            raise ValueError('invalid ELF dynamic symbols')
        strings = sections[link]
        pool = data[strings[4]:strings[4] + strings[5]]
        for at in range(offset, offset + size, item_size):
            name, info, _, defined, _, _ = struct.unpack_from('<IBBHQQ', data, at)
            if defined and info >> 4 in (1, 2):
                end = pool.find(b'\x00', name)
                if end < 0:
                    raise ValueError('invalid ELF symbol name')
                symbols.add(pool[name:end].decode('ascii', errors='replace'))
    if any('Java_com_ipaengine_graph_GraphEngine_' + name not in symbols for name in NATIVE_METHODS):
        raise ValueError('missing a GraphEngine JNI dynamic export')


def validate_dictionary(data, keys):
    if len(data) < 108 or data[:8] != b'IPADWG01' or struct.unpack_from('<I', data, 8)[0] != 2:
        raise ValueError('dictionary must be an unwrapped IPADWG01 v2 file')
    magic, version, nodes, arcs, actual_keys, words, alphabet, root, *offsets = struct.unpack_from('<8s7I9Q', data)
    if (keys != actual_keys or not nodes or not words or not 1 <= alphabet <= 255
            or root >= nodes or offsets[0] != 108 or offsets[-1] != len(data)
            or any(a > b for a, b in zip(offsets, offsets[1:]))):
        raise ValueError('dictionary header/counts disagree with manifest')
    widths = {0: alphabet * 4, 1: (nodes + 1) * 3, 2: nodes * 3,
              3: arcs * 4, 4: (keys + 1) * 3, 6: ((words + 15) // 16 + 1) * 3}
    if any(offsets[index + 1] - offsets[index] != size for index, size in widths.items()):
        raise ValueError('invalid dictionary section sizes')


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=False)
    if result.returncode:
        raise ValueError(f'Git read failed ({args[0]}): {result.stderr.decode(errors="replace").strip()}')
    return result.stdout


def batch_path(public, batch):
    if not re.fullmatch(r'[A-Za-z0-9_\-][A-Za-z0-9_.\-]{0,99}', batch):
        raise ValueError('batch must be one safe directory name')
    return public / 'pre-release' / batch


def verify(public, batch, commit=None, root=ROOT):
    directory = batch_path(public, batch)
    manifest = load_json((directory / 'client.json').read_bytes())
    if 'artifactCommit' in manifest:
        raise ValueError('client.json describes its own batch; artifactCommit belongs only in board-update.json')
    validate_manifest(manifest)
    artifacts = {}
    for prefix, (filename, _) in FILES.items():
        relative = f'pre-release/{batch}/{filename}'
        if manifest[prefix + 'Path'] != relative:
            raise ValueError('batch must contain a complete standalone engine/dictionary bundle')
        path = public / relative
        if path.is_symlink():
            raise ValueError('artifact must be a regular file, not a symlink')
        data = path.read_bytes()
        if len(data) != manifest[prefix + 'Bytes'] or digest(data) != manifest[prefix + 'Sha256']:
            raise ValueError(f'{prefix} bytes/SHA-256 do not match client.json')
        artifacts[prefix] = data
    validate_dex(artifacts['dex'])
    validate_native(artifacts['native'])
    validate_dictionary(artifacts['dictionary'], manifest['dictionaryKeys'])
    source = load_json((directory / 'build.json').read_bytes())
    for prefix in FILES:
        if source.get(prefix + 'Sha256') != manifest[prefix + 'Sha256']:
            raise ValueError('build provenance does not match the DEX/JNI/dictionary pair')
    source_path = safe_path(source.get('sourceAarPath'))
    source_file = public / source_path
    if (source_file.is_symlink() or not source_file.is_file()
            or public.resolve() not in source_file.resolve().parents):
        raise ValueError('source AAR must be a regular public file, not a symlink')
    aar = source_file.read_bytes()
    if digest(aar) != source.get('sourceAarSha256'):
        raise ValueError('source AAR does not match recorded build provenance')
    with zipfile.ZipFile(__import__('io').BytesIO(aar)) as archive:
        if (archive.read('jni/arm64-v8a/libipa_graph_android.so') != artifacts['native']
                or archive.read('assets/ipa/lexicon.ipad') != artifacts['dictionary']
                or digest(archive.read('classes.jar')) != source.get('classesJarSha256')):
            raise ValueError('DEX/JNI/dictionary must originate from the same recorded AAR')
    dictionary_manifest = load_json((directory / 'lexicon.manifest.json').read_bytes())
    if (dictionary_manifest.get('complete') is not True
            or dictionary_manifest.get('version') != 2
            or dictionary_manifest.get('ipa_keys') != manifest['dictionaryKeys']
            or dictionary_manifest.get('bytes') != manifest['dictionaryBytes']
            or dictionary_manifest.get('sha256') != manifest['dictionarySha256']):
        raise ValueError('compiled dictionary metadata does not match client artifacts')
    expected_files = {*[v[0] for v in FILES.values()], 'client.json', 'build.json',
                      'lexicon.manifest.json', 'THIRD_PARTY_NOTICES.json', 'README.md', 'SHA256SUMS'}
    if {p.name for p in directory.iterdir()} != expected_files:
        raise ValueError('unexpected/missing client batch files')
    sums = (directory / 'SHA256SUMS').read_text(encoding='utf-8')
    expected_sums = ''.join(f'{digest(p.read_bytes())}  {p.name}\n' for p in sorted(directory.iterdir())
                            if p.name != 'SHA256SUMS')
    if sums != expected_sums:
        raise ValueError('SHA256SUMS does not match all batch files')
    if commit is not None:
        if not COMMIT.fullmatch(commit):
            raise ValueError('artifact commit must be a full lowercase SHA-1')
        if git(root, 'cat-file', '-t', commit).strip() != b'commit':
            raise ValueError('artifactCommit is not a commit object')
        for path in directory.iterdir():
            relative = path.relative_to(public).as_posix()
            obj = commit + ':' + relative
            mode = git(root, 'ls-tree', commit, '--', relative).split(b' ', 1)[0]
            if mode != b'100644' or git(root, 'cat-file', 'blob', obj) != path.read_bytes():
                raise ValueError(f'artifactCommit does not contain the current artifact: {relative}')
        if git(root, 'cat-file', 'blob', commit + ':' + source_path) != aar:
            raise ValueError('artifactCommit must also contain the source AAR used to build this batch')
    return manifest


def sdk_tools(sdk, d8=None):
    sdk = Path(sdk)
    if d8 is None:
        candidates = list((sdk / 'build-tools').glob('*/d8'))
        if not candidates:
            raise ValueError('installed SDK D8 not found; provide --d8 or --sdk (no download is performed)')
        d8 = max(candidates, key=lambda p: tuple(int(x) if x.isdigit() else 0 for x in p.parent.name.split('.')))
    jars = list((sdk / 'platforms').glob('*/android.jar'))
    if not jars or not Path(d8).is_file():
        raise ValueError('D8 and an installed Android platform jar are required')
    return Path(d8).resolve(), max(jars, key=lambda p: p.stat().st_mtime_ns)


def prepare(public, aar, batch, engine_version, dictionary_version, sdk, d8=None):
    destination = batch_path(public, batch)
    aar = Path(aar).resolve()
    try:
        aar_path = aar.relative_to(public.resolve()).as_posix()
    except ValueError as error:
        raise ValueError('prepare uses an already packaged AAR inside remote/ so provenance is public') from error
    release = load_json((aar.parent / 'release.json').read_bytes())
    source = aar.read_bytes()
    if (release.get('version') != engine_version or release.get('artifact') != aar.name
            or release.get('artifact_sha256') != digest(source)
            or release.get('artifact_bytes') != len(source)
            or release.get('min_sdk') != 26):
        raise ValueError('AAR does not match its packaged version, SHA-256, bytes, or minSdk')
    d8, android_jar = sdk_tools(sdk, d8)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.client-', dir=destination.parent) as temporary:
        stage = Path(temporary)
        with zipfile.ZipFile(aar) as archive:
            jar = archive.read('classes.jar')
            for name, archive_name in {
                'libipa_graph_android.so': 'jni/arm64-v8a/libipa_graph_android.so',
                'lexicon.ipad': 'assets/ipa/lexicon.ipad',
                'lexicon.manifest.json': 'assets/ipa/lexicon.manifest.json',
            }.items():
                (stage / name).write_bytes(archive.read(archive_name))
        with tempfile.TemporaryDirectory(prefix='ipa-d8-') as scratch:
            classes = Path(scratch) / 'classes.jar'
            classes.write_bytes(jar)
            subprocess.run([str(d8), '--release', '--min-api', '26', '--lib', str(android_jar),
                            '--output', str(stage), str(classes)], check=True, capture_output=True)
        metadata = load_json((stage / 'lexicon.manifest.json').read_bytes())
        manifest = {'schemaVersion': 1, 'engineVersion': engine_version,
                    'dictionaryVersion': dictionary_version, 'abi': 'arm64-v8a', 'minSdk': 26,
                    'entryPoint': ENTRY, 'dictionaryFormat': 2, 'dictionaryKeys': metadata['ipa_keys']}
        for prefix, (filename, _) in FILES.items():
            value = (stage / filename).read_bytes()
            manifest.update({prefix + 'Path': f'pre-release/{batch}/{filename}',
                             prefix + 'Bytes': len(value), prefix + 'Sha256': digest(value)})
        validate_manifest(manifest)
        save_json(stage / 'client.json', manifest)
        save_json(stage / 'build.json', {
            'sourceAarPath': aar_path, 'sourceAarSha256': digest(source),
            'classesJarSha256': digest(jar), 'd8BuildToolsVersion': d8.parent.name,
            'minApi': 26, **{key: value for key, value in manifest.items() if key.endswith('Sha256')},
            'validation': 'DEX header/signature, ARM64 JNI exports, same-AAR provenance, raw v2 dictionary; no new device execution.',
        })
        save_json(stage / 'THIRD_PARTY_NOTICES.json', {
            'version': engine_version, 'scope': 'Source-specific data notices; no blanket license is assigned.',
            'sources': metadata['sources'],
        })
        (stage / 'README.md').write_text(
            f'# IPA Board client artifacts: {batch}\n\n'
            f'Engine `{engine_version}`; dictionary `{dictionary_version}`. These standalone files '
            'are extracted from the same packaged AAR. classes.jar was converted by the installed '
            f'Android SDK D8 {d8.parent.name}, min API 26. DEX and JNI must be installed together.\n\n'
            '`lexicon.ipad` is an unwrapped, developer-compiled IPADWG01 v2 24-bit dictionary. '
            '`client.json` describes this immutable batch; the client discovers updates exclusively '
            'through the repository root `board-update.json`. This batch is advertised only after '
            'its complete public Git commit has been pushed. See [integration documentation](../../README.md).\n\n'
            '`build.json` records matching AAR/class/JNI provenance. `SHA256SUMS` covers every other '
            'batch file. Preserve the dictionary manifest and source notices. No new phone tests '
            'or performance measurements are claimed by this conversion.\n', encoding='utf-8')
        (stage / 'SHA256SUMS').write_text(''.join(
            f'{digest(path.read_bytes())}  {path.name}\n' for path in sorted(stage.iterdir())), encoding='utf-8')
        # Validate the stage using a temporary public tree with the source AAR reachable.
        with tempfile.TemporaryDirectory(prefix='ipa-client-check-') as check:
            check_public = Path(check)
            target = batch_path(check_public, batch)
            target.parent.mkdir(parents=True)
            shutil.copytree(stage, target)
            check_aar = check_public / aar_path
            check_aar.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(aar, check_aar)
            verify(check_public, batch)
        if destination.exists():
            if ({p.name for p in destination.iterdir()} != {p.name for p in stage.iterdir()}
                    or any((destination / p.name).read_bytes() != p.read_bytes() for p in stage.iterdir())):
                raise ValueError('immutable batch already exists with different contents; choose a new batch')
        else:
            stage.chmod(0o755)
            stage.rename(destination)
    return manifest


def publish_manifest(public, batch, commit, remote='public', branch=BRANCH, root=ROOT,
                     git_proxy=None):
    if branch != BRANCH:
        raise ValueError('IPA Board currently reads only the fixed development branch: ' + BRANCH)
    manifest = verify(public, batch, commit, root)
    manifest['artifactCommit'] = commit
    validate_manifest(manifest, require_commit=True)
    # Local tracking refs alone cannot prove the artifacts were pushed.
    remote_command = ['git', '-C', str(root)]
    if git_proxy is not None:
        remote_command += ['-c', 'http.proxy=' + git_proxy]
    result = subprocess.run([*remote_command, 'ls-remote', remote, 'refs/heads/' + branch],
                            capture_output=True, text=True, timeout=45, check=False)
    if result.returncode or not re.fullmatch(r'[0-9a-f]{40}\trefs/heads/' + re.escape(branch) + r'\n?', result.stdout):
        raise ValueError('cannot verify the actual remote publication branch; do not advertise artifacts yet')
    tip = result.stdout.split()[0]
    ancestry = subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', commit, tip],
                              capture_output=True, check=False)
    if ancestry.returncode:
        raise ValueError('artifactCommit is not published on the fixed remote branch; push artifacts/fetch its tip first')
    # atomic replacement; all publication gates precede the only mutable output.
    value = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    load_json(value)
    destination = public / 'board-update.json'
    with tempfile.NamedTemporaryFile(prefix='.board-update-', dir=public, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(value)
    try:
        temporary.chmod(0o644)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    preparation = commands.add_parser('prepare', help='create immutable binaries; leave board-update.json unchanged')
    preparation.add_argument('--aar', type=Path, required=True)
    preparation.add_argument('--batch', required=True)
    preparation.add_argument('--engine-version', required=True)
    preparation.add_argument('--dictionary-version', required=True)
    preparation.add_argument('--sdk', type=Path, default=Path(os.environ.get('ANDROID_SDK_ROOT') or
                             os.environ.get('ANDROID_HOME') or Path.home() / 'Library/Android/sdk'))
    preparation.add_argument('--d8', type=Path)
    verification = commands.add_parser('verify', help='verify files and optionally their public Git commit')
    verification.add_argument('--batch', required=True)
    verification.add_argument('--artifact-commit')
    publication = commands.add_parser('publish-manifest', help='advertise an already pushed artifact commit')
    publication.add_argument('--batch', required=True)
    publication.add_argument('--artifact-commit', required=True)
    publication.add_argument('--remote', default='public')
    publication.add_argument('--branch', default=BRANCH)
    publication.add_argument('--git-proxy', help='HTTP proxy for the remote check only; does not change Git configuration')
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            result = prepare(PUBLIC, args.aar, args.batch, args.engine_version,
                             args.dictionary_version, args.sdk, args.d8)
        elif args.command == 'verify':
            result = verify(PUBLIC, args.batch, args.artifact_commit)
        else:
            result = publish_manifest(PUBLIC, args.batch, args.artifact_commit, args.remote,
                                      args.branch, git_proxy=args.git_proxy)
    except (ValueError, KeyError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        parser.exit(1, f'{error}\n')
    print(json.dumps({'status': 'passed', 'command': args.command, 'batch': args.batch,
                      'manifest': result}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
