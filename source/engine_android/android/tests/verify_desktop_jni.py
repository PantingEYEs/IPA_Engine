"""Exercise the real JNI bridge on a desktop JVM, without claiming Android execution.

The SDK's org.json methods are stubs on a desktop JVM. The Java harness checks
public normalization/lifecycle and reads private native JSON only for Unicode
transport checks; Android JSONObject parsing and asset extraction still require
an emulator/device. Generated artifacts stay under data/engine_android/probes/.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from engine_android.scripts.build_rust_core import build as build_rust
from cli_tests.tests.fixtures.packed_dictionary import build_jni_fixture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--android-jar', required=True, type=Path)
    parser.add_argument('--java-home', type=Path)
    args = parser.parse_args()
    if platform.system() not in ('Darwin', 'Linux'):
        parser.error('desktop JNI smoke supports macOS and Linux')
    java_home = args.java_home
    if java_home is None:
        if os.environ.get('JAVA_HOME'):
            java_home = Path(os.environ['JAVA_HOME'])
        elif platform.system() == 'Darwin':
            java_home = Path(subprocess.check_output(['/usr/libexec/java_home'], text=True).strip())
        else:
            java_home = Path(shutil.which('javac') or '').resolve().parents[1]
    java_home = java_home.resolve()
    jar = args.android_jar.resolve()
    if not jar.is_file():
        parser.error('android.jar was not found')
    build = ROOT / 'data/engine_android/probes/desktop-jni-smoke'
    classes = build / 'classes'
    classes.mkdir(parents=True, exist_ok=True)
    compiler = shutil.which('clang++') or shutil.which('g++')
    if not compiler:
        parser.error('a desktop C++17 compiler is required')
    library = build / ('libipa_graph_android.dylib' if platform.system() == 'Darwin'
                       else 'libipa_graph_android.so')
    shared = '-dynamiclib' if platform.system() == 'Darwin' else '-shared'
    rust_library = build_rust(bins=()) / 'libipa_graph.a'
    system_libraries = ['-framework', 'Security', '-framework', 'CoreFoundation', '-liconv'] if platform.system() == 'Darwin' else ['-ldl', '-lpthread', '-lm']
    subprocess.run([compiler, '-std=c++17', '-O2', '-fPIC', shared, '-Wall', '-Wextra', '-Wpedantic',
                    '-I', str(java_home / 'include'),
                    '-I', str(java_home / 'include' / ('darwin' if platform.system() == 'Darwin' else 'linux')),
                    '-I', str(ROOT / 'engine_android/android/src/main/cpp'),
                    str(ROOT / 'engine_android/android/src/main/cpp/ipa_graph_jni.cpp'), str(rust_library),
                    *system_libraries, '-o', str(library)], check=True)
    subprocess.run([str(java_home / 'bin/javac'), '-encoding', 'UTF-8', '-source', '8', '-target', '8',
                    '-Xlint:-options', '-classpath', str(jar), '-d', str(classes),
                    str(ROOT / 'engine_android/android/src/main/java/com/ipaengine/graph/GraphEngine.java'),
                    str(ROOT / 'engine_android/android/tests/JniSmoke.java')], check=True)
    quoted = 'A' + chr(34) + 'B' + chr(92) + 'C'
    fixture = build / ('fixture-' + chr(0x5b57) + '-' + chr(0x1f600) + '.ipad')
    fixture.write_bytes(build_jni_fixture())
    result = subprocess.run([str(java_home / 'bin/java'), '-Xcheck:jni',
                             '-Djava.library.path=' + str(build), '-classpath', str(classes) + os.pathsep + str(jar),
                             'JniSmoke', str(fixture)], check=True, text=True, capture_output=True)
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(records) == 7
    assert records[0]['status'] == 'ok'
    texts = {candidate['text'] for candidate in records[0]['candidates']}
    assert chr(0x20000) in texts, 'supplementary output was corrupted'
    assert quoted in texts, 'quote/backslash JSON escaping was corrupted'
    assert records[1]['candidates'][0]['text'] == '\u00e9', 'NFC output was corrupted'
    assert records[2]['candidates'][0]['text'] == '\u5b66\u6821', 'Japanese UTF-8 output was corrupted'
    assert records[3]['status'] == records[4]['status'] == 'unsupported_input'
    assert all(candidate['region'] == 'zh-CN' for candidate in records[5]['candidates'])
    assert any(c['text'] == 'about' and c['omitted_symbols'] == 1 for c in records[6]['candidates']), 'JNI omission option not applied'
    print(result.stderr.strip())
    print(json.dumps({'status': 'passed', 'platform': platform.system(), 'queries': len(records),
                      'checks': ['Unicode filename', 'standard UTF-8 supplementary output', 'JSON escaping',
                                 'NFC', 'invalid UTF-16', 'region policy', 'closed handle', 'missing dictionary',
                                 'omission option and diagnostics'],
                      'android_device_execution': False}, indent=2))


if __name__ == '__main__':
    main()
