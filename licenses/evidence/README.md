# Build-specific dependency evidence

Recorded on 2026-11-11 for Mozc commit
`a4541df29db443b49f81cf54665873b68fde9efa` plus the IPA Board compatibility patch.
See `../../THIRD_PARTY_NOTICES.md` for component provenance and redistribution
notices, and `dependency-audit.json` for exact input/output hashes.

The raw response files come from the completed Bazel native build. The expanded
link commands were obtained with the fixed NDK's `clang -###` preview; no new
link or Bazel build/query was run for the audit. The preview uses persistent
external repository paths because a later build may replace execroot symlinks.
Expanded commands normalize machine paths with `${NDK}`, `${BAZEL_EXECROOT}`,
`${BAZEL_OUTPUT_BASE}` and `${MOZC_ROOT}` placeholders. Raw response files retain
their original workspace-relative paths.

The resolved MODULE files are copied from actual downloaded repositories.
Lock-file registry records can include lower-version requests and are not
presented as the selected module list. License comparisons use actual downloaded
source files. Runtime startup objects and static archives are hashed separately.

This audit conservatively records archive inputs; it does not claim to identify
every retained archive member. It is not a formal SPDX/CycloneDX SBOM and does
not claim runtime or device testing. SHA256SUMS covers these evidence files.
