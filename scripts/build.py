#!/usr/bin/env python3
# Copyright 2026 PantingEYEs. All rights reserved.
"""Build both Android ABIs from verified official sources and new read-only JNI."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys

from bootstrap import ROOT, digest, verify_source

ABIS = ('arm64-v8a', 'x86_64')
TRIPLES = {'arm64-v8a': 'aarch64-linux-android', 'x86_64': 'x86_64-linux-android'}

def record(path):
    return {'sha256': digest(path), 'sizeBytes': path.stat().st_size}

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()

def run(args, **kwargs):
    env = {k: v for k, v in os.environ.items() if k not in {
        'CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS', 'CMAKE_PREFIX_PATH', 'CMAKE_TOOLCHAIN_FILE'}}
    subprocess.run([str(a) for a in args], check=True, env=env, **kwargs)

def toolchain(ndk, cmake):
    properties = (ndk / 'source.properties').read_text()
    if 'Pkg.Revision = 29.0.14206865' not in properties:
        raise ValueError('Expected NDK 29.0.14206865')
    version = subprocess.check_output([str(cmake), '--version'], text=True).splitlines()[0]
    if version != 'cmake version 3.31.6':
        raise ValueError('Expected CMake 3.31.6')
    llvm = ndk / 'toolchains/llvm/prebuilt/linux-x86_64'
    names = ['source.properties', 'build/cmake/android.toolchain.cmake']
    names += [f'toolchains/llvm/prebuilt/linux-x86_64/bin/{name}' for name in
              ('clang++', 'ld.lld', 'llvm-ar', 'llvm-strip')]
    names += [f'toolchains/llvm/prebuilt/linux-x86_64/sysroot/usr/lib/{target}/libc++_static.a'
              for target in TRIPLES.values()]
    result = {'ndkRevision': '29.0.14206865', 'cmakeVersion': '3.31.6',
              'files': {name: record(ndk / name) for name in names},
              'cmakeBinary': record(cmake), 'makeBinary': record(Path(shutil.which('make')))}
    return llvm, result

def recipe_records(root=ROOT):
    paths = [root / 'sources.lock.json', root / 'CMakeLists.txt',
             root / 'scripts/bootstrap.py', root / 'scripts/build.py']
    paths += sorted(p for p in (root / 'bridge').rglob('*') if p.is_file())
    if not (root / 'bridge/latinime_jni.cpp').is_file() or not (root / 'bridge/exports.map').is_file():
        raise ValueError('New read-only bridge source is not present')
    return {p.relative_to(root).as_posix(): record(p) for p in paths}

def verify_published_provenance(proof, root=ROOT):
    if proof.get('schemaVersion') != 1:
        raise ValueError('Unsupported build provenance')
    inputs = proof['inputs']
    if inputs['recipe'] != recipe_records(root):
        raise ValueError('Published recipe differs from compiled recipe')
    lock = json.loads((root / 'sources.lock.json').read_text())
    if inputs['upstreamCommit'] != lock['upstream']['commit'] or inputs['buildOptions'] != lock['build']:
        raise ValueError('Published upstream/build options differ')
    if set(inputs['coreArchives']) != set(ABIS):
        raise ValueError('Incomplete ABI core inputs')
    runtime = inputs.get('runtimeLinkInputs', {})
    if set(runtime) != set(ABIS) or any(len(rows) < 10 for rows in runtime.values()):
        raise ValueError('Incomplete published NDK link inputs')
    expected = hashlib.sha256(canonical(inputs)).hexdigest()
    if proof['buildId'] != expected:
        raise ValueError('Invalid build provenance digest')
    java_class = root / 'artifacts/java/org/ipaboard/latinime/LatinImeNative.class'
    if record(java_class) != inputs['javaClass']:
        raise ValueError('Compiled Java interface differs from native build inputs')
    for abi in ABIS:
        path = root / 'artifacts' / abi / 'libipa_latinime.so'
        if record(path) != proof['artifacts'][abi]:
            raise ValueError(f'Published library differs: {abi}')
        if (';build=' + expected).encode() not in path.read_bytes():
            raise ValueError(f'Stale native identity: {abi}')
    return expected

def verify_local_provenance(root=ROOT):
    proof = json.loads((root / 'artifacts/build-provenance.json').read_text())
    verify_source(root)
    verify_published_provenance(proof, root)
    for abi in ABIS:
        archive = root / '.work' / abi / 'liblatinime_core.a'
        if record(archive) != proof['inputs']['coreArchives'][abi]:
            raise ValueError(f'Compiled core archive differs: {abi}')
    context = json.loads((root / '.work/build-context.json').read_text())
    if runtime_link_inputs(Path(context['ndk']), root) != proof['inputs']['runtimeLinkInputs']:
        raise ValueError('Local NDK runtime inputs differ from published proof')
    return proof

def runtime_link_inputs(ndk, root=ROOT):
    """Read lld's actual dependency list, rejecting any unaccounted input."""
    result = {}
    ndk = ndk.resolve()
    for abi in ABIS:
        directory = root / '.work' / abi
        dependency = directory / 'CMakeFiles/ipa_latinime.dir/link.d'
        first = dependency.read_text().split('\n\n', 1)[0].replace('\\\n', ' ')
        tokens = shlex.split(first)[1:]
        local = {(root / 'bridge/exports.map').resolve(),
                 (directory / 'CMakeFiles/ipa_latinime.dir/bridge/latinime_jni.cpp.o').resolve(),
                 (directory / 'liblatinime_core.a').resolve()}
        runtime = {}
        seen_local = set()
        for token in tokens:
            path = Path(token)
            path = (path if path.is_absolute() else directory / path).resolve()
            if path in local:
                seen_local.add(path)
            elif path.is_relative_to(ndk):
                runtime[path.relative_to(ndk).as_posix()] = record(path)
            else:
                raise ValueError('Unreviewed native link input: ' + path.name)
        if seen_local != local:
            raise ValueError('Incomplete local native link dependency list')
        required = ('libc++_static.a', 'libc++abi.a', 'libatomic.a', 'libunwind.a',
                    'crtbegin_so.o', 'crtend_so.o', 'libc.so', 'libm.so', 'libdl.so')
        names = {Path(name).name for name in runtime}
        if not set(required) <= names or not any(name.startswith('libclang_rt.builtins-') for name in names):
            raise ValueError('Incomplete NDK runtime link dependency list')
        result[abi] = dict(sorted(runtime.items()))
    return result

