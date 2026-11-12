# IPA Rime JNI boundary

Copyright (c) 2026 PantingEYEs. All rights reserved.
No open-source license is currently granted for this project-authored adapter or its Java API. Upstream dependencies retain their own licenses.

`rime_jni.cpp` and `../java/org/ipaboard/rime/RimeNative.java` define a small, versioned interface for the source-built Rime/OpenCC runtime. The implementation is newly written against upstream interfaces and the IPA Board input/output contract. It does not contain Trime JNI code or copy the old IPA Board candidate bridge implementation.

## Build boundary

Compile this file and the pinned upstream librime sources into the same `libipa_rime.so`. Link the matching OpenCC build into that library. Do not substitute a binary librime from a different build: candidate ranges require upstream C++ objects which must stay inside this one link unit. No C++ object, raw pointer, allocator-owned buffer, JNA structure, or private upstream type crosses into Java.

The build must define string macros `IPA_RIME_BUILD_ID` and `IPA_OPENCC_VERSION`; omission is a compile error. The first identifies the locked dependency sources and adapter sources. The second identifies the actual linked OpenCC. The build must disable upstream runtime logging when logs could contain input or private resource paths. The adapter itself never logs input or paths.

Java loads `ipa_rime` and verifies ABI version 1 in its class initializer. `identity()` returns UTF-8 metadata with ABI version, packet version, librime version, OpenCC version, and build identifier. It does not report that any test has passed.

## Ownership and operations

All calls use one native mutex. Rime is process-global; exactly one owner may be open at a time, and no other code may initialize, finalize, or mutate the linked Rime runtime. Call `open` and queries on a background worker. `open` initializes Rime, waits for maintenance to finish, and opens the two OpenCC converters. Paths must be separate shared/user directories and absolute, valid, NUL-free standard UTF-8. Java strings are deliberately not accepted, avoiding JNI modified UTF-8.

The returned positive integer is a monotonically increasing handle, not a native address. Stale, invalid, and repeated-close handles fail. Failed initialization cleans up owned resources. Explicit `close` destroys sessions, closes both converters, and finalizes Rime. A second simultaneous `open` fails. Later reopening obtains a different handle.

`query(handle, schema, spelling, limit)` accepts `luna_pinyin`, `terra_pinyin`, or `bopomofo`, 1..256 bytes of printable ASCII encoded spelling, and a limit of 1..40. Zhuyin must be encoded into the scheme's ASCII keys by the caller; returned byte offsets refer to those keys, not displayed Unicode symbols. A session is allocated lazily per schema. Missing schema identities are rejected before use, because upstream `select_schema` can accept missing schema files.

Every query discards previous input/pending commit, feeds the spelling exactly once, snapshots the menu, and discards input/pending commit afterward. Enumeration never selects or commits a candidate. Preparing an upstream lazy menu cache is allowed. If querying throws, the affected session is destroyed; sessions for other schemas remain usable. Any unhandled key or unsafe coverage returns status 2 and no candidates. The native composition state is checked before/after enumeration. No per-candidate retyping or selection replay is used.

Schema options `ascii_mode`, `simplification`, `zh_hans`, `zh_hant_hk`, and `zh_hant_tw` are false; `_auto_commit` is true. Caller-provided schema patches must disable personal learning and completion. Simplified/traditional pairing remains separate from candidate generation: `convert(handle, text, direction)` uses OpenCC, with direction 0 for traditional-to-simplified and 1 for simplified-to-traditional. It accepts up to 1 MiB of valid NUL-free UTF-8, including empty input, and caps output at 1 MiB. Candidate ordering and conversion-pair policy belong to the caller.

Native exceptions are contained. Java receives a stable short error code via `IllegalArgumentException`, `IllegalStateException`, or `OutOfMemoryError`, without embedding user input or paths. Callers must preserve original text on unavailable/unsafe results and reject stale asynchronous results themselves.

## Candidate packet version 3

All integers are unsigned 32-bit little-endian. The returned Java `byte[]` owns its contents. A packet is at most 1 MiB, contains at most 40 entries, and each candidate text is at most 16 KiB of validated standard UTF-8.

| Header byte offset | Field |
| --- | --- |
| 0 | Magic `0x52494d45` |
| 4 | Packet version `3` |
| 8 | Status: `0` success, `2` unsafe/unsupported input coverage |
| 12 | Candidate count |
| 16 | Retained encoded input byte length |
| 20 | Total packet bytes |
| 24 | Already processed selected/literal prefix end, relative to retained input |
| 28 | Committed leading input byte count |

Entries immediately follow the 32-byte header. Each 20-byte entry has rank, native candidate start, native candidate end, text offset, and text byte length. Ranks are consecutive from zero. Text bytes follow entries in order with no gaps or terminating NUL. Each text combines any already committed/selected/literal prefix with that candidate; it does not append unconsumed raw suffix. Ranges are native encoded-input ranges and are not fabricated from text lengths.

For status 0, retained bytes plus committed leading bytes equal the supplied spelling length. The retained input must be an exact suffix of the supplied spelling. A nonzero dropped prefix additionally requires pending committed text, and is not accepted for Bopomofo. Prefix reconstruction checks native segment boundaries rather than estimating coverage. For status 2, the packet contains only the header, its input-length field equals the original supplied spelling length, and all other range/count fields are zero. Consumers must treat it as no conversion.

## Verification required by an integrator

Use isolated resource/user directories. Cover Luna, Terra (all tone keys and omitted tones), Bopomofo (including Unicode leading-tone rejection at the encoder and preserved literal prefixes when deliberately bypassing it), partial consumption and exact suffix preservation, quoted/punctuation prefixes, mixed-language caller mapping, traditional/simplified pairs, and missing-schema isolation. Check deterministic repeated queries, at most 40 ordered candidates, unhandled ASCII fallback, invalid UTF-8/NUL/oversize arguments, invalid/stale handles, double-open rejection, converter-open failure followed by successful open, and close/reopen. Ensure user dictionaries are not created or updated. Strict 16 KiB Android load/execution and APK alignment are separate checks; compiling this source alone proves neither.
A status-0 result may contain a retained literal prefix. For example, bypassing the Unicode encoder and submitting encoded 6su can yield 6你 with start/prefix-end 1 and end 3. This preserves all encoded input; it is not unsafe coverage. The application must still reject an invalid leading Unicode tone before encoding.
