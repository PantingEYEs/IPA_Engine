"""Build and optionally run an isolated APK testing the actual AAR's Java/JNI/assets.

Without --adb this only builds. It never installs or modifies an input-method
host. The probe has its own package and private dictionary cache.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import random
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "com.ipaengine.graph.probe"
INSTRUMENTATION = PACKAGE + "/" + PACKAGE + ".GraphProbe"


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1048576), b""):
            value.update(block)
    return value.hexdigest()


def run(command, timeout=120):
    process = subprocess.run(list(map(str, command)), text=True, capture_output=True, timeout=timeout)
    if process.returncode:
        raise RuntimeError("command failed: " + " ".join(map(str, command)) + "\n" + process.stdout + process.stderr)
    return process.stdout + process.stderr


def version(path):
    return tuple(map(int, re.findall(r"\d+", Path(path).name)))


def toolchain(sdk):
    candidates = sorted((sdk / "build-tools").glob("*"), key=version, reverse=True)
    tools = next((path for path in candidates if all((path / name).is_file()
                 for name in ("aapt2", "d8", "zipalign", "apksigner"))), None)
    platforms = sorted((sdk / "platforms").glob("*/android.jar"), key=lambda p: version(p.parent), reverse=True)
    if tools is None or not platforms:
        raise RuntimeError("Android build tools/platform jar missing; provide --sdk with aapt2, D8, zipalign and apksigner")
    javac = shutil.which("javac")
    keytool = shutil.which("keytool")
    if not javac or not keytool:
        raise RuntimeError("JDK javac and keytool required")
    return tools, platforms[0], javac, keytool


def add(archive, filename, name, stored=False):
    info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, Path(filename).read_bytes())


def build(aar, sdk, output_dir, case_path):
    if not aar.is_file():
        raise RuntimeError("full AAR not found: " + str(aar))
    tools, android_jar, javac, keytool = toolchain(sdk)
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="probe-build-", dir=output_dir) as temporary:
        work = Path(temporary)
        required = ("classes.jar", "jni/arm64-v8a/libipa_graph_android.so",
                    "assets/ipa/lexicon.ipad", "assets/ipa/lexicon.manifest.json")
        with zipfile.ZipFile(aar) as source:
            for name in required:
                if name not in source.namelist():
                    raise RuntimeError("AAR lacks required packaged component: " + name)
                destination = work / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                with source.open(name) as incoming, destination.open("wb") as target:
                    shutil.copyfileobj(incoming, target)
        dictionary = work / required[2]
        manifest_data = json.loads((work / required[3]).read_text())
        if (not manifest_data.get("complete") or manifest_data.get("status") != "complete"
                or dictionary.stat().st_size != manifest_data.get("bytes")
                or digest(dictionary) != manifest_data.get("sha256")):
            raise RuntimeError("AAR asset size/completeness/SHA256 verification failed")
        if not case_path.is_file():
            raise RuntimeError("balanced source case JSONL missing: " + str(case_path))
        regions = ("en-US", "en-GB", "zh-CN", "zh-TW", "ja-JP")
        groups = {region: [] for region in regions}
        for line in case_path.read_text().splitlines():
            if not line.strip():
                continue
            case = json.loads(line)
            selected = case.get("regions") or []
            if len(selected) != 1 or selected[0] not in groups:
                raise RuntimeError("benchmark cases require one supported region per source observation")
            raw = case["raw"]
            if (not isinstance(raw, str) or not raw or len(raw.encode("utf-16-le")) // 2 > 64
                    or "\n" in raw or "\r" in raw):
                raise RuntimeError("invalid benchmark IPA in source cases")
            groups[selected[0]].append(raw)
        generator = random.Random(1717)
        for region in regions:
            if len(groups[region]) < 1000:
                raise RuntimeError("benchmark requires at least 1000 source cases for " + region)
            groups[region] = generator.sample(groups[region], 1000)
        # Interleave regions so warmup and every repeated batch stay balanced.
        raw_cases = [groups[region][index] for index in range(1000) for region in regions]
        case_asset = work / "probe/ipa-cases.txt"
        case_asset.parent.mkdir()
        case_asset.write_text("\n".join(raw_cases) + "\n")
        case_metadata = {"cases": 5000, "source_jsonl": str(case_path.resolve()),
                         "regions": {region: 1000 for region in regions}, "seed": 1717,
                         "ordering": "region-interleaved", "sha256": digest(case_asset),
                         "scope": "probe APK only; not included in either published AAR"}
        case_manifest = case_asset.parent / "cases.manifest.json"
        case_manifest.write_text(json.dumps(case_metadata, ensure_ascii=False) + "\n")
        classes = work / "probe-classes"
        classes.mkdir()
        run([javac, "--release", "8", "-encoding", "UTF-8", "-classpath",
             str(android_jar) + os.pathsep + str(work / "classes.jar"), "-d", classes,
             ROOT / "android/tests/GraphProbe.java"])
        probe_jar = work / "probe.jar"
        with zipfile.ZipFile(probe_jar, "w") as archive:
            for filename in sorted(classes.rglob("*.class")):
                add(archive, filename, filename.relative_to(classes).as_posix())
        dex = work / "dex"
        dex.mkdir()
        run([tools / "d8", "--release", "--min-api", "26", "--lib", android_jar,
             "--output", dex, work / "classes.jar", probe_jar])
        manifest = work / "AndroidManifest.xml"
        manifest.write_text('<?xml version="1.0" encoding="utf-8"?>\n'
            '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="' + PACKAGE + '">\n'
            ' <uses-sdk android:minSdkVersion="26" android:targetSdkVersion="35"/>\n'
            ' <application android:label="IPA graph probe" android:debuggable="true" '
            'android:allowBackup="false" android:extractNativeLibs="false"/>\n'
            ' <instrumentation android:name="' + PACKAGE + '.GraphProbe" android:targetPackage="' + PACKAGE + '" '
            'android:functionalTest="true"/>\n</manifest>\n')
        base = work / "base.apk"
        run([tools / "aapt2", "link", "-I", android_jar, "--manifest", manifest,
             "--min-sdk-version", "26", "--target-sdk-version", "35", "-o", base])
        unsigned = work / "unaligned.apk"
        shutil.copyfile(base, unsigned)
        with zipfile.ZipFile(unsigned, "a") as archive:
            for filename in sorted(dex.glob("*.dex")):
                add(archive, filename, filename.name)
            add(archive, work / required[1], "lib/arm64-v8a/libipa_graph_android.so", stored=True)
            add(archive, dictionary, required[2], stored=True)
            add(archive, work / required[3], required[3])
            add(archive, case_asset, "assets/probe/ipa-cases.txt")
            add(archive, case_manifest, "assets/probe/cases.manifest.json")
        aligned = work / "aligned.apk"
        run([tools / "zipalign", "-P", "16", "-f", "4", unsigned, aligned])
        keystore = output_dir / "probe-debug.keystore"
        if not keystore.exists():
            run([keytool, "-genkeypair", "-noprompt", "-keystore", keystore, "-storepass", "android",
                 "-keypass", "android", "-alias", "androiddebugkey", "-keyalg", "RSA", "-keysize", "2048",
                 "-validity", "10000", "-dname", "CN=IPAEngineProbe, OU=Development, O=IPAEngine, C=CN"])
        apk = output_dir / "ipa-graph-probe.apk"
        pending = work / "signed.apk"
        run([tools / "apksigner", "sign", "--ks", keystore, "--ks-key-alias", "androiddebugkey",
             "--ks-pass", "pass:android", "--key-pass", "pass:android", "--out", pending, aligned])
        signature = run([tools / "apksigner", "verify", "--verbose", "--print-certs", pending])
        run([tools / "zipalign", "-c", "-P", "16", "4", pending])
        shutil.copyfile(pending, apk)
    return {"status": "built", "package": PACKAGE, "instrumentation": INSTRUMENTATION,
            "apk": str(apk.resolve()), "apk_bytes": apk.stat().st_size, "apk_sha256": digest(apk),
            "aar": str(aar.resolve()), "aar_sha256": digest(aar),
            "dictionary_bytes": manifest_data["bytes"], "dictionary_sha256": manifest_data["sha256"],
            "build_tools": str(tools), "android_jar": str(android_jar), "min_sdk": 26,
            "target_sdk": 35, "abi": "arm64-v8a", "signature_verified": True,
            "benchmark_case_asset": case_metadata,
            "native_zip_alignment_kib": 16, "signature_details": signature.strip(),
            "android_device_execution": False}


def execute(adb, serial, build_report, warmup, iterations, soak_seconds, query_rate):
    command = [adb]
    if serial:
        command += ["-s", serial]
    run(command + ["get-state"], timeout=30)
    # Only the isolated probe is installed. No input method or host app is changed.
    installation = run(command + ["install", "-r", build_report["apk"]], timeout=120)
    output = run(command + ["shell", "am", "instrument", "-w", "-r", "-e", "warmup", str(warmup),
                           "-e", "iterations", str(iterations), "-e", "soak_seconds", str(soak_seconds),
                           "-e", "query_rate", str(query_rate), INSTRUMENTATION],
                 timeout=max(180, math.ceil(soak_seconds) + 180))
    reports = [line.split("INSTRUMENTATION_RESULT: report=", 1)[1]
               for line in output.splitlines() if line.startswith("INSTRUMENTATION_RESULT: report=")]
    if not reports:
        raise RuntimeError("instrumentation produced no structured report:\n" + output)
    result = json.loads(reports[-1])
    result["serial"] = serial
    result["install_output"] = installation.strip()
    result["instrumentation_output"] = output
    if result.get("package") != PACKAGE:
        raise RuntimeError("instrumentation report package mismatch")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aar", type=Path, default=ROOT / "data/android/24/ipa-graph-full.aar")
    parser.add_argument("--sdk", type=Path, default=Path(os.environ.get("ANDROID_SDK_ROOT")
                        or os.environ.get("ANDROID_HOME") or (Path.home() / "Library/Android/sdk"
                        if platform.system() == "Darwin" else Path.home() / "Android/Sdk")))
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/android/probe")
    parser.add_argument("--output", type=Path, default=ROOT / "data/reports/android-wrapper-probe.json")
    parser.add_argument("--benchmark-cases", type=Path, default=ROOT / "data/compiled/source-cases.jsonl")
    parser.add_argument("--warmup", type=int, default=1000)
    parser.add_argument("--iterations", type=int, default=10000)
    parser.add_argument("--soak-seconds", type=float, default=0, help="optional paced endurance test; default disabled")
    parser.add_argument("--query-rate", type=float, default=10, help="paced soak queries per second")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--build-only", action="store_true")
    mode.add_argument("--adb", nargs="?", const="auto", help="install and run probe, optionally with this adb executable")
    parser.add_argument("--serial", help="specific authorized device serial")
    args = parser.parse_args()
    if args.warmup < 0 or args.warmup > 1000000 or args.iterations < 1 or args.iterations > 1000000:
        parser.error("warmup must be 0..1000000; iterations must be 1..1000000")
    if not math.isfinite(args.soak_seconds) or not 0 <= args.soak_seconds <= 3600:
        parser.error("soak-seconds must be finite and between 0 and 3600")
    if not math.isfinite(args.query_rate) or not 1 <= args.query_rate <= 60:
        parser.error("query-rate must be finite and between 1 and 60")
    report = {"status": "error", "android_device_execution": False}
    failed = False
    try:
        report["build"] = build(args.aar.resolve(), args.sdk.resolve(), args.output_dir.resolve(), args.benchmark_cases.resolve())
        report["status"] = "built"
        if args.adb:
            adb = args.sdk / "platform-tools/adb" if args.adb == "auto" else Path(args.adb)
            if not adb.is_file():
                raise RuntimeError("adb executable missing; supply --adb PATH or --sdk")
            report["device"] = execute(str(adb), args.serial, report["build"], args.warmup, args.iterations,
                                       args.soak_seconds, args.query_rate)
            report["android_device_execution"] = True
            report["status"] = "passed" if report["device"].get("passed") else "failed"
            failed = report["status"] != "passed"
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        report["error"] = str(error)
        report["status"] = "error"
        failed = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    summary = {"status": report["status"], "report": str(args.output.resolve()),
               "android_device_execution": report["android_device_execution"]}
    if "build" in report:
        summary.update(apk=report["build"]["apk"], apk_bytes=report["build"]["apk_bytes"],
                       aar_sha256=report["build"]["aar_sha256"])
    if "device" in report:
        device = report["device"]
        summary.update(model=device.get("model"), sdk_int=device.get("sdk_int"),
                       checks=len(device.get("checks", [])),
                       failed_checks=[check for check in device.get("checks", []) if not check.get("passed")])
        if "wrapper_benchmark" in device:
            summary["wrapper_benchmark"] = device["wrapper_benchmark"]
        if "wrapper_soak" in device:
            summary["wrapper_soak"] = {key: value for key, value in device["wrapper_soak"].items()
                                       if key != "memory_samples"}
        if "fatal_error" in device:
            summary["fatal_error"] = device["fatal_error"]
    if "error" in report:
        summary["error"] = report["error"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
