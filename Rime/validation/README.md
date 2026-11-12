# Validation scope

Final result: **37 selected JVM tests and 44 Android tests passed** (15 direct-interface cases and 29 existing application compatibility cases). Both successful Android transactions restored the original application state and removed their backups. Builds, lint, all packaged ELF layouts, and both APK ZIP layouts passed.

The application baseline is IPA Board commit `51072bf3599ea680a50debed527efa02912f96e1`. Integration checks use a separate disposable checkout. Production IPA Board files and the daily-use phone are not changed by this work.

The private checkout replaces the Rime adapter with the new `RimeNative` interface and deploys this branch's exact compatibility resources. Its APK excludes `librime_jni.so`, `libipa_rime_batch.so`, and `libjnidispatch.so`. The new native library bytes inside the APK are compared to both delivered ABI files. Legacy test-only Java/JNA declarations remain available to compile the existing test tree, but their old native libraries are absent and the legacy pointer-inspection tests are not executed.

## Recorded checks

- `artifacts.json`: native layout, exact six-symbol JNI surface, platform-only dependencies, AAR contents, build identity, resource and notice fingerprints.
- `artifact-tests.json`: positive and negative artifact verifier cases, including stale per-ABI native identities and stale Java classes.
- `build-provenance-tests.json`: source/recipe binding and rejection of replaced native inputs, including portable verification without a build cache.
- `resources.json`: official-source resource rebuilding, original-byte comparisons, missing/corrupt input rejection, and offline verification.
- `application-host-tests.json`: selected input encoding, packet validation, segmentation, policy, and candidate-ranking JVM tests; application/test APK builds and lint.
- `apk-16kb-audit.json`: every packaged native file and both APKs' ZIP alignment, without an alignment exemption list.
- `chinese-source-results.json` and `runtime-evidence.json`: direct JNI behavior, actual page size and build identity, Pinyin/Terra/Bopomofo, Unicode conversion, partial ranges, quote prefixes, limits, failure recovery, owner lifecycle, no-learning policy, and concurrent callers.
- `chinese-compat-results.json`: IPA Board's existing multilingual service, Zhuyin, and segmented-composition test classes against the replacement runtime.
- `validation-inputs.json`: exact private fixture/adapter hashes and the runtime/platform boundaries.

Device checks must use IPA Board's `tools/regression.py run` backup/restore transaction. The recorded successful runs must also show verified restoration and deletion of the backup. The manifest sets `android:pageSizeCompat="disabled"`, and the runtime fixture requires `sysconf(_SC_PAGESIZE) == 16384`.

The first device attempt exposed a fixture setup defect: instrumentation runs with the target application's UID, so its owned temporary directory must use `targetContext.cacheDir`, while test assets are still read from the instrumentation package. All 15 cases stopped before native assertions on that attempt; rollback succeeded. The fixture was corrected and the test APK rebuilt before the subsequent runs. This was not counted as an engine compatibility pass or failure.

The next run passed 14 of 15 cases. Its remaining assertion incorrectly equated an invalid leading Unicode tone with raw ASCII supplied directly to JNI. The official schema retained `6su` as a literal `6` prefix plus Chinese candidates, with exact coverage. The fixture was corrected to require preservation of that literal and its range, while retaining the Unicode encoder rejection and the existing application-level invalid-tone test. No native implementation change was required.

## Limits

Android execution coverage is **API 36 / x86_64 / strict 16 KiB**. ARM64 is built and statically validated, but is not run on a device in this task. OpenCC source was exercised through its host C API build. The standalone Android shared libraries received static checks; Android functional coverage here uses OpenCC statically integrated into Rime. No Android 4 KiB device testing, full IPA Board regression, release/AAB validation, or broad editor/device matrix is claimed.

The private application adapter is derived from IPA Board's existing source. It is intentionally not redistributed as part of this independent source-build branch. These records establish the tested native handoff; permanent application migration and its public regression changes remain application-repository work.

Run the portable artifact checks from the repository root:

```sh
python3 OpenCC/scripts/verify_artifacts.py
python3 Rime/scripts/verify_artifacts.py
python3 -m unittest discover -s OpenCC/tests -p 'test_*.py' -v
python3 -m unittest discover -s Rime/tests -p 'test_*.py' -v
```

Upstream host OpenCC test reproduction is documented in `../../OpenCC/README.md`.
