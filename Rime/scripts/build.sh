#!/usr/bin/env bash
# Copyright (c) 2026 PantingEYEs. All rights reserved.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
: "${ANDROID_NDK_HOME:?Set ANDROID_NDK_HOME to NDK r29}"
: "${RIME_CMAKE:?Set RIME_CMAKE to CMake 3.31.6}"
BUILD=${RIME_BUILD_DIR:-$ROOT/.work/build}
JOBS=${RIME_BUILD_JOBS:-4}
BOOST=${RIME_BOOST_DIR:-$ROOT/.work/boost}
python3 "$ROOT/scripts/prepare.py"
python3 - "$ANDROID_NDK_HOME" "$RIME_CMAKE" <<'PY'
from pathlib import Path
import subprocess,sys
assert 'Pkg.Revision = 29.0.14206865' in (Path(sys.argv[1])/'source.properties').read_text()
assert subprocess.check_output([sys.argv[2],'--version'],text=True).splitlines()[0] == 'cmake version 3.31.6'
PY
unset CFLAGS CXXFLAGS CPPFLAGS LDFLAGS CMAKE_PREFIX_PATH CMAKE_TOOLCHAIN_FILE
mkdir -p "$BUILD" "$ROOT/logs"
python3 "$ROOT/scripts/build_provenance.py" begin-core
for ABI in arm64-v8a x86_64; do
    PREFIX="$BUILD/install/$ABI"
    OPENCC="$ROOT/../OpenCC/artifacts/$ABI/static"
    test -f "$OPENCC/lib/libopencc.a"
    test -f "$OPENCC/lib/libmarisa.a"
    COMMON=(-G 'Unix Makefiles' -DCMAKE_BUILD_TYPE=Release -DCMAKE_POSITION_INDEPENDENT_CODE=ON
        -DCMAKE_TOOLCHAIN_FILE="$ANDROID_NDK_HOME/build/cmake/android.toolchain.cmake"
        -DANDROID_ABI="$ABI" -DANDROID_PLATFORM=android-26 -DANDROID_STL=c++_static
        -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_INSTALL_LIBDIR=lib
        -DBUILD_SHARED_LIBS=OFF -DCMAKE_EXPORT_COMPILE_COMMANDS=ON)
    "$RIME_CMAKE" -S "$ROOT/.work/librime/deps/leveldb" -B "$BUILD/leveldb-$ABI" "${COMMON[@]}" \
        -DLEVELDB_BUILD_TESTS=OFF -DLEVELDB_BUILD_BENCHMARKS=OFF \
        -DHAVE_CRC32C=0 -DHAVE_SNAPPY=0 -DHAVE_TCMALLOC=0 2>&1 | tee "$ROOT/logs/configure-leveldb-$ABI.log"
    "$RIME_CMAKE" --build "$BUILD/leveldb-$ABI" --target install --parallel "$JOBS" 2>&1 | tee "$ROOT/logs/build-leveldb-$ABI.log"
    "$RIME_CMAKE" -S "$ROOT/.work/librime/deps/yaml-cpp" -B "$BUILD/yaml-$ABI" "${COMMON[@]}" \
        -DYAML_CPP_BUILD_TESTS=OFF -DYAML_CPP_BUILD_TOOLS=OFF -DYAML_CPP_BUILD_CONTRIB=OFF \
        -DYAML_BUILD_SHARED_LIBS=OFF -DYAML_CPP_INSTALL=ON -DYAML_CPP_FORMAT_SOURCE=OFF 2>&1 | tee "$ROOT/logs/configure-yaml-$ABI.log"
    "$RIME_CMAKE" --build "$BUILD/yaml-$ABI" --target install --parallel "$JOBS" 2>&1 | tee "$ROOT/logs/build-yaml-$ABI.log"
    "$RIME_CMAKE" -S "$ROOT/.work/librime" -B "$BUILD/rime-$ABI" "${COMMON[@]}" \
        -DBUILD_STATIC=ON -DBUILD_TEST=OFF -DBUILD_DATA=OFF -DBUILD_SAMPLE=OFF \
        -DENABLE_LOGGING=OFF -DENABLE_EXTERNAL_PLUGINS=OFF -DENABLE_THREADING=ON -DENABLE_TIMESTAMP=OFF \
        -DBoost_NO_BOOST_CMAKE=ON -DBoost_INCLUDE_DIR="$BOOST" \
        -DYamlCpp_INCLUDE_PATH="$PREFIX/include" -DYamlCpp_NEW_API="$PREFIX/include" -DYamlCpp_LIBRARY="$PREFIX/lib/libyaml-cpp.a" \
        -DLevelDb_INCLUDE_PATH="$PREFIX/include" -DLevelDb_LIBRARY="$PREFIX/lib/libleveldb.a" \
        -DMarisa_INCLUDE_PATH="$OPENCC/include" -DMarisa_LIBRARY="$OPENCC/lib/libmarisa.a" \
        -DOpencc_INCLUDE_PATH="$OPENCC/include" -DOpencc_LIBRARY="$OPENCC/lib/libopencc.a;$OPENCC/lib/libmarisa.a" \
        2>&1 | tee "$ROOT/logs/configure-rime-$ABI.log"
    "$RIME_CMAKE" --build "$BUILD/rime-$ABI" --target rime-static --parallel "$JOBS" 2>&1 | tee "$ROOT/logs/build-rime-$ABI.log"
done
python3 "$ROOT/scripts/build_provenance.py" record-core
echo 'Both official librime static cores are built; link the reviewed bridge with the same pinned inputs.'
