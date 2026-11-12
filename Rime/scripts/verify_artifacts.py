#!/usr/bin/env python3
# Copyright 2026 PantingEYEs. All rights reserved.
"""Offline native/AAR/resource verification. Does not install or execute Android code."""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import io
import json
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
AAR_NAME = "ipa-rime-android-1.17.0-opencc-1.4.2.aar"
ABIS = {"arm64-v8a": 183, "x86_64": 62}
JNI_EXPORTS = {
    "Java_org_ipaboard_rime_RimeNative_" + name
    for name in ("abiVersion", "identity", "open", "query", "convert", "close")
}
spec = importlib.util.spec_from_file_location(
    "opencc_artifact_validation",
    ROOT.parent / "OpenCC/scripts/verify_artifacts.py",
)
elf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(elf)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def verified_provenance(manifest):
    spec = importlib.util.spec_from_file_location("rime_build_provenance", ROOT / "scripts/build_provenance.py")
    provenance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provenance)
    return provenance.verify_published_provenance(manifest["buildProvenance"], root=ROOT)

def source_identity():
    manifest = json.loads((ROOT / "artifacts/manifest.json").read_text())
    return verified_provenance(manifest)["buildId"]

def inspect_native(data, abi, expected_build_id=None):
    result = elf.inspect_elf(
        data, ABIS[abi], expected_soname="libipa_rime.so",
        required_exports=JNI_EXPORTS, exact_exports=True,
    )
    if expected_build_id is not None:
        if len(expected_build_id) != 64 or any(c not in "0123456789abcdef" for c in expected_build_id):
            raise ValueError("Invalid expected native build identity")
        if b";build=" + expected_build_id.encode() not in data:
            raise ValueError("Native source identity mismatch")
        result["identityMatched"] = True
    return result

