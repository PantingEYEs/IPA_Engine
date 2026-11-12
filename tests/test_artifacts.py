# Copyright 2026 PantingEYEs. All rights reserved.
"""Mutation tests for actual published native and AAR artifacts; no device/cache needed."""
import copy
import io
import json
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build
import verify_artifacts as verify
from package import archive

class ArtifactsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.proof = json.loads((ROOT / 'artifacts/build-provenance.json').read_text())
        cls.identity = cls.proof['buildId']
        cls.libraries = {abi: (ROOT / 'artifacts' / abi / 'libipa_latinime.so').read_bytes() for abi in build.ABIS}
        cls.native = cls.libraries['arm64-v8a']
        cls.aar = (ROOT / 'artifacts' / verify.AAR_NAME).read_bytes()

    def test_published_package(self):
        verify.inspect_package()

    def test_wrong_abi_rejected(self):
        with self.assertRaisesRegex(ValueError, 'ABI'):
            verify.inspect_native(self.native, 'x86_64')

    def test_4kb_load_rejected(self):
        data = bytearray(self.native)
        h = struct.unpack_from('<16sHHIQQQIHHHHHH', data)
        for i in range(h[10]):
            offset = h[5] + i * h[9]
            if struct.unpack_from('<I', data, offset)[0] == 1:
                struct.pack_into('<Q', data, offset + 48, 4096)
                break
        with self.assertRaisesRegex(ValueError, '16KB LOAD'):
            verify.inspect_native(bytes(data), 'arm64-v8a')

    def test_missing_relro_rejected(self):
        data = bytearray(self.native)
        h = struct.unpack_from('<16sHHIQQQIHHHHHH', data)
        for i in range(h[10]):
            offset = h[5] + i * h[9]
            if struct.unpack_from('<I', data, offset)[0] == 0x6474e552:
                struct.pack_into('<I', data, offset, 0)
        with self.assertRaisesRegex(ValueError, 'RELRO'):
            verify.inspect_native(bytes(data), 'arm64-v8a')

    def test_stale_native_identity_rejected(self):
        data = self.native.replace(b';build=' + self.identity.encode(), b';build=' + b'0' * 64)
        self.assertNotEqual(data, self.native)
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            verify.inspect_native(data, 'arm64-v8a', self.identity)

    def test_dynamic_dependency_rejected(self):
        self.assertIn(b'libc.so\0', self.native)
        data = self.native.replace(b'libc.so\0', b'evil.so\0')
        with self.assertRaisesRegex(ValueError, 'dynamic dependency'):
            verify.inspect_native(data, 'arm64-v8a')

    def test_unexpected_public_api_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unexpected public exports'):
            verify.elf.inspect_elf(self.native, 183, 'libipa_latinime.so', set(), True)

    def test_core_archive_digest_tamper_rejected(self):
        proof = copy.deepcopy(self.proof)
        proof['inputs']['coreArchives']['arm64-v8a']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'provenance digest'):
            build.verify_published_provenance(proof)

    def test_runtime_archive_tamper_rejected(self):
        proof = copy.deepcopy(self.proof)
        runtime = proof['inputs']['runtimeLinkInputs']['arm64-v8a']
        name = next(name for name in runtime if name.endswith('/libc++abi.a'))
        runtime[name]['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'provenance digest'):
            build.verify_published_provenance(proof)

    def test_incomplete_runtime_inventory_rejected(self):
        proof = copy.deepcopy(self.proof)
        del proof['inputs']['runtimeLinkInputs']['x86_64']
        with self.assertRaisesRegex(ValueError, 'NDK link inputs'):
            build.verify_published_provenance(proof)

    def test_standalone_proof_mismatch_rejected(self):
        manifest = json.loads((ROOT / 'artifacts/manifest.json').read_text())
        proof = copy.deepcopy(self.proof)
        proof['buildId'] = '0' * 64
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'artifacts').mkdir()
            (root / 'artifacts/manifest.json').write_text(json.dumps(manifest))
            (root / 'artifacts/build-provenance.json').write_text(json.dumps(proof))
            # The independent license inventory is unrelated to this package-manifest mutation.
            with patch.object(verify, 'verify_inventory', return_value={}):
                with self.assertRaisesRegex(ValueError, 'Standalone build provenance differs'):
                    verify.inspect_package(root)

    def test_missing_mandatory_artifact_rejected(self):
        manifest = json.loads((ROOT / 'artifacts/manifest.json').read_text())
        del manifest['files']['dictionary/main_en-US.dict']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'artifacts').mkdir()
            (root / 'artifacts/manifest.json').write_text(json.dumps(manifest))
            (root / 'artifacts/build-provenance.json').write_text(json.dumps(self.proof))
            with patch.object(verify, 'verify_inventory', return_value={}):
                with self.assertRaisesRegex(ValueError, 'mandatory files'):
                    verify.inspect_package(root)

    def altered_recipe(self, name):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in self.proof['inputs']['recipe']:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / relative, target)
            path = root / name
            path.write_bytes(path.read_bytes() + b'\nchanged after compilation\n')
            with self.assertRaisesRegex(ValueError, 'recipe differs'):
                build.verify_published_provenance(self.proof, root)

    def test_changed_source_lock_rejected(self):
        self.altered_recipe('sources.lock.json')

    def test_changed_bridge_source_rejected(self):
        self.altered_recipe('bridge/latinime_jni.cpp')

    def test_changed_build_recipe_rejected(self):
        self.altered_recipe('CMakeLists.txt')

    def test_aar_native_mismatch_rejected(self):
        entries = verify.archive_entries(self.aar)
        entries['jni/arm64-v8a/libipa_latinime.so'] += b'corruption'
        with self.assertRaisesRegex(ValueError, 'native bytes differ'):
            verify.inspect_aar(archive(entries), self.libraries, self.proof)

    def test_extra_native_library_rejected(self):
        entries = verify.archive_entries(self.aar)
        entries['jni/arm64-v8a/liblegacy.so'] = self.native
        with self.assertRaisesRegex(ValueError, 'native inventory'):
            verify.inspect_aar(archive(entries), self.libraries, self.proof)

    def test_changed_java_bytecode_rejected(self):
        entries = verify.archive_entries(self.aar)
        classes = verify.archive_entries(entries['classes.jar'])
        classes[verify.CLASS_NAME] += b'changed'
        entries['classes.jar'] = archive(classes)
        with self.assertRaisesRegex(ValueError, 'Java bytecode differs'):
            verify.inspect_aar(archive(entries), self.libraries, self.proof)

    def test_aar_missing_notice_rejected(self):
        entries = verify.archive_entries(self.aar)
        del entries['META-INF/licenses/native/HeliBoard-Apache-2.0.txt']
        with self.assertRaisesRegex(ValueError, 'license bytes differ'):
            verify.inspect_aar(archive(entries), self.libraries, self.proof)

    def test_duplicate_zip_entry_rejected(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as output, warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            output.writestr('a', b'1')
            output.writestr('a', b'2')
        with self.assertRaisesRegex(ValueError, 'Duplicate ZIP'):
            verify.archive_entries(stream.getvalue())

    def test_unsafe_zip_entry_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unsafe ZIP'):
            verify.archive_entries(archive({'../a': b'no'}))

if __name__ == '__main__':
    unittest.main()
