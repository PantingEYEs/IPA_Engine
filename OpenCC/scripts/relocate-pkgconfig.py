#!/usr/bin/env python3
# Copyright 2026 PantingEYEs. All rights reserved.
"""Keep the installed pkg-config metadata usable after moving its prefix."""
from pathlib import Path
import sys
prefix = Path(sys.argv[1])
path = prefix / "lib/pkgconfig/opencc.pc"
content = path.read_text().replace("prefix=/ipa/opencc", "prefix=${pcfiledir}/../..")
content = content.replace("/ipa/opencc/lib", "${prefix}/lib")
content = content.replace("/ipa/opencc/include", "${prefix}/include")
if "Libs.private:" not in content:
    content += "\nLibs.private: -lmarisa -ldl\n"
path.write_text(content)
