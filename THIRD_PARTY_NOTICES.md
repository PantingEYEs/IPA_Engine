# Third-party notices for the Mozc engine and OSS dataset

This directory packages an Android JNI library and the Mozc OSS dataset from
[google/mozc](https://github.com/google/mozc) at commit
`a4541df29db443b49f81cf54665873b68fde9efa` (2026-11-09), with the IPA Board
compatibility patch recorded in `sources.lock.json`. The patch retains the
upstream JNI entry and adds the legacy `MozcJNI_initialize` entry; it also sets
Android API level 26. It does not replace the upstream license.

These notices concern upstream software and data. No project-wide license has
been selected for this repository's new build scripts or documentation; this
file does not assign one or relicense third-party material.

## Scope and verification status

License source materials were collected on 2026-11-11 for these targets:

- `//android/jni:mozc.arm64` (`arm64-v8a`)
- `//android/jni:mozc.x86_64` (`x86_64`)
- `//data_manager/oss:mozc_dataset_for_oss` (`mozc.data`)

The completed build was audited on 2026-11-11 using the actual link response
files for both Android ABIs, their resolved external repositories, the NDK
link-driver expansion, the final ELF dynamic sections, and the dataset inputs.
Six copied dependency license/author files match the downloaded build sources
byte for byte. The final stripped libraries and dataset hashes are recorded in
[the dependency audit](licenses/evidence/dependency-audit.json).

This is a **build-specific input and notice audit**, not a formal SPDX or
CycloneDX software bill of materials. Archive inputs are covered conservatively;
individual retained archive members were not reconstructed. It also does not
claim runtime, device or application compatibility validation. Carry these
notices with the recorded binary/data distribution.

`licenses/sources.json` records the original source and SHA-256 of each copied
license or reference file. Full texts, rather than only license identifiers, are
retained in `licenses/`.

## Engine and runtime components

| Component | Source version | Notice material | Scope |
| --- | --- | --- | --- |
| Mozc | `a4541df29db443b49f81cf54665873b68fde9efa` | `licenses/Mozc-LICENSE.txt`; Mozc section of `licenses/Mozc-engine-and-data-NOTICES.txt` | BSD-style engine license; the original root file also carries the dictionary notice. Both upstream copyright notices are preserved without rewriting their differing year ranges. |
| Abseil C++ | `20260107.1` | `licenses/Abseil-20260107.1-LICENSE.txt`; `licenses/Abseil-20260107.1-AUTHORS.txt` | Apache License 2.0; a direct JNI/engine dependency in the pinned build definitions. |
| Protocol Buffers | `34.1` | `licenses/Protobuf-34.1-LICENSE.txt` | BSD 3-Clause; native protocol runtime. |
| utf8_range | bundled in Protocol Buffers `v34.1` | `licenses/Protobuf-34.1-utf8_range-LICENSE.txt` | MIT; the Protobuf runtime build depends on `utf8_validity` and `utf8_range`. |
| zlib | resolved Bazel module `1.3.1.bcr.5`, upstream `1.3.1` | `licenses/zlib-1.3.1-LICENSE.txt` | Verified in both ABI link response files; the copied license matches the actual downloaded zlib repository. |
| Android NDK runtime/toolchain material | r29, package `29.0.14206865` | `licenses/NDK-r29-NOTICE.txt`; `licenses/NDK-r29-NOTICE.toolchain.txt` | Original notices from the exact NDK package used by the build. These broad files preserve notices for libc++, compiler runtime and other package components; copying them does not assert that every listed NDK component is linked into Mozc. |

Mozc's [module declarations](https://github.com/google/mozc/blob/a4541df29db443b49f81cf54665873b68fde9efa/src/MODULE.bazel)
are version evidence, not proof that every declared module is in the Android
binary. For example, desktop components, test libraries, Python tooling, Bazel
rules and the build executables themselves must not be described as shipped
Android runtime dependencies solely because they are fetched during a build.
The completed audit covers both static link inputs and `DT_NEEDED` libraries.
Both ABIs link external code from Abseil, Protobuf (including its bundled
utf8_range) and zlib. NDK inputs include `libc++_static.a`, `libc++abi.a`,
`libunwind.a`, `libatomic.a`, compiler-rt builtins and Android startup/end objects.
The exact files and hashes are recorded in the audit. These runtime components
are covered by the original NDK notice files retained above.

Both final libraries require only `liblog.so`, `libc.so`, `libm.so` and `libdl.so`
from Android. They do not require a separately shipped `libc++_shared.so`.
Qt, Windows Implementation Library and RE2 are absent from these Android link
inputs. Keeping the cross-platform reference notices does not add those
components to the delivered engine.

## OSS dictionary and other dataset inputs

The [dataset target](https://github.com/google/mozc/blob/a4541df29db443b49f81cf54665873b68fde9efa/src/data_manager/oss/BUILD.bazel)
uses the OSS dictionary, manually maintained Mozc data, Japanese usage data,
postal data, and the tracked auxiliary tables for prediction, symbols, emoji,
emoticons, reading correction, single-kanji conversion and accessibility.
This is the Mozc OSS dataset, not the proprietary Google Japanese Input dataset.

| Data source | Provenance | Retained notice |
| --- | --- | --- |
| IPAdic / NAIST / ICOT | Upstream identifies `mecab-ipadic-2.7.0-20070801` (`ipadic-2.7.0`) as the base vocabulary. | Original full notice and ICOT disclaimer in `Mozc-LICENSE.txt`, the IPAdic block in `Mozc-engine-and-data-NOTICES.txt`, and the unmodified upstream dictionary README. Do not shorten this to an assumed standard BSD license. |
| Okinawa Dictionary | Upstream says it enriches named entities from Okinawa Dictionary. | Original Japanese public-domain statement in `Mozc-engine-and-data-NOTICES.txt` and the upstream dictionary README. |
| Mozc-maintained additions and auxiliary tables | Tracked data in the pinned source tree; upstream describes extra inflections, katakana words and compounds. | Preserve the Mozc notice and any notices attached to individual source files. No separate CLDR-derived vocabulary or proprietary Google corpus is claimed here. |
| Japanese Usage Dictionary | `hiroyuki-komatsu/japanese-usage-dictionary`, tag `2025-01-25`; dataset input `@ja_usage_dict//:usage_dict.txt`. | `Japanese-Usage-Dictionary-2025-01-25-LICENSE.txt` (BSD 2-Clause), including the Hiroyuki Komatsu, Shoji Ogura and Tsuyoshi Horo notice. |
| Tamachi Phonetic Kanji Alphabet | Listed by the pinned official Mozc credits; the dataset also includes the tracked accessibility-description data. Exact per-entry ancestry has not been independently reconstructed. | Original MIT statement retained conservatively in `Mozc-engine-and-data-NOTICES.txt`. |
| Japan Post postal codes | `KEN_ALL.CSV` and `JIGYOSYO.CSV`, mirrored snapshot dated 2026-04-03 as described below. | Original `zipcode` and `zipcode for jigyosyo` notices and redistribution statements from the pinned Mozc credits, retained in `Mozc-engine-and-data-NOTICES.txt`. |

The Japanese Usage Dictionary archive has upstream SHA-256
`140f24f71f7d78d5eb5b93a9c079f44f23728dca5ab16bf857fe8a3fa1bf20ed`.

The [OSS dictionary build](https://github.com/google/mozc/blob/a4541df29db443b49f81cf54665873b68fde9efa/src/data/dictionary_oss/BUILD.bazel)
adds `//dictionary:zip_code_data`. The corresponding
[postal-data rule](https://github.com/google/mozc/blob/a4541df29db443b49f81cf54665873b68fde9efa/src/dictionary/BUILD.bazel)
uses the two CSV inputs, declared in `src/MODULE.bazel` from the
[Japan Post data mirror snapshot](https://github.com/hiroyuki-komatsu/japanpost_zipcode/tree/621d059fbcbfae17bfca15b439692bae934268c3).
The upstream archive hashes are:

- `ken_all.zip`: `3adc97d8e19e1168a6cdcb2fce759d0e9e613a9a3b630cc180aecfd56ae81eaf`
- `jigyosyo.zip`: `14be1030f607c5f819f2f45a485e89511907bdd1d62e7fb1931b6e48a3431c3a`

The upstream source comments identify the original Japan Post downloads at
[postal code data](https://www.post.japanpost.jp/zipcode/dl/kogaki/zip/ken_all.zip)
and [large-office postal code data](https://www.post.japanpost.jp/zipcode/dl/jigyosyo/zip/jigyosyo.zip).
The notices here reproduce the versioned Mozc credit statements; they do not
claim that the current contents or wording of those live downloads were audited.

`licenses/reference/mozc-dictionary-oss-README.txt` is preserved verbatim. Its
statement that OSS excludes Japanese postal codes predates the present build
rules and must not be used to describe this pinned dataset: the actual rules
include the postal inputs above.

## Official credits and the Android target

The [official installer build file](https://github.com/google/mozc/blob/a4541df29db443b49f81cf54665873b68fde9efa/src/data/installer/BUILD.bazel)
defines `//data/installer:android_asset_licenses` as a private rule that simply
copies `credits_en.html` to `assets/licenses.html`. It does not derive notices
from a configured dependency graph. The JNI and dataset targets listed above
do not themselves package an Android front-end license screen.

`licenses/reference/mozc-credits-en.html` is the unchanged official page. It
also contains Qt Base and Windows Implementation Library notices. Their presence
in that cross-platform reference is not evidence that they are dependencies of
this Android engine build. `Mozc-engine-and-data-NOTICES.txt` extracts selected
engine/data notice blocks without changing their text, decodes HTML entities,
and adds headings and provenance. Separate files retain the actual pinned
Abseil, Protobuf, usage-dictionary and toolchain license texts.

## Build evidence and redistribution

The audit evidence is retained in `licenses/evidence/`:

- `dependency-audit.json`: selected module versions, comparisons against actual
  downloaded license files, native/data hashes, NDK runtime input hashes and
  the final dynamic dependency lists.
- `*-link.params`: unchanged response files from the successful native build.
- `*-link-driver-expanded.json`: a read-only `clang -###` expansion used to
  identify runtime inputs added by the NDK driver; it did not relink the files.
- `*-elf-dynamic.txt` and `*-distribution-elf-dynamic.txt`: dynamic sections of
  the original link outputs and the final stripped release libraries.
- `*-resolved-MODULE.bazel.txt` and `module-lock-runtime-records.json`: actual
  external module declarations and relevant registry records from the completed
  build's lock file. Lower-version registry requests are distinguished from the
  selected module versions.
- `SHA256SUMS`: checksums for the retained audit evidence.

The source and license audit found no uncovered component among the inspected
native link inputs and the pinned OSS dataset sources. The completed dataset
build generated the postal-code intermediate and the Japanese usage tables;
its downloaded usage-dictionary license matches the saved copy. Exact CSV,
usage-source, generated-intermediate and final dataset hashes are in the audit.

Final files covered by this audit:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `arm64-v8a/libmozc.so` | 5,478,432 | `d7cc97611b162e1577dc419811bc4a39b72a9af0be4f380b68597519e64d9b12` |
| `x86_64/libmozc.so` | 5,642,432 | `04831587ad807ba1e26144ae8fb34c67d40e6f9193294500f680a1be7616b3ac` |
| `mozc.data` | 18,955,930 | `26d8d5391e37dba926df23e6f01e90158dd755af111712ce84f065a700a852b5` |

The original unstripped link-output hashes are retained separately from the
release hashes, so the raw linker evidence remains attributable after stripping.

When redistributing these libraries or `mozc.data`, include this document and
the applicable full license/notice files, keep them accessible in the consuming
application's distribution materials, and identify the local compatibility
modifications. Source distributions must retain notices attached to their
source files. Recheck the audit if source, dataset, toolchain, dependency
resolution or native link inputs change; these hashes apply only to this build.

This collection does not select a license for the new wrapper repository and
has not modified the existing IPA Board application's notices or assets.
