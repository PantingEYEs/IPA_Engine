#!/usr/bin/env bash
# Rebuild the paired Android JNI libraries and OSS dataset from pinned sources.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
SRC="$ROOT/.work/upstream"
TOOLS="$ROOT/.cache/tools"
# Command substitution propagates validation failures; process substitution alone
# would let mapfile hide a failed source-lock check.
CONFIG=$(python3 "$ROOT/scripts/check_sources.py" --print-config)
mapfile -t LOCK <<< "$CONFIG"
REPOSITORY=${LOCK[0]}
REV=${LOCK[1]}
BAZEL_VERSION=${LOCK[3]}
BAZEL_URL=${LOCK[4]}
BAZEL_SHA=${LOCK[5]}
NDK_RELEASE=${LOCK[6]}
NDK_URL=${LOCK[7]}
NDK_SHA=${LOCK[8]}
ANDROID_TARGETS=("${LOCK[10]}" "${LOCK[11]}")
DATA_TARGET=${LOCK[12]}
ABIS=("${LOCK[13]}" "${LOCK[14]}")
mkdir -p "$TOOLS" "$ROOT/logs" "$ROOT/.work"
if [[ ! -d "$SRC/.git" ]]; then
  git -c credential.helper= clone "$REPOSITORY" "$SRC"
  git -C "$SRC" checkout --detach "$REV"
fi
python3 "$ROOT/scripts/check_sources.py" --source "$SRC" --apply-patch
BAZEL="$TOOLS/bazel-$BAZEL_VERSION-linux-x86_64"
if [[ ! -f "$BAZEL" ]]; then
  curl -fL --retry 3 -o "$BAZEL" "$BAZEL_URL"
fi
printf '%s  %s\n' "$BAZEL_SHA" "$BAZEL" | sha256sum -c -
chmod +x "$BAZEL"
cd "$SRC/src"
NDK_ARCHIVE="third_party_cache/${NDK_URL##*/}"
mkdir -p third_party_cache
if [[ ! -f "$NDK_ARCHIVE" ]]; then
  curl -fL --retry 3 -o "$NDK_ARCHIVE" "$NDK_URL"
fi
printf '%s  %s\n' "$NDK_SHA" "$NDK_ARCHIVE" | sha256sum -c -
NDK_DIRECTORY="third_party/ndk/android-ndk-$NDK_RELEASE"
if [[ ! -f "$NDK_DIRECTORY/source.properties" ]]; then
  mkdir -p third_party/ndk
  if command -v bsdtar >/dev/null; then
    bsdtar -xf "$NDK_ARCHIVE" -C third_party/ndk
  else
    unzip "$NDK_ARCHIVE" -d third_party/ndk
  fi
fi
LLVM_STRIP="$NDK_DIRECTORY/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-strip"
[[ -x "$LLVM_STRIP" ]] || { echo "Missing locked NDK strip tool: $LLVM_STRIP" >&2; exit 1; }
# WSL users may choose a disposable Linux cache for faster compilation.
OUTPUT_ROOT=${MOZC_BAZEL_OUTPUT_ROOT:-$ROOT/.cache/bazel}
JOBS=${MOZC_BUILD_JOBS:-4}
# Only the pinned workspace RC is allowed. Do not alter any personal RC file.
unset BAZELRC
STARTUP=(--nosystem_rc --nohome_rc --noworkspace_rc "--bazelrc=$SRC/src/.bazelrc" --output_user_root="$OUTPUT_ROOT")
if [[ -n ${MOZC_JAVA_TRUSTSTORE:-} ]]; then
  STARTUP+=(--host_jvm_args="-Djavax.net.ssl.trustStore=$MOZC_JAVA_TRUSTSTORE" --host_jvm_args=-Djavax.net.ssl.trustStoreType=PKCS12 --host_jvm_args=-Djavax.net.ssl.trustStorePassword=changeit)
