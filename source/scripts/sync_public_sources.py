"""Copy an explicit, dependency-closed engine source snapshot into remote/source/."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
# File names, rather than extensions or directory globs, are the publication boundary.
SOURCE_FILES = frozenset('''
rust/ipa_graph/Cargo.toml
rust/ipa_graph/Cargo.lock
rust/ipa_graph/README.md
rust/ipa_graph/src/lib.rs
rust/ipa_graph/src/mapping.rs
rust/ipa_graph/src/unicode.rs
rust/ipa_graph/src/ffi.rs
rust/ipa_graph/src/bin/ipa_graph.rs
rust/ipa_graph/src/bin/build_dawg.rs
rust/ipa_graph/tests/core.rs
rust/ipa_graph/tests/fixtures/omissions.ipad
rust/ipa_graph/tests/fixtures/synthetic-24.ipad
rust/ipa_graph/tests/fixtures/synthetic-32.ipad
android/CMakeLists.txt
android/README.md
android/consumer-rules.pro
android/src/main/AndroidManifest.xml
android/src/main/cpp/ipa_graph_jni.cpp
android/src/main/cpp/ipa_graph_rust.h
android/src/main/java/com/ipaengine/graph/GraphEngine.java
android/tests/GraphProbe.java
android/tests/JniSmoke.java
android/tests/verify_desktop_jni.py
graph/CMakeLists.txt
graph/__init__.py
graph/build_dawg.cpp
graph/compiler.py
graph/format_spec.md
graph/lexicon_reader.py
graph/include/ipa_graph.h
graph/include/ipa_unicode.h
graph/src/ipa_graph.cpp
graph/src/ipa_graph_cli.cpp
graph/tests/make_fixture.py
graph/tests/runtime_test.cpp
scripts/build_rust_core.py
scripts/build_android_library.py
scripts/compile_graph_dictionary.py
scripts/convert_graph_indices.py
scripts/compare_rust_engine.py
scripts/engine_cli.py
scripts/normalize.py
scripts/prepare_model_data.py
scripts/package_client_release.py
scripts/package_pre_release.py
scripts/publish_release.py
scripts/sync_public_sources.py
scripts/verify_public_export.py
scripts/test_android_wrapper.py
tests/test_graph_compiler.py
tests/test_graph_omissions.py
tests/test_engine_cli.py
tests/test_rust_migration.py
tests/test_public_sources.py
tests/cases/graph_cases.jsonl
docs/引擎开发规范.md
docs/图词典引擎.md
docs/图结构选型.md
docs/远程发布流程.md
'''.split())
GENERATED_FILES = frozenset({'README.md', '.gitignore', 'source-manifest.json', 'SHA256SUMS',
                             'tests/__init__.py'})


def permitted_source(path):
    """Accept exactly the files emitted by sync_sources, with no normalization."""
    return path in SOURCE_FILES or path in GENERATED_FILES


README = '''# Engine source snapshot

This directory contains the first-party Rust engine, Android Java/JNI wrapper,
offline dictionary compiler, build tools, format specification and synthetic tests.
Run the following commands from this directory; Python 3.10+ and Rust 1.89+ are required.

```sh
python3 scripts/build_rust_core.py
cargo test --offline --manifest-path rust/ipa_graph/Cargo.toml
python3 graph/tests/make_fixture.py /tmp/fixture.ipad
python3 scripts/engine_cli.py --dictionary /tmp/fixture.ipad query a
python3 -m unittest discover -s tests -p 'test_graph_compiler.py'
```

Production runtime and DAWG minimization use Rust. Python prepares lexical records,
assembles the packed dictionary and associates frequencies offline. The previous C++
implementation remains as a migration reference. Android packaging requires an
installed Android SDK, NDK, JDK and Rust aarch64-linux-android target; no raw corpus
is required to build against an already compiled dictionary from ../pre-release/.
See [Rust build notes](rust/ipa_graph/README.md) and [Android API](android/README.md).

Unprocessed archives, observation databases, collection catalogs, learned weights,
private reports, caches and full-project Git history are excluded. The hand-written
test cases and generated tiny dictionaries are validation fixtures, not corpus exports.
To recompile a full dictionary, supply your own screened SQLite observation database;
the schema is exercised in tests/test_graph_compiler.py. Processing-code defaults
mention local data/ paths without shipping their contents.

source-manifest.json records each public source file's SHA-256 and the runtime input
digest used by the Android build. Publishing source does not assign a software license;
no project-wide license has been selected. Data provenance notices remain attached
to compiled artifacts. Publication tools require the complete project's remote/
layout; the source snapshot is for inspection, engine builds and synthetic tests.
'''


def sync_sources(root=ROOT, public=None):
    """Validate all inputs before atomically replacing only the source snapshot."""
    root = Path(root).resolve()
    public = Path(public) if public is not None else root / 'remote'
    files = {}
    for name in sorted(SOURCE_FILES):
        path = root / name
        if any(part.is_symlink() for part in (path, *path.parents) if part != root.parent):
            raise ValueError('source publication rejects symbolic links: ' + name)
        if not path.is_file():
            raise ValueError('required public source is missing: ' + name)
        files[name] = path.read_bytes()
    # Share the Android builder's definition of inputs instead of inventing another hash.
    from scripts.build_rust_core import source_digest
    runtime_digest = source_digest(root)
    entries = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    source_digest_value = hashlib.sha256(json.dumps(entries, sort_keys=True,
                                separators=(',', ':')).encode()).hexdigest()
    manifest = {'format': 1, 'sourceSha256': source_digest_value,
                'runtimeSourceSha256': runtime_digest, 'files': entries,
                'excluded': ['raw data', 'observation databases', 'models', 'private Git history'],
                'license': 'No project-wide software license has been selected.'}
    public.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.source-', dir=public) as temporary:
        stage = Path(temporary) / 'source'
        stage.mkdir()
        for name, data in files.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        if source_digest(stage) != runtime_digest:
            raise ValueError('runtime inputs include files outside the public source allowlist')
        (stage / 'README.md').write_text(README, encoding='utf-8')
        (stage / '.gitignore').write_text('.tools/\n__pycache__/\n*.py[cod]\n/data/\n/target/\n'
                                         '/rust/ipa_graph/target/\n/android/build/\n/graph/build/\n', encoding='utf-8')
        (stage / 'tests/__init__.py').write_text('', encoding='utf-8')
        (stage / 'source-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False,
                                                  indent=2) + '\n', encoding='utf-8')
        checksums = []
        for path in sorted(stage.rglob('*')):
            if path.is_file():
                checksums.append(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  '
                                 f'{path.relative_to(stage).as_posix()}\n')
        (stage / 'SHA256SUMS').write_text(''.join(checksums), encoding='utf-8')
        target, backup = public / 'source', public / '.source-backup'
        if backup.exists():
            raise ValueError('source backup exists; inspect it before synchronizing')
        if target.is_symlink():
            raise ValueError('source output cannot be a symbolic link')
        if target.exists():
            target.rename(backup)
        try:
            stage.rename(target)
        except OSError:
            if backup.exists():
                backup.rename(target)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--public', type=Path)
    args = parser.parse_args()
    try:
        manifest = sync_sources(args.root, args.public)
    except (ValueError, OSError) as error:
        parser.exit(1, f'Source synchronization failed: {error}\n')
    print(json.dumps({key: manifest[key] for key in ('sourceSha256', 'runtimeSourceSha256')}, indent=2))


if __name__ == '__main__':
    main()
