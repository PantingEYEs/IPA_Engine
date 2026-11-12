# LatinIME source-build validation

This record covers the independent English engine build and an isolated IPA Board
compatibility fixture. It does not claim formal IPA Board integration or completion
of Issue #52. The production Board worktree and installed phone preview were not
modified.

## Tested inputs

- HeliBoard 4.1 source: `9f5bb635c2e8609dcd95dc7506c0c58fba82a52c`.
- Native build identity: `6ddfef54ed2c518f5435994eccb14cb2b19897da8ee826c179c8b124aac03258`.
- IPA Board fixture base: `327b3d30bc798c8f3111c876dce6301834e05510`.
- Unchanged dictionary SHA-256: `bd950ef4b57655120eee65cee62a5d216a63f721d9a8bb759ce2022437840443`.
- The AAR and fixture-source hashes are in `fixture-inputs.json`. Runtime identity
  was compared against the expected artifact identity, not generated from the
  library under test.

## Host and static checks

- Both Android ABI builds succeeded. Their final libraries each export only the
  six new JNI methods; ELF load alignment, offset congruence, RELRO, machine,
  dependencies, build identity and AAR contents passed `scripts/verify_artifacts.py`.
- 21 artifact/provenance tests passed, including mutation rejection.
  Source/notice inventory checks and six independent mutation checks passed.
- The isolated application's 37 selected JVM tests, debug and
  instrumentation builds, asset validation, suite registration and lint passed.
  Lint retained 296 existing warnings and filtered 129 accepted baseline errors;
  no new blocking lint error was accepted.
- Every packaged native library in both fixture APKs passed the ELF audit, and
  both APKs passed `zipalign -c -P 16 -v 4`. APK details and hashes are recorded in
  `apk-16kb-audit.json`. The old LatinIME binary stays in this test APK only as a
  differential oracle; it is absent from the new engine AAR.

## Strict 16KB runtime

Android API 36, x86_64, a dedicated read-only emulator. The compiled application
manifest has `android:pageSizeCompat="disabled"`; the actual instrumentation
process independently reported page size 16384. No 4KB runtime was required or
claimed. ARM64 was compiled and statically checked, not executed on a device.

55 instrumentation tests passed. The latest per-class results include
1 failed and 0 skipped tests.
The six core/coordinator compatibility classes passed all 55 tests.

| Class | Passed | Latest status |
| --- | ---: | --- |
| `LatinImeSourceBuildTest` | 12 | passed |
| `MultilingualEngineTest` | 11 | passed |
| `EngineControlsTest` | 3 | passed |
| `EngineSettingsContractTest` | 4 | passed |
| `DiagnosticsContractTest` | 11 | passed |
| `CompositionControllerTest` | 14 | passed |
| `EnglishImeIntegrationTest` | 0 | failed |

The new core matched the old core's candidate text/score/type sets for 12 synthetic
completion, correction, prediction, BOS and short-context requests. Probability
comparisons also passed, including unknown Unicode words. This is targeted
compatibility evidence, not proof of equivalence for every possible input or a
fixed-candidate-ranking specification.

The new-interface tests additionally covered case/apostrophe behavior, independent
feature toggles, mixed literal contents, dictionary immutability and deployment
repair, standard UTF-8 and supplementary paths, malformed input rejection,
bounded owners, stale handles, failed-open cleanup, repeated contexts and
concurrent query/close behavior. Existing coordinator and diagnostics tests ran
against the new EnglishService adapter. Their synthetic candidate evidence is in
`runtime-evidence.json`; no user's text or phone data is included.

Device work used `python3 tools/regression.py run --suite latinime-upstream
--serial emulator-5582 --no-build`. Backup validation completed before installation;
restoration was verified and backups deleted afterward. Pending backups were empty.
The dedicated emulator was stopped after final verification. The daily phone was
not used.

The first device attempt stopped at JUnit class initialization because a private
Kotlin test method inferred a non-void return type. No engine test body ran in
that attempt. The fixture methods were given explicit Unit return types, rebuilt,
and the protected suite was rerun. That attempt also restored and deleted its
backup; it is not counted as a passing engine run. Attempt results remain in
`runtime-summary.json`.

The original IME test twice timed out waiting for its owned keyboard before any
input action. Only that class was retried; the passing 55 checks were not rerun.
Its private fixture was then updated to use the repository's existing IME setup
synchronization and readiness diagnostics. The focused IME check remained blocked before keyboard input. Its UI input/result assertions are not verified. This is retained as an integration-stage coverage gap; no whole-suite pass is claimed.
The last diagnostic had the test IME selected/enabled and its editor focused,
active, attached and resumed, but no owned keyboard node appeared. The root cause
is not established; this record does not attribute it to the new English core or
claim that an environment fault has been proved.
The core run's exact inputs and APKs are retained separately in
`core-fixture-inputs.json` and `core-apk-16kb-audit.json`; the latest overlay and
test APK are in `fixture-inputs.json` and `apk-16kb-audit.json`.

## Reproduction and remaining scope

Native and AAR builds are reproducible from the public pinned recipe in the root
README. Published artifact/inventory checks work without the build cache or a
sibling engine checkout.

The Android run used a private overlay retained locally under
`D:\IPA_Board\.artifacts\issue52\fixture`, applied to
`/var/tmp/ipa-board-issue52-20261111`. The overlay adapts the existing GPL Board
test host; it is intentionally not redistributed as newly licensed engine code.
`fixture-inputs.json` records the exact overlay hashes and suite. Formal Board
integration and permanent regression coverage are the next stage.

There is no ARM64 runtime, release/AAB runtime, other API or broad-editor coverage
claim. This validation is not a performance benchmark or a project-license verdict.
The separate dictionary licensing boundary remains documented in
`../dictionary-sources.lock.json` and `../licenses/README.md`.
