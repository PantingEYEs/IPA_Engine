# IPA Board E5 runtime compatibility validation

The maintainer targets 16KB-page devices only. 4KB support is not a product requirement or acceptance gate. The completed 4KB records below are historical migration comparisons; do not repeat 4KB runs by default. Final acceptance uses the strict 16KB run.


These checks use an isolated IPA Board checkout at commit `a2e3bc84cc9471831e0dd049c570edd8f4d96d1f`. They do not integrate the rebuilt library into the primary IPA Board workspace or modify the phone.

## Recorded original-library baseline

- Original Extensions AAR: version 0.13.0, SHA-256 `cb98c6fbeac1a9707228c4b76cfbb395b4dc4d310900aefaa672561d17fd6d9d`.
- Main ONNX Runtime AAR: version 1.23.2, SHA-256 `82048d1f462218adae4ba76477089ab0ba76093d84f733540066db1a8ba6b827`; unchanged by this work.
- Actual baseline process: API 35, x86_64, 64-bit, **4096-byte** memory pages.
- Focused host unit tests: **34 passed**, seven classes; APK builds, lint, suite validation and engine resource checks passed. Lint retained 298 existing warnings and filtered 129 existing errors through the project's baseline.
- New runtime fixture: **8 passed**; original tokenizer outputs for **17** synthetic cases and **22** actual E5 score values captured.
- Existing semantic integration: **4 passed** after the isolated x86 prerequisite adjustment described below.
- Every executed regression run restored and verified the original application state and deleted its backup.

`baseline-runtime-results.json` summarizes the evidence. `onnx-tokenizer-baseline.json` and `onnx-e5-baseline.json` contain actual captured outputs, not fabricated token IDs or expected rankings. They use only fixed synthetic text. Initial and focused-rerun reports remain separate so failed attempts are not silently erased.

The first combined run passed all eight runtime checks and two of four existing integration checks. Two integration checks timed out waiting for every enabled native engine to become ready: their default setup enabled the ARM64-only IPA engine in an x86_64 process. The minimal `fixtures/semantic-x86-fixture.patch` disables only that unsupported backend for this test class on non-ARM64 processes and restores its previous preference after each test. Rime, LatinIME, Mozc, timeouts and existing assertions are unchanged. All four integration checks then passed. This is an isolated test-platform prerequisite adjustment, not an engine fix or a production change.

## Historical rebuilt-AAR comparison on 4KB Android

The final rebuilt AAR SHA-256 is `20620659f2695dc5e36cca7f339e64c606f36413b9a42c144eac0aace73cecc6`. On the same actual API 35 x86_64 / 4096-byte-page process:

- **12/12** focused runtime/integration tests passed in one protected run.
- All **17** tokenizer cases exactly matched the original AAR, token for token.
- All **22** E5 scores matched within `0.0001`; the largest measured absolute difference was **3.78955841706663e-09**.
- The complete unchanged 118,346,824-byte E5 model loaded and ran successfully, including English, Chinese and Japanese following-context probes.
- The rebuilt public JNI registration method resolved successfully.
- Original APKs and private app data were restored and verified, the backup was deleted, and the pending-recovery list was empty.
- Focused host checks passed again: 34 unit tests, both APKs, suite/resource validation and lint. Lint reported 295 warnings with the same 129 existing baseline-filtered errors.

`compare-4k-runtime-results.json` includes exact application/test APK hashes and the numerical comparison results. The companion tokenizer/probe/environment JSON files preserve the observed synthetic outputs. The strict 16KB acceptance run is recorded below; the 4KB run is retained only as historical comparison.
## Strict 16KB acceptance result

The same final AAR passed **12/12** tests in an actual **API 36, x86_64, 16384-byte-page** process. The isolated application manifest set `android:pageSizeCompat="disabled"`; the compiled APK manifest was checked for that setting. All 17 token sequences matched exactly, all 22 E5 scores stayed within `0.0001`, and the maximum observed absolute difference was `3.78955841706663e-09`.

Both debug APKs passed `zipalign -c -P 16 -v 4`. All **20** native entries across the two APKs passed ELF LOAD/RELRO inspection; no unrelated native library was excluded for this run. The complete original E5 model, the corrected JNI method and real multilingual semantic integration executed successfully. Backup restoration was verified, backups were deleted and pending recovery was empty. See `compare-16k-runtime-results.json`, `strict16-environment.json`, and `apk-16kb/apk-16kb-audit.json`.

## What the fixture verifies

`fixtures/OnnxExtensionsBuildCompatibilityTest.kt` contains eight tests:

