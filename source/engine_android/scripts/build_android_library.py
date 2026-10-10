"""Build standalone arm64-v8a Android components and an on-device CLI with the NDK.

No Gradle download is needed: javac, the installed Android platform jar, NDK,
and the Python standard library produce the Java classes, JNI library and CLI.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from engine_android.scripts.build_rust_core import build as build_rust, source_digest


def digest(path):
    sha=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):sha.update(block)
    return sha.hexdigest()


def zip_add(archive,path,name):
    info=zipfile.ZipInfo(name,(2026,1,1,0,0,0))
    info.compress_type=zipfile.ZIP_DEFLATED
    info.external_attr=0o100644<<16
    archive.writestr(info,Path(path).read_bytes())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ndk',type=Path,default=ROOT/'.tools/android/android-ndk-r30')
    parser.add_argument('--sdk',type=Path,default=Path(os.environ.get('ANDROID_SDK_ROOT') or os.environ.get('ANDROID_HOME') or (Path.home()/'Library/Android/sdk' if platform.system()=='Darwin' else Path.home()/'Android/Sdk')))
    parser.add_argument('--android-jar',type=Path)
    parser.add_argument('--dictionary',type=Path,default=ROOT/'data/dictionary/compiled/lexicon.ipad')
    parser.add_argument('--manifest',type=Path,default=ROOT/'data/dictionary/compiled/lexicon.manifest.json')
    parser.add_argument('--output-dir',type=Path,default=ROOT/'data/engine_android/builds/24')
    parser.add_argument('--api',type=int,default=26)
    parser.add_argument('--core-only',action='store_true',help='skip copying dictionary files to the output directory')
    args=parser.parse_args()
    if args.api<26:parser.error('this wrapper requires Android API>=26')
    host={'Darwin':'darwin-x86_64','Linux':'linux-x86_64','Windows':'windows-x86_64'}[platform.system()]
    compiler=args.ndk/'toolchains/llvm/prebuilt'/host/'bin'/f'aarch64-linux-android{args.api}-clang++'
    if not compiler.exists():parser.error('NDK compiler not found; provide --ndk')
    android_jar=args.android_jar
    if android_jar is None:
        available=list((args.sdk/'platforms').glob('*/android.jar'))
        if not available:parser.error('Android platform jar not found; provide --android-jar or --sdk')
        android_jar=max(available,key=lambda p:p.stat().st_mtime_ns)
    javac=shutil.which('javac')
    if not javac:parser.error('JDK javac required')
    if not args.core_only:
        metadata=json.loads(args.manifest.read_text())
        if not metadata.get('complete'):parser.error('bundling an explicitly limited smoke lexicon is forbidden')
        if args.dictionary.stat().st_size>=40_000_000:parser.error('dictionary exceeds40MB comparison safety limit')
        if digest(args.dictionary)!=metadata['sha256']:parser.error('dictionary SHA256 differs from manifest')
    rust_output = build_rust('aarch64-linux-android', args.ndk, args.api)
    rust_library = rust_output / 'libipa_graph.a'
    args.output_dir.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ipa-android-',dir=args.output_dir) as temp:
        build=Path(temp);classes=build/'classes';classes.mkdir()
        sources=sorted((ROOT/'engine_android/android/src/main/java').rglob('*.java'))
        subprocess.run([javac,'--release','8','-encoding','UTF-8','-classpath',str(android_jar),
                        '-d',str(classes),*[str(p) for p in sources]],check=True)
        jar=build/'classes.jar'
        with zipfile.ZipFile(jar,'w') as z:
            for p in sorted(classes.rglob('*.class')):zip_add(z,p,p.relative_to(classes).as_posix())
        library=build/'libipa_graph_android.so'
        common=[str(compiler),'-std=c++17','-O3','-fPIC','-ffunction-sections','-fdata-sections',
                '-I',str(ROOT/'engine_android/android/src/main/cpp'),'-static-libstdc++','-Wl,--gc-sections',
                '-Wl,-z,max-page-size=16384','-Wl,--strip-all']
        subprocess.run([*common,'-shared','-fvisibility=hidden',
                        str(ROOT/'engine_android/android/src/main/cpp/ipa_graph_jni.cpp'),str(rust_library),
                        '-Wl,--exclude-libs,ALL','-ldl','-lm','-llog','-o',str(library)],check=True)
        cli=build/'ipa_graph'
        shutil.copyfile(rust_output / 'ipa_graph', cli)
        readelf=compiler.parent/'llvm-readelf'
        elf=subprocess.run([str(readelf),'-h','-d','-l',str(library)],check=True,capture_output=True,text=True).stdout
        if 'AArch64' not in elf:raise AssertionError('wrong Android ABI')
        required=['Java_com_ipaengine_graph_GraphEngine_nativeOpen','Java_com_ipaengine_graph_GraphEngine_nativeQuery']
        symbols=subprocess.run([str(readelf),'--dyn-syms',str(library)],check=True,capture_output=True,text=True).stdout
        for symbol in required:
            if symbol not in symbols:raise AssertionError(f'missing JNI export {symbol}')

        outputs={'classes.jar':jar,'libipa_graph_android.so':library,
                 'ipa_graph-arm64-v8a':cli}
        if not args.core_only:
            outputs['lexicon.ipad']=args.dictionary
            outputs['lexicon.manifest.json']=args.manifest
        for name,source in outputs.items():
            pending=build/(name+'.pending')
            shutil.copyfile(source,pending)
            pending.replace(args.output_dir/name)
            if name=='ipa_graph-arm64-v8a':
                (args.output_dir/name).chmod(0o755)
        elf_path=build/'native-elf.txt'
        elf_path.write_text(elf)
        elf_path.replace(args.output_dir/'native-elf.txt')

        native_verified=False
        native_report=ROOT/'data/cli_tests/reports/graph-s21-comparison.json'
        if not args.core_only:
            if native_report.is_file():
                try:
                    verified=json.loads(native_report.read_text())
                    native_verified=(verified.get('status')=='passed'
                        and verified['artifacts']['cli']['sha256']==digest(cli)
                        and any(verified['artifacts'][width]['sha256']==metadata['sha256'] for width in ('24','32')))
                except (KeyError,TypeError,ValueError):pass
        artifact_report={
            name:{'bytes':(args.output_dir/name).stat().st_size,
                  'sha256':digest(args.output_dir/name)}
            for name in outputs
        }
        report=dict(abi='arm64-v8a',min_sdk=args.api,platform='Android',implementation='rust',
                    rust_target='aarch64-linux-android',rust_source_sha256=source_digest(),
                    rust_version=subprocess.check_output(['rustc','--version'],text=True).strip(),
                    rust_library_sha256=digest(rust_library),
                    ndk=(args.ndk/'source.properties').read_text(),
                    artifacts=artifact_report,
                    so_bytes=library.stat().st_size,cli_bytes=cli.stat().st_size,
                    dictionary_bytes=0 if args.core_only else args.dictionary.stat().st_size,
                    dictionary_sha256=None if args.core_only else metadata['sha256'],
                    android_platform=android_jar.parent.name,
                    jni_exports_verified=True,aarch64_elf_verified=True,
                    native_device_verified=native_verified,
                    wrapper_device_verified=False,
                    native_device_report=str(native_report) if native_verified else None,
                    note='Actual NDK cross-compilation. Device verification is tied to matching artifact hashes '
                         'in the native CLI report; Android wrapper/device verification is not performed by this build. '
                         'IME host integration remains separate.')
        (args.output_dir/'build-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
