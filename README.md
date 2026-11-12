# IPA Engine: Chinese runtime build

This independent `chinese` branch contains the official-source builds, adaptation interface, compatibility resources, license records, and Android artifacts for IPA Board's Chinese engine.

- **[OpenCC](OpenCC/README.md):** OpenCC 1.4.2, with independently usable shared libraries and static development prefixes for `arm64-v8a` and `x86_64`.
- **[Rime](Rime/README.md):** librime 1.17.0 with a narrow Java/JNI interface, statically linked OpenCC, and the existing Luna Pinyin, Terra Pinyin, and Bopomofo resource profiles.

The branch starts with a root commit. It does not contain the histories or files of the repository's other engine branches. Binary artifacts are deliberately checked in for this branch at the owner's request. Build caches, upstream working checkouts, private application validation copies, and device backups are excluded.

New project-authored adapters and build scripts are copyright 2026 PantingEYEs, with all rights reserved; no project license has been selected. Third-party code and data retain their respective licenses. See [NOTICE](NOTICE.md), [Rime license inventory](Rime/licenses/README.md), and [OpenCC license inventory](OpenCC/licenses/README.md).

This is a build and handoff branch. IPA Board's production checkout is not migrated by creating it. The application integration, application-level notices, and any overall project license decision remain separate work.