fi
COMMON=(--config=release_build --jobs="$JOBS" --local_resources=cpu="$JOBS" --local_resources=memory=8192 --noshow_progress --color=no --curses=no)
# Stage beside the repository so rename publication cannot silently copy across
# filesystems. Nothing below writes the existing artifacts until verification.
STAGE=$(mktemp -d "$ROOT/.work/artifacts-stage.XXXXXXXX")
cleanup() {
  status=$?
  trap - EXIT INT TERM
  python3 - "$ROOT" "$STAGE" <<'PY'
from pathlib import Path
import shutil
import sys

root, stage = (Path(value).resolve() for value in sys.argv[1:])
if stage.parent != root / ".work" or not stage.name.startswith("artifacts-stage."):
    raise SystemExit("Refusing to clean an unexpected staging path")
if stage.exists():
    shutil.rmtree(stage)
PY
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
"$BAZEL" "${STARTUP[@]}" build --config=oss_android "${COMMON[@]}" "${ANDROID_TARGETS[@]}" 2>&1 | tee "$ROOT/logs/android-build.log"
for index in 0 1; do
  abi=${ABIS[index]}
  lib=$("$BAZEL" "${STARTUP[@]}" cquery --config=oss_android --config=release_build "${ANDROID_TARGETS[index]}" --output=files --noshow_progress 2>"$ROOT/logs/cquery-$abi.log")
  [[ -f "$lib" ]] || { echo "Missing output for $abi: $lib" >&2; exit 1; }
  mkdir -p "$STAGE/$abi" "$ROOT/.work/symbols/$abi"
  # Retain the full library privately for diagnostics; only strip the staged copy.
  cp "$lib" "$ROOT/.work/symbols/$abi/libmozc.so"
  cp "$lib" "$STAGE/$abi/libmozc.so"
  "$LLVM_STRIP" --strip-unneeded "$STAGE/$abi/libmozc.so"
done
"$BAZEL" "${STARTUP[@]}" build --config=oss_linux "${COMMON[@]}" "$DATA_TARGET" 2>&1 | tee "$ROOT/logs/dataset-build.log"
# Resolve the actual configured dataset output, rather than trusting bazel-bin.
data_files=$("$BAZEL" "${STARTUP[@]}" cquery --config=oss_linux --config=release_build "$DATA_TARGET" --output=files --noshow_progress 2>"$ROOT/logs/cquery-dataset.log")
data=""
while IFS= read -r candidate; do
  if [[ "${candidate##*/}" == mozc.data ]]; then
    [[ -z "$data" ]] || { echo 'Dataset target returned multiple mozc.data outputs' >&2; exit 1; }
    data=$candidate
  fi
done <<< "$data_files"
[[ -n "$data" && -f "$data" ]] || { echo 'Missing mozc.data output' >&2; exit 1; }
cp "$data" "$STAGE/mozc.data"
# Recheck sources after compilation to catch edits made while the build ran.
python3 "$ROOT/scripts/check_sources.py" --source "$SRC"
python3 "$ROOT/scripts/verify_artifacts.py" --artifacts "$STAGE" --output "$STAGE/manifest.json"
python3 - "$ROOT" "$STAGE" <<'PY'
from pathlib import Path
import os
import shutil
import signal
import sys
import tempfile

root, stage = (Path(value).resolve() for value in sys.argv[1:])
destination = root / "artifacts"
if stage.parent != root / ".work" or not stage.name.startswith("artifacts-stage."):
    raise SystemExit("Refusing to publish an unexpected staging path")
if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
    raise SystemExit("Existing artifacts must be an ordinary directory")
if destination.exists() and destination.resolve().parent != root:
    raise SystemExit("Existing artifacts escape the repository")
backup = Path(tempfile.mkdtemp(prefix="artifacts-backup.", dir=root / ".work"))
previous = backup / "artifacts"

def interrupted(signum, frame):
    raise InterruptedError(f"Publication interrupted by signal {signum}")

signal.signal(signal.SIGTERM, interrupted)
signal.signal(signal.SIGINT, interrupted)
try:
    # os.rename fails rather than copying on an unexpected filesystem boundary.
    if destination.exists():
        os.rename(destination, previous)
    os.rename(stage, destination)
    # Establish the commit point before leaving the protected block.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
except BaseException:
    # Roll back even when interruption arrived between the two rename calls,
    # or immediately after the new directory became visible.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        if not stage.exists() and destination.exists():
            os.rename(destination, stage)
        if previous.exists():
            os.rename(previous, destination)
    except BaseException:
        print(f"Publication rollback needs attention; old artifacts preserved at {previous}",
              file=sys.stderr)
        raise
    else:
        shutil.rmtree(backup, ignore_errors=True)
    raise
else:
    # The new complete set is committed. Cleanup failure must not turn a
    # successful publication into a reported failed build.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        shutil.rmtree(backup)
    except OSError as error:
        print(f"Published successfully; previous artifacts remain at {backup}: {error}",
              file=sys.stderr)
    print(f"Published verified artifacts to {destination}")
PY
