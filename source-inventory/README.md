# Selected-source provenance review

## Pinned native selection

The build consumes HeliBoard v4.1 at `9f5bb635c2e8609dcd95dc7506c0c58fba82a52c`. `native-files.json` records each selected file's SHA-256, size, original copyright/license header and immutable source URL. All 82 selected core compilation units and 139 available headers expressly carry Apache-2.0 notices. The directory's one-line `backward/v402/Readme.txt` is retained as descriptive upstream material, is not compiled, and is not assigned an invented file-level license.

Ten upstream JNI source/header files and 21 upstream test units are reviewed separately as references, not build inputs. None of the five old registration compilation units is compiled. The new bridge calls the native core directly; this avoids importing the full application, registration tables, Java utility graph, Android UI, icon, optional proprietary gesture plugin, contacts, personal dictionaries or learning entry points. Retaining upstream dynamic-dictionary implementation inside the core archive is not permission to expose learning: the new API must open the fixed static dictionary read-only and offer no mutation operations. Unused core sections may be removed by the linker; the inventory does not claim every reviewed header is in each final binary.

`excluded-java.json` records the real mixed Apache/GPL headers on BinaryDictionary, DicTraverseSession, ProximityInfo and BinaryDictionaryUtils. A project-maintained API is used instead. Merely deleting SPDX identifiers from those old files would not satisfy this boundary. Existing IPA Board adapters and any private validation derived from them retain their existing terms and are not part of the newly authored native API.

## Existing dictionary, independently recorded

`dictionary/main_en-US.dict` comes directly from the fixed HeliBoard source asset, not from a new dictionary compilation. Its Git blob is identical to OpenBoard v1.4.5 (`6c7582aae8577f2953a597a547924bbea3d832f4`) `app/src/main/res/raw/main_en.dict`: blob `081a8c8c603aaa6b89dab4c8fba4633cc3a89566`, 2,956,881 bytes, SHA-256 `bd950ef4b57655120eee65cee62a5d216a63f721d9a8bb759ce2022437840443`.

The binary format is **202**. The separate dictionary header attribute `version=54` identifies the dataset version, not the binary format. Other recorded attributes include `id=main:en_us`, `locale=en_US`, and `date=1414726260`. The original matching wordlist is retained as `dictionary/en_wordlist.combined.gz` for source/provenance evidence only. It is not rebuilt, normalized or fed to any dictionary compiler.

The pinned Codeberg source declaration points to that OpenBoard wordlist. OpenBoard's `8d46e0e` commit identifies OANC as the bigram source; `206fc18e` produces the final recorded dictionary/wordlist blobs. Both modifications occurred under OpenBoard's GPL-3.0 repository license. No distinct license in the wordlist or dictionary source declaration was found. Preserve the independent uncertainty instead of borrowing Apache-2.0 from the C++ library. See `../licenses/README.md` and the machine-readable dictionary lock.

## Verification

`published-files.json` pins the full published inventory, notices, existing dictionary and corresponding wordlist. Offline checking needs no private cache:

```sh
python3 source-inventory/verify.py
```

To additionally match original files and NDK notices after source preparation:

```sh
python3 source-inventory/verify.py --source-tree .cache/HeliBoard --ndk /path/to/android-ndk-r29
```

These checks verify hashes, native selection, notice/header correspondence, and dictionary metadata. They are not a legal opinion, Android execution result, proof of dictionary recompilation, or claim that IPA Board has finished its project-license transition. This directory documents an independent build; the application's existing English integration remains unchanged in this task.

