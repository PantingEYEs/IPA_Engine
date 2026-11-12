#!/usr/bin/env bash
# Copyright 2026 PantingEYEs. All rights reserved.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
: "${ANDROID_NDK_HOME:?Set ANDROID_NDK_HOME to Android NDK r29}"
CMAKE=${OPENCC_CMAKE:?Set OPENCC_CMAKE to CMake 3.31.6}
SOURCE="$ROOT/.work/upstream"
JOBS=${JOBS:-6}
test "$(git -C "$SOURCE" rev-parse HEAD)" = 025f371dc76b598d77384fbdab90c937471844d8
test -z "$(git -C "$SOURCE" status --porcelain --untracked-files=no)"
grep -q 'Pkg.Revision = 29.0.14206865' "$ANDROID_NDK_HOME/source.properties"
test "$("$CMAKE" --version | head -1)" = 'cmake version 3.31.6'
unset CFLAGS CXXFLAGS CPPFLAGS LDFLAGS CMAKE_PREFIX_PATH CMAKE_TOOLCHAIN_FILE
mkdir -p "$ROOT/logs" "$ROOT/.work/build" "$ROOT/artifacts"
for ABI in arm64-v8a x86_64; do
  for KIND in static shared; do
    BUILD="$ROOT/.work/build/$ABI-$KIND"
    PREFIX="$ROOT/artifacts/$ABI/$KIND"
    SHARED=OFF
    if test "$KIND" = shared; then SHARED=ON; fi
    "$CMAKE" -S "$ROOT/integration" -B "$BUILD" -G 'Unix Makefiles' \
      -DOPENCC_SOURCE_DIR="$SOURCE" \
      -DCMAKE_TOOLCHAIN_FILE="$ANDROID_NDK_HOME/build/cmake/android.toolchain.cmake" \
      -DANDROID_ABI="$ABI" -DANDROID_PLATFORM=android-26 -DANDROID_STL=c++_static \
      -DCMAKE_BUILD_TYPE=Release -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
      -DCMAKE_EXPORT_COMPILE_COMMANDS=ON -DCMAKE_INSTALL_PREFIX=/ipa/opencc \
      -DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_CXX_FLAGS_RELEASE="-O3 -DNDEBUG" \
      -DBUILD_SHARED_LIBS="$SHARED" > "$ROOT/logs/configure-$ABI-$KIND.log" 2>&1
    "$CMAKE" --build "$BUILD" --target libopencc --parallel "$JOBS" > "$ROOT/logs/build-$ABI-$KIND.log" 2>&1
    # Install the library and headers without executing Android dictionary tools.
    "$CMAKE" -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_INSTALL_LOCAL_ONLY=TRUE -P "$BUILD/upstream/src/cmake_install.cmake" > "$ROOT/logs/install-$ABI-$KIND.log" 2>&1
    mkdir -p "$PREFIX/lib/cmake/opencc" "$PREFIX/lib/pkgconfig" "$PREFIX/include"
    cp "$BUILD/upstream/OpenCCConfig.cmake" "$BUILD/upstream/OpenCCConfigVersion.cmake" "$PREFIX/lib/cmake/opencc/"
    cp "$BUILD/upstream/opencc.pc" "$PREFIX/lib/pkgconfig/"
    python3 "$ROOT/scripts/relocate-pkgconfig.py" "$PREFIX"
    cp -R "$SOURCE/deps/marisa-0.3.1/include/." "$PREFIX/include/"
    if test "$KIND" = shared; then
      mkdir -p "$ROOT/.work/symbols/$ABI"
      cp "$PREFIX/lib/libopencc.so" "$ROOT/.work/symbols/$ABI/"
      "$ANDROID_NDK_HOME/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-strip" --strip-unneeded "$PREFIX/lib/libopencc.so"
      cp "$PREFIX/lib/libopencc.so" "$ROOT/artifacts/$ABI/libopencc.so"
    fi
    echo "Built $ABI $KIND: $PREFIX"
  done
done
python3 "$ROOT/scripts/verify_artifacts.py" --write-manifest
python3 -m unittest discover -s "$ROOT/tests" -p "test_*.py" -v
