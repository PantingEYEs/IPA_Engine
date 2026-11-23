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

IPADWG01 v2 (24-bit) and v3 (32-bit) remain supported. The v0.0.5-dev engine
also supports the 24-bit IPAPACK1 container (external dictionary format 4).
Its directory is read first; each enabled graph and reverse index is mapped
and validated independently. Legacy graphs are validated on loading.
Unix 64-bit targets use read-only mmap; other targets use owned bytes. Loaded
files must remain immutable: replace updates with a new file, never truncate
or overwrite an active mapping. The mmap and C ABI boundaries have documented
unsafe code; parsing and querying use checked slices and owned containers.

Single-graph input limits, scoring and timeout behavior remain compatible.
With multiple graphs, each has the configured omission-state budget and all
share one query deadline (at most 60 ms). Exact matches are probed before
omission searches. Added languages increase work and candidate competition;
the original single-graph measurements do not describe six-language operation.
Context scoring and neural reranking are not implemented. The previous C++
runtime and builder have been removed; Android and default CLI builds use Rust.

## Independent language packs

The combined delivery contains `core` (English/Chinese/Japanese), `ru`, `ar`
and `ko`. Default construction loads and enables all four packs. `PackMode::Off`
unmaps a pack once in-flight snapshots finish; `ReverseOnly` retains reverse
lookup without forward-search auxiliary arrays; `Input` provides both. Region
filtering alone does not release resources. The Arabic pack is an interim
source-default normative reference, with source/variety evidence retained.

```rust,ignore
use ipa_graph::{Engine, PackMode};
// SAFETY: keep the backing inode immutable until every engine clone drops.
let engine = unsafe { Engine::load_with_defaults("lexicon.ipad", false)? };
engine.set_pack_mode("core", PackMode::Input)?;
engine.set_pack_mode("ru", PackMode::ReverseOnly)?;
let readings = engine.reverse("привет")?;
let resources = engine.list_packs();
```

`Engine::add_dictionary(path, load_defaults)` mounts independently delivered
single-pack containers into the same engine, rejecting duplicate IDs/regions.
It has the same unsafe immutable-file contract as loading. Failed mounting or
activation leaves the previous state usable. Updating an installed pack uses
a new engine and new immutable files. At most eight packs and less than
96,000,000 installed bytes are accepted; each graph/reverse slice remains below
40,000,000 bytes. The original core graph remains byte-identical.

Reverse lookup uses a word-to-reading CSR index rather than scanning the full
dictionary. It respects pack modes and region policy, returns at most 512
readings with an explicit `truncated` flag, and requires exact NFC word forms.
Legacy single graphs retain a slower scan fallback. Rust/C callers provide NFC;
Java/Python normalize it. Java/C/CLI entry points and examples are documented in
[the Android interface](android/README.md).

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

## Local quality improvements after v0.0.4

The working core keeps ordinary subsequence recall separate from syllable-onset
alignment, so repeated consonants in a coda cannot steal an abbreviation's next
onset match. It does not infer unmarked boundaries or increase the state budget.
Strict prefix timeouts retain already completed candidates and set `truncated`;
the strict timeout status remains unchanged. These changes preserve the JNI and
24/32-bit dictionary formats. They have desktop regression and JNI verification;
no new Android device or power measurements are claimed. Full local evidence is
in `cli_tests/docs/评估与升级-2026-11.md` and `data/cli_tests/reports/`.

The next local iteration adds per-input ordered suffix-feasibility pruning for
non-exact inputs with at least four expanded symbols and sufficient budgets.
A bounded annotation-only probe keeps common missing-annotation inputs on the
cheap path. Analysis uses at most a quarter of the remaining deadline; unknown
analysis falls back to ordinary pruning. The current full dictionary needs about
1.09 MiB extra temporary storage per active analyzed query, with unchanged
32-byte heap states and JNI/file formats. This improves severe omission recall
while increasing some native p95 times; it has no new phone/power qualification.
Local evidence: `cli_tests/docs/顺序可达性升级-2026-11.md`.