def reusable_core(previous, tools, root=ROOT):
    if previous['buildId'] != hashlib.sha256(canonical(previous['inputs'])).hexdigest():
        raise ValueError('Previous core proof is invalid')
    for abi in ABIS:
        library = root / 'artifacts' / abi / 'libipa_latinime.so'
        if record(library) != previous['artifacts'][abi] or (';build=' + previous['buildId']).encode() not in library.read_bytes():
            raise ValueError('Previous native artifact does not match core proof')
    for name in ('sources.lock.json', 'CMakeLists.txt', 'scripts/bootstrap.py'):
        if previous['inputs']['recipe'][name] != record(root / name):
            raise ValueError('Cannot relink after core source/recipe changes')
    for key, value in tools.items():
        if previous['inputs']['toolchain'][key] != value:
            raise ValueError('Cannot relink after toolchain changes')
    for abi in ABIS:
        if record(root / '.work' / abi / 'liblatinime_core.a') != previous['inputs']['coreArchives'][abi]:
            raise ValueError('Cannot relink an unverified core archive')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ndk', type=Path, default=os.environ.get('ANDROID_NDK_HOME'))
    parser.add_argument('--cmake', type=Path, default=os.environ.get('CMAKE_BIN'))
    parser.add_argument('--javac', type=Path, default=os.environ.get('JAVAC_BIN'))
    parser.add_argument('--jobs', type=int, default=8)
    parser.add_argument('--core-only', action='store_true')
    parser.add_argument('--relink', action='store_true', help='Reuse only the previous verified, unchanged core archives')
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if args.verify_only:
        print(verify_local_provenance()['buildId'])
        return
    if not args.ndk or not args.cmake:
        parser.error('--ndk and --cmake (or ANDROID_NDK_HOME/CMAKE_BIN) are required')
    lock, source = verify_source()
    ndk, cmake = args.ndk.resolve(), args.cmake.resolve()
    llvm, tools = toolchain(ndk, cmake)
    work = ROOT / '.work'
    work.mkdir(exist_ok=True)
    generated = work / 'core-sources.cmake'
    generated.write_text('set(LATINIME_CORE_SOURCES\n' + ''.join(f'  "{p}"\n' for p in lock['upstream']['coreSources']) + ')\n')
    common = [cmake, '-S', ROOT, '--fresh', '-G', 'Unix Makefiles', '-DCMAKE_BUILD_TYPE=Release',
              f'-DCMAKE_TOOLCHAIN_FILE={ndk}/build/cmake/android.toolchain.cmake',
              '-DANDROID_PLATFORM=android-26', '-DANDROID_STL=c++_static',
              f'-DHELIBOARD_SOURCE={source}', f'-DGENERATED_SOURCE_LIST={generated}',
              '-DANDROID_SUPPORT_FLEXIBLE_PAGE_SIZES=ON', '-DCMAKE_EXPORT_COMPILE_COMMANDS=ON']
    if args.relink:
        previous = json.loads((ROOT / 'artifacts/build-provenance.json').read_text())
        reusable_core(previous, tools)
    else:
        for abi in ABIS:
            directory = work / abi
            run(common + ['-B', directory, f'-DANDROID_ABI={abi}', '-DLATINIME_BUILD_BRIDGE=OFF'])
            run([cmake, '--build', directory, '--target', 'latinime_core', '-j', args.jobs])
    if args.core_only:
        print('Both verified upstream core archives built')
        return
    if not args.javac:
        parser.error('--javac (or JAVAC_BIN) is required for the complete JNI build')
    javac = args.javac.resolve()
    javac_version = subprocess.check_output([str(javac), '-version'], text=True).strip()
    if not javac_version.startswith('javac 21.'):
        raise ValueError('Expected JDK 21 javac')
    classes = ROOT / 'artifacts/java'
    classes.mkdir(parents=True, exist_ok=True)
    run([javac, '--release', '11', '-encoding', 'UTF-8', '-g:none', '-d', classes,
         ROOT / 'bridge/java/org/ipaboard/latinime/LatinImeNative.java'])
    java_class = classes / 'org/ipaboard/latinime/LatinImeNative.class'
    tools['javac'] = {'version': javac_version, 'binary': record(javac), 'release': 11}
    header = work / 'identity'
    header.mkdir(exist_ok=True)
    def identity_header(build_id):
        constants = {'IPA_LATINIME_BUILD_ID': build_id,
                     'IPA_LATINIME_SOURCE_REVISION': lock['upstream']['commit'],
                     'IPA_LATINIME_DICTIONARY_SHA256': lock['dictionary']['sha256']}
        text = '// Generated from verified build inputs.\n#pragma once\n'
        text += ''.join(f'#define {key} "{value}"\n' for key, value in constants.items())
        text += '#define IPA_LATINIME_ABI_VERSION 1\n#define IPA_LATINIME_PACKET_VERSION 1\n'
        (header / 'build_identity.h').write_text(text)
    # A discovery link obtains the exact runtime archives/stubs/CRT chosen by clang/lld.
    # Its temporary identity is never staged or packaged.
    identity_header('0' * 64)
    link_common = [part for part in common if part != '--fresh']
    core_records = {abi: record(work / abi / 'liblatinime_core.a') for abi in ABIS}
    for abi in ABIS:
        directory = work / abi
        run(link_common + ['-B', directory, f'-DANDROID_ABI={abi}', '-DLATINIME_BUILD_BRIDGE=ON', f'-DIDENTITY_INCLUDE={header}'])
        run([cmake, '--build', directory, '--target', 'ipa_latinime', '-j', args.jobs])
        if record(directory / 'liblatinime_core.a') != core_records[abi]:
            raise ValueError('Core archive changed during discovery link')
    runtime = runtime_link_inputs(ndk)
    inputs = {'upstreamCommit': lock['upstream']['commit'], 'buildOptions': lock['build'],
              'javaClass': record(java_class), 'recipe': recipe_records(), 'toolchain': tools,
              'runtimeLinkInputs': runtime, 'coreArchives': core_records}
    build_id = hashlib.sha256(canonical(inputs)).hexdigest()
    identity_header(build_id)
    artifacts = ROOT / 'artifacts'
    result = {}
    for abi in ABIS:
        directory = work / abi
        run([cmake, '--build', directory, '--target', 'ipa_latinime', '-j', args.jobs])
        target = artifacts / abi / 'libipa_latinime.so'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(directory / 'libipa_latinime.so', target)
        run([llvm / 'bin/llvm-strip', '--strip-unneeded', target])
        result[abi] = record(target)
    if runtime_link_inputs(ndk) != runtime:
        raise ValueError('NDK runtime inputs changed during final link')
    if any(record(work / abi / 'liblatinime_core.a') != core_records[abi] for abi in ABIS):
        raise ValueError('Core archive changed during final link')
    # Detect edits during the build before marking either ABI publishable.
    verify_source()
    if recipe_records() != inputs['recipe']:
        raise ValueError('Recipe changed during compilation; rebuild before publishing')
    proof = {'schemaVersion': 1, 'buildId': build_id, 'inputs': inputs, 'artifacts': result}
    verify_published_provenance(proof)
    (work / 'build-context.json').write_text(json.dumps({'ndk': str(ndk), 'cmake': str(cmake), 'javac': str(javac)}) + '\n')
    (artifacts / 'build-provenance.json').write_text(json.dumps(proof, indent=2) + '\n')
    print(json.dumps({'buildId': build_id, 'artifacts': result}, indent=2))

if __name__ == '__main__':
    main()
