#!/usr/bin/env bash
# Copyright 2026 PantingEYEs. All rights reserved.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CMAKE=${OPENCC_CMAKE:?Set OPENCC_CMAKE to CMake 3.31.6}
BUILD="$ROOT/.work/build/host"
test "$(git -C "$ROOT/.work/upstream" rev-parse HEAD)" = 025f371dc76b598d77384fbdab90c937471844d8
mkdir -p "$ROOT/logs"
"$CMAKE" -S "$ROOT/.work/upstream" -B "$BUILD" -G 'Unix Makefiles' \
 -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DENABLE_GTEST=ON \
 -DENABLE_BENCHMARK=OFF -DBUILD_OPENCC_JIEBA_PLUGIN=OFF \
 -DBUILD_PYTHON=OFF -DBUILD_DOCUMENTATION=OFF -DOPENCC_ENABLE_INSTALL=ON \
 -DCMAKE_INSTALL_PREFIX=/ipa/opencc -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
 > "$ROOT/logs/configure-host.log" 2>&1
"$CMAKE" --build "$BUILD" --parallel "${JOBS:-6}" > "$ROOT/logs/build-host.log" 2>&1
(cd "$BUILD"; "$(dirname "$CMAKE")/ctest" --output-on-failure --parallel "${JOBS:-6}") > "$ROOT/logs/ctest-host.log" 2>&1
"$CMAKE" --install "$BUILD" --prefix "$ROOT/.work/install-host" > "$ROOT/logs/install-host.log" 2>&1
CXX=${CXX:-g++}
"$CXX" -std=c++17 "$ROOT/tests/c_api_compatibility.cpp" \
 -I"$ROOT/.work/install-host/include/opencc" \
 "$ROOT/.work/install-host/lib/libopencc.a" "$ROOT/.work/install-host/lib/libmarisa.a" \
 -ldl -pthread -o "$ROOT/.work/c-api-compatibility"
"$ROOT/.work/c-api-compatibility" "$ROOT/../Rime/resources/opencc" > "$ROOT/logs/c-api-compatibility.json"