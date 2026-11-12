# License source files

See `../THIRD_PARTY_NOTICES.md` for scope, data provenance and redistribution
requirements. `sources.json` contains the original URLs, versions,
transformation notes and SHA-256 checksums of the source materials.

- `Mozc-LICENSE.txt` is a byte-for-byte copy of the pinned upstream root license.
- `Mozc-engine-and-data-NOTICES.txt` retains selected upstream credit blocks,
  including the full IPAdic/ICOT text and Japan Post statements.
- Versioned dependency files are downloaded unchanged from their upstream tags.
- Both NDK notice files and `reference/ndk-r29-source.properties` are copied
  unchanged from Android NDK r29 (`29.0.14206865`). The notice files cover the
  broader NDK package; they do not identify the final set of linked components.
- `reference/` preserves the official cross-platform credits and dictionary
  README. Their original formatting and historical statements are unchanged.
  In particular, the README's exclusion of postal data conflicts with the
  current build rules, as explained in the root notice document.

This directory includes a completed build-specific input and license audit in
`evidence/`. It is not a formal SPDX or CycloneDX SBOM. The audit records both
unstripped link evidence and final stripped release file hashes.
No license is assigned here to the repository's new build scripts.
