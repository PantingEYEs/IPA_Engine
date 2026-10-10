# CLI and final-engine tests

This phase contains the development-host command-line interface and tests that
validate the finished engine after build, before or during runtime evaluation.
It does not own dictionary compilation tests, Rust unit tests, Android JNI
wrapper tests, data-pipeline tests, GRU tests, or release workflow tests; those
remain with their respective phases.

## Contents

- `scripts/engine_cli.py`: query, REPL, reverse lookup, case runner, and
  dictionary comparison entry points.
- `scripts/compiled_lexicon.py`: independent read-only dictionary reader used
  for desktop reverse lookup.
- `scripts/compare_dictionary_formats.py`: independent packed-format oracle and
  host comparison/benchmark for the Rust CLI across 24/32-bit dictionaries.
- `scripts/test_android_device.py`: on-device tests for the standalone native
  CLI executable.
- `tests/`: host CLI behavior and omission-matching tests.
- `tests/fixtures/packed_dictionary.py`: small independent binary dictionary
  fixture builder shared by CLI, JNI, and release tests.
- `cases/graph_cases.jsonl`: small, hand-maintained regression cases.

Test data and reports generated from full local datasets belong in the shared
`data/cli_tests/` area. Test cases under `cases/` are small checked-in fixtures.
The reduced public `source/` snapshot includes the compiled runtime dictionary,
but not the source-observation database or its sampling tools; its case commands
use the checked-in regression fixtures or a caller-provided JSONL file.

Run commands from the repository root:

```sh
python3 cli_tests/scripts/engine_cli.py query 'həˈloʊ' --region en-US
python3 cli_tests/scripts/engine_cli.py test
python3 -m unittest cli_tests.tests.test_engine_cli \
  cli_tests.tests.test_graph_omissions
python3 cli_tests/scripts/compare_dictionary_formats.py --help
```

The CLI lazily builds the same Rust executable used by Android. It does not
load the SQLite observation database for queries; reverse lookup uses the
selected compiled dictionary directly. Rust, compiler, JNI, and Android
component tests are documented and run within their owning directories.
