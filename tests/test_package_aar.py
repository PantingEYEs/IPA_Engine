"""Deterministic packaging and incomplete-input rejection, without Android tooling."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "package_aar.py"
SPEC = importlib.util.spec_from_file_location("ortx_package_aar", SCRIPT)
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)
AAR = "onnxruntime-extensions-android-0.13.0-ipa-e5-16kb.aar"
LIBS = ("libortextensions.so", "libonnxruntime_extensions4j_jni.so")


class PackageAarTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.stage = self.root / "stage"
        self.stage.mkdir()
        self.classes = self.root / "classes"
        namespace = self.classes / "ai/onnxruntime/extensions"
        namespace.mkdir(parents=True)
        for name in ("OrtxPackage", "OrtxLibrary"):
            (namespace / (name + ".class")).write_bytes(("synthetic class " + name).encode())
        for abi in ("arm64-v8a", "x86_64"):
            (self.stage / abi).mkdir()
            for name in LIBS:
                (self.stage / abi / name).write_bytes((abi + "/" + name).encode())
        manifest = self.root / ".work/upstream/java/src/main/AndroidManifest.xml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text('<manifest package="ai.onnxruntime.extensions"/>', encoding="utf-8")
        self.licenses = self.root / "licenses"
        (self.licenses / "nested").mkdir(parents=True)
        texts = {"LICENSE.txt": b"Original license notice\n", "nested/NOTICE.txt": b"Original component notice\n"}
        files = []
        for name, content in texts.items():
            (self.licenses / name).write_bytes(content)
            files.append({"file": name, "sha256": hashlib.sha256(content).hexdigest(),
                          "component": "Synthetic test component", "source": "https://example.invalid/source"})
        (self.licenses / "sources.json").write_text(json.dumps({"schemaVersion": 1, "files": files}), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def package(self):
        with patch.object(package, "ROOT", self.root), patch("sys.argv", ["package_aar.py", "--stage", str(self.stage), "--classes", str(self.classes)]):
            package.main()
        return (self.stage / AAR).read_bytes()

    def test_repeated_packaging_is_byte_identical_and_members_are_complete(self):
        first = self.package()
        # File timestamps and filesystem enumeration order must not affect archives.
        for path in self.classes.rglob("*.class"):
            path.touch()
        second = self.package()
        self.assertEqual(first, second)
        with zipfile.ZipFile(io.BytesIO(first)) as archive:
            self.assertEqual(archive.namelist(), sorted(archive.namelist()))
            for entry in archive.infolist():
                self.assertEqual(entry.date_time, (2025, 1, 1, 0, 0, 0))
            for abi in ("arm64-v8a", "x86_64"):
                for name in LIBS:
                    self.assertEqual(archive.read(f"jni/{abi}/{name}"), (self.stage / abi / name).read_bytes())
            for path in self.licenses.rglob("*"):
                if path.is_file():
                    relative = path.relative_to(self.licenses).as_posix()
                    self.assertEqual(archive.read("META-INF/licenses/" + relative), path.read_bytes())
            with zipfile.ZipFile(io.BytesIO(archive.read("classes.jar"))) as jar:
                self.assertEqual(set(jar.namelist()), {"ai/onnxruntime/extensions/OrtxPackage.class", "ai/onnxruntime/extensions/OrtxLibrary.class"})
        self.assertFalse((self.stage / "classes.jar").exists())

    def test_min_sdk_is_a_manifest_runtime_requirement(self):
        with zipfile.ZipFile(io.BytesIO(self.package())) as archive:
            manifest = ET.fromstring(archive.read("AndroidManifest.xml"))
            self.assertEqual(manifest.attrib["package"], "ai.onnxruntime.extensions")
            uses = manifest.find("uses-sdk")
            self.assertIsNotNone(uses)
            self.assertEqual(uses.attrib["{http://schemas.android.com/apk/res/android}minSdkVersion"], "26")
            metadata = archive.read("META-INF/com/android/build/gradle/aar-metadata.properties")
            self.assertIn(b"minCompileSdk=26\n", metadata)

    def test_missing_or_extra_class_rejected(self):
        extra = self.classes / "Unrelated.class"
        extra.write_bytes(b"unexpected")
        with self.assertRaises(SystemExit):
            self.package()
        extra.unlink()
        (self.classes / "ai/onnxruntime/extensions/OrtxLibrary.class").unlink()
        with self.assertRaises(SystemExit):
            self.package()
        self.assertFalse((self.stage / AAR).exists())

    def test_missing_or_extra_native_library_rejected(self):
        target = self.stage / "x86_64" / LIBS[1]
        original = target.read_bytes()
        target.unlink()
        with self.assertRaises(SystemExit):
            self.package()
        target.write_bytes(original)
        (target.parent / "libobsolete.so").write_bytes(b"unexpected")
        with self.assertRaises(SystemExit):
            self.package()
        self.assertFalse((self.stage / AAR).exists())

    def test_license_hash_missing_file_and_empty_text_rejected(self):
        target = self.licenses / "LICENSE.txt"
        original = target.read_bytes()
        target.write_bytes(b"tampered license")
        with self.assertRaises((SystemExit, ValueError)):
            self.package()
        target.unlink()
        with self.assertRaises((SystemExit, OSError, ValueError)):
            self.package()
        target.write_bytes(b"")
        index = self.licenses / "sources.json"
        sources = json.loads(index.read_text())
        sources["files"][0]["sha256"] = hashlib.sha256(b"").hexdigest()
        index.write_text(json.dumps(sources))
        with self.assertRaises((SystemExit, ValueError)):
            self.package()
        self.assertFalse((self.stage / AAR).exists())

    def test_only_registered_notices_are_packaged_regardless_of_suffix(self):
        (self.licenses / "unlisted-private-note.txt").write_bytes(b"must not be packaged")
        relative = "nested/toolchain.properties"
        content = b"Pkg.Revision = 29.0.14206865\n"
        (self.licenses / relative).write_bytes(content)
        index = self.licenses / "sources.json"
        inventory = json.loads(index.read_text())
        inventory["files"].append({"file": relative, "sha256": hashlib.sha256(content).hexdigest()})
        index.write_text(json.dumps(inventory))
        with zipfile.ZipFile(io.BytesIO(self.package())) as archive:
            self.assertNotIn("META-INF/licenses/unlisted-private-note.txt", archive.namelist())
            self.assertEqual(archive.read("META-INF/licenses/" + relative), content)
            expected = {"META-INF/licenses/" + item["file"] for item in inventory["files"]}
            expected.add("META-INF/licenses/sources.json")
            actual = {name for name in archive.namelist() if name.startswith("META-INF/licenses/")}
            self.assertEqual(actual, expected)

    def test_windows_notice_paths_rejected_before_access_or_output(self):
        index = self.licenses / "sources.json"
        inventory = json.loads(index.read_text())
        for unsafe in ("..\\outside.txt", "C:\\outside.txt", "C:/outside.txt", "\\\\server\\share\\notice.txt"):
            inventory["files"][0]["file"] = unsafe
            index.write_text(json.dumps(inventory))
            with self.subTest(path=unsafe):
                with self.assertRaisesRegex(SystemExit, "Unsafe notice path"):
                    self.package()
                self.assertFalse((self.stage / AAR).exists())

    def test_missing_license_index_rejected(self):
        (self.licenses / "sources.json").unlink()
        with self.assertRaises((SystemExit, OSError, ValueError)):
            self.package()
        self.assertFalse((self.stage / AAR).exists())


if __name__ == "__main__":
    unittest.main()
