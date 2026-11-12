"""Synthetic ELF and JVM/ZIP contract tests; no native code, Java or E5 model is executed."""
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
import warnings
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_artifacts.py"
SPEC = importlib.util.spec_from_file_location("ortx_verify_artifacts", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)

DYNAMIC_OFFSET = 0x4100
STRINGS_OFFSET = 0x4300
SYMBOLS_OFFSET = 0x4400
HASH_OFFSET = 0x4500
VIRTUAL_DELTA = 0x4000


def synthetic_elf(machine: int = 183, needed: tuple[str, ...] = ("libc.so", "libm.so"),
                  symbols: tuple[str, ...] = verify.REQUIRED_EXPORTS[verify.LIBRARIES[1]],
                  hash_format: str = "sysv") -> bytes:
    """Two PT_LOADs have different file and virtual bases; section headers are absent."""
    data = bytearray(0x4800)
    ident = b"\x7fELF" + bytes((2, 1, 1, 0, 0)) + bytes(7)
    verify.ELF_HEADER.pack_into(data, 0, ident, 3, machine, 1, 0, 64, 0, 0,
                                64, 56, 4, 0, 0, 0)
    verify.PROGRAM_HEADER.pack_into(data, 64, 1, 5, 0, 0, 0, 0x1000, 0x1000, 0x4000)
    verify.PROGRAM_HEADER.pack_into(data, 120, 1, 6, 0x4000, 0x8000, 0,
                                   0x800, 0x4000, 0x4000)
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
    verify.PROGRAM_HEADER.pack_into(data, 232, verify.PT_GNU_RELRO, 4, 0x4000,
                                   0x8000, 0, 0x800, 0x4000, 1)
    return bytes(data)


def changed(data: bytes, offset: int, format: str, value: int) -> bytes:
    result = bytearray(data)
    struct.pack_into(format, result, offset, value)
    return bytes(result)



def synthetic_class(name: str, methods: list[tuple[str, str, int]] | None = None,
                    library: str = "onnxruntime_extensions4j_jni") -> bytes:
    pool = []
    def utf(value):
        raw = value.encode("utf-8")
        pool.append(b"\1" + struct.pack(">H", len(raw)) + raw)
        return len(pool)
    def reference(tag, number):
        pool.append(struct.pack(">BH", tag, number))
        return len(pool)
    identity = reference(7, utf(name))
    parent = reference(7, utf("java/lang/Object"))
    reference(8, utf(library))
    code_name = utf("Code")
    if methods is None:
        methods = [(key[0], key[1], flags) for key, flags in verify.JAVA_INTERFACES[name].items()]
    encoded = []
    for method, descriptor, flags in methods:
        ni, di = utf(method), utf(descriptor)
        attributes = b""
        if not flags & 0x100:
            body = b"\xb1" if descriptor.endswith("V") else b"\x01\xb0"
            code = struct.pack(">HHI", 1, 1, len(body)) + body + b"\0\0\0\0"
            attributes = struct.pack(">HI", code_name, len(code)) + code
        encoded.append(struct.pack(">HHHH", flags, ni, di, int(bool(attributes))) + attributes)
    return (b"\xca\xfe\xba\xbe" + struct.pack(">HHH", 0, 52, len(pool) + 1) + b"".join(pool)
            + struct.pack(">HHHHHH", 0x21, identity, parent, 0, 0, len(encoded))
            + b"".join(encoded) + b"\0\0")


def zipped(entries: dict[str, bytes] | list[tuple[str, bytes]]) -> bytes:
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for key, value in (entries.items() if isinstance(entries, dict) else entries):
                archive.writestr(key, value)
    return output.getvalue()


def aar_members(natives: dict[str, bytes]) -> dict[str, bytes]:
    classes = {name + ".class": synthetic_class(name) for name in verify.JAVA_INTERFACES}
    return {"AndroidManifest.xml": b'<manifest package="ai.onnxruntime.extensions"/>',
            "classes.jar": zipped(classes), **{"jni/" + name: value for name, value in natives.items()}}


def make_artifacts(directory: Path) -> dict[str, bytes]:
    natives = {}
    for abi, (machine, _) in verify.ABI_MACHINES.items():
        for library in verify.LIBRARIES:
            relative = f"{abi}/{library}"
            path = directory / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            content = synthetic_elf(machine=machine, symbols=verify.REQUIRED_EXPORTS[library])
            path.write_bytes(content)
            natives[relative] = content
    (directory / "extensions-test.aar").write_bytes(zipped(aar_members(natives)))
    return natives


