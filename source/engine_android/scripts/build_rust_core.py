"""Build the dependency-free Rust core/CLI on the host or Android ARM64."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def source_digest(root=ROOT):
    root = Path(root)
    engine = root / 'engine_android'
    paths = [engine / 'ipa_graph/Cargo.toml', engine / 'ipa_graph/Cargo.lock']
    paths += list((engine / 'ipa_graph/src').rglob('*.rs'))
    paths += list((engine / 'android/src/main/cpp').glob('*'))
    paths += list((engine / 'android/src/main/java').rglob('*.java'))
    paths += [engine / 'android/src/main/AndroidManifest.xml', engine / 'android/consumer-rules.pro']
    digest = hashlib.sha256()
    for path in sorted(paths):
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode() + b'\0')
            digest.update(path.read_bytes() + b'\0')
    return digest.hexdigest()


def build(target=None, ndk=None, api=26, bins=('ipa_graph',), root=ROOT):
    root = Path(root).resolve()
    cargo = shutil.which('cargo') or str(Path.home() / '.cargo/bin/cargo')
    environment = os.environ.copy()
    if target:
        if target != 'aarch64-linux-android' or api < 26:
            raise ValueError('Android builds require aarch64-linux-android and API >= 26')
        ndk = Path(ndk or os.environ.get('ANDROID_NDK_HOME') or root / '.tools/android/android-ndk-r30')
        host = {'Darwin': 'darwin-x86_64', 'Linux': 'linux-x86_64', 'Windows': 'windows-x86_64'}[platform.system()]
        linker = ndk / f'toolchains/llvm/prebuilt/{host}/bin/aarch64-linux-android{api}-clang'
        if not linker.is_file():
            raise ValueError('installed NDK not found; provide --ndk or ANDROID_NDK_HOME')
        environment['CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER'] = str(linker)
        environment['CARGO_TARGET_AARCH64_LINUX_ANDROID_RUSTFLAGS'] = '-C link-arg=-Wl,-z,max-page-size=16384'
    destination = root / 'data/engine_android/builds/rust'
    command = [cargo, 'build', '--offline', '--release', '--manifest-path',
               str(root / 'engine_android/ipa_graph/Cargo.toml'), '--target-dir', str(destination), '--lib']
    for name in bins:
        command += ['--bin', name]
    if target:
        command += ['--target', target]
    subprocess.run(command, env=environment, check=True)
    output = destination / target / 'release' if target else destination / 'release'
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', choices=['aarch64-linux-android'])
    parser.add_argument('--ndk', type=Path)
    parser.add_argument('--api', type=int, default=26)
    args = parser.parse_args()
    output = build(args.target, args.ndk, args.api, bins=('ipa_graph', 'build_dawg'))
    print(json.dumps({'output': str(output), 'source_sha256': source_digest()}, indent=2))


if __name__ == '__main__':
    main()
