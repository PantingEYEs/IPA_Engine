# CLI and final-engine tests

This phase contains the development-host command-line interface and tests that
validate the finished engine after build, before or during runtime evaluation.
It does not own dictionary compilation tests, Rust unit tests, Android JNI
wrapper tests, data-pipeline tests, GRU tests, or release workflow tests; those
remain with their respective phases.

## Contents

- `scripts/engine_cli.py`: query, REPL, reverse lookup, and case runner.
- `scripts/compiled_lexicon.py`: independent legacy single-graph reader for
  offline inspection. Runtime reverse lookup uses the Rust core.
- `tests/`: host CLI behavior and omission-matching tests.
- `tests/fixtures/packed_dictionary.py`: small independent binary dictionary
  fixture builder shared by CLI, JNI, and release tests.
- `cases/graph_cases.jsonl`: small, hand-maintained regression cases.

Test data and reports generated from full local datasets belong in the shared
`data/cli_tests/` area. Test cases under `cases/` are small checked-in fixtures.
The reduced public `source/` snapshot excludes the runtime dictionary,
source-observation database and its sampling tools; download the dictionary
and manifest from a GitHub Release before querying. Its case commands
use the checked-in regression fixtures or a caller-provided JSONL file.

Run commands from the repository root:

```sh
python3 cli_tests/scripts/engine_cli.py query 'həˈloʊ' --region en-US
python3 cli_tests/scripts/engine_cli.py test
python3 -m unittest cli_tests.tests.test_engine_cli \
  cli_tests.tests.test_graph_omissions
```

The CLI lazily builds the same Rust executable used by Android. It does not
load the SQLite observation database for queries or reverse lookup. Both paths
run in the selected native engine and honor its pack modes. Rust, compiler, JNI, and Android
component tests are documented and run within their owning directories.
Standalone 24/32-bit comparison and on-device benchmark utilities are not
included: the reduced source snapshot does not provide a 32-bit dictionary or
the generated Android CLI/device prerequisites.

The omission suite also checks syllable-initial abbreviations (`nx`, `n x`),
ranking against high-frequency coda matches, strict-mode behavior, wrong-order
rejection and tiny search budgets. The full-dictionary case list includes `nx`
with `zh-CN` enabled and requires `你好` in the first five returned candidates.

## Quality evaluation

Full local project only: `cli_tests/docs/引擎评估.md` defines reproducible
scores; `cli_tests/docs/评估与升级-2026-11.md` records measured gains,
regressions, corpus gaps and rejected experiments. These tools, reports and
source-derived observation cases are not included in the reduced public snapshot. Source-derived stress cases
are not independent language coverage or contextual accuracy.

```sh
python3 cli_tests/scripts/evaluate_engine.py --per-region 100 \
  --output data/cli_tests/reports/quality.json
python3 cli_tests/scripts/evaluate_vocabulary.py \
  --output data/cli_tests/reports/vocabulary-probe.json
```

The vocabulary probe uses a small handwritten checklist and verifies postings
in the requested language, not merely global word-pool presence. Its score
applies only to that checklist and does not validate the IPA readings.

The second local iteration is recorded in `cli_tests/docs/顺序可达性升级-2026-11.md`.
The evaluator's `--all-regions` flag enables all five regions for every query,
while per-region report labels still identify each target observation's origin.

## Language packs

Use `--dictionary` before the command to select the combined multilingual file:

```sh
python3 cli_tests/scripts/engine_cli.py --dictionary data/dictionary/compiled/lexicon-multilingual.ipad packs --json
python3 cli_tests/scripts/engine_cli.py --dictionary data/dictionary/compiled/lexicon-multilingual.ipad query 'kot' --region ru-RU
python3 cli_tests/scripts/engine_cli.py --dictionary data/dictionary/compiled/lexicon-multilingual.ipad reverse '한국' --json
python3 cli_tests/scripts/engine_cli.py --dictionary data/dictionary/compiled/lexicon-multilingual.ipad repl --no-default-packs --pack-mode core:input --pack-mode ru:reverse
```

The combined container defaults to `core`, `ru`, `ar` and `ko` loaded in input
mode. `--no-default-packs` opens just the directory; repeated `--pack-mode
ID:off|reverse|input` selects explicit modes. Region choices extend the original
five with `ru-RU`, `ar-001`, `ko-KR`. Arabic is the source-default normative
reference layer; its label does not certify a pure MSA corpus.

For future separate delivery, repeated `--extra-dictionary PATH` attaches
additional containers with distinct pack IDs/regions. It honors the same
`--no-default-packs` choice; enable attached packs with `--pack-mode` as needed.
Duplicate packs fail instead of replacing a live mapping. Each independent
file must be a pack container retaining its language identity, rather than an
unlabelled legacy inner graph. The combined delivery already includes all
four packs, so attaching one of those packs again is an error.

The interactive commands are `packs`, `pack ru off`, `pack ru reverse`, and
`pack ru input`. `reverse WORD` queries the current native session, so pack
switches take effect immediately. `test` still samples a regression case and
shows its settings, input, output and expectation check. An absent word emits
no reverse-lookup row. `ipa INPUT` forces forward lookup when a word and IPA
spelling could be confused.

The native REPL retains one JSON response per line. Tooling can send bare IPA,
`:query\tIPA`, `:reverse\tWORD`, `:packs`, or `:pack\tID\tMODE` (`\t` means a
literal tab). Invalid control commands return an error object while keeping
the session open. Benchmark/evaluation callers sending plain IPA need no change.
