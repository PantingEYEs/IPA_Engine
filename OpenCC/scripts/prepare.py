#!/usr/bin/env python3
# Copyright 2026 PantingEYEs. All rights reserved.
"""Fetch and verify official pinned OpenCC sources; build itself is offline."""
from pathlib import Path
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads((ROOT / "sources.lock.json").read_text())
SRC = ROOT / ".work/upstream"
upstream = LOCK["upstream"]

def run(*args):
    return subprocess.check_output(args, text=True).strip()

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

SRC.parent.mkdir(parents=True, exist_ok=True)
if not (SRC / ".git").exists():
    subprocess.run(["git", "clone", "--depth", "1", "--branch", upstream["tag"],
                    upstream["repository"], str(SRC)], check=True)
if run("git", "-C", str(SRC), "rev-parse", "HEAD") != upstream["commit"]:
    raise SystemExit("Existing checkout differs; preserve it and use a fresh work directory.")
if run("git", "-C", str(SRC), "rev-parse", "HEAD^{tree}") != upstream["tree"]:
    raise SystemExit("Unexpected source tree.")
if run("git", "-C", str(SRC), "status", "--porcelain", "--untracked-files=no"):
    raise SystemExit("Pinned source checkout contains modifications.")
for name, dependency in LOCK["dependencies"].items():
    base = SRC / dependency["path"]
    files = sorted((p for p in base.rglob("*") if p.is_file()),
                   key=lambda p: p.relative_to(base).parts)
    fingerprint = hashlib.sha256()
    for path in files:
        fingerprint.update((path.relative_to(base).as_posix() + "\0" + digest(path) + "\n").encode())
    if len(files) != dependency["files"] or fingerprint.hexdigest() != dependency["sha256"]:
        raise SystemExit("Vendored dependency fingerprint mismatch: " + name)
for notice in json.loads((ROOT / "licenses/sources.json").read_text())["notices"]:
    if digest(ROOT / "licenses" / notice["path"]) != notice["sha256"]:
        raise SystemExit("Notice fingerprint mismatch: " + notice["path"])
print("Verified official OpenCC " + upstream["tag"] + " " + upstream["commit"])
