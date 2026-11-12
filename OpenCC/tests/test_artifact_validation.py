# Copyright 2026 PantingEYEs. All rights reserved.
"""Reject materially unsafe or incompatible changes to the real build outputs."""
import importlib.util
from pathlib import Path
import struct
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("verifier", ROOT / "scripts/verify_artifacts.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)

class ArtifactValidationTest(unittest.TestCase):
    def setUp(self):
        self.data = (ROOT / "artifacts/arm64-v8a/libopencc.so").read_bytes()

    def header_offset(self, kind):
        header = struct.unpack_from("<16sHHIQQQIHHHHHH", self.data)
        for index in range(header[10]):
            offset = header[5] + index * header[9]
            if struct.unpack_from("<I", self.data, offset)[0] == kind:
                return offset
        self.fail("Required program header missing")

    def assert_rejected(self, mutated, reason):
        with self.assertRaisesRegex(ValueError, reason):
            verifier.inspect_elf(bytes(mutated), 183)

    def test_real_abi_outputs(self):
        for abi, machine in verifier.EXPECTED_ABIS.items():
            with self.subTest(abi=abi):
                data = (ROOT / "artifacts" / abi / "libopencc.so").read_bytes()
                verifier.inspect_elf(data, machine)

    def test_rejects_four_kb_load_alignment(self):
        mutated = bytearray(self.data)
        struct.pack_into("<Q", mutated, self.header_offset(1) + 48, 4096)
        self.assert_rejected(mutated, "16KB LOAD")

    def test_rejects_wrong_abi(self):
        mutated = bytearray(self.data)
        struct.pack_into("<H", mutated, 18, 62)
        self.assert_rejected(mutated, "ABI")

    def test_rejects_partial_writable_relro_page(self):
        mutated = bytearray(self.data)
        location = self.header_offset(0x6474e552) + 40
        size = struct.unpack_from("<Q", self.data, location)[0]
        struct.pack_into("<Q", mutated, location, size - 1)
        self.assert_rejected(mutated, "RELRO")

    def test_rejects_missing_relro(self):
        mutated = bytearray(self.data)
        struct.pack_into("<I", mutated, self.header_offset(0x6474e552), 0)
        self.assert_rejected(mutated, "Missing GNU RELRO")
    def test_rejects_changed_soname(self):
        mutated = self.data.replace(b"libopencc.so\0", b"libinvalidXX\0")
        self.assert_rejected(mutated, "SONAME")

    def test_rejects_unexpected_dynamic_dependency(self):
        mutated = self.data.replace(b"libdl.so\0", b"libXX.so\0")
        self.assert_rejected(mutated, "dynamic dependency")

    def test_rejects_missing_c_entrypoint(self):
        mutated = self.data.replace(b"opencc_open\0", b"opencc_xxxx\0")
        self.assert_rejected(mutated, "Missing C API")

if __name__ == "__main__":
    unittest.main()
