"""Desktop-only reverse lookup of mappings retained in the compiled dictionary.

Word lookup uses binary search over the front-coded pool. Only a found word
requires a posting scan; IPA paths are reconstructed for its matching keys.
No source database, full dictionary expansion or Android changes are needed.
"""
import mmap
from pathlib import Path
import struct
import unicodedata

REGIONS = ('en-US', 'en-GB', 'zh-CN', 'zh-TW', 'ja-JP')
HEADER = struct.Struct('<8s7I9Q')


class CompiledLexicon:
    def __init__(self, path):
        self.file = Path(path).open('rb')
        self.data = None
        try:
            size = self.file.seek(0, 2)
            if not HEADER.size <= size < 40_000_000:
                raise ValueError('invalid compiled dictionary size')
            self.data = mmap.mmap(self.file.fileno(), 0, access=mmap.ACCESS_READ)
            magic, version, self.nodes, self.arcs, self.keys, self.words, alpha, self.root, *offsets = HEADER.unpack_from(self.data)
            if magic != b'IPADWG01' or version not in (2, 3):
                raise ValueError('expected compiled 24/32-bit IPADWG01 dictionary')
            self.width = 3 if version == 2 else 4
            self.final = 1 << (self.width * 8 - 1)
            self.offsets = offsets
            if (not self.nodes or not self.keys or not self.words or not 1 <= alpha <= 255
                    or self.root >= self.nodes or offsets[0] != HEADER.size or offsets[-1] != size
                    or any(a > b for a, b in zip(offsets, offsets[1:]))):
                raise ValueError('invalid dictionary header')
            sizes = {0: alpha * 4, 1: (self.nodes + 1) * self.width,
                     2: self.nodes * self.width, 3: self.arcs * (self.width + 1),
                     4: (self.keys + 1) * self.width,
                     6: ((self.words + 15) // 16 + 1) * self.width}
            if any(offsets[s + 1] - offsets[s] != n for s, n in sizes.items()):
                raise ValueError('invalid dictionary section lengths')
            self.alphabet = struct.unpack_from(f'<{alpha}I', self.data, offsets[0])
            if any(cp == 0 or cp > 0x10ffff or 0xd800 <= cp <= 0xdfff for cp in self.alphabet):
                raise ValueError('invalid dictionary alphabet')
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.data is not None:
            self.data.close()
            self.data = None
        self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()

    def index(self, section, index):
        at = self.offsets[section] + index * self.width
        if index < 0 or at + self.width > self.offsets[section + 1]:
            raise ValueError('dictionary index out of bounds')
        return int.from_bytes(self.data[at:at + self.width], 'little')

    def varint(self, at, end):
        value = 0
        for shift in range(0, 35, 7):
            if at >= end:
                break
            byte = self.data[at]
            at += 1
            value |= (byte & 127) << shift
            if byte < 128 and value <= 0xffffffff:
                return value, at
        raise ValueError('invalid dictionary varint')

    def word(self, wid):
        if not 0 <= wid < self.words:
            raise ValueError('invalid dictionary word ID')
        block = wid // 16
        at = self.offsets[7] + self.index(6, block)
        end = self.offsets[7] + self.index(6, block + 1)
        if not self.offsets[7] <= at <= end <= self.offsets[8]:
            raise ValueError('invalid word block offsets')
        value = b''
        for _ in range(wid % 16 + 1):
            shared, at = self.varint(at, end)
            length, at = self.varint(at, end)
            if shared > len(value) or at + length > end:
                raise ValueError('invalid front-coded word')
            value = value[:shared] + self.data[at:at + length]
            at += length
        return value.decode('utf-8')

    def word_id(self, text):
        # NFC and exact spelling, matching the compiled output pool. No guessed
        # case-folding, simplified/traditional conversion, or reading generation.
        text = unicodedata.normalize('NFC', text)
        lo, hi = 0, self.words
        while lo < hi:
            mid = (lo + hi) // 2
            value = self.word(mid)
            if value < text:
                lo = mid + 1
            else:
                hi = mid
        return lo if lo < self.words and self.word(lo) == text else None

    def key(self, rank):
        if not 0 <= rank < self.keys:
            raise ValueError('invalid IPA key rank')
        node, value = self.root, []
        while len(value) <= 64:
            if self.index(2, node) & self.final:
                if rank == 0:
                    return ''.join(value)
                rank -= 1
            start, end = self.index(1, node), self.index(1, node + 1)
            if not 0 <= start <= end <= self.arcs:
                raise ValueError('invalid graph arc offsets')
            for arc in range(start, end):
                at = self.offsets[3] + arc * (self.width + 1)
                label = self.data[at]
                child = int.from_bytes(self.data[at + 1:at + 1 + self.width], 'little')
                if not 1 <= label <= len(self.alphabet) or child >= node:
                    raise ValueError('invalid graph arc')
                count = self.index(2, child) & (self.final - 1)
                if rank < count:
                    value.append(chr(self.alphabet[label - 1]))
                    node = child
                    break
                rank -= count
            else:
                raise ValueError('IPA rank exceeds graph subtree')
        raise ValueError('IPA path exceeds input limit')

    def reverse(self, text, regions=None):
        wid = self.word_id(text)
        if wid is None:
            return []
        enabled = set(regions) if regions is not None else set(REGIONS)
        if not enabled.issubset(REGIONS):
            raise ValueError('unknown reverse lookup region')
        readings = []
        for rank in range(self.keys):
            at = self.offsets[5] + self.index(4, rank)
            end = self.offsets[5] + self.index(4, rank + 1)
            if not self.offsets[5] <= at <= end <= self.offsets[6]:
                raise ValueError('invalid posting offsets')
            count, at = self.varint(at, end)
            word = 0
            matches = []
            for _ in range(count):
                code, at = self.varint(at, end)
                word += code >> 4
                region = code & 7
                if at >= end or word >= self.words or region >= len(REGIONS) or self.data[at] > 180:
                    raise ValueError('invalid dictionary posting')
                score = self.data[at]
                at += 1
                if word > wid:
                    break  # Records are sorted by word ID within every key.
                if word == wid and REGIONS[region] in enabled:
                    matches.append(dict(region=REGIONS[region], derived=bool(code & 8),
                                        frequency_score=score, frequency_zipf=score / 20))
            if matches:
                ipa = self.key(rank)
                readings.extend(dict(ipa=ipa, **match) for match in matches)
        return readings
