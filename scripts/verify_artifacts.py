#!/usr/bin/env python3
# Copyright 2026 PantingEYEs. All rights reserved.
"""Validate published artifacts without source caches, NDK or sibling checkouts."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET
from build import ROOT, ABIS, record, verify_published_provenance
from package import AAR_NAME, CLASS_NAME, verify_inventory, aar_notices
import elf

MACHINES = {'arm64-v8a': 183, 'x86_64': 62}
EXPORTS = {'Java_org_ipaboard_latinime_LatinImeNative_' + name for name in
           ('abiVersion', 'identity', 'open', 'query', 'probability', 'close')}

def sha(data):
    return hashlib.sha256(data).hexdigest()

def archive_entries(data):
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        names = source.namelist()
        if len(set(names)) != len(names):
            raise ValueError('Duplicate ZIP entry')
        if any(name.startswith('/') or '\\' in name or '..' in name.split('/') for name in names):
            raise ValueError('Unsafe ZIP entry')
        return {name: source.read(name) for name in names}

def inspect_native(data, abi, build_id=None):
    result = elf.inspect_elf(data, MACHINES[abi], expected_soname='libipa_latinime.so',
                             required_exports=EXPORTS, exact_exports=True)
    if build_id is not None:
        if len(build_id) != 64 or any(c not in '0123456789abcdef' for c in build_id):
            raise ValueError('Invalid native build identity')
        if b';build=' + build_id.encode() not in data:
            raise ValueError('Native build identity mismatch')
    return result

def inspect_aar(data, libraries, proof, root=ROOT):
    entries = archive_entries(data)
    expected = {f'jni/{abi}/libipa_latinime.so' for abi in ABIS}
    if {p for p in entries if p.startswith('jni/') or p.endswith('.so')} != expected:
        raise ValueError('AAR native inventory differs')
    required = {'classes.jar', 'AndroidManifest.xml', 'R.txt', 'proguard.txt',
                'META-INF/ipa-latinime-build-id.txt', 'META-INF/ipa-latinime-build-provenance.json'}
    if not required <= entries.keys():
        raise ValueError('Missing AAR interface or identity entry')
    for abi, library in libraries.items():
        if entries[f'jni/{abi}/libipa_latinime.so'] != library:
            raise ValueError('AAR native bytes differ')
    if entries['META-INF/ipa-latinime-build-id.txt'] != (proof['buildId'] + '\n').encode():
        raise ValueError('AAR build identity differs')
    if json.loads(entries['META-INF/ipa-latinime-build-provenance.json']) != proof:
        raise ValueError('AAR build provenance differs')
    classes = archive_entries(entries['classes.jar'])
    if set(classes) != {CLASS_NAME}:
        raise ValueError('AAR Java class inventory differs')
    compiled = classes[CLASS_NAME]
    if {'sha256': sha(compiled), 'sizeBytes': len(compiled)} != proof['inputs']['javaClass']:
        raise ValueError('AAR Java bytecode differs from native build')
    if compiled[:8] != b'\xca\xfe\xba\xbe\0\0\0\x37':
        raise ValueError('Expected Java 11 bytecode')
    manifest = ET.fromstring(entries['AndroidManifest.xml'])
    sdk = manifest.find('uses-sdk')
    if manifest.get('package') != 'org.ipaboard.latinime' or sdk is None or sdk.get('{http://schemas.android.com/apk/res/android}minSdkVersion') != '26':
        raise ValueError('AAR manifest contract differs')
    if entries['proguard.txt'] != b'-keep class org.ipaboard.latinime.LatinImeNative { *; }\n':
        raise ValueError('AAR keep rules differ')
    notices = aar_notices(root)
    if {k: v for k, v in entries.items() if k.startswith('META-INF/licenses/') or k == 'META-INF/NOTICE.txt'} != notices:
        raise ValueError('AAR license bytes differ')
    if set(entries) != required | expected | notices.keys():
        raise ValueError('Unexpected AAR entry')
    return {'javaRelease': 11, 'jniAbiCount': len(libraries), 'noticeCount': len(notices)}

def verify_manifest_structure(manifest, independent_proof):
    if manifest.get('schemaVersion') != 1:
        raise ValueError('Unsupported package manifest')
    if manifest['buildProvenance'] != independent_proof:
        raise ValueError('Standalone build provenance differs from package manifest')
    required = {f'artifacts/{AAR_NAME}', 'artifacts/classes.jar',
                'artifacts/build-provenance.json', f'artifacts/java/{CLASS_NAME}',
                'dictionary/main_en-US.dict'}
    required.update(f'artifacts/{abi}/libipa_latinime.so' for abi in ABIS)
    if set(manifest['files']) != required:
        raise ValueError('Package artifact inventory differs from mandatory files')
    if manifest['javaClass'] != independent_proof['inputs']['javaClass']:
        raise ValueError('Package Java interface identity differs')
    if manifest['upstreamCommit'] != independent_proof['inputs']['upstreamCommit'] or manifest['upstreamVersion'] != '4.1':
        raise ValueError('Package upstream identity differs')

def inspect_package(root=ROOT):
    inventory = verify_inventory(root)
    manifest = json.loads((root / 'artifacts/manifest.json').read_text())
    proof = json.loads((root / 'artifacts/build-provenance.json').read_text())
    verify_manifest_structure(manifest, proof)
    identity = verify_published_provenance(proof, root)
    if manifest['buildId'] != identity or manifest['sourceLockSha256'] != record(root / 'sources.lock.json')['sha256'] or manifest['dictionaryLockSha256'] != record(root / 'dictionary-sources.lock.json')['sha256'] or manifest['sourceInventorySha256'] != record(root / 'source-inventory/published-files.json')['sha256']:
        raise ValueError('Package lock or identity mismatch')
    for name, expected in manifest['files'].items():
        if name.startswith('/') or '..' in Path(name).parts or record(root / name) != expected:
            raise ValueError('Package artifact mismatch: ' + name)
    libraries = {abi: (root / 'artifacts' / abi / 'libipa_latinime.so').read_bytes() for abi in ABIS}
    native = {abi: inspect_native(data, abi, identity) for abi, data in libraries.items()}
    aar = inspect_aar((root / 'artifacts' / AAR_NAME).read_bytes(), libraries, proof, root)
    return {'buildId': identity, 'native': native, 'aar': aar, 'inventory': inventory}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = inspect_package()
    text = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(text)

if __name__ == '__main__':
    main()
