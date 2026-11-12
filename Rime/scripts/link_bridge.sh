#!/usr/bin/env bash
# Copyright (c) 2026 PantingEYEs. All rights reserved.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
: "${ANDROID_NDK_HOME:?Set NDK r29}"
: "${RIME_CMAKE:?Set CMake 3.31.6}"
: "${JAVA_HOME:?Set JDK 21}"
BUILD=${RIME_BUILD_DIR:-$ROOT/.work/build}
BOOST=${RIME_BOOST_DIR:-$ROOT/.work/boost}
JOBS=${RIME_BUILD_JOBS:-4}
read -r -a ABIS <<< "${RIME_BRIDGE_ABIS:-arm64-v8a x86_64}"
for ABI in "${ABIS[@]}"; do
    case "$ABI" in
      arm64-v8a|x86_64) ;;
      *) echo "Unsupported RIME_BRIDGE_ABIS value: $ABI" >&2; exit 2 ;;
    esac
done
python3 "$ROOT/scripts/prepare.py"
mkdir -p "$ROOT/.work/bridge-stage" "$ROOT/logs" "$BUILD/java/classes"
"$JAVA_HOME/bin/javac" --release 8 -encoding UTF-8 -d "$BUILD/java/classes" "$ROOT/java/org/ipaboard/rime/RimeNative.java"
IDENTITY=$(python3 "$ROOT/scripts/build_provenance.py" prepare-link)
for ABI in "${ABIS[@]}"; do
    "$RIME_CMAKE" -S "$ROOT/bridge-build" -B "$BUILD/bridge-$ABI" -G 'Unix Makefiles' \
      -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
      -DCMAKE_TOOLCHAIN_FILE="$ANDROID_NDK_HOME/build/cmake/android.toolchain.cmake" \
      -DANDROID_ABI="$ABI" -DANDROID_PLATFORM=android-26 -DANDROID_STL=c++_static \
      -DRIME_CORE_BUILD="$BUILD/rime-$ABI" -DRIME_DEP_PREFIX="$BUILD/install/$ABI" \
      -DOPENCC_PREFIX="$ROOT/../OpenCC/artifacts/$ABI/static" -DBOOST_ROOT="$BOOST" \
      -DIPA_RIME_BUILD_ID="$IDENTITY" 2>&1 | tee "$ROOT/logs/configure-bridge-$ABI.log"
    "$RIME_CMAKE" --build "$BUILD/bridge-$ABI" --parallel "$JOBS" 2>&1 | tee "$ROOT/logs/build-bridge-$ABI.log"
    mkdir -p "$ROOT/.work/bridge-stage/$ABI"
    cp "$BUILD/bridge-$ABI/libipa_rime.so" "$ROOT/.work/bridge-stage/$ABI/libipa_rime.so"
    "$ANDROID_NDK_HOME/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-strip" --strip-unneeded "$ROOT/.work/bridge-stage/$ABI/libipa_rime.so"
done
VERIFIED_IDENTITY=$(python3 "$ROOT/scripts/build_provenance.py" verify-link)
test "$VERIFIED_IDENTITY" = "$IDENTITY"
printf '%s\n' "$IDENTITY" > "$ROOT/.work/bridge-stage/build-id.txt"
printf 'JNI bridge built for: %s; verify and package before publishing artifacts.\n' "${ABIS[*]}"
