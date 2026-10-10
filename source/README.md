# Engine source snapshot

This is a buildable, reduced project snapshot. It contains the Rust engine,
Android Java/JNI wrapper, CLI, engine tests, and the complete compiled 24-bit
runtime dictionary. It excludes collection/preprocessing, dictionary source
data and compiler, GRU training, and release tools. From this directory, Python
3.10+ and Rust 1.89+ are required.

```sh
python3 engine_android/scripts/build_rust_core.py
cargo test --offline --manifest-path engine_android/ipa_graph/Cargo.toml --target-dir data/engine_android/builds/rust-test
python3 cli_tests/scripts/engine_cli.py query 'həˈləʊ' --region en-GB
python3 -m unittest cli_tests.tests.test_engine_cli cli_tests.tests.test_graph_omissions
python3 engine_android/scripts/build_android_library.py --ndk /path/to/android-ndk --sdk /path/to/Android/sdk
```

The dictionary and its provenance manifest are in
`data/dictionary/compiled/`; engine and CLI commands use them by default.
Android packaging requires an installed Android SDK, NDK, JDK and Rust
`aarch64-linux-android` target. Build outputs and test reports go under the
ignored `data/` area. See [engine build notes](engine_android/README.md) and
[Android API](engine_android/android/README.md).

Unprocessed archives, observation databases, collection catalogs, pre-graph
dictionary data, learned weights, private reports, caches and full-project Git
history are excluded. The hand-written test cases and generated tiny dictionaries
are validation fixtures, not corpus exports.

source-manifest.json records each public source file's SHA-256 and the runtime input
digest used by the Android build. Publishing source does not assign a software license;
no project-wide license has been selected. Data provenance notices remain attached
to the included dictionary manifest. Publishing tools are not included.
