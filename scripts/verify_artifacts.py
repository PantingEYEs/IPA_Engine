#!/usr/bin/env python3
"""Verify packaged Mozc ELF layout and JNI exports without loading native code.

Only Python's standard library is used. These checks do not test dictionary
compatibility, conversion quality, JNI execution, or operation on a 16 KiB device.
ELF layout reference: https://gabi.xinuos.com/elf/07-pheader.html
Dynamic linking reference: https://gabi.xinuos.com/elf/08-dynamic.html
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile

PAGE_SIZE = 16384
ABI_MACHINES = {"arm64-v8a": (183, "AArch64"), "x86_64": (62, "AMD x86-64")}
ALLOWED_NEEDED = frozenset({
    "libc.so", "libm.so", "libdl.so", "liblog.so", "libandroid.so", "libz.so",
})
JNI_PREFIX = "Java_com_google_android_apps_inputmethod_libs_mozc_session_"
REQUIRED_JNI_SYMBOLS = (
    JNI_PREFIX + "MozcJNI_initialize",
    JNI_PREFIX + "MozcJni_initialize",
)
PT_LOAD, PT_DYNAMIC = 1, 2
DT_NULL, DT_NEEDED, DT_HASH, DT_STRTAB, DT_SYMTAB = 0, 1, 4, 5, 6
DT_STRSZ, DT_SYMENT, DT_GNU_HASH = 10, 11, 0x6FFFFEF5
ELF_HEADER = struct.Struct("<16sHHIQQQIHHHHHH")
PROGRAM_HEADER = struct.Struct("<IIQQQQQQ")
DYNAMIC_ENTRY = struct.Struct("<qQ")
SYMBOL_ENTRY = struct.Struct("<IBBHQQ")


class VerificationError(ValueError):
    """A file failed a required static artifact contract."""


class Elf64:
    def __init__(self, data: bytes):
        self.data = data
        self.loads: list[dict] = []
        self.dynamic: dict[int, list[int]] = {}

    def checked_range(self, offset: int, size: int, label: str) -> int:
        if offset < 0 or size < 0 or offset > len(self.data) or size > len(self.data) - offset:
            raise VerificationError(f"{label}: file range is out of bounds")
        return offset

    def unpack(self, layout: struct.Struct, offset: int, label: str) -> tuple:
        return layout.unpack_from(self.data, self.checked_range(offset, layout.size, label))

    def mapped_span(self, address: int) -> tuple[int, int]:
        for segment in self.loads:
            delta = address - segment["virtual_address"]
            if 0 <= delta < segment["file_size"]:
                return segment["offset"] + delta, segment["file_size"] - delta
        raise VerificationError("dynamic address is not in a file-backed PT_LOAD segment")

    def mapped_offset(self, address: int, size: int, label: str) -> int:
        offset, available = self.mapped_span(address)
        if size < 0 or size > available:
            raise VerificationError(f"{label}: extends outside its file-backed PT_LOAD segment")
        return self.checked_range(offset, size, label)

    def tag(self, kind: int, label: str) -> int:
        values = self.dynamic.get(kind, [])
        if len(values) != 1:
            raise VerificationError(f"{label}: expected exactly one dynamic tag")
        return values[0]

    def cstring(self, table_offset: int, table_size: int, index: int) -> str:
        if index < 0 or index >= table_size:
            raise VerificationError("dynamic string index is out of bounds")
        start = table_offset + index
        end = self.data.find(b"\0", start, table_offset + table_size)
        if end < 0:
            raise VerificationError("dynamic string is not NUL-terminated")
        try:
            return self.data[start:end].decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise VerificationError("dynamic string is not valid UTF-8") from error

    def symbol_count(self) -> tuple[int, str]:
        """Use loader-visible hash tables, not optional or forgeable section names."""
        counts: list[tuple[int, str]] = []
        if DT_HASH in self.dynamic:
            address = self.tag(DT_HASH, "DT_HASH")
            offset = self.mapped_offset(address, 8, "DT_HASH header")
            buckets, chains = self.unpack(struct.Struct("<II"), offset, "DT_HASH header")
            if buckets == 0 or chains == 0:
                raise VerificationError("DT_HASH has an empty bucket or symbol table")
            self.mapped_offset(address, 8 + 4 * (buckets + chains), "DT_HASH table")
            counts.append((chains, "sysv"))
        if DT_GNU_HASH in self.dynamic:
            address = self.tag(DT_GNU_HASH, "DT_GNU_HASH")
            offset = self.mapped_offset(address, 16, "DT_GNU_HASH header")
            buckets, first_symbol, bloom_words, _ = self.unpack(
                struct.Struct("<IIII"), offset, "DT_GNU_HASH header")
            if buckets == 0 or bloom_words == 0 or first_symbol == 0:
                raise VerificationError("DT_GNU_HASH has invalid empty tables")
            if bloom_words & (bloom_words - 1):
                raise VerificationError("DT_GNU_HASH bloom size is not a power of two")
            bucket_relative = 16 + bloom_words * 8
            chain_relative = bucket_relative + buckets * 4
            self.mapped_offset(address, chain_relative, "DT_GNU_HASH buckets")
            largest_bucket = 0
            for index in range(buckets):
                (value,) = self.unpack(struct.Struct("<I"),
                    offset + bucket_relative + index * 4, "DT_GNU_HASH bucket")
                if value != 0 and value < first_symbol:
                    raise VerificationError("DT_GNU_HASH bucket precedes first hashed symbol")
                largest_bucket = max(largest_bucket, value)
            count = first_symbol
            if largest_bucket:
                # GNU chains are contiguous. The highest nonempty bucket reaches
                # the final symbol; the segment's file extent bounds the walk.
                index = largest_bucket
                chain_address = address + chain_relative + (index - first_symbol) * 4
                chain_offset, available = self.mapped_span(chain_address)
                for position in range(available // 4):
                    (value,) = self.unpack(struct.Struct("<I"),
                        chain_offset + position * 4, "DT_GNU_HASH chain")
                    if value & 1:
                        count = index + position + 1
                        break
                else:
                    raise VerificationError("DT_GNU_HASH chain has no terminator")
            counts.append((count, "gnu"))
        if not counts:
            raise VerificationError("missing DT_HASH/DT_GNU_HASH dynamic symbol count")
        if len(counts) == 2 and counts[1][0] > counts[0][0]:
            raise VerificationError("GNU symbol count exceeds DT_HASH symbol count")
        return counts[0]

    def inspect(self, abi: str) -> dict:
        if abi not in ABI_MACHINES:
            raise VerificationError(f"unsupported expected ABI: {abi}")
        if len(self.data) < ELF_HEADER.size:
            raise VerificationError("truncated ELF64 header")
        ident = self.data[:16]
        if ident[:4] != b"\x7fELF":
            raise VerificationError("not an ELF file")
        if ident[4] != 2:
            raise VerificationError("expected ELF64 class")
        if ident[5] != 1:
            raise VerificationError("expected little-endian ELF")
        if ident[6] != 1:
            raise VerificationError("unsupported ELF identification version")
        (_, elf_type, machine, version, _, phoff, _, _, ehsize,
         phentsize, phnum, _, _, _) = self.unpack(ELF_HEADER, 0, "ELF64 header")
        if elf_type != 3:
            raise VerificationError("expected an ET_DYN shared library")
        expected_machine, machine_name = ABI_MACHINES[abi]
        if machine != expected_machine:
            raise VerificationError(
                f"wrong ELF machine for {abi}: expected {expected_machine}, found {machine}")
        if version != 1 or ehsize != ELF_HEADER.size:
            raise VerificationError("invalid ELF64 header version or size")
        if phnum == 0 or phnum == 0xFFFF:
            raise VerificationError("missing or unsupported extended program-header count")
        if phentsize != PROGRAM_HEADER.size:
            raise VerificationError("invalid ELF64 program-header entry size")
        self.checked_range(phoff, phnum * phentsize, "program-header table")
        dynamic_segments = []
        for index in range(phnum):
            kind, flags, offset, address, _, file_size, memory_size, alignment = self.unpack(
                PROGRAM_HEADER, phoff + index * phentsize, "program header")
            self.checked_range(offset, file_size, "program segment")
            if kind == PT_LOAD:
                if file_size > memory_size:
                    raise VerificationError(f"PT_LOAD[{index}]: file size exceeds memory size")
                if alignment < PAGE_SIZE or alignment & (alignment - 1):
                    raise VerificationError(
                        f"PT_LOAD[{index}]: alignment {alignment} is not a power of two >= {PAGE_SIZE}")
                if offset % alignment != address % alignment:
                    raise VerificationError(f"PT_LOAD[{index}]: offset/vaddr are not congruent modulo p_align")
                self.loads.append({
                    "program_header_index": index, "flags": flags,
                    "offset": offset, "virtual_address": address,
                    "file_size": file_size, "memory_size": memory_size,
                    "alignment": alignment,
                })
            elif kind == PT_DYNAMIC:
                dynamic_segments.append((offset, address, file_size))
        if not self.loads:
            raise VerificationError("no PT_LOAD segments")
        if len(dynamic_segments) != 1:
            raise VerificationError("expected exactly one PT_DYNAMIC segment")
        dynamic_offset, dynamic_address, dynamic_size = dynamic_segments[0]
        if dynamic_size == 0 or dynamic_size % DYNAMIC_ENTRY.size:
            raise VerificationError("invalid PT_DYNAMIC size")
        if self.mapped_offset(dynamic_address, dynamic_size, "PT_DYNAMIC") != dynamic_offset:
            raise VerificationError("PT_DYNAMIC file offset does not match its virtual mapping")
        for offset in range(dynamic_offset, dynamic_offset + dynamic_size, DYNAMIC_ENTRY.size):
            kind, value = self.unpack(DYNAMIC_ENTRY, offset, "dynamic entry")
            if kind == DT_NULL:
                break
            self.dynamic.setdefault(kind, []).append(value)
        else:
            raise VerificationError("PT_DYNAMIC has no DT_NULL terminator")

        strings_size = self.tag(DT_STRSZ, "DT_STRSZ")
        if strings_size == 0:
            raise VerificationError("empty dynamic string table")
        strings_offset = self.mapped_offset(self.tag(DT_STRTAB, "DT_STRTAB"),
                                            strings_size, "dynamic string table")
        if self.data[strings_offset] != 0:
            raise VerificationError("dynamic string table lacks its initial empty string")
        needed = [self.cstring(strings_offset, strings_size, index)
                  for index in self.dynamic.get(DT_NEEDED, [])]
        forbidden = sorted(set(needed) - ALLOWED_NEEDED)
        if forbidden:
            raise VerificationError("unpackaged/non-system DT_NEEDED: " + ", ".join(forbidden))
        if self.tag(DT_SYMENT, "DT_SYMENT") != SYMBOL_ENTRY.size:
            raise VerificationError("invalid ELF64 dynamic symbol entry size")
        symbol_count, hash_format = self.symbol_count()
        symbols_offset = self.mapped_offset(self.tag(DT_SYMTAB, "DT_SYMTAB"),
            symbol_count * SYMBOL_ENTRY.size, "dynamic symbol table")
        exported_functions = {}
        for index in range(symbol_count):
            name_index, info, other, section, value, size = self.unpack(
                SYMBOL_ENTRY, symbols_offset + index * SYMBOL_ENTRY.size, "dynamic symbol")
            name = self.cstring(strings_offset, strings_size, name_index)
            binding, symbol_type, visibility = info >> 4, info & 0xF, other & 0x3
            if (section != 0 and binding in (1, 2) and symbol_type == 2
                    and visibility in (0, 3)):
                executable = any(segment["flags"] & 1
                    and segment["virtual_address"] <= value
                    and value + max(size, 1) <= segment["virtual_address"] + segment["file_size"]
                    for segment in self.loads)
                if executable:
                    exported_functions[name] = {"value": value, "size": size}
        missing = sorted(set(REQUIRED_JNI_SYMBOLS) - exported_functions.keys())
        if missing:
            raise VerificationError("missing defined, visible dynamic JNI function exports: " + ", ".join(missing))
        return {
            "abi": abi, "elf_class": 64, "endianness": "little", "elf_type": "ET_DYN",
            "machine_id": machine, "machine": machine_name,
            "minimum_load_alignment": min(segment["alignment"] for segment in self.loads),
            "load_segments": self.loads, "needed": needed,
            "dynamic_symbol_count": symbol_count, "symbol_hash_format": hash_format,
            "required_jni_exports": {name: exported_functions[name] for name in REQUIRED_JNI_SYMBOLS},
        }


def file_metadata(path: Path, keep_bytes: bool = False) -> tuple[dict, bytes | None]:
    if path.is_symlink() or not path.is_file():
        raise VerificationError("expected a regular, non-symlink file")
    digest = hashlib.sha256()
    size = 0
    chunks = [] if keep_bytes else None
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
            if chunks is not None:
                chunks.append(chunk)
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or size != after.st_size:
        raise VerificationError("file changed while verification was reading it")
    if size == 0:
        raise VerificationError("file is empty")
    return {"size_bytes": size, "sha256": digest.hexdigest()}, (
        b"".join(chunks) if chunks is not None else None)


def verify_artifacts(directory: Path) -> dict:
    """Return a manifest only after both ABIs and the dictionary pass."""
    results = []
    errors = []
    for abi in ABI_MACHINES:
        relative = Path(abi) / "libmozc.so"
        try:
            metadata, data = file_metadata(directory / relative, keep_bytes=True)
            assert data is not None
            inspected = Elf64(data).inspect(abi)
            results.append({"path": relative.as_posix(), "kind": "shared_library",
                            **metadata, **inspected})
        except (OSError, VerificationError) as error:
            errors.append(f"{relative.as_posix()}: {error}")
    try:
        metadata, _ = file_metadata(directory / "mozc.data")
        results.append({"path": "mozc.data", "kind": "dictionary", **metadata,
                        "validation": "nonempty_file_only"})
    except (OSError, VerificationError) as error:
        errors.append(f"mozc.data: {error}")
    if errors:
        raise VerificationError("\n".join(errors))
    return {
        "schema_version": 1,
        "validated": True,
        "validation_scope": "static ELF, dependency, JNI-export and nonempty-dictionary checks",
        "runtime_validation_included": False,
        "page_size_bytes": PAGE_SIZE,
        "allowed_system_dependencies": sorted(ALLOWED_NEEDED),
        "required_jni_symbols": list(REQUIRED_JNI_SYMBOLS),
        "artifacts": results,
    }


def write_manifest(path: Path, document: str, directory: Path) -> None:
    inputs = [(directory / abi / "libmozc.so").resolve() for abi in ABI_MACHINES]
    inputs.append((directory / "mozc.data").resolve())
    if path.resolve() in inputs:
        raise VerificationError("--output must not overwrite an input artifact")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".mozc-manifest-", suffix=".tmp",
                                                       dir=path.parent)
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(document)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path,
                        default=Path(__file__).resolve().parents[1] / "artifacts")
    parser.add_argument("--output", type=Path,
                        help="Atomically save the successful JSON manifest; never written on verification failure.")
    arguments = parser.parse_args(argv)
    try:
        result = verify_artifacts(arguments.artifacts)
        document = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
        if arguments.output is not None:
            write_manifest(arguments.output, document, arguments.artifacts)
    except (OSError, VerificationError) as error:
        print(json.dumps({"validated": False, "runtime_validation_included": False,
                          "errors": str(error).splitlines()}, indent=2, ensure_ascii=False))
        return 1
    print(document, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
