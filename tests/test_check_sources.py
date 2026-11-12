"""Synthetic source-lock and publication tests; no native build or device use."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_sources", REPO / "scripts/check_sources.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
# Keep the tests usable before and after the reviewed replacement is promoted.
build_script = REPO / "scripts/build.next.sh"
if not build_script.exists():
    build_script = REPO / "scripts/build.sh"
build = build_script.read_text(encoding="utf-8")
publisher = build.rsplit('python3 - "$ROOT" "$STAGE" <<\'PY\'\n', 1)[1].rsplit("\nPY", 1)[0]
compile(publisher, "build.next.sh:publish", "exec")


def command(directory, *args):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    result = subprocess.run(
        ["git", "-c", "init.templateDir=", "-C", str(directory), *args],
        env=env, capture_output=True, check=True)
    return result.stdout


class SourceChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mozc-check-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        command(self.source, "init", "--quiet")
        command(self.source, "config", "user.name", "Synthetic test")
        command(self.source, "config", "user.email", "test@example.invalid")
        command(self.source, "config", "core.autocrlf", "false")
        (self.source / "src").mkdir()
        self.module = self.source / "src/MODULE.bazel"
        self.original_module = (
            'android_ndk_repository_extension.configure(\n'
            '    path = "$WORKSPACE_ROOT/third_party/ndk/android-ndk-r29",\n'
            ')\n'
        )
        self.module.write_text(self.original_module)
        (self.source / "native.cc").write_text("original\n")
        (self.source / "extra.cc").write_text("unchanged\n")
        (self.source / ".gitignore").write_text("MODULE.bazel.lock\n/cache/\n")
        command(self.source, "add", ".")
        command(self.source, "commit", "--quiet", "-m", "fixture")
        revision = command(self.source, "rev-parse", "HEAD").decode().strip()
        self.module.write_text(self.original_module.replace(
            "configure(\n", "configure(\n    api_level = 26,\n"))
        (self.source / "native.cc").write_text("original\ncompatibility\n")
        patch_bytes = command(self.source, "diff", "--binary", "HEAD")
        command(self.source, "restore", "src/MODULE.bazel", "native.cc")
        (self.root / "patches").mkdir()
        self.patch = self.root / "patches/compat.patch"
        self.patch.write_bytes(patch_bytes)
        self.lock = json.loads((REPO / "sources.lock.json").read_text())
        self.lock["upstream"].update(
            commit=revision, patch="patches/compat.patch",
            patchSha256=hashlib.sha256(patch_bytes).hexdigest())
        self.save_lock()

    def save_lock(self):
        (self.root / "sources.lock.json").write_text(json.dumps(self.lock))

    def check(self, apply=False):
        lock, patch = checker.load_lock(self.root)
        return checker.verify_sources(self.root, self.source, lock, patch, apply)

    def test_pristine_requires_explicit_apply_then_accepts_exact_patch(self):
        with self.assertRaisesRegex(checker.SourceError, "has not been applied"):
            self.check()
        self.assertEqual(self.module.read_text(), self.original_module)
        self.assertTrue(self.check(True)["verified"])
        self.assertTrue(self.check()["verified"])

    def test_rejects_unrelated_tracked_change_without_touching_files(self):
        self.check(True)
        extra = self.source / "extra.cc"
        extra.write_text("not pinned\n")
        with self.assertRaisesRegex(checker.SourceError, "Extra tracked"):
            self.check()
        self.assertEqual(extra.read_text(), "not pinned\n")

    def test_rejects_extra_edit_within_patched_file(self):
        self.check(True)
        native = self.source / "native.cc"
        native.write_text(native.read_text() + "extra change\n")
        with self.assertRaisesRegex(checker.SourceError, "differs from"):
            self.check(True)

    def test_accepts_ignored_bazel_lock_and_cache_and_filemode_noise(self):
        self.check(True)
        (self.source / "src/MODULE.bazel.lock").write_text("{}")
        (self.source / "cache").mkdir()
        (self.source / "cache/item").write_text("cache")
        (self.source / "native.cc").chmod(0o755)
        self.assertTrue(self.check()["verified"])

    def test_rejects_unignored_extra_input(self):
        self.check(True)
        (self.source / "surprise.cc").write_text("new input")
        with self.assertRaisesRegex(checker.SourceError, "Untracked source"):
            self.check()

    def test_rejects_changed_patch_digest(self):
        self.patch.write_bytes(self.patch.read_bytes() + b"\n")
        with self.assertRaisesRegex(checker.SourceError, "SHA-256"):
            self.check(True)

    def test_rejects_wrong_schema_and_revision(self):
        self.lock["schemaVersion"] = 99
        self.save_lock()
        with self.assertRaisesRegex(checker.SourceError, "schemaVersion"):
            self.check(True)
        self.lock["schemaVersion"] = 1
        self.lock["upstream"]["commit"] = "a" * 40
        self.save_lock()
        with self.assertRaisesRegex(checker.SourceError, "revision"):
            self.check(True)

    def test_rejects_lock_api_disagreement(self):
        self.check(True)
        self.lock["toolchain"]["androidApiLevel"] = 27
        self.save_lock()
        with self.assertRaisesRegex(checker.SourceError, "API level"):
            self.check()


class PublicationChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mozc-publish-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        work = self.root / ".work"
        work.mkdir()
        self.stage = work / "artifacts-stage.test"
        self.stage.mkdir()
        self.destination = self.root / "artifacts"
        self.destination.mkdir()
        for path, value in ((self.destination, "old"), (self.stage, "new")):
            (path / "manifest.json").write_text(value)
            (path / "arm64-v8a").mkdir()
            (path / "arm64-v8a/libmozc.so").write_text(value + " library")
            (path / "mozc.data").write_text(value + " dictionary")
        self.old = self.snapshot(self.destination)

    def snapshot(self, directory):
        return {str(path.relative_to(directory)): path.read_bytes()
                for path in directory.rglob("*") if path.is_file()}

    def publish(self, rename=None):
        with mock.patch.object(sys, "argv", ["publish", str(self.root), str(self.stage)]), \
             mock.patch("signal.signal"), contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            if rename:
                with mock.patch("os.rename", side_effect=rename):
                    exec(publisher, {"__name__": "__main__"})
            else:
                exec(publisher, {"__name__": "__main__"})

    def test_success_publishes_complete_set(self):
        expected = self.snapshot(self.stage)
        self.publish()
        self.assertEqual(self.snapshot(self.destination), expected)
        self.assertFalse(self.stage.exists())
        self.assertEqual(list((self.root / ".work").iterdir()), [])

    def test_first_rename_failure_keeps_previous_bytes(self):
        with self.assertRaises(PermissionError):
            self.publish(lambda *_: (_ for _ in ()).throw(PermissionError("injected")))
        self.assertEqual(self.snapshot(self.destination), self.old)
        self.assertTrue(self.stage.exists())

    def test_second_rename_failure_restores_previous_bytes(self):
        real_rename = os.rename
        count = 0
        def fail_second(source, destination):
            nonlocal count
            count += 1
            if count == 2:
                raise PermissionError("injected stage rename failure")
            real_rename(source, destination)
        with self.assertRaises(PermissionError):
            self.publish(fail_second)
        self.assertEqual(self.snapshot(self.destination), self.old)
        self.assertTrue(self.stage.exists())

    def test_interrupt_after_either_rename_restores_previous_bytes(self):
        for interrupt_after in (1, 2):
            with self.subTest(interrupt_after=interrupt_after):
                real_rename = os.rename
                count = 0
                def interrupt(source, destination):
                    nonlocal count
                    count += 1
                    real_rename(source, destination)
                    if count == interrupt_after:
                        raise KeyboardInterrupt("injected after successful rename")
                with self.assertRaises(KeyboardInterrupt):
                    self.publish(interrupt)
                self.assertEqual(self.snapshot(self.destination), self.old)
                self.assertTrue(self.stage.exists())

    def test_first_publication_without_existing_artifacts(self):
        shutil.rmtree(self.destination)
        expected = self.snapshot(self.stage)
        self.publish()
        self.assertEqual(self.snapshot(self.destination), expected)

    def test_rollback_failure_preserves_old_set_in_backup(self):
        real_rename = os.rename
        count = 0
        def fail_publish_and_rollback(source, destination):
            nonlocal count
            count += 1
            if count >= 2:
                raise PermissionError("injected unavailable destination")
            real_rename(source, destination)
        with self.assertRaises(PermissionError):
            self.publish(fail_publish_and_rollback)
        backups = list((self.root / ".work").glob("artifacts-backup.*/artifacts"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(self.snapshot(backups[0]), self.old)


if __name__ == "__main__":
    unittest.main(verbosity=2)
