# IPADWG01 versions 2 and 3

All integers are little endian. Sections are contiguous without padding. The
108-byte header contains magic `IPADWG01`, seven uint32 values (`version=2|3`,
node count, arc count, IPA key count, unique text count, alphabet count, root
node ID), eight uint64 section offsets in the order below, and one uint64 end
offset equal to the complete file size. Version 1 was a measured uint32 array
baseline. The compiler defaults to version 2; `--index-bits 32` selects version
3. The header remains 108 bytes for both versions.

| Section | Encoding |
| --- | --- |
| alphabet | Alphabet count uint32 Unicode scalar values in ascending order. Labels are index + 1, at most 255. |
| node_offsets | Node count + 1 uint24 arc indices. |
| node_counts | Node count uint24 values: bit 23 marks final; low 23 bits count accepted suffix strings. |
| arcs | Arc count four-byte records: uint8 alphabet label, uint24 target node ID. Labels increase within a node. Children precede parents. |
| post_offsets | IPA key count + 1 uint24 byte offsets into post_blob. |
| post_blob | Each key's unsigned varint count followed by postings. |
| word_block_offsets | ceil(unique text count / 16) + 1 uint24 byte offsets into word_blob. |
| word_blob | Front-coded UTF-8 text blocks, each containing up to 16 words. |

Version 3 uses uint32 for all four index/count arrays (`node_offsets`,
`node_counts`, `post_offsets`, `word_block_offsets`), with final bit 31 and low
31 accepted-suffix-count bits. Each arc is exactly **five bytes**: uint8 label,
uint32 target, without alignment padding. The alphabet, header, postings and
front-coded text encodings are otherwise identical. The phone parser selects
width from the version; it must not reinterpret v2 bytes as native uint32.

`scripts/convert_graph_indices.py --source ... --output ... --index-bits 24|32`
changes only integer widths and final-bit locations. It validates the source
manifest SHA256, retains source licenses/provenance, hashes the unchanged
posting and text blobs, and never rescans SQLite. Converting v2 → v3 → v2
recreates the original dictionary bytes and SHA256 exactly. Output must use a
different path, so comparing widths cannot replace the default dictionary.

The DAFSA is a minimal deterministic acyclic **acceptor**, independent of
candidate payloads. Sorted incremental construction merges states whose final
flag and sorted `(label, canonical child ID)` signatures are identical. Posting
lists are addressed by the accepted key's lexicographic rank: before following
an edge add one if the current node is final, then add suffix counts of smaller
outgoing labels. Start at rank zero; the terminal's accumulated value is its
posting directory index. This preserves sharing when different IPA keys have
different candidate lists.

A posting record is `unsigned_varint((delta_word_id << 4) | region |
(derived << 3))`, then one uint8 score. Records are sorted by `(word_id, region)`;
previous word ID resets to zero for each key. Region IDs are 0=en-US, 1=en-GB,
2=zh-CN, 3=zh-TW, 4=ja-JP. Bit 3 marks a derived reading. Score is half-up
rounded `clamp(Zipf, 0, 9) * 20`; missing frequency is zero. Bits 0..2 cannot
encode any other region.

Word IDs index globally unique NFC texts in Unicode scalar order. Each word is
`varint(shared_prefix_bytes) + varint(suffix_bytes) + suffix`. The first word of
each 16-word block has zero shared prefix. A byte prefix may end inside a UTF-8
scalar; decoding reconstructs the entire byte string before validating UTF-8.
No NUL terminator is present.

For v2, graph targets and arc indices must fit uint24. Accepted key counts fit
23 bits; posting and text blobs are each smaller than 2^24 bytes. Version 3 uses
uint32 targets/offsets and 31 accepted-suffix-count bits. The compiler rejects
overflow, invalid lexical text, and packages larger than the explicit byte
limit; it never truncates fields or removes usable entries to satisfy a size
limit. Raw input is limited to 64 UTF-16 units; output texts may use up to 1000
UTF-16 units. NFC, tone marks, stress, separators, and all five regions are
preserved.

The JSON manifest and optional provenance JSONL are offline audit artifacts.
Only the `.ipad` lexical package is required by the phone runtime. Source rows
are deduplicated by `(IPA, output text, region)` with direct provenance preferred
over screened derived readings. Frequency evidence is maximum comparable word
Zipf from the same region or mixed region, scanned once per language; raw
counts are never added across corpora or assigned to individual readings.
