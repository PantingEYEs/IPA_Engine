"""Offline source-integrity tests against disposable local Git repositories."""
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prepare.py"
SPEC = importlib.util.spec_from_file_location("ortx_prepare", SCRIPT)
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


class PrepareTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.git("init", "--initial-branch=fixture")
        self.git("config", "user.name", "Artifact Test")
        self.git("config", "user.email", "artifact-test@example.invalid")
        self.git("config", "core.autocrlf", "false")
        (self.source / "operators").mkdir()
        (self.source / "operators" / "source.cc").write_text("int value = 1;\n", encoding="utf-8")
        (self.source / ".gitignore").write_text("build_*\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "fixture")
        self.commit = self.git("rev-parse", "HEAD").decode().strip()
        self.entry = {"repository": str(self.source), "tag": "fixture", "commit": self.commit}

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.check_output(["git", "-c", "credential.helper=", *args], cwd=self.source,
                                       stderr=subprocess.STDOUT)

    def checkout(self, patches=()):
        with redirect_stdout(io.StringIO()):
            prepare.checkout("fixture", self.entry, self.source, list(patches))

    def approved_patch(self):
        source = self.source / "operators" / "source.cc"
        source.write_text("int value = 2;\n", encoding="utf-8")
        data = self.git("diff", "--binary", "HEAD")
        self.git("restore", "--", "operators/source.cc")
        target = self.root / "approved.patch"
        target.write_bytes(data)
        return target

    def test_clean_pinned_source_and_approved_patch_are_idempotent(self):
        self.checkout()
        approved = self.approved_patch()
        self.checkout([approved])
        self.checkout([approved])
        self.assertEqual((self.source / "operators" / "source.cc").read_text(), "int value = 2;\n")
        self.assertEqual(self.git("rev-parse", "HEAD").decode().strip(), self.commit)

    def test_unapproved_tracked_changes_fail_without_discarding_them(self):
        source = self.source / "operators" / "source.cc"
        source.write_text("int personal_change = 9;\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.checkout()
        self.assertEqual(source.read_text(), "int personal_change = 9;\n")

    def test_wrong_commit_is_rejected_before_source_mutation(self):
        self.entry["commit"] = "0" * 40
        with self.assertRaisesRegex(SystemExit, "wrong commit"):
            self.checkout()
        self.assertEqual((self.source / "operators" / "source.cc").read_text(), "int value = 1;\n")

    def test_regular_and_ignored_injected_source_files_are_rejected(self):
        for name in ("injected.cc", "build_injected.cc"):
            path = self.source / "operators" / name
            path.write_text("int injected = 7;\n", encoding="utf-8")
            with self.subTest(name=name):
                with self.assertRaises(SystemExit):
                    self.checkout()
                self.assertEqual(path.read_text(), "int injected = 7;\n")
            path.unlink()

    def test_index_flags_cannot_hide_modified_compiled_source(self):
        relative = "operators/source.cc"
        source = self.source / relative
        for mode, undo in (("--skip-worktree", "--no-skip-worktree"),
                           ("--assume-unchanged", "--no-assume-unchanged")):
            self.git("update-index", mode, relative)
            source.write_text("int hidden_change = 11;\n", encoding="utf-8")
            with self.subTest(mode=mode):
                with self.assertRaises(SystemExit):
                    self.checkout()
                self.assertIn("hidden_change", source.read_text())
            self.git("update-index", undo, relative)
            self.git("restore", "--", relative)

    def test_lock_patch_digest_checked_before_clone_or_apply(self):
        local_patch = self.root / "change.patch"
        local_patch.write_bytes(b"not a patch")
        lock = {"upstream": {**self.entry, "patch": "change.patch", "patchSha256": "0" * 64},
                "dependencies": {}, "ortHeaders": {}}
        (self.root / "sources.lock.json").write_text(json.dumps(lock), encoding="utf-8")
        with patch.object(prepare, "ROOT", self.root):
            with self.assertRaisesRegex(SystemExit, "patch hash mismatch"):
                prepare.main()
        self.assertFalse((self.root / ".work").exists())


if __name__ == "__main__":
    unittest.main()
