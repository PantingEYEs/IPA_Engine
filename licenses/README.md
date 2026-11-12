# License sources and build inventory

This directory preserves the notices for the source-built ONNX Runtime Extensions
0.13.0 Android package. The selected operator profile enables SentencePiece
encoding and decoding only. It uses the upstream Java wrapper, ONNX Runtime
1.23.2 API headers, and Android NDK r29 with the static C++ runtime.

| Component | Pinned source | Role in the selected build | Notice |
| --- | --- | --- | --- |
| ONNX Runtime Extensions | v0.13.0, `9d5638b0b1f28ed2ca9ef589d5ec3ea86f859432` | Custom operators, JNI bridge and Java wrapper | [MIT](ONNX-Runtime-Extensions-0.13.0-MIT.txt) |
| SentencePiece | v0.1.96, `d8711f55d9b2cb9c77a00adcc18108482b29b675` | Tokenization, model loading and normalization | [Apache-2.0](SentencePiece-0.1.96-LICENSE.txt) |
| SentencePiece bundled Abseil subset | The same SentencePiece commit; not a separate modern Abseil release | String/flag source and helper headers | [Apache-2.0](SentencePiece-0.1.96-bundled-Abseil-LICENSE.txt) |
| Darts-clone | Bundled in the same SentencePiece commit | Header-based double-array trie used by model lookup and normalization | [BSD-3-Clause](SentencePiece-0.1.96-Darts-clone-LICENSE.txt) |
| Protocol Buffers lite runtime | v21.12, `f0dc78d7e6e331b8c6bb2d5283e06aa26883ca7c` | Static protobuf-lite and generated SentencePiece message support | [BSD-3-Clause](Protobuf-21.12-LICENSE.txt) |
| Microsoft GSL | v4.0.0, `a3534567187d2edc428efd3f13466ff75fe5805c` | Header-only utilities | [MIT](GSL-4.0.0-LICENSE.txt) |
| ONNX Runtime API headers | v1.23.2, extracted from the pinned official Android AAR | Public C/C++ API headers | [MIT](ONNX-Runtime-1.23.2-MIT.txt), plus the [retained float16 conversion notice](ONNX-Runtime-1.23.2-Float16-Notice.txt) |
| Android NDK | r29, `29.0.14206865` | Static C++ and compiler runtime; Android platform headers/startup objects | [Package notices](NDK-r29-NOTICE.txt), [toolchain/runtime notices](NDK-r29-NOTICE.toolchain.txt) |

`ONNX-Runtime-Extensions-0.13.0-ThirdPartyNotices.txt` is retained byte-for-byte
as an upstream reference, not as the build-specific dependency inventory. It
includes disabled operators and labels GSL differently from the actual v4.0.0
CMake dependency. Upstream cgmanifest.json also lists protobuf v3.20.3 while the
executed SentencePiece dependency rules use v21.12. The resolved source and link
evidence take precedence for this build.

The enabled SentencePiece inference sources use bundled Abseil and Darts-clone.
The SentencePiece bundled protobuf-lite implementation is disabled in favor of
the separately pinned protobuf v21.12 library. esaxx is referenced by the trainer
source, which is not part of the requested inference library target. Disabled
Extensions operators do not require their additional libraries such as OpenCV,
BlingFire, RE2, DLIB, or nlohmann/json.

The NDK notice files are byte-for-byte copies from the actual build NDK. They
cover a broader package, including tools that are not redistributed. Their
presence does not mean that every component in those notices is linked into the
produced libraries. The LLVM runtime notices include Apache-2.0 with LLVM
exceptions and retained legacy license text. The final evidence identifies the
runtime archives that actually contributed to this build.

The ORT header MIT license was downloaded unchanged from the official v1.23.2
tag. The float16 notice preserves comment lines from the actual AAR header,
including the Fabian Giesen attribution and permissive terms. All eleven
redistributed API headers also retain their original comments. The full ONNX
Runtime inference library is external to this package and is not rebuilt or
redistributed here. E5 model weights, tokenizer models and their data licenses
are also outside this artifact package.

`sources.json` records each copied source document and SHA-256 checksum.
The completed [build audit](evidence/dependency-audit.json) records both ABI
variants and all four final native library hashes. Each ABI compiled 57 source
files and included 105 project/dependency headers. The audit preserves portable
link commands, contributed archive members, ELF dependencies and input hashes.
The final libraries link only the Android system libraries libc, libdl, liblog
and libm dynamically. Their NDK archive contributions are libc++_static,
libc++abi, libunwind and compiler-rt builtins. No esaxx, bundled protobuf-lite
or utf8_range headers occur in the compiled inference inputs.
The complete NDK notices retain the applicable runtime and platform texts.

The ten unchanged notice/package files are byte-equal to the actual source
tree or NDK files. The remaining two entries are the official ORT v1.23.2 MIT
download and the explicitly documented float16 header excerpt; their recorded
hashes are verified without claiming they are unchanged full build-source files.
Build evidence and this explanation are published in the repository; the AAR
contains the exact source documents listed in sources.json and sources.json.
Run collect_evidence.py after a completed build to regenerate the audit.
It is not a formal SPDX or CycloneDX SBOM and does not assign a license to the
repository's newly written build scripts or documentation.

