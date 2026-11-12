# Rime build notices

This directory preserves notices for the official sources and generated
resource package used by this build. `native-sources.json` records the source
commit or archive identity, original file SHA-256, copied byte range when a
notice is embedded in a header, and the SHA-256 of every local native notice.
`../sources.lock.json` identifies the native build inputs; the separate
`../resource-sources.lock.json` identifies the schemas and dictionaries.

## Native code and headers

| Component | Source used | Notice here |
| --- | --- | --- |
| librime 1.17.0 | Official librime commit `33e78140250125871856cdc5b42ddc6a5fcd3cd4` | `native/librime-LICENSE.txt`, BSD-3-Clause |
| LevelDB | Official commit `99b3c03b3284f5886f9ef9a4ef703d57373e61be` | `native/LevelDB-LICENSE.txt` and `native/LevelDB-AUTHORS.txt`, BSD-3-Clause |
| yaml-cpp | Official commit `2f86d13775d119edbb69af52e5f566fd65c6953b` | `native/yaml-cpp-LICENSE.txt`, MIT |
| Dragonbox within yaml-cpp | `src/contrib/dragonbox.h` at the same yaml-cpp commit | `native/yaml-cpp-Dragonbox-NOTICE.txt`; upstream expressly grants the MIT option for use in yaml-cpp |
| Boost 1.89.0 | Official release archive, SHA-256 locked | `native/Boost-LICENSE_1_0.txt`, Boost Software License 1.0 |
| Darts-clone within librime | `include/darts.h` and its accompanying `include/COPYING.darts-clone` in the pinned librime tree | `native/librime-Darts-COPYING.txt`, BSD-3-Clause |
| UTF8-CPP within librime | `include/utf8.h` and checked, unchecked, core, cpp17 headers in the pinned librime tree | Five `native/UTF8-*-NOTICE.txt` files, original Boost-style grant and individual copyright years |
| X11 key definitions within librime | `include/X11/keysym.h` and `keysymdef.h` in the pinned librime tree | Two `native/X11-*-NOTICE.txt` files, original The Open Group and Digital Equipment Corporation notices |

Header notices are exact leading byte ranges, not rewritten summaries or
replacement license headers. The source lock makes those extractions
independently reproducible. Full corresponding headers remain available in the
pinned source trees used by the build. The UTF8 headers have different
copyright years, so all five notices are retained even where their license
terms are otherwise identical.

The compiler command inventory and generated dependency files were inspected.
In particular, `fptostring.cpp` includes Dragonbox even with
`YAML_CPP_BUILD_CONTRIB=OFF`; that build option does not remove this header.
The Rime headers listed above occur in actual compiled translation units.
This inspection establishes which notices are needed; it does not itself
establish Android runtime compatibility.

## Shared OpenCC and toolchain notices

Rime statically links the OpenCC 1.4.2 and marisa archives supplied by the
sibling `OpenCC` build. It reuses that build's vendored marisa source and
headers, including the documented vendor differences; it does not link a
second independently built marisa implementation.

Keep the following files from [the OpenCC license inventory](../../OpenCC/licenses/README.md)
with any standalone redistribution of the Rime native artifact:

- `../../OpenCC/licenses/OpenCC-1.4.2-LICENSE.txt` — Apache-2.0.
- `../../OpenCC/licenses/Marisa-0.3.1-COPYING.md` — original dual-license notice;
  this build uses its BSD-2-Clause option.
- `../../OpenCC/licenses/RapidJSON-1.1.0-license.txt` — MIT and the original
  bundled notices.
- `../../OpenCC/licenses/Darts-clone-COPYING.md` — BSD-2-Clause for OpenCC's
  own embedded Darts implementation. This is a different vendored copy from
  librime's BSD-3-Clause Darts header, and both notices are retained.
- `../../OpenCC/licenses/NDK-r29-NOTICE.txt` and
  `../../OpenCC/licenses/NDK-r29-NOTICE.toolchain.txt` — original NDK r29
  notices, including static C++/ABI and compiler runtime terms.

`native-sources.json` includes hashes and paths for these shared notices.
The broad NDK notice files are preserved in full; their presence does not
assert that every toolchain component is linked into the library.

The configured Rime runtime excludes glog, gflags, GoogleTest, external Rime
plugins, Snappy, external crc32c, and tcmalloc. LevelDB's own CRC implementation
is still compiled as part of LevelDB. Test-only GoogleTest notices in the
sibling OpenCC tree describe its host verification and are not a Rime runtime
dependency. No Trime code or Trime-extracted library is used in this build.

## Resources and project-authored files

[Resource notices](resources/README.md) cover the separately pinned Rime
schemas, lexical data, and OpenCC conversion data. They retain upstream LGPL
v3 notices, the accompanying neutral GPL v3 text, CC-CEDICT attribution, and
applicable CC-BY-SA terms. Replacing the native runtime does not change these
resource licenses.

The newly authored IPA Engine bridge, build scripts, and policy overlays are
copyright (c) 2026 PantingEYEs, all rights reserved. No project-level software
license has been selected for those files. This does not replace or narrow the
licenses attached to the upstream components. The former IPA Board bridge is
not copied or relicensed by this inventory. These notices do not claim that
IPA Board as a whole has changed its license.
