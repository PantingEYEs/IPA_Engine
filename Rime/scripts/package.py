#!/usr/bin/env python3
"""Package the verified two-ABI JNI build and its Java interface deterministically."""
# Copyright (c) 2026 PantingEYEs. All rights reserved.
from __future__ import annotations
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import zipfile
import build_provenance
import subprocess
import sys
import verify_notices

ROOT = Path(__file__).resolve().parents[1]
ABIS = ("arm64-v8a", "x86_64")
AAR = "ipa-rime-android-1.17.0-opencc-1.4.2.aar"

def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def archive(entries: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            output.writestr(info, data, compresslevel=9)
    return stream.getvalue()

def publish(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        output.write(data)
    os.replace(temporary, path)

def main() -> None:
    subprocess.run([sys.executable, str(ROOT / 'scripts/prepare_resources.py')],
                   check=True, stdout=subprocess.DEVNULL)
    verify_notices.verify(ROOT)
    build = Path(os.environ.get("RIME_BUILD_DIR", ROOT / ".work/build"))
    classes_root = build / "java/classes"
    classes = {p.relative_to(classes_root).as_posix(): p.read_bytes() for p in classes_root.rglob("*.class")}
    if set(classes) != {"org/ipaboard/rime/RimeNative.class"}:
        raise ValueError("Expected exactly the compiled RimeNative interface")
    identity = (ROOT / ".work/bridge-stage/build-id.txt").read_text().strip()
    if len(identity) != 64 or any(c not in "0123456789abcdef" for c in identity):
        raise ValueError("Invalid native source identity")
    provenance = build_provenance.verify_link_provenance(root=ROOT)
    if identity != provenance['buildId']:
        raise ValueError("Native build is stale relative to reviewed sources")
    libraries = {abi: (ROOT / f".work/bridge-stage/{abi}/libipa_rime.so").read_bytes() for abi in ABIS}
    if any((b';build=' + identity.encode()) not in data for data in libraries.values()):
        raise ValueError('A staged ABI library is stale relative to the verified build identity')
    entries = {
        "AndroidManifest.xml": b'<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="org.ipaboard.rime"><uses-sdk android:minSdkVersion="26"/></manifest>\n',
        "classes.jar": archive(classes),
        "R.txt": b"",
        "proguard.txt": b"-keep class org.ipaboard.rime.RimeNative { *; }\n",
        "META-INF/ipa-rime-build-id.txt": (identity + "\n").encode(),
    }
    for folder, label in [(ROOT / "licenses", "Rime"), (ROOT.parent / "OpenCC/licenses", "OpenCC")]:
        if not (folder / "README.md").is_file():
            raise ValueError(f"License inventory missing: {label}")
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                entries[f"META-INF/licenses/{label}/{path.relative_to(folder).as_posix()}"] = path.read_bytes()
    for abi, data in libraries.items():
        entries[f"jni/{abi}/libipa_rime.so"] = data
    aar = archive(entries)
    resource_entries = {p.relative_to(ROOT / "resources").as_posix(): p.read_bytes()
                        for p in (ROOT / "resources").rglob("*") if p.is_file()}
    if not resource_entries:
        raise ValueError("Missing compatibility resources")
    resource_zip = archive(resource_entries)
    files = {f"{abi}/libipa_rime.so": data for abi, data in libraries.items()}
    files[AAR] = aar
    files["ipa-rime-resources.zip"] = resource_zip
    manifest = {
        "schemaVersion": 1, "interfaceVersion": 1, "candidatePacketVersion": 3,
        "buildId": identity, "librimeVersion": "1.17.0", "openccVersion": "1.4.2",
        "androidApi": 26, "pageSize": 16384,
        "files": {name: {"sha256": sha(data), "size": len(data)} for name, data in files.items()},
        "resourceLockSha256": sha((ROOT / "resource-sources.lock.json").read_bytes()),
        "sourceLockSha256": sha((ROOT / "sources.lock.json").read_bytes()),
        "buildProvenance": provenance,
    }
    for name, data in files.items():
        publish(ROOT / "artifacts" / name, data)
    publish(ROOT / "artifacts/manifest.json", (json.dumps(manifest, indent=2) + "\n").encode())
    print(json.dumps({"buildId": identity, "packaged": manifest["files"]}, indent=2))

if __name__ == "__main__":
    main()
