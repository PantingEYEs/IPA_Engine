# Copyright 2026 PantingEYEs. All rights reserved.
"""Exercise compatibility failures against the actual packaged artifact bytes."""
from pathlib import Path
import importlib.util
import io
import json
import struct
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("verify_rime", ROOT / "scripts/verify_artifacts.py")
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)

def archive(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as result:
        for name, value in entries.items():
            result.writestr(name, value)
    return stream.getvalue()

class VerifyArtifactTest(unittest.TestCase):
    def setUp(self):
        self.libraries = {abi: (ROOT / "artifacts" / abi / "libipa_rime.so").read_bytes()
                          for abi in verify.ABIS}
        self.data = self.libraries["arm64-v8a"]
        self.aar = (ROOT / "artifacts" / verify.AAR_NAME).read_bytes()
        self.identity = verify.source_identity()
        self.java_record = json.loads((ROOT / "artifacts/manifest.json").read_text())["buildProvenance"]["link"]["inputs"]["java/RimeNative.class"]

    def test_real_package(self):
        verify.inspect_package()

    def test_rejects_stale_native_build_id(self):
        data = self.data.replace(b";build=" + self.identity.encode(), b";build=" + b"0" * 64)
        self.assertNotEqual(data, self.data)
        with self.assertRaisesRegex(ValueError, "Native source identity mismatch"):
            verify.inspect_native(data, "arm64-v8a", self.identity)
    def test_rejects_wrong_abi(self):
        with self.assertRaisesRegex(ValueError, "ABI"):
            verify.inspect_native(self.data, "x86_64")

    def test_rejects_4kb_load_alignment(self):
        data = bytearray(self.data)
        header = struct.unpack_from("<16sHHIQQQIHHHHHH", data)
        for index in range(header[10]):
            offset = header[5] + index * header[9]
            if struct.unpack_from("<I", data, offset)[0] == 1:
                struct.pack_into("<Q", data, offset + 48, 4096)
                break
        with self.assertRaisesRegex(ValueError, "16KB LOAD"):
            verify.inspect_native(bytes(data), "arm64-v8a")

    def test_rejects_unwanted_public_api(self):
        data = (ROOT.parent / "OpenCC/artifacts/arm64-v8a/libopencc.so").read_bytes()
        with self.assertRaisesRegex(ValueError, "Unexpected public exports"):
            verify.elf.inspect_elf(data, 183, required_exports=set(), exact_exports=True)

    def test_rejects_aar_native_mismatch(self):
        entries = verify.archive_entries(self.aar)
        entries["jni/arm64-v8a/libipa_rime.so"] += b"corruption"
        with self.assertRaisesRegex(ValueError, "native bytes differ"):
            verify.inspect_aar(archive(entries), self.libraries, self.identity, self.java_record)

    def test_rejects_missing_interface(self):
        entries = verify.archive_entries(self.aar)
        del entries["classes.jar"]
        with self.assertRaisesRegex(ValueError, "Missing AAR"):
            verify.inspect_aar(archive(entries), self.libraries, self.identity, self.java_record)

    def test_rejects_extra_native_dependency(self):
        entries = verify.archive_entries(self.aar)
        entries["jni/arm64-v8a/liblegacy.so"] = self.data
        with self.assertRaisesRegex(ValueError, "native entry inventory"):
            verify.inspect_aar(archive(entries), self.libraries, self.identity, self.java_record)

    def test_rejects_wrong_java_bytecode(self):
        entries = verify.archive_entries(self.aar)
        classes = verify.archive_entries(entries["classes.jar"])
        classes["org/ipaboard/rime/RimeNative.class"] += b"stale"
        entries["classes.jar"] = archive(classes)
        with self.assertRaisesRegex(ValueError, "Java bytecode differs"):
            verify.inspect_aar(archive(entries), self.libraries, self.identity, self.java_record)
    def test_rejects_duplicate_zip_entry(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as output:
            output.writestr("x", b"first")
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                output.writestr("x", b"second")
        with self.assertRaisesRegex(ValueError, "Duplicate ZIP"):
            verify.archive_entries(stream.getvalue())

if __name__ == "__main__":
    unittest.main()
