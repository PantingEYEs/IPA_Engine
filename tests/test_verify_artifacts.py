"""Synthetic ELF checks only: no native code or Mozc dictionary is executed."""
from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_artifacts.py"
SPEC = importlib.util.spec_from_file_location("mozc_verify_artifacts", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)

DYNAMIC_OFFSET = 0x4100
STRINGS_OFFSET = 0x4300
SYMBOLS_OFFSET = 0x4400
HASH_OFFSET = 0x4500
VIRTUAL_DELTA = 0x4000


def synthetic_elf(machine: int = 183, needed: tuple[str, ...] = ("libc.so", "libm.so"),
                  symbols: tuple[str, ...] = verify.REQUIRED_JNI_SYMBOLS,
                  hash_format: str = "sysv") -> bytes:
    """Two PT_LOADs have different file and virtual bases; section headers are absent."""
    data = bytearray(0x4800)
    ident = b"\x7fELF" + bytes((2, 1, 1, 0, 0)) + bytes(7)
    verify.ELF_HEADER.pack_into(data, 0, ident, 3, machine, 1, 0, 64, 0, 0,
                                64, 56, 3, 0, 0, 0)
    verify.PROGRAM_HEADER.pack_into(data, 64, 1, 5, 0, 0, 0, 0x1000, 0x1000, 0x4000)
    verify.PROGRAM_HEADER.pack_into(data, 120, 1, 6, 0x4000, 0x8000, 0,
                                   0x800, 0x1000, 0x4000)
    strings = bytearray(b"\0")
    indices = {}
    for name in (*needed, *symbols):
        indices[name] = len(strings)
        strings += name.encode("utf-8") + b"\0"
    assert len(strings) <= SYMBOLS_OFFSET - STRINGS_OFFSET
    data[STRINGS_OFFSET:STRINGS_OFFSET + len(strings)] = strings
    for index, name in enumerate(symbols, 1):
        verify.SYMBOL_ENTRY.pack_into(data, SYMBOLS_OFFSET + index * 24,
            indices[name], 0x12, 0, 1, 0x300 + index * 0x20, 16)
    symbol_count = len(symbols) + 1
    if hash_format == "sysv":
        struct.pack_into("<II", data, HASH_OFFSET, 1, symbol_count)
        struct.pack_into("<I", data, HASH_OFFSET + 8, 1 if symbols else 0)
        for index in range(symbol_count):
            struct.pack_into("<I", data, HASH_OFFSET + 12 + index * 4,
                index + 1 if 0 < index < symbol_count - 1 else 0)
        hash_tag = verify.DT_HASH
    elif hash_format == "gnu":
        struct.pack_into("<IIII", data, HASH_OFFSET, 1, 1, 1, 5)
        struct.pack_into("<Q", data, HASH_OFFSET + 16, 0xFFFFFFFFFFFFFFFF)
        struct.pack_into("<I", data, HASH_OFFSET + 24, 1 if symbols else 0)
        for index, name in enumerate(symbols):
            value = 5381
            for byte in name.encode("utf-8"):
                value = ((value * 33) + byte) & 0xFFFFFFFF
            value = (value & ~1) | (index == len(symbols) - 1)
            struct.pack_into("<I", data, HASH_OFFSET + 28 + index * 4, value)
        hash_tag = verify.DT_GNU_HASH
    else:
        raise ValueError(hash_format)
    entries = [(verify.DT_NEEDED, indices[name]) for name in needed]
    entries += [
        (verify.DT_STRTAB, STRINGS_OFFSET + VIRTUAL_DELTA),
        (verify.DT_STRSZ, len(strings)),
        (verify.DT_SYMTAB, SYMBOLS_OFFSET + VIRTUAL_DELTA),
        (verify.DT_SYMENT, 24),
        (hash_tag, HASH_OFFSET + VIRTUAL_DELTA),
        (verify.DT_NULL, 0),
    ]
    for index, entry in enumerate(entries):
        verify.DYNAMIC_ENTRY.pack_into(data, DYNAMIC_OFFSET + index * 16, *entry)
    verify.PROGRAM_HEADER.pack_into(data, 176, 2, 6, DYNAMIC_OFFSET,
        DYNAMIC_OFFSET + VIRTUAL_DELTA, 0, len(entries) * 16, len(entries) * 16, 8)
    return bytes(data)


