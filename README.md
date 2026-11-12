# LatinIME English core for IPA Board

This independent `english` branch builds the English native core selected from
HeliBoard 4.1, pinned to commit
`9f5bb635c2e8609dcd95dc7506c0c58fba82a52c`. It is an orphan branch: its history is
separate from the other IPA Engine engines.

The dictionary is the existing prebuilt `main_en-US.dict`. It is copied without
modification and is **not compiled** by this project. Its SHA-256 is
`bd950ef4b57655120eee65cee62a5d216a63f721d9a8bb759ce2022437840443`.

## What this build contains

- The upstream native core selected by `NativeFileList.mk`, with the existing
  dictionary readers and suggestion algorithms. No HeliBoard application code,
  UI, contacts, gesture library, personal dictionary data or application-level
  personal dictionary feature, or dictionary compiler is
  imported into the shipped engine.
- A new, narrow `org.ipaboard.latinime.LatinImeNative` JNI interface. It exposes
  build/ABI identity, read-only open, query, probability lookup, and close.
  The old `com.android.inputmethod` Java declarations and JNI registrations are
  not part of the published library or bindings.
- Native outputs for `arm64-v8a` and `x86_64`, with 16KB ELF load-segment
  alignment. The C++ runtime is linked statically. Actual runtime coverage is
  recorded separately from static checks.

Completion, spelling correction and short-context prediction are supplied by
the core and unchanged dictionary. IPA Board continues to own case handling,
candidate-kind filtering, mixed-text segmentation, cross-language ranking,
stale-request handling and literal-text fallback. This is not a replacement
keyboard application and does not add personal learning.

## Build

Build on Linux x86_64 (WSL is supported), with Python 3, Git, GNU Make, Android
NDK `29.0.14206865`, CMake `3.31.6`, and JDK 21 (producing Java 11 bytecode).
Pass the actual tool locations; no sibling engine checkout is required.

```sh
python3 scripts/bootstrap.py
python3 scripts/build.py --ndk /path/to/android-ndk-r29 \
  --cmake /path/to/cmake-3.31.6/bin/cmake \
  --javac /path/to/jdk-21/bin/javac --jobs 8
python3 scripts/package.py
python3 scripts/verify_artifacts.py
python3 source-inventory/verify.py
```

The bootstrap fetches the pinned official commit and checks every declared
source file. After the first fetch, use `python3 scripts/bootstrap.py --offline`
to validate the cached inputs without network access. The build does not run
HeliBoard Gradle tasks, fetch a replacement dictionary, or compile a word list.

`sources.lock.json` records the selected inputs and build options.
`artifacts/build-provenance.json` binds the source recipe, native toolchain,
compiled core archives and both final libraries to the runtime build identity.
`.cache/` and `.work/` are local work directories and are not committed.

## Integration and verification

The integration artifact is
`artifacts/ipa-latinime-android-heliboard-4.1.aar`; the separate native libraries
and unchanged dictionary remain available in `artifacts/` and `dictionary/`.
The AAR does not contain the dictionary, which its consumer must provide and
attribute separately.

See `bridge/README.md` for the new interface and `validation/` for the actual build and
runtime evidence. The ABI contract belongs to this package; consumers must not
reuse the old HeliBoard Java registration declarations with the new library.

Use the supplied prebuilt dictionary bytes. Before opening, the consumer must
verify the whole dictionary SHA-256 and keep the file immutable for the lifetime
of its native owners. Native open validates its header but does not replace that
cryptographic check. Open is read-only, and no learning,
add-word or flush API is exposed. Dictionary handles must be closed when their
owner is finished. A process-wide owner may remain alive across service rebinds.

Android's SDK/API declaration, ELF alignment, APK packaging alignment and actual
process page size are distinct checks. A passing static ARM64 inspection is not
an ARM64 device test. A 16KB test is not a claim about all APIs or editors. The
current task does not require 4KB runtime coverage.

The runtime validation uses an isolated copy of IPA Board, whose existing
application/test-host license is separate from this engine. Adapted host source
is retained locally for the next integration step; it is not bundled into this
branch's native library or AAR. This stage leaves IPA Board's checked-out #52
branch and installed preview unchanged, and does not close Issue #52.

## Sources and licensing

Licenses are tracked separately for the selected native source, new integration
code and the dictionary. Read `licenses/` and `source-inventory/` before
redistributing or changing license declarations.

The selected upstream C++ sources retain their Apache-2.0 notices. Newly authored
IPA Engine bridge/build code is copyright PantingEYEs, with rights reserved
pending the maintainer's project-license decision; this branch does not assign
it a new open-source license.

The unchanged dictionary has its own provenance and unresolved licensing
boundary. Its bytes match a historical OpenBoard asset, whose source tree
carried GPL-3.0; a separately explicit permissive license for the complete
processed dictionary has not been established. Keeping this dictionary does
not make it Apache-2.0. The evidence and original notices are retained without
relabeling the asset.

Consequently this build provides an independently built alternative for the
subsequent IPA Board integration. It does **not** certify that all English resources, or IPA
Board as a whole, can be relicensed. The final project-license decision remains
separate from this engineering deliverable.
