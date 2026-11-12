# LatinIME component notices

The selected native files come from HeliBoard v4.1, commit `9f5bb635c2e8609dcd95dc7506c0c58fba82a52c`. The 82 compiled core units and 139 available headers each retain their explicit AOSP Apache-2.0 notice. File hashes, original notice headers and source links are in `../source-inventory/native-files.json`. This is a selected-file assessment, not a statement that HeliBoard's complete application uses Apache-2.0.

The new independent bridge, Java API and build/verification scripts retain copyright 2026 PantingEYEs, all rights reserved. No project license has been selected for them. The old HeliBoard/IPA Board Java registration classes with `Apache-2.0 AND GPL-3.0-only` declarations are excluded from this distribution's build; their notices have not been removed or rewritten to obtain that result. New code is developed against the reviewed native API. This is not a claim of a formal legal clean-room process.

The Android library uses NDK 29.0.14206865 with `c++_static`. Original `NOTICE` and `NOTICE.toolchain` files are preserved under `toolchain/`, including applicable libc++, libc++abi and compiler-runtime notices (Apache-2.0 with LLVM exceptions and their retained accompanying terms). These broad upstream collections also mention build tools and components which are not necessarily linked; their inclusion must not be read as an inventory of runtime dependencies. The exact linked inputs and dynamic dependencies belong to the build and ELF records.

The existing English dictionary is independent data, not covered by the native code's Apache grant. It remains byte-for-byte the published OpenBoard/HeliBoard artifact. Its exact format, hash and upstream chain are in `../dictionary-sources.lock.json`. The OpenBoard distribution and the relevant dictionary modifications occurred under a GPL-3.0 repository license; the dictionary/wordlist itself supplies no separate file-level license. Therefore the inventory records `NOASSERTION` for an independent file-level license and retains the GPL text, original source declaration and corresponding wordlist. Do not describe the complete dictionary as Apache-only or GPL-free.

The OANC data publisher permits use and redistribution, including commercial use. OpenBoard's bigram commit names that source, but this does not separately license all processing or the whole English wordlist. Further permission or project-license conclusions remain a separate review.

This independent build does not change IPA Board's existing English adapter, installed application, dictionaries or overall license. Formal application integration and Issue #52 acceptance remain separate work.

