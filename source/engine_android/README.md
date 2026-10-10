# Rust IPA graph core

Production dictionary validation, exact/ordered-omission/prefix search, numeric
tone aliases, deduplication and frequency ranking use Rust. `build_dawg` is the
Rust minimizer used by the offline Python compiler. There are no crates.io
dependencies. Java and a small C++ JNI UTF-16 adapter preserve the Android API.

The production engine implementation, its Rust tests, Android wrapper, and
engine-specific build instructions live in this phase. Generated Android
packages, probe results, and build reports belong in `data/engine_android/`;
the final cross-phase CLI and host/device acceptance suite belongs in
`cli_tests/`.

From the full project root, or `source/` in the public repository:

```sh
python3 engine_android/scripts/build_rust_core.py
cargo test --offline --manifest-path engine_android/ipa_graph/Cargo.toml
python3 -m unittest cli_tests.tests.test_graph_omissions
python3 cli_tests/scripts/engine_cli.py --dictionary /path/to/lexicon.ipad query 'maɪ'
python3 cli_tests/scripts/engine_cli.py --dictionary /path/to/lexicon.ipad query 'ni35 xɑʊ̯21'
```

Rust 1.89+ and Python 3 are required. Rust unit tests are in
`engine_android/ipa_graph/tests/`; final-engine tests are in `cli_tests/`.
Synthetic fixtures need no raw corpus or SQLite archive. For Android ARM64:

```sh
rustup target add aarch64-linux-android
python3 engine_android/scripts/build_android_library.py \
  --ndk /path/to/android-ndk --sdk /path/to/Android/sdk \
  --dictionary /path/to/lexicon.ipad --manifest /path/to/lexicon.manifest.json \
  --output-dir data/engine_android/builds/android
```

The Rust API exposes `Engine`, `QueryOptions`, `QueryResult` and `Candidate`.
`Engine::load(path)` is unsafe because it maps an external file: the caller
must keep that file immutable until the last cloned engine is dropped.
The C ABI is in `engine_android/android/src/main/cpp/ipa_graph_rust.h`; owned output strings
must be released by its free function, and calls must finish before closing a
handle. JNI names and `com.ipaengine.graph.GraphEngine` remain unchanged.
Java normalizes input to NFC; direct Rust/C callers provide NFC themselves.

IPADWG01 v2 (24-bit) and v3 (32-bit) remain supported; Android releases use v2.
The entire directory, graph, postings and word pool are validated on loading.
Unix 64-bit targets use read-only mmap; other targets use owned bytes. Loaded
files must remain immutable: replace updates with a new file, never truncate
or overwrite an active mapping. The mmap and C ABI boundaries have documented
unsafe code; parsing and querying use checked slices and owned containers.

Input limits, matching budgets, scoring and timeout behavior remain compatible.
Context scoring and neural reranking are not implemented. The previous C++
runtime and builder have been removed; Android and default CLI builds use Rust.
