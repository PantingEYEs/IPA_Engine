#!/usr/bin/env python3
# Copyright (c) 2026 PantingEYEs. All rights reserved.
# No project-level software license has been selected.
"""Offline provenance regression checks against the published package.

Run with --write-report to save validation/build-provenance-tests.json.
No private build cache, Android toolchain, network, or device is required.
Two private-input tests create synthetic archives/headers and stub only the
subsequent compiler/toolchain inspection; input hashing and rejection are real.
"""
from pathlib import Path
import argparse
import copy
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_provenance', ROOT/'scripts/build_provenance.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
MANIFEST = json.loads((ROOT/'artifacts/manifest.json').read_text())
PUBLIC = MANIFEST['buildProvenance']


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ipa-rime-provenance-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)/'Rime'
        for relative in module.LINK_RECIPE:
            destination = self.root.parent/relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT.parent/relative, destination)
        # Plain copies work on Windows without symlink privileges. No test can
        # mutate the repository's actual public static prefixes through an alias.
        for abi in module.ABIS:
            shutil.copytree(ROOT.parent/'OpenCC/artifacts'/abi/'static',
                            self.root.parent/'OpenCC/artifacts'/abi/'static')

    @contextmanager
    def synthetic_private_core(self):
        paths = {}
        for label in sorted(module.expected_labels()):
            target = self.root.parent/'synthetic-inputs'/label
            if label.endswith('.a'):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'!<arch>\nsynthetic test input: '+label.encode())
            else:
                target.mkdir(parents=True, exist_ok=True)
                (target/'header.hpp').write_text('// synthetic original header\n')
            paths[label] = str(target)
        core = copy.deepcopy(PUBLIC['core'])
        core['inputs'] = module.records(paths)
        environment = {key: str(self.root.parent/('synthetic-'+key))
                       for key in ('build', 'boost', 'ndk', 'cmake')}
        proof = {'statement': core, 'digest': module.digest(core),
                 'private': {'inputPaths': paths, 'environment': environment}}
        module.save(self.root/'.work/core-provenance.json', proof)
        with patch.object(module, 'toolchain', return_value=core['toolchain']), \
             patch.object(module, 'compiler_evidence', return_value=core['compilation']):
            # Establish that the fixture is accepted before altering its bytes.
            self.assertEqual(proof, module.verify_core(self.root))
            yield paths

    def test_published_proof_verifies_without_any_private_work_directory(self):
        self.assertFalse((self.root/'.work').exists())
        self.assertEqual(PUBLIC, module.verify_published_provenance(PUBLIC, self.root))

    def test_modified_recipe_is_rejected(self):
        script = self.root/'scripts/build.sh'
        script.write_bytes(script.read_bytes()+b'\n# changed recipe\n')
        with self.assertRaisesRegex(ValueError, 'stale relative to sources/recipe'):
            module.verify_published_provenance(PUBLIC, self.root)

    def test_modified_public_proof_digest_is_rejected(self):
        modified = copy.deepcopy(PUBLIC)
        modified['link']['inputs']['arm64-v8a/librime.a']['sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'provenance digest'):
            module.verify_published_provenance(modified, self.root)

    def test_rehashed_changed_link_archive_is_not_a_matching_core(self):
        core = copy.deepcopy(PUBLIC['core'])
        link = copy.deepcopy(PUBLIC['link'])
        link['inputs']['x86_64/libyaml-cpp.a']['sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'differs from recorded core'):
            module.verify_published_provenance(module.make_public(core, link), self.root)

    def test_rehashed_changed_public_opencc_input_is_rejected(self):
        core = copy.deepcopy(PUBLIC['core'])
        link = copy.deepcopy(PUBLIC['link'])
        label = 'OpenCC/x86_64/libopencc.a'
        for value in (core, link):
            value['inputs'][label]['sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'Published OpenCC input differs'):
            module.verify_published_provenance(module.make_public(core, link), self.root)

    def test_substituted_static_archive_is_rejected(self):
        with self.synthetic_private_core() as paths:
            Path(paths['arm64-v8a/librime.a']).write_bytes(
                b'!<arch>\nsame archive marker but substituted content')
            with self.assertRaisesRegex(ValueError, 'archive/header inputs changed'):
                module.verify_core(self.root)

    def test_stale_installed_headers_are_rejected(self):
        with self.synthetic_private_core() as paths:
            (Path(paths['x86_64/dependency-headers'])/'header.hpp').write_text(
                '// stale installed header content\n')
            with self.assertRaisesRegex(ValueError, 'archive/header inputs changed'):
                module.verify_core(self.root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-report', action='store_true')
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ProvenanceTests)
    names = [test.id().rsplit('.', 1)[-1] for test in suite]
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if args.write_report:
        report = {
            'schemaVersion': 1, 'buildId': MANIFEST['buildId'],
            'publishedManifestSha256': hashlib.sha256((ROOT/'artifacts/manifest.json').read_bytes()).hexdigest(),
            'testSource': 'tests/test_build_provenance.py',
            'testSourceSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'passed': result.testsRun-len(result.failures)-len(result.errors),
            'failed': len(result.failures), 'errors': len(result.errors),
            'tests': names, 'requiresPrivateBuildCache': False,
            'privateInputFixture': 'Synthetic archives and headers; only subsequent compiler/toolchain inspection is stubbed in the two private-input rejection tests.',
            'actualBuildInputsModified': False,
            'androidRuntimeExecutedByTheseChecks': False,
        }
        destination = ROOT/'validation/build-provenance-tests.json'
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    raise SystemExit(not result.wasSuccessful())


if __name__ == '__main__':
    main()
