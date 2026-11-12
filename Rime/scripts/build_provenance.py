#!/usr/bin/env python3
# Copyright (c) 2026 PantingEYEs. All rights reserved.
"""Bind native build recipes to the exact archives/headers used by the linker.

Private path records stay in .work. Published proof records contain hashes and
logical labels only and can be verified in a fresh checkout without build caches.
This is a reproducibility record, not a signed third-party build attestation.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ABIS = ('arm64-v8a', 'x86_64')
CORE_RECIPE = ('Rime/sources.lock.json', 'Rime/scripts/prepare.py',
               'Rime/scripts/build.sh', 'Rime/scripts/build_provenance.py',
               'OpenCC/sources.lock.json', 'OpenCC/artifacts/manifest.json',
               'OpenCC/integration/CMakeLists.txt', 'OpenCC/scripts/build.sh')
LINK_RECIPE = CORE_RECIPE + ('Rime/scripts/link_bridge.sh',
               'Rime/bridge/rime_jni.cpp', 'Rime/java/org/ipaboard/rime/RimeNative.java',
               'Rime/bridge-build/CMakeLists.txt', 'Rime/bridge-build/exports.map')

def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()

def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def file_record(path):
    path = Path(path)
    with path.open('rb') as stream:
        sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'sha256': sha, 'sizeBytes': path.stat().st_size}

def input_record(path):
    path = Path(path)
    if path.is_file():
        return {'kind': 'file', **file_record(path)}
    if not path.is_dir():
        raise ValueError('Missing native input')
    entries = {p.relative_to(path).as_posix(): file_record(p)
               for p in sorted(path.rglob('*')) if p.is_file()}
    if not entries:
        raise ValueError('Empty native header directory')
    return {'kind': 'tree', 'fileCount': len(entries), 'sha256': digest(entries)}

def recipe(root, names):
    return {name: file_record(root.parent/name) for name in names}

def load(path):
    return json.loads(Path(path).read_text())

def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(json.dumps(value, indent=2).encode()+b'\n')
    os.replace(temporary, path)

def run_opencc_verifier(root):
    subprocess.run([sys.executable, str(root.parent/'OpenCC/scripts/verify_artifacts.py')],
                   check=True, stdout=subprocess.DEVNULL)

def core_paths(root, build, boost):
    paths = {'boost-headers': str(boost)}
    for abi in ABIS:
        paths[abi+'/librime.a'] = str(build/('rime-'+abi)/'lib/librime.a')
        prefix = build/'install'/abi
        for name in ('libyaml-cpp.a', 'libleveldb.a'):
            paths[abi+'/'+name] = str(prefix/'lib'/name)
        paths[abi+'/dependency-headers'] = str(prefix/'include')
        prefix = root.parent/'OpenCC/artifacts'/abi/'static'
        for name in ('libopencc.a', 'libmarisa.a'):
            paths['OpenCC/'+abi+'/'+name] = str(prefix/'lib'/name)
        paths['OpenCC/'+abi+'/headers'] = str(prefix/'include')
    return paths

def expected_labels():
    return set(core_paths(Path('/repo/Rime'), Path('/build'), Path('/boost')))

def records(paths):
    result = {}
    for label, path in paths.items():
        if label.endswith('.a'):
            with Path(path).open('rb') as stream:
                if stream.read(8) != b'!<arch>\n':
                    raise ValueError('Invalid static archive: '+label)
        result[label] = input_record(path)
    return result

def cache_values(path):
    result = {}
    for line in path.read_text().splitlines():
        if line.startswith(('#', '//')) or '=' not in line or ':' not in line.split('=', 1)[0]:
            continue
        key, value = line.split('=', 1)
        result[key.split(':', 1)[0]] = value
    return result

def compiler_evidence(root, build, boost, ndk):
    evidence = {}
    replacements = [(str(root.parent), '$REPO'), (str(build), '$BUILD'),
                    (str(boost), '$BOOST'), (str(ndk), '$NDK')]
    replacements.sort(key=lambda pair: len(pair[0]), reverse=True)
    def normalize(text):
        for original, token in replacements:
            text = text.replace(original, token)
        return text
    for component, source in (('leveldb', root/'.work/librime/deps/leveldb'),
                              ('yaml', root/'.work/librime/deps/yaml-cpp'),
                              ('rime', root/'.work/librime')):
        for abi in ABIS:
            folder = build/(component+'-'+abi)
            cache = cache_values(folder/'CMakeCache.txt')
            required = {'ANDROID_ABI': abi, 'ANDROID_PLATFORM': 'android-26',
                        'ANDROID_STL': 'c++_static', 'BUILD_SHARED_LIBS': 'OFF'}
            if component == 'rime':
                required.update(BUILD_STATIC='ON', BUILD_TEST='OFF', ENABLE_LOGGING='OFF',
                    ENABLE_EXTERNAL_PLUGINS='OFF', ENABLE_THREADING='ON', ENABLE_TIMESTAMP='OFF')
            elif component == 'yaml':
                required.update(YAML_CPP_BUILD_TESTS='OFF', YAML_CPP_BUILD_TOOLS='OFF',
                                YAML_CPP_BUILD_CONTRIB='OFF')
            else:
                required.update(LEVELDB_BUILD_TESTS='OFF', LEVELDB_BUILD_BENCHMARKS='OFF',
                                HAVE_CRC32C='0', HAVE_SNAPPY='0', HAVE_TCMALLOC='0')
            if any(cache.get(key) != value for key, value in required.items()):
                raise ValueError('Unexpected native build configuration: '+component+'/'+abi)
            if Path(cache['CMAKE_HOME_DIRECTORY']).resolve() != source.resolve():
                raise ValueError('Native build uses a different source directory')
            commands = load(folder/'compile_commands.json')
            if not commands or any(not Path(item['file']).resolve().is_relative_to(source.resolve())
                                   for item in commands):
                raise ValueError('Unexpected compilation input directory')
            tracked = set(subprocess.check_output(['git', '-C', str(source), 'ls-files', '-z'])
                          .decode().split('\0'))
            for item in commands:
                arguments = item.get('arguments') or shlex.split(item['command'])
                if not Path(arguments[0]).resolve().is_relative_to(ndk.resolve()):
                    raise ValueError('Native build uses a different compiler')
                if Path(item['file']).relative_to(source).as_posix() not in tracked:
                    raise ValueError('An untracked source entered the native compile commands')
            evidence[component+'/'+abi] = {'settings': required,
                'commandCount': len(commands),
                'commandsSha256': hashlib.sha256(normalize(canonical(commands).decode()).encode()).hexdigest()}
    return evidence

def environment(root):
    return {'build': str(Path(os.environ.get('RIME_BUILD_DIR', root/'.work/build')).resolve()),
            'boost': str(Path(os.environ.get('RIME_BOOST_DIR', root/'.work/boost')).resolve()),
            'ndk': str(Path(os.environ['ANDROID_NDK_HOME']).resolve()),
            'cmake': str(Path(os.environ['RIME_CMAKE']).resolve())}

def toolchain(env):
    ndk = Path(env['ndk'])
    properties = (ndk/'source.properties').read_text()
    if 'Pkg.Revision = 29.0.14206865' not in properties:
        raise ValueError('Unexpected Android NDK')
    version = subprocess.check_output([env['cmake'], '--version'], text=True).splitlines()[0]
    if version != 'cmake version 3.31.6':
        raise ValueError('Unexpected CMake')
    return {'ndkRevision': '29.0.14206865', 'cmakeVersion': '3.31.6',
            'ndkProperties': file_record(ndk/'source.properties'),
            'compiler': file_record(ndk/'toolchains/llvm/prebuilt/linux-x86_64/bin/clang++'),
            'cmake': file_record(env['cmake'])}

def begin_core(root):
    run_opencc_verifier(root)
    env = environment(root)
    save(root/'.work/core-build-start.json', {'recipe': recipe(root, CORE_RECIPE),
                                            'toolchain': toolchain(env), 'private': env})

def record_core(root):
    begin = load(root/'.work/core-build-start.json')
    env = environment(root)
    if begin != {'recipe': recipe(root, CORE_RECIPE), 'toolchain': toolchain(env), 'private': env}:
        raise ValueError('Build inputs changed while the core was building')
    run_opencc_verifier(root)
    paths = core_paths(root, Path(env['build']), Path(env['boost']))
    statement = {'schemaVersion': 1, 'recipe': begin['recipe'], 'toolchain': begin['toolchain'],
                 'inputs': records(paths), 'compilation': compiler_evidence(root,
                    Path(env['build']), Path(env['boost']), Path(env['ndk']))}
    save(root/'.work/core-provenance.json', {'statement': statement,
        'digest': digest(statement), 'private': {'environment': env, 'inputPaths': paths}})

def verify_core(root, require_environment=False):
    proof = load(root/'.work/core-provenance.json')
    statement = proof['statement']
    if statement['recipe'] != recipe(root, CORE_RECIPE) or proof['digest'] != digest(statement):
        raise ValueError('Core build recipe/source identity is stale')
    if set(statement['inputs']) != expected_labels():
        raise ValueError('Incomplete core input inventory')
    if statement['inputs'] != records(proof['private']['inputPaths']):
        raise ValueError('Core archive/header inputs changed after the recorded build')
    env = proof['private']['environment']
    if (require_environment and env != environment(root)) or statement['toolchain'] != toolchain(env):
        raise ValueError('Different build environment/toolchain from the recorded core')
    if statement['compilation'] != compiler_evidence(root, Path(env['build']), Path(env['boost']), Path(env['ndk'])):
        raise ValueError('Core compiler inputs changed after the recorded build')
    return proof

def make_public(core, link):
    return {'schemaVersion': 1, 'core': core, 'link': link,
            'coreDigest': digest(core), 'linkInputsDigest': digest(link['inputs']),
            'recipeDigest': digest(link['recipe']), 'buildId': digest({'core': core, 'link': link})}

def prepare_link(root):
    run_opencc_verifier(root)
    core = verify_core(root, require_environment=True)
    paths = dict(core['private']['inputPaths'])
    paths['java/RimeNative.class'] = str(Path(core['private']['environment']['build'])/
                                       'java/classes/org/ipaboard/rime/RimeNative.class')
    link = {'recipe': recipe(root, LINK_RECIPE), 'inputs': records(paths)}
    proof = make_public(core['statement'], link)
    save(root/'.work/link-provenance.json', {'public': proof, 'private': {'inputPaths': paths}})
    return proof['buildId']

def verify_published_provenance(proof, root=ROOT):
    """Validate public identities without requiring any ignored build directory."""
    root = Path(root)
    if proof.get('schemaVersion') != 1 or proof != make_public(proof['core'], proof['link']):
        raise ValueError('Invalid native build provenance digest')
    if proof['core']['recipe'] != recipe(root, CORE_RECIPE) or proof['link']['recipe'] != recipe(root, LINK_RECIPE):
        raise ValueError('Published native provenance is stale relative to sources/recipe')
    if set(proof['core']['inputs']) != expected_labels() or set(proof['link']['inputs']) != expected_labels() | {'java/RimeNative.class'}:
        raise ValueError('Incomplete published native input inventory')
    if any(proof['core']['inputs'][label] != proof['link']['inputs'][label] for label in expected_labels()):
        raise ValueError('Link input differs from recorded core dependency')
    for abi in ABIS:
        prefix = root.parent/'OpenCC/artifacts'/abi/'static'
        for label, path in [('OpenCC/'+abi+'/'+name, prefix/'lib'/name)
                            for name in ('libopencc.a', 'libmarisa.a')] + [('OpenCC/'+abi+'/headers', prefix/'include')]:
            if proof['link']['inputs'][label] != input_record(path):
                raise ValueError('Published OpenCC input differs: '+label)
    return proof

def verify_link_provenance(root=ROOT):
    """Build/package gate: also require and recheck every actual local input."""
    root = Path(root)
    private = load(root/'.work/link-provenance.json')
    proof = verify_published_provenance(private['public'], root)
    core = verify_core(root)
    if core['statement'] != proof['core'] or records(private['private']['inputPaths']) != proof['link']['inputs']:
        raise ValueError('Link inputs changed after native build identity was issued')
    return proof

def source_identity(root=ROOT):
    return verify_link_provenance(root)['buildId']

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('begin-core', 'record-core', 'verify-core', 'prepare-link', 'verify-link'))
    args = parser.parse_args()
    if args.command == 'begin-core': begin_core(ROOT)
    elif args.command == 'record-core': record_core(ROOT)
    elif args.command == 'verify-core': verify_core(ROOT)
    elif args.command == 'prepare-link': print(prepare_link(ROOT))
    else: print(source_identity(ROOT))

if __name__ == '__main__':
    main()