def changed(data: bytes, offset: int, format: str, value: int) -> bytes:
    result = bytearray(data)
    struct.pack_into(format, result, offset, value)
    return bytes(result)


def make_artifacts(directory: Path) -> None:
    for abi, (machine, _) in verify.ABI_MACHINES.items():
        path = directory / abi / "libmozc.so"
        path.parent.mkdir(parents=True)
        path.write_bytes(synthetic_elf(machine=machine))
    (directory / "mozc.data").write_bytes(b"synthetic nonempty dictionary fixture")


class ElfValidationTest(unittest.TestCase):
    def assert_invalid(self, data: bytes, message: str, abi: str = "arm64-v8a") -> None:
        with self.assertRaisesRegex(verify.VerificationError, message):
            verify.Elf64(data).inspect(abi)

    def test_both_abis_and_both_dynamic_hash_formats_without_section_headers(self) -> None:
        for abi, (machine, _) in verify.ABI_MACHINES.items():
            for hash_format in ("sysv", "gnu"):
                with self.subTest(abi=abi, hash_format=hash_format):
                    result = verify.Elf64(synthetic_elf(machine=machine, hash_format=hash_format)).inspect(abi)
                    self.assertEqual(result["machine_id"], machine)
                    self.assertEqual(result["minimum_load_alignment"], 16384)
                    self.assertEqual(result["symbol_hash_format"], hash_format)
                    self.assertEqual(result["needed"], ["libc.so", "libm.so"])
                    self.assertEqual(set(result["required_jni_exports"]), set(verify.REQUIRED_JNI_SYMBOLS))
                    self.assertEqual(len(result["load_segments"]), 2)

    def test_wrong_machine_class_endianness_and_truncated_headers(self) -> None:
        data = synthetic_elf()
        for broken, message in [
            (data[:63], "truncated ELF64 header"),
            (b"NOPE" + data[4:], "not an ELF"),
            (changed(data, 4, "<B", 1), "ELF64 class"),
            (changed(data, 5, "<B", 2), "little-endian"),
            (changed(data, 16, "<H", 2), "ET_DYN"),
            (changed(data, 18, "<H", 62), "wrong ELF machine"),
            (changed(data, 32, "<Q", len(data) - 1), "program-header table"),
        ]:
            with self.subTest(message=message):
                self.assert_invalid(broken, message)

    def test_checks_every_load_segment_and_not_just_the_first(self) -> None:
        data = synthetic_elf()
        for alignment in (0, 1, 4096, 24576):
            with self.subTest(alignment=alignment):
                self.assert_invalid(changed(data, 120 + 48, "<Q", alignment), "PT_LOAD.*alignment")
        self.assert_invalid(changed(data, 120 + 16, "<Q", 0x8001), "not congruent")
        self.assert_invalid(changed(data, 120 + 40, "<Q", 0x400), "file size exceeds memory")
        self.assert_invalid(changed(data, 120 + 32, "<Q", 0x100000), "out of bounds")

    def test_rejects_unpackaged_shared_cpp_and_other_unapproved_libraries(self) -> None:
        for library in ("libc++_shared.so", "libunknown.so", "/system/lib64/libc.so"):
            with self.subTest(library=library):
                self.assert_invalid(synthetic_elf(needed=("libc.so", library)), "non-system DT_NEEDED")

    def test_required_jni_must_be_defined_visible_functions_in_executable_memory(self) -> None:
        self.assert_invalid(synthetic_elf(symbols=verify.REQUIRED_JNI_SYMBOLS[:1]), "missing defined")
        first_symbol = SYMBOLS_OFFSET + 24
        data = synthetic_elf()
        for offset, format, value in [
            (first_symbol + 6, "<H", 0),      # SHN_UNDEF import is not an export.
            (first_symbol + 5, "<B", 2),      # STV_HIDDEN.
            (first_symbol + 4, "<B", 0x02),   # STB_LOCAL.
            (first_symbol + 4, "<B", 0x11),   # STT_OBJECT.
            (first_symbol + 8, "<Q", 0x8400), # Non-executable data address.
        ]:
            with self.subTest(offset=offset, value=value):
                self.assert_invalid(changed(data, offset, format, value), "missing defined")

    def test_dynamic_offsets_and_strings_cannot_escape_the_mapped_file(self) -> None:
        data = synthetic_elf()
        # The first two tags are DT_NEEDED; the next one is DT_STRTAB.
        self.assert_invalid(changed(data, DYNAMIC_OFFSET + 2 * 16 + 8, "<Q", 0xDEADBEEF),
                            "not in a file-backed")
        self.assert_invalid(changed(data, DYNAMIC_OFFSET + 8, "<Q", 0xFFFF),
                            "string index is out of bounds")
        self.assert_invalid(changed(data, HASH_OFFSET + 4, "<I", 0xFFFFFFFF),
                            "DT_HASH table")
        # Remove DT_NULL without changing the declared dynamic table extent.
        self.assert_invalid(changed(data, DYNAMIC_OFFSET + 7 * 16, "<q", 21),
                            "no DT_NULL terminator")

    def test_gnu_hash_walk_is_bounded_and_validates_bucket_indices(self) -> None:
        data = synthetic_elf(hash_format="gnu")
        self.assert_invalid(changed(data, HASH_OFFSET + 4, "<I", 3),
                            "bucket precedes first")
        unterminated = bytearray(data)
        unterminated[HASH_OFFSET + 28:] = bytes(len(data) - HASH_OFFSET - 28)
        self.assert_invalid(bytes(unterminated), "chain has no terminator")