1. Real process page size and 64-bit architecture, pinned semantic assets, and the rebuilt public JNI registration entry point. The unused original JNI method has a mismatched exported name, so that assertion runs only for the rebuilt AAR.
2. Exact old/new token equality for 17 multilingual and Unicode samples: empty/prefix-only text, English, Chinese, Japanese, composed/decomposed accents, fullwidth text, IPA, supplementary/ZWJ emoji, whitespace, mixed scripts, punctuation, format characters and long input. Repeat tokenization must be deterministic.
3. The real tokenizer graph requires custom-operator registration; the same graph runs through the registered Extensions library in the positive test.
4. Real quantized E5 inference for English/Chinese context and following-context identity in English, Chinese and Japanese. Compare mode checks **every one of the 22 captured scores** within an explicitly bounded tolerance and reports the largest observed difference.
5. Immediate and mid-candidate cancellation returns no partial result; subsequent cached inference remains correct.
6. Empty context returns neutral scores; long preceding text does not discard following-context influence.
7. Rejected semantic-service binding preserves the complete native candidate set and reports the unavailable state.
8. Test-owned deployment obstruction and malformed model fail at the expected stages; a valid tokenizer initializes before malformed model parsing fails, and healthy E5 inference succeeds afterward. Synthetic resources are isolated and removed in `finally`.

The existing four `SemanticContextIntegrationTest` methods additionally cover opt-out without loading E5, real service IPC and multilingual candidate preservation, live disable/stale-result rejection, and both-sided editor snapshots. No production test hook is needed.

## Reproducing 16KB acceptance

Use a separate Git checkout of the pinned IPA Board commit. Set `IPA_TEST_CHECKOUT` to that copy and `ONNX_WORKSPACE` to this ranking checkout. `prepare_fixture.py` refuses the original `D:\IPA_Board` / `/mnt/d/IPA_Board` workspace. It stages validation files, generates test-owned malformed-model assets, applies the platform fixture patch, updates only the replacement AAR file hash/size in the isolated resource lock after checking the reviewed artifact manifest, and registers the new class in the required primary/full suite listings as well as focused suites.

Historical baseline-capture command (not required for subsequent acceptance; reuse the committed synthetic baseline):

```sh
python3 "$ONNX_WORKSPACE/validation/prepare_fixture.py" \
  --checkout "$IPA_TEST_CHECKOUT" --mode capture --page-size 4096
```

For the rebuilt AAR, first copy `artifacts/onnxruntime-extensions-android-0.13.0-ipa-e5-16kb.aar` to the isolated copy's existing `app/libs/onnxruntime-extensions-android-0.13.0.aar`. Keep the ONNX Runtime main AAR, E5 model, tokenizer graph, centering data and production semantic code unchanged. Then prepare compare mode:

```sh
python3 "$ONNX_WORKSPACE/validation/prepare_fixture.py" \
  --checkout "$IPA_TEST_CHECKOUT" --mode compare --page-size 16384 \
  --score-absolute-tolerance 0.0001
```

For strict acceptance, also add `android:pageSizeCompat="disabled"` to the isolated target application manifest before building, and verify the compiled setting with `aapt2 dump xmltree`. Use a real 16KB Android process; `0.0001` is the recorded score tolerance. The fixture refuses tolerances above `0.001`. Token IDs must match exactly at every page size. If executing an ARM64 process, explicitly set `--process-architecture arm64-v8a`.

With the project's supported JDK, Android SDK and cached dependencies, run host checks first from the isolated copy:

```sh
python3 -B tools/regression.py validate
python3 -B tools/verify_engine_assets.py
bash ./gradlew testDebugUnitTest \
  --tests '*CandidateRankerTest' --tests '*MixedCompositionCandidatesTest' \
  --tests '*EngineQueryPolicyTest' --tests '*SegmentedCandidatesTest' \
  --tests '*EngineRuntimeStatesTest' --tests '*EnglishContextBoundaryTest' \
  --tests '*EnglishContextTest' \
  assembleDebug assembleDebugAndroidTest lintDebug --offline
```

Use the existing protected regression runner and an explicitly selected dedicated emulator. Resolve any pending recovery first; never bypass the runner with direct installation or instrumentation:

```sh
python3 -B tools/regression.py pending
python3 -B tools/regression.py run --suite onnx-extensions \
  --serial YOUR_DEDICATED_EMULATOR --no-build
python3 -B tools/regression.py pending
```

The complete focused suite has **12 tests**: eight runtime-fixture tests and four existing integration tests. Focused suites `onnx-extension-runtime` and `onnx-semantic-integration` also exist for justified retries. Each run still performs full application backup, restore verification and backup deletion.

The fixture emits Base64 UTF-8 JSON instrumentation status fields `onnx_tokenizer_baseline_b64`, `onnx_e5_probe_b64`, and `onnx_runtime_environment_b64`. Protected regression output preserves these fields through rollback. For a new original-library baseline, decode the first two fields into the baseline JSON filenames above; compare mode requires those assets and validates their format/model/tokenizer identity. Never pull or publish private application data as test evidence.

## Scope limits

These checks exercise IPA Board's current XLM-R SentencePiece tokenizer and E5 inference path, JNI loading, real service integration and existing semantic policies. They do not certify every optional Extensions operator, every Android/ABI combination, release signing, memory-pressure behavior or arbitrary editor applications. ARM64 ELF inspection is not an executed ARM64 device test. Static ELF PT_LOAD/RELRO inspection and APK zip alignment are separate evidence; successful inference in a process reporting 16384-byte pages is required for an executed 16KB runtime claim.