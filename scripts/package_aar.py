#!/usr/bin/env python3
"""Create deterministic standard AAR from original upstream Java and native output."""
import argparse, pathlib, zipfile, hashlib, json
ROOT=pathlib.Path(__file__).resolve().parents[1]
def archive(path,entries):
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name,data in sorted(entries.items()):
            item=zipfile.ZipInfo(name,date_time=(2025,1,1,0,0,0));item.compress_type=zipfile.ZIP_DEFLATED
            item.external_attr=0o100644<<16
            z.writestr(item,data)
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--stage',type=pathlib.Path,required=True);parser.add_argument('--classes',type=pathlib.Path,required=True)
    args=parser.parse_args(); stage=args.stage
    for abi in ('arm64-v8a','x86_64'):
        actual={p.name for p in (stage/abi).glob('*.so') if p.is_file()}
        if actual!={'libortextensions.so','libonnxruntime_extensions4j_jni.so'}:
            raise SystemExit(f'Unexpected native library set for {abi}: {actual}')
    notices=ROOT/'licenses/sources.json'
    inventory=json.loads(notices.read_text())
    if not inventory.get('files'):raise SystemExit('Empty notice inventory')
    for item in inventory['files']:
        name=item['file']
        if not isinstance(name,str) or '\\' in name or ':' in name or pathlib.PurePosixPath(name).is_absolute() or '..' in pathlib.PurePosixPath(name).parts:
            raise SystemExit('Unsafe notice path')
        notice=ROOT/'licenses'/name
        if not notice.is_file() or not notice.read_bytes() or hashlib.sha256(notice.read_bytes()).hexdigest()!=item['sha256']:
            raise SystemExit(f'License missing, empty or changed: {name}')
    classes={p.relative_to(args.classes).as_posix():p.read_bytes() for p in args.classes.rglob('*.class')}
    if set(classes)!={'ai/onnxruntime/extensions/OrtxPackage.class','ai/onnxruntime/extensions/OrtxLibrary.class'}:
        raise SystemExit(f'Unexpected Java classes: {list(classes)}')
    jar=stage/'classes.jar';archive(jar,classes)
    entries={'classes.jar':jar.read_bytes(),'AndroidManifest.xml':b'<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="ai.onnxruntime.extensions"><uses-sdk android:minSdkVersion="26" /></manifest>\n' ,'R.txt':b'', 'META-INF/com/android/build/gradle/aar-metadata.properties':b'aarFormatVersion=1.0\naarMetadataVersion=1.0\nminCompileSdk=26\nminCompileSdkExtension=0\n'}
    for abi in ('arm64-v8a','x86_64'):
        for p in (stage/abi).glob('*.so'):entries[f'jni/{abi}/{p.name}']=p.read_bytes()
    # Include full original notice texts and exact component attribution.
    entries['META-INF/licenses/sources.json']=notices.read_bytes()
    for item in inventory['files']:
        entries['META-INF/licenses/'+item['file']]=(ROOT/'licenses'/item['file']).read_bytes()
    archive(stage/'onnxruntime-extensions-android-0.13.0-ipa-e5-16kb.aar',entries)
    jar.unlink()
if __name__=='__main__':main()
