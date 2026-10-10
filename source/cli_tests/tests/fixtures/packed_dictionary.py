"""Small, independent packed-format oracle (no production compiler import)."""
from __future__ import annotations

import struct
import sys
from pathlib import Path

DEFAULT_ENTRIES = {
    "a": [("alpha", 0, 100, False), ("alpha", 1, 90, False), ("啊", 2, 70, False)],
    "ab": [("about", 0, 80, False)],
    "ac": [("爱", 2, 90, True)],
    "ba": [("alpha", 0, 100, False)],
    "cb": [("about", 0, 100, False), ("爱", 2, 90, True), ("爱", 3, 120, False)],
    "ɕoː": [("学校", 4, 100, False)],
    "ni˨˩˦": [("你好", 2, 100, False), ("你好", 3, 80, False)],
}


def vint(n: int) -> bytes:
    out = bytearray()
    while n >= 128:
        out.append((n & 127) | 128)
        n >>= 7
    out.append(n)
    return bytes(out)


def u24(n: int) -> bytes:
    return n.to_bytes(3, "little")


def build(bits: int = 24, entries: dict | None = None) -> bytes:
    if bits not in (24, 32):
        raise ValueError("index bits must be 24 or 32")
    width = bits // 8
    pack = lambda n: n.to_bytes(width, "little")
    if entries is None:
        entries = {key: records.copy() for key, records in DEFAULT_ENTRIES.items()}
        for i in range(26):
            entries["x" + chr(97 + i)] = [(f"word-{i:02d}", 0, i, False)]
    keys = sorted(entries)
    alphabet = sorted({ord(c) for k in keys for c in k})
    labels = {cp: i + 1 for i, cp in enumerate(alphabet)}
    words = sorted({w for e in entries.values() for w, *_ in e}, key=lambda w: w.encode())
    word_ids = {word: i for i, word in enumerate(words)}
    trie: dict = {}
    for k in keys:
        t = trie
        for c in k:
            t = t.setdefault(labels[ord(c)], {})
        t[0] = True
    registry: dict = {}
    nodes: list = []

    def intern(t: dict) -> int:
        arcs = tuple((label, intern(child)) for label, child in sorted(t.items()) if label)
        signature = (0 in t, arcs)
        if signature in registry:
            return registry[signature]
        number = len(nodes)
        count = int(0 in t) + sum(nodes[child][2] for _, child in arcs)
        nodes.append((0 in t, arcs, count))
        registry[signature] = number
        return number

    root = intern(trie)
    offsets = [0]
    arc_blob = bytearray()
    counts = bytearray()
    for final, arcs, count in nodes:
        for label, child in arcs:
            arc_blob.extend(bytes([label]) + pack(child))
        offsets.append(len(arc_blob) // (width + 1))
        counts.extend(pack(count | ((1 << (bits - 1)) if final else 0)))
    posts = bytearray()
    post_offsets = [0]
    for k in keys:
        records = sorted(entries[k], key=lambda r: (word_ids[r[0]], r[1]))
        posts.extend(vint(len(records)))
        previous = 0
        for word, region, score, derived in records:
            wid = word_ids[word]
            posts.extend(vint(((wid - previous) << 4) | region | (8 if derived else 0)))
            posts.append(score)
            previous = wid
        post_offsets.append(len(posts))
    word_blob = bytearray()
    block_offsets = []
    for block in range(0, len(words), 16):
        block_offsets.append(len(word_blob))
        previous = b""
        for word in words[block:block + 16]:
            encoded = word.encode()
            shared = 0
            while shared < min(len(previous), len(encoded)) and previous[shared] == encoded[shared]:
                shared += 1
            word_blob.extend(vint(shared) + vint(len(encoded) - shared) + encoded[shared:])
            previous = encoded
    block_offsets.append(len(word_blob))
    sections = [b"".join(struct.pack("<I", cp) for cp in alphabet),
                b"".join(pack(n) for n in offsets), counts, arc_blob,
                b"".join(pack(n) for n in post_offsets), posts,
                b"".join(pack(n) for n in block_offsets), word_blob]
    directory = [108]
    for section in sections:
        directory.append(directory[-1] + len(section))
    header = b"IPADWG01" + struct.pack("<7I9Q", 2 if bits == 24 else 3, len(nodes), len(arc_blob) // (width + 1),
                                          len(keys), len(words), len(alphabet), root, *directory)
    return header + b"".join(sections)


def build_jni_fixture() -> bytes:
    entries = {key: records.copy() for key, records in DEFAULT_ENTRIES.items()}
    entries["a"].extend([("\U00020000", 2, 75, False), ('A"B\\C', 0, 65, False)])
    entries["á"] = [("é", 0, 60, False)]
    return build(entries=entries)


if __name__ == "__main__":
    Path(sys.argv[1]).write_bytes(build(int(sys.argv[2]) if len(sys.argv) > 2 else 24))
