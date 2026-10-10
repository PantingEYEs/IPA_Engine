"""Validate the public source boundary without private corpora, Git or Android tools."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.sync_public_sources import GENERATED_FILES, SOURCE_FILES, sync_sources
from scripts.verify_public_export import permitted


class PublicSourceTest(unittest.TestCase):
    def populate(self, root):
        for name in SOURCE_FILES:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# fixture: ' + name + '\n', encoding='utf-8')

    def test_only_explicit_sources_are_copied_and_stale_files_are_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.populate(root)
            for name in ('data/raw/raw.json', 'data/ipa.sqlite', 'catalog/sources.json',
                         'rust/ipa_graph/target/private.rs', 'scripts/acquire.py',
                         'model/checkpoint.pt'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('excluded\n')
            public = root / 'remote'
            (public / 'source/data').mkdir(parents=True)
            (public / 'source/data/stale.json').write_text('stale private file\n')
            (public / 'pre-release').mkdir()
            (public / 'pre-release/README.md').write_text('keep published artifacts\n')
            manifest = sync_sources(root)
            emitted = {p.relative_to(public / 'source').as_posix()
                       for p in (public / 'source').rglob('*') if p.is_file()}
            self.assertEqual(emitted, SOURCE_FILES | GENERATED_FILES)
            self.assertTrue(all(permitted('source/' + name) for name in emitted))
            self.assertEqual((public / 'pre-release/README.md').read_text(), 'keep published artifacts\n')
            self.assertEqual(manifest['files'], {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                                                for name in SOURCE_FILES})
            self.assertEqual(json.loads((public / 'source/source-manifest.json').read_text()), manifest)
            for name in ('data/raw.json', '../README.md', 'rust/ipa_graph/src/../Cargo.toml',
                         'rust/ipa_graph/src/unlisted.rs', 'scripts/acquire.py', 'model/model.rs'):
                self.assertFalse(permitted('source/' + name), name)

    def test_missing_source_or_symlink_does_not_replace_existing_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.populate(root)
            sync_sources(root)
            original = (root / 'remote/source/source-manifest.json').read_bytes()
            source = root / 'graph/compiler.py'
            source.unlink()
            with self.assertRaisesRegex(ValueError, 'missing'):
                sync_sources(root)
            source.symlink_to(root / 'graph/lexicon_reader.py')
            with self.assertRaisesRegex(ValueError, 'symbolic links'):
                sync_sources(root)
            self.assertEqual((root / 'remote/source/source-manifest.json').read_bytes(), original)

    def test_unlisted_runtime_input_is_rejected_instead_of_silently_omitted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.populate(root)
            (root / 'rust/ipa_graph/src/unlisted.rs').write_text('// extra build input\n')
            with self.assertRaisesRegex(ValueError, 'outside the public source allowlist'):
                sync_sources(root)
            self.assertFalse((root / 'remote/source').exists())


if __name__ == '__main__':
    unittest.main()
