# OpenCC runtime provenance

The Android artifacts are built directly from official OpenCC ver.1.4.2, commit
025f371dc76b598d77384fbdab90c937471844d8. No Trime source, binary, JNI, or build
script is used.

The runtime includes OpenCC (Apache-2.0), its bundled RapidJSON headers (MIT and
the retained upstream bundled notices), Darts-clone (BSD-2-Clause), and
marisa-trie 0.3.1. The BSD-2-Clause option is selected for marisa; its original
BSD-2-Clause OR LGPL-2.1-or-later notice remains intact. Static Android C++
runtime and toolchain notices are retained in their original form. Broad NDK
notices do not imply that every listed toolchain component is linked.

OpenCC's vendored marisa differs from official v0.3.1 in three private source
files. Two changes concern Windows-only APIs; the other zero-initializes the
complete FlatVector allocation. Public headers match the official version.
See ../validation/marisa-source-comparison.json and
../validation/marisa-vendor-differences.patch. Rime should use this same
marisa archive and headers when linking this OpenCC archive.

GoogleTest is used only for host verification and is not linked into the
Android library. The upstream host test build also exercises an optional
Jieba fixture; the Android library build does not enable that plugin or its
dependencies. Host-only commands and generated dictionaries remain local
build work, not shipped Android runtime dependencies.

Exact notice source paths and SHA-256 digests are in sources.json. The
source lock records fingerprints of the actual vendored dependency trees.
The new IPA build scripts and wrapper retain PantingEYEs copyright; no
additional project license has been selected. These records do not decide
IPA Board's future project-wide license, or relicense its dictionaries,
schemas, adapters, or other engines.