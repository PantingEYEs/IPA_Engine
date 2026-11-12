# IPA Board integration

The native bundle exposes IPA Board's existing `MozcJNI` class entry. Two small host changes are still required. The optional [adapter patch](ipa-board-adapter.patch) records them against the source based on IPA Board commit `1c091b8f7cb205839f1271837e8f0dddf9f74159`. It was not applied to the active IPA Board development branch or the phone.

1. Deploy this bundle's `mozc.data` into a new generation directory (`mozc-26d8d5391e37`, based on its SHA-256). The old adapter only copies data when the file is missing, so reusing `mozc-v0.1.2` could load the old dataset into the new library.
2. In `MozcEngine.predict()` only, set both request fields: `zero_query_suggestion = true` (field 1) and `mixed_conversion = true` (field 2). With the adapter's existing wire helpers, the request payload becomes `m(9, n(1, 1) + n(2, 1))`. Leave normal conversion queries and the incognito / `NO_HISTORY` configuration unchanged.

The second change is required by the current upstream predictor dispatch. The old `ddd9730...` engine invoked zero-query decoding independently of the mixed-conversion flag; the new pinned engine invokes that decoder in its mixed-conversion branch. An actual controlled Android comparison, using the same final library and dataset, returned no continuation with the old request and restored `ございます` with field 2 enabled. No phrase-specific replacement or prediction-algorithm patch is involved.

The patch contains only these two source changes. Integration also requires copying both final ABI libraries and the paired dataset, updating IPA Board's resource hashes and provenance, and carrying the supplied third-party notices. Check the complete [validation record](../VALIDATION.md) before using the files. The native source compatibility patch under `../patches/` and this host integration patch serve different purposes.

Reference implementations are fixed to the source versions examined:

- [New predictor dispatch](https://github.com/google/mozc/blob/a4541df29db443b49f81cf54665873b68fde9efa/src/prediction/dictionary_predictor.cc)
- [Official mobile request setup](https://github.com/google/mozc/blob/a4541df29db443b49f81cf54665873b68fde9efa/src/request/request_test_util.cc)
- [Old zero-query dispatch](https://github.com/google/mozc/blob/ddd9730b068387631e3b4d212314ef0ed93befe0/src/prediction/dictionary_prediction_aggregator.cc)
