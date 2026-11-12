#!/usr/bin/env python3
# Copyright (c) 2026 PantingEYEs. All rights reserved.
# No project-level software license has been selected.
"""Prepare or verify the pinned Chinese resource profile without Trime inputs.

The default action is a deterministic, offline verification. --prepare rebuilds
the files from hash-checked upstream inputs; --offline forbids all downloads.
This checks source bytes and generation, not native or Android compatibility.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
POLICY_PREFIX = (
    "# Copyright (c) 2026 PantingEYEs. All rights reserved.\n"
    "# Project-authored policy overlay; no project license has been selected.\n"
)


class ResourceError(ValueError):
    pass


def policy_bytes(name: str) -> bytes:
    if name == "default.custom.yaml":
        text = "patch:\n  schema_list:\n    - schema: luna_pinyin\n    - schema: terra_pinyin\n    - schema: bopomofo\n"
    elif name in ("luna_pinyin.custom.yaml", "terra_pinyin.custom.yaml", "bopomofo.custom.yaml"):
        text = "patch:\n  translator/enable_user_dict: false\n  translator/enable_completion: false\n  switches/@0/reset: 0\n"
        if name != "luna_pinyin.custom.yaml":
            text += "  switches/@2/reset: 0\n"
    else:
        raise ResourceError("Unknown policy: " + name)
    return (POLICY_PREFIX + text).encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def opencc_config_bytes(data: bytes) -> bytes:
    # OpenCC 1.4's schema requires the formerly implicit short-circuit policy.
    # Keep the pinned conversion chains and dictionary priority unchanged.
    marker = b'"type": "group",\n'
    if data.count(marker) != 1 or b'"match_policy"' in data:
        raise ResourceError("Unexpected shape of pinned OpenCC compatibility config")
    return (data.replace(b'"ocd2"', b'"text"').replace(b".ocd2", b".txt")
            .replace(marker, marker + b'      "match_policy": "short_circuit",\n'))


def safe_path(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or "\\" in relative:
        raise ResourceError("Unsafe output path: " + relative)
    if not relative.startswith(("resources/", "licenses/resources/")):
        raise ResourceError("Output outside the resource profile: " + relative)
    target = root.joinpath(*path.parts).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ResourceError("Output escapes the requested root")
    return target


def load_lock(path: Path) -> dict:
    lock = json.loads(path.read_text(encoding="utf-8"))
    if lock.get("schemaVersion") != 1 or lock.get("profile") != "ipa-board-chinese-compat-v1":
        raise ResourceError("Unsupported resource manifest")
    sources = {item["id"]: item for item in lock["sources"]}
    if len(sources) != len(lock["sources"]):
        raise ResourceError("Duplicate source identity")
    seen = set()
    for item in lock["files"]:
        output = item["output"]
        if output in seen or item["sourceId"] not in sources:
            raise ResourceError("Duplicate output or missing source: " + output)
        seen.add(output)
        if not re.fullmatch(r"[a-f0-9]{64}", item["sha256"]) or type(item["sizeBytes"]) is not int or item["sizeBytes"] < 1:
            raise ResourceError("Invalid output identity: " + output)
        safe_path(path.parent, output)
        if item["transform"] == "project-policy-v1":
            if item["sourceId"] != "project-policy":
                raise ResourceError("Project overlay uses an upstream source identity")
        elif item["transform"] not in ("copy", "opencc-text-dictionaries-v2") or not re.fullmatch(r"[a-f0-9]{64}", item.get("sourceSha256", "")):
            raise ResourceError("Unsupported transform or source hash: " + output)
    return lock


def source_url(source: dict, item: dict) -> str:
    if "url" in source:
        url = source["url"]
    else:
        repository = source["repository"]
        revision = source["commit"]
        path = item["upstreamPath"]
        if not repository.startswith("https://github.com/") or not re.fullmatch(r"[a-f0-9]{40}", revision):
            raise ResourceError("Source is not pinned to a GitHub commit")
        if PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
            raise ResourceError("Unsafe upstream path")
        url = repository.replace("https://github.com/", "https://raw.githubusercontent.com/", 1) + "/" + revision + "/" + path
    if not url.startswith("https://"):
        raise ResourceError("Only HTTPS sources are accepted")
    return url


def read_source(source: dict, item: dict, cache: Path, offline: bool) -> bytes:
    expected = item["sourceSha256"]
    cached = cache / expected
    if cached.is_file():
        data = cached.read_bytes()
    else:
        if offline:
            raise ResourceError("Source missing from offline cache: " + item["output"])
        request = urllib.request.Request(source_url(source, item), headers={"User-Agent": "IPA-Engine-resource-builder"})
        with urllib.request.urlopen(request, timeout=45) as response:
            data = response.read(64 * 1024 * 1024 + 1)
        if len(data) > 64 * 1024 * 1024:
            raise ResourceError("Source exceeds the resource size limit")
    if digest(data) != expected:
        raise ResourceError("Upstream/cache hash mismatch: " + item["output"])
    if not cached.exists():
        cache.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
    return data


def prepare(lock: dict, root: Path, cache: Path, offline: bool) -> None:
    sources = {item["id"]: item for item in lock["sources"]}
    prepared = {}
    for item in lock["files"]:
        if item["transform"] == "project-policy-v1":
            data = policy_bytes(item["policyId"])
        else:
            data = read_source(sources[item["sourceId"]], item, cache, offline)
            if item["transform"] == "opencc-text-dictionaries-v2":
                data = opencc_config_bytes(data)
        if len(data) != item["sizeBytes"] or digest(data) != item["sha256"]:
            raise ResourceError("Generated output differs from the lock: " + item["output"])
        prepared[safe_path(root, item["output"])] = data
    # Do not publish any output until all inputs and generated bytes have passed.
    for destination, data in prepared.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".prepare-", delete=False) as stream:
                temp = Path(stream.name)
                stream.write(data)
            os.replace(temp, destination)
        finally:
            if temp is not None and temp.exists():
                temp.unlink()


def verify(lock: dict, root: Path) -> dict:
    for item in lock["files"]:
        path = safe_path(root, item["output"])
        data = path.read_bytes()
        if len(data) != item["sizeBytes"] or digest(data) != item["sha256"]:
            raise ResourceError("Prepared resource differs from lock: " + item["output"])
        if item["transform"] == "project-policy-v1" and data != policy_bytes(item["policyId"]):
            raise ResourceError("Policy contract differs from generator")
    expected = {item["output"] for item in lock["files"] if item["output"].startswith("resources/")}
    actual = {path.relative_to(root).as_posix() for path in (root / "resources").rglob("*") if path.is_file()}
    if actual != expected:
        raise ResourceError("Unlisted or missing runtime resources: " + repr(sorted(actual ^ expected)))
    return {"verified": True, "profile": lock["profile"], "resources": len(expected), "licenseFiles": len(lock["files"]) - len(expected), "nativeExecutionIncluded": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", help="Rebuild pinned resources before verification")
    parser.add_argument("--offline", action="store_true", help="Reject missing cached inputs instead of downloading")
    parser.add_argument("--manifest", type=Path, default=ROOT / "resource-sources.lock.json")
    parser.add_argument("--output", type=Path, default=ROOT)
    parser.add_argument("--cache", type=Path, default=ROOT / ".work/resource-cache")
    args = parser.parse_args()
    try:
        lock = load_lock(args.manifest)
        if args.prepare:
            prepare(lock, args.output.resolve(), args.cache, args.offline)
        print(json.dumps(verify(lock, args.output.resolve())))
    except (ResourceError, OSError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