class ElfValidationTest(unittest.TestCase):
    def assert_invalid(self, data: bytes, message: str, abi: str = "arm64-v8a") -> None:
        with self.assertRaisesRegex(verify.VerificationError, message):
            verify.Elf64(data).inspect(abi, verify.LIBRARIES[1])

    def test_two_abis_two_libraries_and_both_hash_formats_without_sections(self):
        for abi, (machine, _) in verify.ABI_MACHINES.items():
            for library, symbols in verify.REQUIRED_EXPORTS.items():
                for hash_format in ("sysv", "gnu"):
                    with self.subTest(abi=abi, library=library, hash=hash_format):
                        data = synthetic_elf(machine=machine, symbols=symbols, hash_format=hash_format)
                        result = verify.Elf64(data).inspect(abi, library)
                        self.assertEqual(result["minimum_load_alignment"], 16384)
                        self.assertEqual(result["symbol_hash_format"], hash_format)
                        self.assertEqual(set(result["required_exports"]), set(symbols))
                        self.assertEqual(result["relro"]["virtual_address"] + result["relro"]["memory_size"], 0xC000)

    def test_wrong_machine_class_endianness_and_truncated_headers(self):
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

    def test_every_load_alignment_and_congruence(self):
        data = synthetic_elf()
        for alignment in (0, 1, 4096, 24576):
            with self.subTest(alignment=alignment):
                self.assert_invalid(changed(data, 120 + 48, "<Q", alignment), "PT_LOAD.*alignment")
        self.assert_invalid(changed(data, 120 + 16, "<Q", 0x8001), "not congruent")
        self.assert_invalid(changed(data, 120 + 40, "<Q", 0x400), "file size exceeds memory")
        self.assert_invalid(changed(data, 120 + 32, "<Q", 0x100000), "out of bounds")

    def test_relro_required_and_must_cover_whole_16kb_pages(self):
        data = synthetic_elf()
        self.assert_invalid(changed(data, 232, "<I", 0), "exactly one PT_GNU_RELRO")
        self.assert_invalid(changed(data, 232 + 40, "<Q", 0x1000), "end is not 16KB aligned")
        self.assert_invalid(changed(data, 232 + 40, "<Q", 0x8000), "not contained")
        self.assert_invalid(changed(data, 120 + 4, "<I", 7), "writable non-executable")
        self.assert_invalid(changed(data, 232 + 8, "<Q", 0x3F00), "offset does not match")

    def test_shared_cpp_absolute_and_unpackaged_dependencies_fail(self):
        for library in ("libc++_shared.so", "libunknown.so", "/system/lib64/libc.so"):
            with self.subTest(library=library):
                self.assert_invalid(synthetic_elf(needed=("libc.so", library)), "non-system DT_NEEDED")
        self.assert_invalid(synthetic_elf(needed=(verify.LIBRARIES[1],)), "self DT_NEEDED")

    def test_required_exports_must_be_defined_visible_functions_in_executable_memory(self):
        self.assert_invalid(synthetic_elf(symbols=("RegisterCustomOps",)), "missing defined")
        # The old upstream Utils symbol does not satisfy the native OrtxLibrary method.
        symbols = tuple(name.replace("OrtxLibrary_", "Utils_") for name in verify.REQUIRED_EXPORTS[verify.LIBRARIES[1]])
        self.assert_invalid(synthetic_elf(symbols=symbols), "missing defined")
        first = SYMBOLS_OFFSET + 24
        for offset, fmt, value in [(first + 6, "<H", 0), (first + 5, "<B", 2),
                                   (first + 4, "<B", 0x02), (first + 4, "<B", 0x11),
                                   (first + 8, "<Q", 0x8400)]:
            with self.subTest(offset=offset, value=value):
                self.assert_invalid(changed(synthetic_elf(), offset, fmt, value), "missing defined")

    def test_dynamic_offsets_strings_and_hashes_are_bounded(self):
        data = synthetic_elf()
        self.assert_invalid(changed(data, DYNAMIC_OFFSET + 2 * 16 + 8, "<Q", 0xDEADBEEF), "not in a file-backed")
        self.assert_invalid(changed(data, DYNAMIC_OFFSET + 8, "<Q", 0xFFFF), "string index is out of bounds")
        self.assert_invalid(changed(data, HASH_OFFSET + 4, "<I", 0xFFFFFFFF), "DT_HASH table")
        self.assert_invalid(changed(data, DYNAMIC_OFFSET + 7 * 16, "<q", 21), "no DT_NULL terminator")
        data = synthetic_elf(hash_format="gnu")
        self.assert_invalid(changed(data, HASH_OFFSET + 4, "<I", 3), "bucket precedes first")
        unterminated = bytearray(data)
        unterminated[HASH_OFFSET + 28:] = bytes(len(data) - HASH_OFFSET - 28)
        self.assert_invalid(bytes(unterminated), "chain has no terminator")


