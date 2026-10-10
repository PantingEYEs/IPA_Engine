# Engine source snapshot

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
