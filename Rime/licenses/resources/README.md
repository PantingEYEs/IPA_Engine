# Chinese resource provenance and license notices

These resources were retrieved from the upstream repositories below, at exact
commits, rather than extracted from a Trime APK. Their bytes match the resources
used by IPA Board at `51072bf3599ea680a50debed527efa02912f96e1`, except for the
documented generated files. The runtime library versions and their licenses are
recorded separately: rebuilding a runtime does not change the licenses of its
schemas, dictionaries, or application integration.

| Upstream source | Pinned commit | Files included | Upstream license notice |
| --- | --- | --- | --- |
| [rime-prelude](https://github.com/rime/rime-prelude) | `3803f09458072e03b9ed396692ce7e1d35c88c95` | default, key bindings, punctuation, symbols | `prelude-LICENSE.txt`, GNU LGPL v3 |
| [rime-luna-pinyin](https://github.com/rime/rime-luna-pinyin) | `46acf03142c12b5aeed7002675046bf7255eed35` | main Luna schema, dictionary, pinyin rules | `luna-LICENSE.txt`, GNU LGPL v3 |
| [rime-essay](https://github.com/rime/rime-essay) | `0766c929ec3e578c2c80861e988decd3703a1c3d` | `essay.txt` vocabulary and weights | `essay-LICENSE.txt`, GNU LGPL v3 |
| [rime-stroke](https://github.com/rime/rime-stroke) | `7c9874c6b2e0b94947653e9a7de6f99623ff27e4` | stroke reverse lookup schema and dictionary | `stroke-LICENSE.txt`, GNU LGPL v3 |
| [rime-terra-pinyin](https://github.com/rime/rime-terra-pinyin) | `723e51bc266cf9464530c1ddedb856aa18e3da34` | Terra schema and tone dictionary | `terra-LICENSE.txt`, GNU LGPL v3; dictionary also carries CC-CEDICT attribution |
| [rime-bopomofo](https://github.com/rime/rime-bopomofo) | `6085c9a38a4a728047862b33d67eee18aa86f3b9` | Bopomofo schema and zhuyin rules | `bopomofo-LICENSE.txt`, GNU LGPL v3 |
| [OpenCC ver.1.2.0](https://github.com/BYVoid/OpenCC/tree/907bfcbbd3aae86ff04bc8eaca67c8af03108ddf) | `907bfcbbd3aae86ff04bc8eaca67c8af03108ddf` | ST/TS character and phrase dictionaries, s2t/t2s configs | `opencc-compat-LICENSE.txt`, Apache-2.0 |

## Preserved attributions

All original file headers are retained. In particular:

- `terra_pinyin.dict.yaml` identifies CC-CEDICT, published by MDBG, and
  [Creative Commons Attribution-ShareAlike 3.0](https://creativecommons.org/licenses/by-sa/3.0/).
  The accompanying `CC-BY-SA-3.0.txt` is the unmodified license text distributed
  by the official SPDX license-list-data repository at the revision recorded
  in the resource lock. Attribution and applicable share-alike requirements
  for this dictionary are independent of the native runtime's license.
- `luna_pinyin.dict.yaml` credits CC-CEDICT, Android Open Source Project
  PinyinIME, Chewing, the Sanpin traditional/simplified dictionary 0.9, OpenCC,
  and Moedict. These credits have not been removed or replaced by a claim that
  all underlying sources have a single new license.
- `stroke.dict.yaml` credits the CNS11643 character database, Kunki Chou,
  Peking University Chinese forum contributors, and the named Rime schema
  authors. The original attribution text and links remain in the source.

The six upstream LGPL files are included separately so each source keeps its
own notice. LGPL v3 incorporates GPL v3; `GPL-3.0.txt` supplies the full neutral
GPL text and is not an attribution to Trime. Redistributors must retain the
relevant notices and make the corresponding covered resource sources and
changes available under their applicable terms. The exact source resources
are included here, not only generated Rime binary dictionaries. The lock and
regeneration script identify how this package was prepared.

This inventory describes the notices in the pinned sources. It is not a claim
that changing the runtime alone relicenses IPA Board, its previous bridge, or
all historical data incorporated by upstream dictionary maintainers.

## Generated files and behavior scope

The four `*.custom.yaml` policy overlays are newly authored for the existing
application contract: the three selectable schemas are Luna Pinyin, Terra
Pinyin, and Bopomofo; user dictionary learning and automatic completion are
disabled; Chinese input and the original traditional form are selected on
initialization. The application bridge must still enforce its per-session
options and no-learning policy. These overlays and the preparation script are
copyright (c) 2026 PantingEYEs, all rights reserved; no project-level software
license has been selected. They do not replace the upstream resource licenses.

The two OpenCC configs are generated from the original ver.1.2.0 files by
changing dictionary type `ocd2` to `text` and suffix `.ocd2` to `.txt`, and adding
the explicit group dictionary setting `match_policy: short_circuit`. The latter
is required by the OpenCC 1.4 schema and equals the runtime's default when the
setting is absent. Dictionary content, conversion chains, and dictionary
priority are unchanged. Apart from this explicit default, `t2s.json` matches
IPA Board's existing file, and `s2t.json` matches the content IPA Board previously
generated at startup as `s2t-text.json`.

OpenCC conversion **data** intentionally remain at ver.1.2.0 to preserve
existing conversion behavior while the separately built **runtime** uses
ver.1.4.2. Runtime and data versions need not match. Updating to newer conversion
data is a separate product change and is not implied by the runtime migration.

The package contains the application's supported traditional/simplified
conversion paths. The official Bopomofo schema also describes optional Hong
Kong and Taiwan conversion filters; the current application disables those
options. Their regional conversion data are therefore not included. A future
integration that enables those options must add their pinned upstream data
before exposing them. Unselected Luna variants are likewise omitted.

## Verify and regenerate

From the `Rime` directory, verify the included bytes without network access:

```sh
python scripts/prepare_resources.py
```

To regenerate them from hash-checked, commit-pinned official sources:

```sh
python scripts/prepare_resources.py --prepare
```

After the download cache has been populated, regeneration can be offline:

```sh
python scripts/prepare_resources.py --prepare --offline
```

`--output PATH` prepares a separate tree; `--cache PATH` selects the source
cache. A missing or corrupted input aborts before any prepared output is
published. The default verification also rejects unlisted runtime resource
files. Keep resource and license bytes unchanged when checking out this branch;
the committed Git attributes disable automatic line-ending conversion for
these paths. Hash verification does not claim native or Android compatibility;
those checks are reported separately.
