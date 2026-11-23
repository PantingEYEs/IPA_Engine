"""Public stdlib-only validation of the aligned multilingual dictionary format.

Runtime graph semantics are checked by Rust; this validates release download
bounds, structural layouts, declared counts, per-range hashes and reverse CSR.
"""
import hashlib
import re
import struct

GRAPH_MAGIC = b'IPADWG01'
GRAPH_HEADER = struct.Struct('<8s7I9Q')
CONTAINER_HEADER = struct.Struct('<8sIIIIQQ')
CONTAINER_ENTRY = struct.Struct('<16sIIQQQQII32s32s')
REVERSE_HEADER = struct.Struct('<8sIIII')
ALIGNMENT = 65536
DEFAULT_BUNDLE_LIMIT = 96_000_000


def validate_container(data, expected_keys=None):
    """Validate release-facing container bounds, payload SHA and inner layouts.

    Deep graph reachability is checked by the Rust loader. Compilation separately
    verifies the complete forward/reverse posting bijection with verify_reverse.
    """
    if not CONTAINER_HEADER.size <= len(data) < DEFAULT_BUNDLE_LIMIT:
        raise ValueError('invalid language container size')
    magic, version, count, keys, words, directory_end, end = CONTAINER_HEADER.unpack_from(data)
    if (magic != b'IPAPACK1' or version != 1 or not 1 <= count <= 8
            or directory_end != CONTAINER_HEADER.size + count * CONTAINER_ENTRY.size
            or end != len(data) or directory_end > end):
        raise ValueError('invalid language container header')
    if expected_keys is not None and keys != expected_keys:
        raise ValueError('language container key count disagrees with manifest')
    packs, ranges, ids, used_mask = [], [], set(), 0
    total_keys = total_words = 0
    for number in range(count):
        name, mask, flags, go, gb, ro, rb, pk, pw, gs, rs = CONTAINER_ENTRY.unpack_from(
            data, CONTAINER_HEADER.size + number * CONTAINER_ENTRY.size)
        raw_id, separator, padding = name.partition(b'\0')
        if (not separator or any(padding) or not re.fullmatch(rb'[A-Za-z0-9_-]{1,15}', raw_id)
                or raw_id in ids or not 0 < mask <= 255 or used_mask & mask
                or flags & ~7 or not flags & 6 or not 0 < pk < 1 << 24 or not 0 < pw < 1 << 24):
            raise ValueError('invalid language container pack policy')
        ids.add(raw_id)
        used_mask |= mask
        payloads = [(go, gb, 108, gs)]
        if flags & 4:
            payloads.append((ro, rb, 24, rs))
        elif ro or rb or any(rs):
            raise ValueError('reverse payload without reverse capability')
        for offset, size, minimum, digest in payloads:
            if (offset < directory_end or offset % ALIGNMENT or not minimum <= size < 40_000_000
                    or offset + size > end):
                raise ValueError('invalid language container payload range')
            ranges.append((offset, offset + size))
            if hashlib.sha256(memoryview(data)[offset:offset + size]).digest() != digest:
                raise ValueError('language container payload SHA-256 mismatch')
        h = GRAPH_HEADER.unpack_from(data, go)
        gm, gv, nodes, arcs, gk, gw, alpha, root, *offsets = h
        sizes = {0: alpha * 4, 1: (nodes + 1) * 3, 2: nodes * 3,
                 3: arcs * 4, 4: (gk + 1) * 3, 6: ((gw + 15) // 16 + 1) * 3}
        if (gm != GRAPH_MAGIC or gv != 2 or not nodes or root >= nodes or not 1 <= alpha <= 255
                or (gk, gw) != (pk, pw) or offsets[0] != GRAPH_HEADER.size or offsets[-1] != gb
                or any(a > b for a, b in zip(offsets, offsets[1:]))
                or any(offsets[s + 1] - offsets[s] != length for s, length in sizes.items())):
            raise ValueError('invalid inner language graph header or section layout')
        if flags & 4:
            rm, rv, rw, rk, records = REVERSE_HEADER.unpack_from(data, ro)
            record_base = ro + REVERSE_HEADER.size + (pw + 1) * 3
            if (rm != b'IPAREV01' or rv != 1 or (rw, rk) != (pw, pk) or records >= 1 << 24
                    or REVERSE_HEADER.size + (pw + 1) * 3 + records * 5 != rb):
                raise ValueError('invalid inner language reverse header')
            def u24(at):
                return data[at] | data[at + 1] << 8 | data[at + 2] << 16
            previous_end = u24(ro + REVERSE_HEADER.size)
            if previous_end != 0:
                raise ValueError('invalid reverse initial offset')
            for word in range(pw):
                group_end = u24(ro + REVERSE_HEADER.size + (word + 1) * 3)
                if not previous_end <= group_end <= records:
                    raise ValueError('invalid reverse CSR bounds')
                previous = None
                for record in range(previous_end, group_end):
                    at = record_base + record * 5
                    key, tag = u24(at), data[at + 3]
                    current = (key, tag & 7)
                    if (key >= pk or tag & ~15 or not mask & (1 << (tag & 7))
                            or data[at + 4] > 180 or previous is not None and current <= previous):
                        raise ValueError('invalid reverse record ordering or values')
                    previous = current
                previous_end = group_end
            if previous_end != records:
                raise ValueError('invalid reverse final offset')
        total_keys += pk
        total_words += pw
        packs.append(dict(id=raw_id.decode('ascii'), region_mask=mask, flags=flags,
                          graph_offset=go, graph_bytes=gb, reverse_offset=ro, reverse_bytes=rb,
                          keys=pk, words=pw, graph_sha256=gs.hex(), reverse_sha256=rs.hex()))
    ranges.sort()
    if any(a[1] > b[0] for a, b in zip(ranges, ranges[1:])) or (total_keys, total_words) != (keys, words):
        raise ValueError('overlapping language container ranges or inconsistent totals')
    return dict(version=4, index_bits=24, ipa_keys=keys, words=words, language_packs=packs)

