#!/usr/bin/env python3
# Copyright 2026 PantingEYEs. All rights reserved.
"""Create a deterministic AAR after verifying the actual build inputs."""
from pathlib import Path
import importlib.util
import io
import json
import zipfile
from build import ROOT, ABIS, record, verify_local_provenance

AAR_NAME = 'ipa-latinime-android-heliboard-4.1.aar'
CLASS_NAME = 'org/ipaboard/latinime/LatinImeNative.class'

def archive(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            output.writestr(info, data, compresslevel=9)
    return stream.getvalue()

def verify_inventory(root=ROOT):
    spec = importlib.util.spec_from_file_location('latinime_inventory', root / 'source-inventory/verify.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify(root=root)

def aar_notices(root=ROOT):
    names = ['licenses/native/HeliBoard-Apache-2.0.txt',
             'licenses/toolchain/NDK-r29-NOTICE.txt',
             'licenses/toolchain/NDK-r29-NOTICE.toolchain.txt']
    result = {'META-INF/' + name: (root / name).read_bytes() for name in names}
    result['META-INF/licenses/native-files.json'] = (root / 'source-inventory/native-files.json').read_bytes()
    result['META-INF/NOTICE.txt'] = (
        'HeliBoard 4.1 LatinIME native core, fixed source 9f5bb635c2e8609dcd95dc7506c0c58fba82a52c.\n'
        'Selected upstream native files: Apache-2.0; original file notices are in native-files.json.\n'
        'Android NDK static C++ runtime: original component notices are retained.\n'
        'New read-only JNI adapter: Copyright 2026 PantingEYEs. All rights reserved.\n'
        'No project-wide replacement license has been selected.\n'
        'This AAR contains no dictionary. The separately supplied main_en-US.dict has its own\n'
        'provenance and license evidence in dictionary-sources.lock.json and licenses/dictionary.\n'
    ).encode()
    return result

def main():
    proof = verify_local_provenance()
    inventory = verify_inventory()
    classes = ROOT / 'artifacts/java'
    class_files = {p.relative_to(classes).as_posix(): p.read_bytes() for p in classes.rglob('*.class')}
    if set(class_files) != {CLASS_NAME}:
        raise ValueError('Unexpected compiled Java class inventory')
    jar = archive(class_files)
    (ROOT / 'artifacts/classes.jar').write_bytes(jar)
    entries = {
        'classes.jar': jar,
        'AndroidManifest.xml': b'<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="org.ipaboard.latinime"><uses-sdk android:minSdkVersion="26"/></manifest>\n',
        'R.txt': b'',
        'proguard.txt': b'-keep class org.ipaboard.latinime.LatinImeNative { *; }\n',
        'META-INF/ipa-latinime-build-id.txt': (proof['buildId'] + '\n').encode(),
        'META-INF/ipa-latinime-build-provenance.json': (json.dumps(proof, indent=2) + '\n').encode(),
    }
    for abi in ABIS:
        entries[f'jni/{abi}/libipa_latinime.so'] = (ROOT / 'artifacts' / abi / 'libipa_latinime.so').read_bytes()
    entries.update(aar_notices())
    aar = ROOT / 'artifacts' / AAR_NAME
    aar.write_bytes(archive(entries))
    paths = [aar, ROOT / 'artifacts/classes.jar', ROOT / 'dictionary/main_en-US.dict',
             ROOT / 'artifacts/build-provenance.json', ROOT / 'artifacts/java' / CLASS_NAME]
    paths += [ROOT / 'artifacts' / abi / 'libipa_latinime.so' for abi in ABIS]
    manifest = {'schemaVersion': 1, 'component': 'HeliBoard LatinIME native core',
                'upstreamVersion': '4.1', 'upstreamCommit': proof['inputs']['upstreamCommit'],
                'buildId': proof['buildId'], 'buildProvenance': proof,
                'sourceLockSha256': record(ROOT / 'sources.lock.json')['sha256'],
                'dictionaryLockSha256': record(ROOT / 'dictionary-sources.lock.json')['sha256'],
                'sourceInventorySha256': record(ROOT / 'source-inventory/published-files.json')['sha256'],
                'javaClass': proof['inputs']['javaClass'],
                'files': {p.relative_to(ROOT).as_posix(): record(p) for p in paths}}
    (ROOT / 'artifacts/manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'aar': AAR_NAME, 'buildId': proof['buildId'], 'inventory': inventory}, indent=2))

if __name__ == '__main__':
    main()
