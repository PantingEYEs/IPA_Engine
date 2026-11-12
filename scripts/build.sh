#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
python3 "$ROOT/scripts/prepare.py"
# A Linux x86_64 host, JDK 21 (emitting Java 8 bytecode), CMake 3.31.6 and NDK r29.
: "${JAVA_HOME:?Set JAVA_HOME to a JDK 21 installation}"
: "${ANDROID_NDK_HOME:?Set ANDROID_NDK_HOME to Android NDK r29 (29.0.14206865)}"
CMAKE=${ORTX_CMAKE:-$ROOT/.cache/tools/cmake-3.31.6-linux-x86_64/bin/cmake}
BUILD=${ORTX_BUILD_DIR:-$ROOT/.work/build}
JOBS=${ORTX_BUILD_JOBS:-4}
unset CFLAGS CXXFLAGS CPPFLAGS LDFLAGS CMAKE_PREFIX_PATH CMAKE_TOOLCHAIN_FILE OCOS_ENABLE_AZURE
if [[ $("$JAVA_HOME/bin/javac" -version 2>&1) != 'javac 21.0.13' ]]; then
  echo 'Expected javac 21.0.13; see sources.lock.json' >&2; exit 1
fi
python3 - "$ROOT" "$ANDROID_NDK_HOME" "$CMAKE" <<'PY'
import json,pathlib,subprocess,sys
root,ndk=map(pathlib.Path,sys.argv[1:3]);lock=json.loads((root/'sources.lock.json').read_text())
if 'Pkg.Revision = '+lock['toolchain']['androidNdkRevision'] not in (ndk/'source.properties').read_text():raise SystemExit('NDK revision mismatch')
if subprocess.check_output([sys.argv[3],'--version']).decode().splitlines()[0]!='cmake version '+lock['toolchain']['cmakeVersion']:raise SystemExit('CMake version mismatch')
PY
mkdir -p "$BUILD/java/classes" "$BUILD/java/headers" "$ROOT/logs"
"$JAVA_HOME/bin/javac" --release 8 -encoding UTF-8 -h "$BUILD/java/headers" -d "$BUILD/java/classes" "$ROOT"/.work/upstream/java/src/main/java/ai/onnxruntime/extensions/*.java
STAGE=$(mktemp -d "$ROOT/.work/artifacts-stage.XXXXXXXX")
for ABI in arm64-v8a x86_64; do
  "$CMAKE" --fresh -S "$ROOT/integration" -B "$BUILD/$ABI" -G 'Unix Makefiles' \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
    -DCMAKE_C_FLAGS= -DCMAKE_CXX_FLAGS= -DCMAKE_C_FLAGS_RELEASE="-O3 -DNDEBUG" -DCMAKE_CXX_FLAGS_RELEASE="-O3 -DNDEBUG" \
    -DCMAKE_TOOLCHAIN_FILE="$ANDROID_NDK_HOME/build/cmake/android.toolchain.cmake" \
    -DANDROID_ABI="$ABI" -DANDROID_PLATFORM=android-26 -DANDROID_STL=c++_static \
    -DORTX_SOURCE="$ROOT/.work/upstream" -DORTX_DEPS="$ROOT/.work/deps" \
    -DORTX_JAVA_HEADERS="$BUILD/java/headers" 2>&1 | tee "$ROOT/logs/configure-$ABI.log"
  "$CMAKE" --build "$BUILD/$ABI" --target extensions_shared onnxruntime_extensions4j_jni --parallel "$JOBS" 2>&1 | tee "$ROOT/logs/build-$ABI.log"
  mkdir -p "$STAGE/$ABI" "$ROOT/.work/symbols/$ABI"
  for LIB in libortextensions.so libonnxruntime_extensions4j_jni.so; do
    cp "$BUILD/$ABI/lib/$LIB" "$ROOT/.work/symbols/$ABI/$LIB"
    cp "$BUILD/$ABI/lib/$LIB" "$STAGE/$ABI/$LIB"
    "$ANDROID_NDK_HOME/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-strip" --strip-unneeded "$STAGE/$ABI/$LIB"
  done
done
python3 "$ROOT/scripts/prepare.py"
python3 "$ROOT/scripts/package_aar.py" --stage "$STAGE" --classes "$BUILD/java/classes"
python3 "$ROOT/scripts/verify_artifacts.py" --artifacts "$STAGE" --output "$STAGE/manifest.json"
python3 - "$ROOT" "$STAGE" <<'PY'
import os,pathlib,sys,time
root,stage=map(lambda s:pathlib.Path(s).resolve(),sys.argv[1:]);dest=root/'artifacts'
if stage.parent!=root/'.work' or not stage.name.startswith('artifacts-stage.'):raise SystemExit('Unexpected stage')
if dest.is_symlink():raise SystemExit('Refusing symlink artifact destination')
backup=root/'.work'/('artifacts-previous-'+str(time.time_ns()))
if dest.exists():os.rename(dest,backup)
try:os.rename(stage,dest)
except BaseException:
 if backup.exists():os.rename(backup,dest)
 raise
print('Verified artifacts published:',dest)
PY
