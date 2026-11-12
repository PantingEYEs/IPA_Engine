# Mozc build and validation

Built on 2026-11-11 from Google Mozc `a4541df29db443b49f81cf54665873b68fde9efa`, with the recorded compatibility patch (23 added lines). The patch adds the existing IPA Board JNI entry and sets Android API 26 as the link baseline. It does not rewrite conversion or prediction behavior.

## Published artifact identity

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| arm64-v8a/libmozc.so | 5,478,432 | `d7cc97611b162e1577dc419811bc4a39b72a9af0be4f380b68597519e64d9b12` |
| x86_64/libmozc.so | 5,642,432 | `04831587ad807ba1e26144ae8fb34c67d40e6f9193294500f680a1be7616b3ac` |
| mozc.data | 18,955,930 | `26d8d5391e37dba926df23e6f01e90158dd755af111712ce84f065a700a852b5` |

These files form one tested bundle. The machine-readable static results are in [artifacts/manifest.json](artifacts/manifest.json). Its `runtime_validation_included: false` describes the scope of the static verifier; separate runtime results are recorded below.

## Build and host checks

- Linux x86_64 (Arch under WSL), Python 3.12.7, host GCC 14.2.1, Bazel 9.0.2, Android NDK r29.
- Both Android JNI targets and the OSS dataset built successfully. The final build used the source-lock checker and only the pinned workspace Bazel configuration.
- All 25 build/ELF-verifier unit tests passed. They cover source revision and patch drift, ABI/ELF bounds, all load segments, JNI visibility, dependency rejection, and failure/interruption during artifact publication.
- Every `PT_LOAD` segment in both final libraries has `p_align = 16384` and valid offset/address congruence. Both old `MozcJNI` and official `MozcJni` initialization symbols are exported.
- Both libraries require only Android system `liblog.so`, `libc.so`, `libm.so`, and `libdl.so`. The C++ runtime is statically linked; no additional `libc++_shared.so` is required.
- The fixed NDK's `llvm-strip --strip-unneeded` reduced the two libraries from 31,568,568 to 11,120,864 bytes. Program headers and dynamic symbols are unchanged. Loadable bytes differ only in normal ELF section-table metadata in the file header; code and loaded data are unchanged. Unstripped diagnostic copies remain local in `.work/symbols/`.
- Actual link inputs, dictionary inputs, dependency versions, license texts and their hashes were audited. See [dependency-audit.json](licenses/evidence/dependency-audit.json) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

- The isolated application passed all 3 `ProtoWireTest` cases, debug application/test APK builds, and `lintDebug`. Lint reported 299 warnings and filtered 129 existing baseline errors; this is not a claim of a warning-free codebase.
- Both isolated APKs passed `zipalign -c -P 16 -v 4`. An independent scan covered every packaged `.so` (15 application files and 1 test asset), checked all load segments and RELRO layout, and found no remaining 4KB-aligned native file. The compiled manifest's page-size compatibility setting was verified as disabled.

## Runtime validation

All 6 original Mozc integration checks passed on the dedicated API 36 x86_64 16KB emulator, with compatibility mode disabled. Both the device and application process reported a page size of 16384; the loaded OSS dataset reported version `24.11.oss`. The protected regression verified restoration, deleted its backup, and left no pending recovery.

The covered behaviors are real dictionary loading and page size; lowercase/uppercase romaji conversion; native prefix segmentation and remaining composition; digits/punctuation and literal arithmetic; zero-query continuation with transient context; and independent conversion/prediction switches through the real Mozc service boundary.

There was one integration difference during validation: the first run passed 4/6 and failed the two zero-query checks. A controlled request comparison then confirmed that the old request returned no continuation, while the same final library/data returned `ございます` when `Request.mixed_conversion` (field 2) was enabled alongside `zero_query_suggestion`. The final 6/6 run used only that additional prediction-session flag. Incognito and `NO_HISTORY` stayed enabled; no candidate assertion was removed, and no native algorithm or dictionary was patched for the test. Each run completed protected restoration and backup deletion.

The two required host changes are recorded in [integration/README.md](integration/README.md), with an optional two-line adapter patch. Runtime results apply to the adapter with this explicit flag and the new data generation directory, not to an unchanged host that continues sending the old request. The sanitized [runtime record](validation/runtime.json) identifies the exact artifacts and tested APKs.

The test uses an isolated IPA Board source copy based on `1c091b8f7cb205839f1271837e8f0dddf9f74159` plus the existing Issue #35 working tree, with only task-local resource replacement and test-environment changes. The normal IPA Board workspace and the user's phone are not used for this regression.

The copy uses a new Mozc data deployment directory, disables Android's page-size compatibility mode, and excludes the unrelated old ONNX Extensions libraries. This isolates Mozc compatibility; it does not certify the unchanged full IPA Board APK for 16KB devices. Temporary copied application/test sources are kept outside this published branch.

## Limits

The API 26 link baseline is not a runtime test on API 26. An x86_64 16KB emulator result does not prove arm64 device behavior. Full IPA Board regression, other engines, phone installation, performance benchmarks, and byte-identical rebuilds on different host toolchains are outside this validation.
