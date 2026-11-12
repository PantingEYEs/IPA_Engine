# Official OpenCC for IPA Board

OpenCC 1.4.2 is built directly from the official BYVoid/OpenCC repository at
commit 025f371dc76b598d77384fbdab90c937471844d8. These artifacts use no Trime
binary, JNI implementation, or build script.

## Deliverables

For each of arm64-v8a and x86_64:

- **artifacts/&lt;ABI&gt;/libopencc.so**: independent OpenCC shared library, plain
  Android SONAME, static C++ runtime and 16 KB LOAD alignment.
- **artifacts/&lt;ABI&gt;/static/**: lib/libopencc.a, lib/libmarisa.a, public
  OpenCC/Marisa headers, relocatable CMake and pkg-config metadata.
- **artifacts/manifest.json**: exact hashes, sizes, ELF layout, dependencies,
  and required C API exports for the delivered artifacts.

The static archives contain the engine objects, not a second copy of libc++.
The final consumer must link its Android C++ runtime consistently. Rime uses
this same OpenCC and Marisa archive; do not add a separate Marisa implementation.
The independent shared library contains its own static runtime. Pass OpenCC
handles and allocated results through the matching OpenCC C API and free
returned strings with opencc_convert_utf8_free.

The Android library retains upstream text, ocd2, and legacy dictionary readers.
It does not include the optional Jieba plugin, tests, command-line programs,
Python bindings, or any dictionary data. Its dynamic dependencies are Android's
libc.so, libm.so, and libdl.so.

## Source and build

Requirements: Linux x86-64 (WSL is sufficient), Git, Python 3.11 or newer, GNU Make,
Android NDK r29 29.0.14206865, and CMake 3.31.6. Download URLs and SHA-256
digests for the toolchain archives are recorded in sources.lock.json.

Run the following build and validation commands from the OpenCC directory,
not the repository root.

~~~sh
export ANDROID_NDK_HOME=/absolute/path/to/android-ndk-r29
export OPENCC_CMAKE=/absolute/path/to/cmake-3.31.6/bin/cmake
python3 scripts/prepare.py
bash scripts/build.sh
~~~

The prepare script fetches the pinned official source and verifies its clean
tracked tree, vendored dependency fingerprints, and retained license notices.
Once prepared, the library build is offline. The build script validates the
source revision and toolchain versions, builds both ABIs as static and shared
libraries, installs only library targets, strips the delivery .so files,
writes the manifest and runs the verifier tests. It does not run Android
dictionary executables on the build host.

The thin CMake wrapper changes only Android packaging: unversioned SONAME,
16 KB linker layout, private static dependency symbols, and explicit build
options. Upstream algorithm sources are unmodified. The pkg-config metadata
is rewritten to be relocatable and records static Marisa/dl dependencies.
The link baseline is Android API 26; this does not lower IPA Board's own
minimum supported Android version.

find_package(OpenCC CONFIG) can use the static prefix and exposes
OpenCC::OpenCC, including its Marisa/dl dependencies. Older Rime FindOpencc
modules require the explicit libraries libopencc.a;libmarisa.a plus dl.
Public Marisa headers match official v0.3.1; the three private upstream vendor
changes are recorded under validation/.

Build caches, the official checkout, host-only generated dictionaries, and
unstripped local symbols stay under .work/ and are not distributed. The
duplicate shared installation prefix is ignored; the canonical .so above
is the delivery file.

## Conversion data and compatibility

IPA Board's current four core conversion dictionaries were traced to official
OpenCC 1.2.0. The neighboring ../Rime/resources/opencc/ retains those pinned
original dictionaries and generates text configurations. Group dictionaries
explicitly set match_policy: short_circuit, matching the old implicit behavior.
This avoids silently replacing the user's conversion vocabulary with 1.4.2
data during an engine build migration.

Modern 1.4.2 dictionaries are available in the official source, but are not
selected as the application defaults or added to these artifacts. A future
data update should be a deliberate, separately validated product change.

## Validation

~~~sh
python3 scripts/verify_artifacts.py
python3 -m unittest discover -s tests -p 'test_*.py' -v
bash scripts/test-host.sh
~~~

The host suite requires a native C++ compiler and the neighboring Rime resource
directory. It builds official host tools/tests and checks the C API against the
pinned text resources. Host fixture binaries and generated modern dictionaries
remain local. The upstream host test suite also builds a Jieba test fixture;
that does not enable or include Jieba in the Android build.

See validation/results.json for the executed checks. Both Android binaries
passed static ELF checks. These results do not claim the standalone OpenCC
shared library has been executed on an Android device. Rime's integrated
runtime validation is recorded separately. No Android 4 KB device support
claim or 4 KB device testing is part of this work.

## Notices

See licenses/README.md, the preserved original notices, and sources.lock.json.
The BSD option is selected for the dual-licensed Marisa component. This native
build's provenance does not decide licenses for IPA Board, its schemas,
dictionaries, other engines, or existing adapters.

Copyright 2026 PantingEYEs. All rights reserved for newly authored build,
verification and integration code. No project license has been selected.
Upstream materials retain their original licenses.
