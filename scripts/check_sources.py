#!/usr/bin/env python3
"""Check the locked Mozc source tree without resetting user changes.

Only the declared patch is accepted. Git's executable-bit noise and ignored
build caches are excluded; unignored extra files are rejected because Bazel
globs can otherwise introduce undeclared inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


class SourceError(ValueError):
    pass


def git(directory: Path, *args: str) -> bytes:
    # Local repository contents still apply, but inherited GIT_DIR/INDEX_FILE
    # and personal diff filters must not redirect this verification.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_OPTIONAL_LOCKS="0")
    command = ["git", "-c", "core.filemode=false", "-c", "core.autocrlf=false",
               "-c", "core.safecrlf=false", "-C", str(directory), *args]
    result = subprocess.run(command, env=env, capture_output=True, check=False)
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise SourceError(f"Git {' '.join(args[:2])} failed: {detail}")
    return result.stdout


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SourceError(message)


def load_lock(root: Path) -> tuple[dict, Path]:
    lock = json.loads((root / "sources.lock.json").read_text(encoding="utf-8"))
    require(lock.get("schemaVersion") == 1, "Unsupported sources.lock schemaVersion")
    require(lock.get("component") == "Mozc", "Expected a Mozc source lock")
    upstream, toolchain = lock["upstream"], lock["toolchain"]
    require(re.fullmatch(r"[0-9a-f]{40}", upstream["commit"]) is not None,
            "The upstream commit must be a full lowercase SHA-1")
    for value in (upstream["patchSha256"], toolchain["bazelSha256"],
                  toolchain["androidNdkSha256"]):
        require(re.fullmatch(r"[0-9a-f]{64}", value) is not None,
                "Every locked SHA-256 must contain 64 lowercase hexadecimal digits")
    for value in (upstream["repository"], toolchain["bazelUrl"],
                  toolchain["androidNdkUrl"]):
        require(isinstance(value, str) and value.startswith("https://")
                and not any(character.isspace() for character in value),
                "Locked download URLs must be whitespace-free HTTPS URLs")
    require(toolchain["host"] == "Linux x86_64", "Unsupported build host in lock")
    require(re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", toolchain["bazelVersion"])
            is not None, "Invalid locked Bazel version")
    require(re.fullmatch(r"r[0-9]+[a-z]?", toolchain["androidNdk"]) is not None,
            "Invalid locked NDK release")
    require(isinstance(toolchain["androidApiLevel"], int)
            and 21 <= toolchain["androidApiLevel"] <= 100,
            "Invalid locked Android API level")
    require(re.fullmatch(r"[0-9]+\.[0-9]+", toolchain["pythonMinimum"]) is not None,
            "Invalid minimum Python version")
    minimum = tuple(map(int, toolchain["pythonMinimum"].split(".")))
    require(sys.version_info[:2] >= minimum,
            f"Python {toolchain['pythonMinimum']} or newer is required")
    require(lock["abis"] == ["arm64-v8a", "x86_64"],
            "This publisher/verifier supports arm64-v8a and x86_64, in that order")
    require(len(lock["targets"]) == 3 and all(
        isinstance(target, str)
        and re.fullmatch(r"//[A-Za-z0-9_/.-]+:[A-Za-z0-9_.-]+", target)
        for target in lock["targets"]), "Expected two JNI targets and one dataset target")
    require(lock["minimumElfLoadAlignment"] == 16384,
            "The verifier currently requires 16384-byte load alignment")
    relative_patch = Path(upstream["patch"])
    require(not relative_patch.is_absolute() and ".." not in relative_patch.parts,
            "The patch must be a relative path within the build repository")
    patch = (root / relative_patch).resolve()
    require(patch.is_relative_to(root), "The patch escapes the build repository")
    require(hashlib.sha256(patch.read_bytes()).hexdigest() == upstream["patchSha256"],
            "The compatibility patch SHA-256 does not match sources.lock.json")
    return lock, patch


def config_lines(lock: dict, patch: Path) -> list[str]:
    upstream, toolchain = lock["upstream"], lock["toolchain"]
    values = [
        upstream["repository"], upstream["commit"], str(patch),
        toolchain["bazelVersion"], toolchain["bazelUrl"], toolchain["bazelSha256"],
        toolchain["androidNdk"], toolchain["androidNdkUrl"],
        toolchain["androidNdkSha256"], str(toolchain["androidApiLevel"]),
        *lock["targets"], *lock["abis"],
    ]
    require(all("\n" not in value and "\r" not in value and "\0" not in value
                for value in values), "Lock values must be single-line strings")
    return values


def nul_paths(data: bytes) -> set[str]:
    return {os.fsdecode(value) for value in data.split(b"\0") if value}


def verify_sources(root: Path, source: Path, lock: dict, patch: Path,
                   apply_patch: bool = False) -> dict:
    repository = Path(os.fsdecode(git(source, "rev-parse", "--show-toplevel")).strip())
    require(repository.resolve() == source, "Source path is not its own Git worktree")
    revision = lock["upstream"]["commit"]
    require(git(source, "rev-parse", "HEAD").decode().strip() == revision,
            "Unexpected upstream revision; existing sources were not changed")
    extra = nul_paths(git(source, "ls-files", "--others", "--exclude-standard", "-z"))
    require(not extra, f"Untracked source inputs are not allowed: {sorted(extra)}")

    paths = []
    for entry in git(source, "apply", "--numstat", "-z", str(patch)).split(b"\0"):
        if not entry:
            continue
        parts = entry.split(b"\t", 2)
        require(len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit()
                and parts[2], "Only ordinary text-file modifications are supported")
        name = os.fsdecode(parts[2])
        relative = Path(name)
        require(not relative.is_absolute() and ".." not in relative.parts,
                "Unsafe file path in patch")
        paths.append(name)
    require(paths and len(paths) == len(set(paths)), "Patch paths must be unique")
    changed = nul_paths(git(source, "diff", "--no-ext-diff", "--no-textconv",
                            "--no-renames", "--name-only", "-z", "HEAD", "--"))
    require(not (changed - set(paths)),
            f"Extra tracked source changes are not allowed: {sorted(changed - set(paths))}")

    # Apply the locked patch only to a temporary copy of the original blobs.
    # This leaves the real index/object store untouched during verification.
    with tempfile.TemporaryDirectory(prefix="mozc-source-check-") as temporary:
        expected = Path(temporary)
        git(expected, "-c", "init.templateDir=", "init", "--quiet")
        original = {}
        for name in paths:
            tree_entry = git(source, "ls-tree", revision, "--", name)
            require(tree_entry.startswith((b"100644 blob ", b"100755 blob ")),
                    f"Patch must modify an existing regular file: {name}")
            original[name] = git(source, "show", f"{revision}:{name}")
            path = expected / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(original[name])
        git(expected, "apply", "--whitespace=nowarn", str(patch))
        pristine = not changed
        for name in paths:
            path = source / name
            require(path.is_file() and not path.is_symlink(),
                    f"Source file is missing or is a symlink: {name}")
            expected_bytes = original[name] if pristine else (expected / name).read_bytes()
            require(path.read_bytes() == expected_bytes,
                    f"Source differs from the locked commit plus patch: {name}")
        if pristine:
            require(apply_patch, "Compatibility patch has not been applied")
            git(source, "apply", "--whitespace=nowarn", str(patch))
        for name in paths:
            require((source / name).read_bytes() == (expected / name).read_bytes(),
                    f"Source patch verification failed: {name}")

    module = (source / "src" / "MODULE.bazel").read_text(encoding="utf-8")
    configuration = re.findall(
        r"android_ndk_repository_extension\.configure\(\s*(.*?)\n\)", module, re.S)
    require(len(configuration) == 1, "Expected one Android NDK configuration")
    toolchain = lock["toolchain"]
    api = re.search(r"\bapi_level\s*=\s*(\d+)\s*,", configuration[0])
    ndk = re.search(r'\bpath\s*=\s*"([^"]+)"\s*,', configuration[0])
    require(api is not None and int(api[1]) == toolchain["androidApiLevel"],
            "Patched Android API level disagrees with the source lock")
    require(ndk is not None and ndk[1] ==
            f"$WORKSPACE_ROOT/third_party/ndk/android-ndk-{toolchain['androidNdk']}",
            "Patched Android NDK path disagrees with the source lock")
    return {"verified": True, "upstream_commit": revision,
            "patch_sha256": lock["upstream"]["patchSha256"], "source": str(source)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--print-config", action="store_true")
    parser.add_argument("--apply-patch", action="store_true",
                        help="Apply the locked patch only when the source tree is pristine")
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        lock, patch = load_lock(root)
        if args.print_config:
            print("\n".join(config_lines(lock, patch)))
        else:
            source = (args.source or root / ".work" / "upstream").resolve()
            print(json.dumps(verify_sources(root, source, lock, patch, args.apply_patch),
                             sort_keys=True))
        return 0
    except (SourceError, OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"Source verification failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