class ManifestValidationTest(unittest.TestCase):
    def test_manifest_contains_measured_hashes_and_no_runtime_claim(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mozc-verifier-test-") as temporary:
            directory = Path(temporary)
            make_artifacts(directory)
            result = verify.verify_artifacts(directory)
            self.assertTrue(result["validated"])
            self.assertFalse(result["runtime_validation_included"])
            self.assertEqual(len(result["artifacts"]), 3)
            for artifact in result["artifacts"]:
                data = (directory / artifact["path"]).read_bytes()
                self.assertEqual(artifact["sha256"], hashlib.sha256(data).hexdigest())
                self.assertEqual(artifact["size_bytes"], len(data))
            dictionary = result["artifacts"][-1]
            self.assertEqual(dictionary["validation"], "nonempty_file_only")

    def test_empty_dictionary_and_missing_abi_fail_together(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mozc-verifier-test-") as temporary:
            directory = Path(temporary)
            make_artifacts(directory)
            (directory / "mozc.data").write_bytes(b"")
            (directory / "x86_64/libmozc.so").unlink()
            with self.assertRaises(verify.VerificationError) as captured:
                verify.verify_artifacts(directory)
            self.assertIn("mozc.data: file is empty", str(captured.exception))
            self.assertIn("x86_64/libmozc.so:", str(captured.exception))

    def test_cli_preserves_previous_manifest_when_verification_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mozc-verifier-test-") as temporary:
            directory = Path(temporary) / "artifacts"
            make_artifacts(directory)
            output = Path(temporary) / "manifest.json"
            arguments = ["--artifacts", str(directory), "--output", str(output)]
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(verify.main(arguments), 0)
            self.assertEqual(json.loads(stdout.getvalue()), json.loads(output.read_text(encoding="utf-8")))
            before = output.read_bytes()
            (directory / "arm64-v8a/libmozc.so").write_bytes(synthetic_elf(needed=("libc++_shared.so",)))
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(verify.main(arguments), 1)
            self.assertFalse(json.loads(stdout.getvalue())["validated"])
            self.assertEqual(output.read_bytes(), before)

    def test_output_cannot_overwrite_an_input_artifact(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mozc-verifier-test-") as temporary:
            directory = Path(temporary)
            make_artifacts(directory)
            output = directory / "mozc.data"
            before = output.read_bytes()
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(verify.main(["--artifacts", str(directory), "--output", str(output)]), 1)
            self.assertIn("must not overwrite", stdout.getvalue())
            self.assertEqual(output.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