class JavaAndArchiveValidationTest(unittest.TestCase):
    def test_public_java_api_and_native_signature(self):
        for name in verify.JAVA_INTERFACES:
            result = verify.ClassFile(synthetic_class(name)).inspect(name)
            self.assertEqual(result["class"], name)
            self.assertEqual(result["classfile_major"], 52)

    def test_native_method_cannot_be_absent_stub_or_wrong_descriptor(self):
        name = "ai/onnxruntime/extensions/OrtxLibrary"
        original = [(key[0], key[1], flags) for key, flags in verify.JAVA_INTERFACES[name].items()]
        variants = [original[:1], [(a, b, c & ~0x100) for a, b, c in original],
                    [(a, "()I" if b == "()J" else b, c) for a, b, c in original]]
        for methods in variants:
            with self.assertRaisesRegex(verify.VerificationError, "interface missing"):
                verify.ClassFile(synthetic_class(name, methods)).inspect(name)

    def test_java_identity_version_library_and_truncation(self):
        name = "ai/onnxruntime/extensions/OrtxLibrary"
        data = synthetic_class(name)
        for broken, message in [(data[:20], "truncated"), (data + b"extra", "trailing"),
                                (changed(data, 6, ">H", 61), "Java 8"),
                                (synthetic_class(name, library="wrong_library"), "wrong native library")]:
            with self.subTest(message=message):
                with self.assertRaisesRegex(verify.VerificationError, message):
                    verify.ClassFile(broken).inspect(name)
        with self.assertRaisesRegex(verify.VerificationError, "identity mismatch"):
            verify.ClassFile(data).inspect("ai/onnxruntime/extensions/OrtxPackage")

    def test_zip_duplicate_paths_traversal_and_corruption(self):
        for data, message in [(zipped([("a", b"1"), ("a", b"2")]), "duplicate"),
                              (zipped({"../escape": b"1"}), "unsafe"),
                              (zipped({"a/b": b"1"}).replace(b"a/b", b"a\\b"), "unsafe"), (b"not zip", "ZIP")]:
            with self.subTest(message=message):
                with self.assertRaisesRegex(verify.VerificationError, message):
                    verify.archive_members(data, "test")

    def test_aar_rejects_wrong_native_bytes_extra_abi_and_missing_java(self):
        with tempfile.TemporaryDirectory() as tmp:
            natives = make_artifacts(Path(tmp))
            members = aar_members(natives)
            broken = dict(members)
            key = "jni/arm64-v8a/libortextensions.so"
            broken[key] = broken[key] + b"changed"
            with self.assertRaisesRegex(verify.VerificationError, "native bytes differ"):
                verify.inspect_aar(zipped(broken), natives)
            broken = {**members, "jni/armeabi-v7a/libortextensions.so": b"unexpected"}
            with self.assertRaisesRegex(verify.VerificationError, "native member set mismatch"):
                verify.inspect_aar(zipped(broken), natives)
            broken = {**members, "classes.jar": zipped({})}
            with self.assertRaisesRegex(verify.VerificationError, "wrapper class missing"):
                verify.inspect_aar(zipped(broken), natives)


class ManifestValidationTest(unittest.TestCase):
    def test_valid_complete_package_and_manifest_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            make_artifacts(directory)
            result = verify.verify_artifacts(directory)
            self.assertTrue(result["validated"])
            self.assertFalse(result["runtime_validation_included"])
            self.assertFalse(result["apk_zip_alignment_included"])
            self.assertEqual(len(result["artifacts"]), 5)
            for artifact in result["artifacts"]:
                self.assertEqual(artifact["sha256"], hashlib.sha256((directory / artifact["path"]).read_bytes()).hexdigest())

    def test_missing_sibling_breaks_dependency_closure(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            make_artifacts(directory)
            (directory / "arm64-v8a" / verify.LIBRARIES[1]).write_bytes(synthetic_elf(needed=(verify.LIBRARIES[0],)))
            (directory / "arm64-v8a" / verify.LIBRARIES[0]).unlink()
            with self.assertRaisesRegex(verify.VerificationError, "does not resolve to a validated sibling"):
                verify.verify_artifacts(directory)

    def test_single_aar_and_no_hidden_extra_native_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            make_artifacts(directory)
            (directory / "another.aar").write_bytes(b"unexpected")
            with self.assertRaisesRegex(verify.VerificationError, "exactly one AAR"):
                verify.verify_artifacts(directory)
            (directory / "another.aar").unlink()
            (directory / "old.so").write_bytes(b"unexpected")
            with self.assertRaisesRegex(verify.VerificationError, "standalone native file set mismatch"):
                verify.verify_artifacts(directory)

    def test_failure_preserves_existing_manifest_and_prevents_artifact_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            make_artifacts(directory)
            output = directory / "result.json"
            output.write_text("previous validated report", encoding="utf-8")
            target = directory / "arm64-v8a" / verify.LIBRARIES[0]
            with self.assertRaisesRegex(verify.VerificationError, "must not overwrite"):
                verify.write_manifest(target, "bad", directory)
            target.write_bytes(b"broken")
            capture = io.StringIO()
            with redirect_stdout(capture):
                status = verify.main(["--artifacts", str(directory), "--output", str(output)])
            self.assertEqual(status, 1)
            self.assertFalse(json.loads(capture.getvalue())["validated"])
            self.assertEqual(output.read_text(encoding="utf-8"), "previous validated report")

    def test_success_writes_parseable_manifest_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            make_artifacts(directory)
            output = directory / "result.json"
            with redirect_stdout(io.StringIO()):
                status = verify.main(["--artifacts", str(directory), "--output", str(output)])
            self.assertEqual(status, 0)
            self.assertTrue(json.loads(output.read_text(encoding="utf-8"))["validated"])
            self.assertEqual(list(directory.glob(".ortx-manifest-*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