def archive_entries(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate ZIP entry")
        if any(name.startswith("/") or "\\" in name or ".." in name.split("/") for name in names):
            raise ValueError("Unsafe ZIP entry")
        return {name: archive.read(name) for name in names}

def inspect_aar(data, libraries, expected_build_id, expected_java):
    entries = archive_entries(data)
    expected_native = {f"jni/{abi}/libipa_rime.so" for abi in ABIS}
    actual_native = {name for name in entries if name.startswith("jni/") or name.endswith(".so")}
    if actual_native != expected_native:
        raise ValueError("AAR native entry inventory differs from two supported ABIs")
    for abi, library in libraries.items():
        if entries[f"jni/{abi}/libipa_rime.so"] != library:
            raise ValueError("AAR native bytes differ from standalone artifact: " + abi)
    required = {"classes.jar", "AndroidManifest.xml", "proguard.txt", "R.txt",
                "META-INF/ipa-rime-build-id.txt"}
    if not required <= entries.keys():
        raise ValueError("Missing AAR interface/metadata entry")
    if entries["META-INF/ipa-rime-build-id.txt"].decode().strip() != expected_build_id:
        raise ValueError("AAR build identity mismatch")
    manifest = ET.fromstring(entries["AndroidManifest.xml"])
    sdk = manifest.find("uses-sdk")
    if manifest.tag != "manifest" or manifest.get("package") != "org.ipaboard.rime":
        raise ValueError("Unexpected AAR manifest package")
    if sdk is None or sdk.get("{http://schemas.android.com/apk/res/android}minSdkVersion") != "26":
        raise ValueError("Unexpected AAR minimum Android API")
    classes = archive_entries(entries["classes.jar"])
    if set(classes) != {"org/ipaboard/rime/RimeNative.class"}:
        raise ValueError("Unexpected Java interface class inventory")
    compiled = classes["org/ipaboard/rime/RimeNative.class"]
    if expected_java != {"kind": "file", "sha256": sha(compiled), "sizeBytes": len(compiled)}:
        raise ValueError("AAR Java bytecode differs from native build provenance")
    if compiled[:8] != b"\xca\xfe\xba\xbe\0\0\0\x34":
        raise ValueError("Expected Java 8 class bytecode")
    if b"-keep class org.ipaboard.rime.RimeNative" not in entries["proguard.txt"]:
        raise ValueError("Missing JNI class keep rule")
    expected_notices = {}
    for folder, label in ((ROOT / "licenses", "Rime"), (ROOT.parent / "OpenCC/licenses", "OpenCC")):
        for path in folder.rglob("*"):
            if path.is_file():
                expected_notices[f"META-INF/licenses/{label}/{path.relative_to(folder).as_posix()}"] = path.read_bytes()
    actual_notices = {name: value for name, value in entries.items() if name.startswith("META-INF/licenses/")}
    if actual_notices != expected_notices or not expected_notices:
        raise ValueError("AAR retained license notices differ from reviewed inventory")
    return {"nativeAbis": list(ABIS), "javaClass": "org/ipaboard/rime/RimeNative.class",
            "minimumAndroidApi": 26, "retainedNoticeFiles": len(actual_notices)}

def verify_resources():
    spec = importlib.util.spec_from_file_location("rime_resource_validation", ROOT / "scripts/prepare_resources.py")
    resources = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(resources)
    lock = resources.load_lock(ROOT / "resource-sources.lock.json")
    return resources.verify(lock, ROOT)
def verify_notices():
    spec = importlib.util.spec_from_file_location("rime_notice_validation", ROOT / "scripts/verify_notices.py")
    notices = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(notices)
    return notices.verify(ROOT)
def inspect_package():
    resource_check = verify_resources()
    notice_check = verify_notices()
    folder = ROOT / "artifacts"
    manifest = json.loads((folder / "manifest.json").read_text())
    expected_files = {f"{abi}/libipa_rime.so" for abi in ABIS} | {AAR_NAME, "ipa-rime-resources.zip"}
    if set(manifest["files"]) != expected_files:
        raise ValueError("Published artifact inventory mismatch")
    for name, metadata in manifest["files"].items():
        data = (folder / name).read_bytes()
        if metadata != {"sha256": sha(data), "size": len(data)}:
            raise ValueError("Published artifact hash/size mismatch: " + name)
    for field, filename in (("sourceLockSha256", "sources.lock.json"),
                             ("resourceLockSha256", "resource-sources.lock.json")):
        if manifest[field] != sha((ROOT / filename).read_bytes()):
            raise ValueError("Stale source/resource lock identity")
    proof = verified_provenance(manifest)
    identity = proof["buildId"]
    if manifest["buildId"] != identity:
        raise ValueError("Stale native build identity")
    libraries = {abi: (folder / abi / "libipa_rime.so").read_bytes() for abi in ABIS}
    results = {abi: inspect_native(data, abi, identity) for abi, data in libraries.items()}
    aar = inspect_aar((folder / AAR_NAME).read_bytes(), libraries, identity,
                      proof["link"]["inputs"]["java/RimeNative.class"])
    resources = archive_entries((folder / "ipa-rime-resources.zip").read_bytes())
    expected_resources = {p.relative_to(ROOT / "resources").as_posix(): p.read_bytes()
                          for p in (ROOT / "resources").rglob("*") if p.is_file()}
    if resources != expected_resources or not resources:
        raise ValueError("Resource ZIP differs from reviewed compatibility resources")
    return {"schemaVersion": 1, "buildId": identity, "native": results, "aar": aar,
            "resourceFiles": len(resources), "resourceSourceVerification": resource_check, "noticeSourceVerification": notice_check, "manifestSha256": sha((folder / "manifest.json").read_bytes()),
            "androidRuntimeExecutedByThisCheck": False}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--stage", action="store_true", help="Validate native staging output only")
    args = parser.parse_args()
    if args.stage:
        stage_id = (ROOT / ".work/bridge-stage/build-id.txt").read_text().strip()
        report = {"native": {
            abi: inspect_native((ROOT / ".work/bridge-stage" / abi / "libipa_rime.so").read_bytes(), abi, stage_id)
            for abi in ABIS}}
    else:
        report = inspect_package()
    if args.write_report:
        if args.stage:
            raise ValueError("Staging validation cannot publish a release report")
        (ROOT / "validation").mkdir(exist_ok=True)
        (ROOT / "validation/artifacts.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
