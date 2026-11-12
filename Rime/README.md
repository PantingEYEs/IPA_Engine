# Rime / OpenCC for IPA Board

The core is official **librime 1.17.0**, revision `33e78140250125871856cdc5b42ddc6a5fcd3cd4`. Keeping the same librime revision as the current application isolates the native packaging and interface changes. **OpenCC 1.4.2** is linked statically, along with its single vendored Marisa implementation, LevelDB 1.23, yaml-cpp at librime's pinned commit `2f86d13775d119edbb69af52e5f566fd65c6953b` (self-reported CMake version 0.8.0), and the required Boost 1.89 headers. No Trime binary or JNI implementation is used.

OpenCC is also available as a separate shared library and static development prefix in the sibling `OpenCC/` directory. Combining it into `libipa_rime.so` for this application avoids another dynamic dependency and keeps internal C++ types inside one build. An application using this AAR does not also need to load `libopencc.so`.

## Deliverables

- `artifacts/ipa-rime-android-1.17.0-opencc-1.4.2.aar`: Java interface, two native ABIs, consumer keep rule, build identity, and notices.
- `artifacts/{arm64-v8a,x86_64}/libipa_rime.so`: the same native files, separately available.
- `resources/` and `artifacts/ipa-rime-resources.zip`: official-source compatibility data and explicitly documented application policy overlays.
- `artifacts/manifest.json`: sizes, hashes, source identity, and resource-lock identity.
- `sources.lock.json`, `resource-sources.lock.json`, and `licenses/`: pinned provenance and original notices.
- `validation/`: executed checks and their limits.

Both Android ABIs target API 26 at the binary level and use NDK r29 with 16 KiB ELF load-segment alignment. IPA Board's supported Android version is a separate product policy. Runtime acceptance is performed in a strict 16 KiB environment; no 4 KiB device support is claimed or required by this task.

## Build

Use Linux with Git, Python 3.11+, a host C/C++ compiler and Make, CMake **3.31.6**, Android NDK **29.0.14206865**, and JDK 21. Build the sibling OpenCC static prefixes first using its instructions. Fetches are pinned and verified; prepared sources can subsequently be used offline.

Run these commands from the repository root:

```sh
export ANDROID_NDK_HOME=/path/to/android-ndk-r29
export RIME_CMAKE=/path/to/cmake-3.31.6/bin/cmake
export JAVA_HOME=/path/to/jdk-21
# Optional: keep many small build/header files on a native Linux filesystem.
export RIME_BUILD_DIR=/path/to/private/rime-build
export RIME_BOOST_DIR=/path/to/private/boost-1.89.0
export RIME_BUILD_JOBS=4
python3 Rime/scripts/prepare_resources.py --prepare
bash Rime/scripts/build.sh
bash Rime/scripts/link_bridge.sh
python3 Rime/scripts/package.py
python3 Rime/scripts/verify_artifacts.py
```

The build disables upstream samples, bundled-data generation, tests, glog, and external plugins in the Android runtime. Threading remains enabled. These choices do not disable the current application's Pinyin, Bopomofo, candidate segmentation, or corpus-based prediction capabilities. Prediction continues to be application logic using `essay.txt`; it does not require a Lua or octagram plugin.

## Interface and integration

See [bridge/README.md](bridge/README.md) for the complete ownership, input, output, and failure contract. `org.ipaboard.rime.RimeNative` exposes six native operations: interface version, build identity, open, query, convert, and close. UTF-8 is passed as byte arrays, including supplementary Unicode characters in conversion results. Native handles are opaque and must not be treated as Rime session pointers.

The adapter owns one runtime per process and serializes calls. It provides lazy per-schema sessions, bounded read-only candidate enumeration, exact consumed-input ranges, explicit converter cleanup, and failure isolation. Querying does not select candidates to discover their consumed lengths. The Java-to-native API does not expose a C++ structure or Rime function table. JNA and a separate candidate bridge are not needed by this interface.

Deploy `resources/` to an application-owned shared directory and use a separate writable user/build directory. Call `open` with both absolute directories and the two bundled OpenCC configuration paths. Use a new deployment version when replacing older application assets; do not reuse a marker from a Trime-derived installation. Close the owner when its process-level service is deliberately disposed.

The caller keeps responsibility for IPA Board's existing Pinyin/Zhuyin spelling encoding, precise byte-to-editor offset mapping, literal-preserving mixed-language assembly, prediction, and display labels. A native candidate packet does not authorize proportional character mapping. The packet version is compatible with the existing published v3 layout, independently implemented here.

The three supplied schema overlays disable user-dictionary learning and completion, matching the current query-oriented application behavior. Choosing a different schema list or exposing learning/region-specific conversion later requires deliberate application policy and the corresponding resource/acceptance changes. Omitted regional OpenCC dictionaries are not loaded while their schema switches are off.

## Licensing boundary

Newly written adapter and build code: copyright 2026 PantingEYEs, all rights reserved; no project license selected. Upstream native components and bundled data retain their own licenses. See [licenses/README.md](licenses/README.md). Building without Trime does not automatically relicense IPA Board or its dictionaries.
