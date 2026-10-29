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
cargo test --offline --manifest-path engine_android/ipa_graph/Cargo.toml --target-dir data/engine_android/builds/rust-test
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

## IPA initial abbreviations

Default omission matching prioritizes short inputs containing 2–8 consonant
symbols as possible syllable-initial abbreviations. It recognizes starts at
the beginning of an IPA key and after explicit spaces/dots, skips vowels and
annotations more cheaply during search, and ranks fully onset-aligned matches
by corpus frequency ahead of arbitrary internal/coda matches. Exact inputs
still rank first. This adds no special word mappings or sound substitutions;
all typed symbols must still occur in order, and strict matching disables it.
Unmarked syllable boundaries are not inferred. Search state/deadline budgets
remain in effect, so an abbreviated query can return a truncated candidate set.

With the existing full 24-bit dictionary, `nx` retrieves `你好` among the first
three candidates for `zh-CN`, and among the first five with all regions enabled.
`女孩` and `男孩` share these initials and can rank higher by source frequency.
Explicit `n x` also participates in onset ranking, while its space must match a
real IPA boundary. Full IPA and numeric tones can refine ambiguous abbreviations.

The change is in the shared Rust core and needs no dictionary-format or JNI API
migration. Local validation outputs are under
`data/cli_tests/reports/initial-abbreviations-*.json`; a rebuilt Android library
and local AAR are under `data/engine_android/builds/initial-abbreviations/`.
The AAR was built for ARM64 and verified by building the Android probe; that
build alone does not confirm execution on a physical device.
