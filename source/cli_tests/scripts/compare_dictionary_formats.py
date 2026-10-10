"""Independent rank/payload oracle and varied-input Rust 24/32-bit benchmark.

This runs the actual native CLI. It does not import the dictionary compiler or
expand the full word pool. Sampling is reproducible; timings describe the host,
not an Android phone or a controlled cold page-cache experiment.
"""
from __future__ import annotations

import argparse
import json
import mmap
import random
import statistics
import struct
import subprocess
from pathlib import Path


class Oracle:
    def __init__(self, path):
        self.file = Path(path).open("rb")
        self.data = mmap.mmap(self.file.fileno(), 0, access=mmap.ACCESS_READ)
        header = struct.unpack_from("<8s7I9Q", self.data)
        _, self.version, self.nodes, self.arcs, self.keys, self.words, self.alphabet_size, self.root, *self.offsets = header
        self.width = 3 if self.version == 2 else 4
        self.final = 1 << (self.width * 8 - 1)
        self.alphabet = struct.unpack_from(f"<{self.alphabet_size}I", self.data, self.offsets[0])
        self.regions = ("en-US", "en-GB", "zh-CN", "zh-TW", "ja-JP")

    def index(self, section, index):
        offset = self.offsets[section] + index * self.width
        return int.from_bytes(self.data[offset:offset + self.width], "little")

    def key(self, rank):
        node = self.root
        value = []
        while True:
            flags = self.index(2, node)
            if flags & self.final:
                if rank == 0:
                    return "".join(value)
                rank -= 1
            for arc in range(self.index(1, node), self.index(1, node + 1)):
                offset = self.offsets[3] + arc * (self.width + 1)
                label = self.data[offset]
                child = int.from_bytes(self.data[offset + 1:offset + self.width + 1], "little")
                count = self.index(2, child) & (self.final - 1)
                if rank < count:
                    value.append(chr(self.alphabet[label - 1]))
                    node = child
                    break
                rank -= count
            else:
                raise AssertionError("oracle rank exceeded graph subtree")

    def varint(self, offset):
        value = shift = 0
        while True:
            byte = self.data[offset]
            offset += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value, offset
            shift += 7

    def word(self, wid):
        offset = self.offsets[7] + self.index(6, wid // 16)
        value = b""
        for _ in range(wid % 16 + 1):
            shared, offset = self.varint(offset)
            suffix, offset = self.varint(offset)
            value = value[:shared] + self.data[offset:offset + suffix]
            offset += suffix
        return value.decode("utf-8")

    def candidates(self, rank, raw, limit=30):
        offset = self.offsets[5] + self.index(4, rank)
        count, offset = self.varint(offset)
        wid = 0
        groups = {}
        for _ in range(count):
            code, offset = self.varint(offset)
            wid += code >> 4
            region = code & 7
            derived = bool(code & 8)
            freq = self.data[offset]
            offset += 1
            score = 10000 + freq - (5 if derived else 0)
            group = groups.setdefault(wid, {"mask": 0, "score": -1})
            group["mask"] |= 1 << region
            if score > group["score"]:
                group.update(score=score, freq=freq, region=region, derived=derived)
        ordered = sorted(groups, key=lambda wid: (-groups[wid]["score"], wid))[:limit]
        result = []
        for position, wid in enumerate(ordered):
            group = groups[wid]
            mask = group["mask"]
            labels = []
            if mask & 3:
                labels.append("EN")
            labels += [label for bit, label in ((4, "简"), (8, "繁"), (16, "日")) if mask & bit]
            result.append({"text": self.word(wid), "language": "/".join(labels),
                           "rank": position, "kind": "conversion", "native_score": group["score"],
                           "context_affinity": 0, "matched_ipa": raw, "frequency_score": group["freq"],
                           "frequency_zipf": group["freq"] / 20, "region": self.regions[group["region"]],
                           "derived": group["derived"], "omitted_symbols": 0})
        return result, count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dictionary24", type=Path)
    parser.add_argument("dictionary32", type=Path)
    parser.add_argument("--binary", type=Path, default=Path(".tools/graph/ipa_graph"))
    parser.add_argument("--sample", type=int, default=5000)
    parser.add_argument("--iterations", type=int, default=100000)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    oracle = Oracle(args.dictionary24)
    ranks = random.Random(1717).sample(range(oracle.keys), min(args.sample, oracle.keys))
    raws = [oracle.key(rank) for rank in ranks]
    raw_lines = "\n".join(raws) + "\n"
    for path in (args.dictionary24, args.dictionary32):
        command = [str(args.binary), "repl", str(path), "--strict"]
        completed = subprocess.run(command, input=raw_lines, text=True, capture_output=True, check=True)
        results = [json.loads(line) for line in completed.stdout.splitlines()]
        assert len(results) == len(ranks)
        for rank, raw, actual in zip(ranks, raws, results):
            expected, records = oracle.candidates(rank, raw)
            assert actual["status"] == "ok" and actual["exact_found"] and not actual["truncated"], (path, rank, raw)
            assert actual["matched_keys"] == 1 and actual["matched_records"] == records, (path, rank, raw)
            assert actual["candidates"] == expected, (path, rank, raw, actual["candidates"], expected)
    benchmarks = {"24": [], "32": []}
    for repetition in range(args.repeats):
        order = (("24", args.dictionary24), ("32", args.dictionary32))
        if repetition % 2:
            order = tuple(reversed(order))
        for label, path in order:
            command = [str(args.binary), "bench", str(path), "-", "--iterations", str(args.iterations),
                       "--warmup", str(len(raws)), "--strict"]
            completed = subprocess.run(command, input=raw_lines, text=True, capture_output=True, check=True)
            benchmarks[label].append(json.loads(completed.stdout))
    summary = {}
    for label, runs in benchmarks.items():
        summary[label] = {name: statistics.median(run[name] for run in runs)
                          for name in ("load_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms")}
        summary[label]["dictionary_bytes"] = runs[0]["dictionary_bytes"]
    result = {"passed": True, "sample_seed": 1717, "independent_oracle_keys": len(raws),
              "comparisons": len(raws) * 2, "iterations_per_run": args.iterations,
              "repeats": args.repeats, "summary_medians": summary, "runs": benchmarks,
              "notes": "Native warm queries on this host, alternating formats. Initialization includes full structural/UTF8 validation; OS page cache not flushed. No S21 measurement."}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
