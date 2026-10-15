import errno
import hashlib
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import registry_checkpoint as checkpoint


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.source = self.root / "source"
        self.source.mkdir(mode=0o700)
        self.destination = self.root / "destination"

    def write(self, name, data=b"content"):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_bytes(data)
        path.chmod(0o600)
        return path

    def test_native_or_fallback_copy_is_independent_after_source_change_and_delete(self):
        original = b"original bytes" * 10000
        path = self.write("artifact", original)
        result = checkpoint.copy_immutable_tree(self.source, self.destination)
        target = self.destination / "artifact"
        self.assertEqual(target.read_bytes(), original)
        self.assertNotEqual((path.stat().st_dev, path.stat().st_ino), (target.stat().st_dev, target.stat().st_ino))
        self.assertEqual(target.stat().st_nlink, 1)
        path.write_bytes(b"source changed")
        self.assertEqual(target.read_bytes(), original)
        path.unlink()
        self.assertEqual(target.read_bytes(), original)
        self.assertTrue(checkpoint.verify_tree(self.destination, result["files"]))
        self.assertEqual(result["cloned"] + result["copied"], 1)
        self.assertEqual(result["logical_bytes"], len(original))
        self.assertEqual(result["allocated_bytes"], target.stat().st_blocks * 512)

    def test_native_failure_falls_back_to_plain_copy(self):
        self.write("artifact", b"fallback")
        with patch.object(checkpoint, "_native_clone", side_effect=OSError(errno.ENOTSUP, "unsupported")):
            result = checkpoint.copy_immutable_tree(self.source, self.destination)
        self.assertEqual(result["copied"], 1)
        self.assertEqual(result["cloned"], 0)
        self.assertEqual((self.destination / "artifact").read_bytes(), b"fallback")
        self.assertTrue(checkpoint.verify_tree(self.destination, result["files"]))

    def test_failed_clone_partial_file_is_overwritten_safely(self):
        self.write("artifact", b"complete bytes")

        def partial(source_fd, destination_dir_fd, name):
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=destination_dir_fd)
            try:
                os.write(fd, b"partial clone longer than the input")
            finally:
                os.close(fd)
            raise OSError(errno.EIO, "interrupted clone")

        with patch.object(checkpoint, "_native_clone", side_effect=partial):
            result = checkpoint.copy_immutable_tree(self.source, self.destination)
        self.assertEqual((self.destination / "artifact").read_bytes(), b"complete bytes")
        self.assertTrue(checkpoint.verify_tree(self.destination, result["files"]))

    def test_run_metadata_and_results_retained_but_locks_are_not(self):
        self.write("a.json", b'{"phase":"spawned"}')
        self.write("a.result.json", b'{"native_rc":0}')
        self.write("a.lock", b"lock")
        result = checkpoint.copy_immutable_tree(self.source, self.destination, skip_lease_locks=True)
        self.assertEqual(set(result["files"]), {"a.json", "a.result.json"})
        self.assertEqual(set(path.name for path in self.destination.iterdir()), {"a.json", "a.result.json"})
        self.assertTrue(checkpoint.verify_tree(self.destination, result["files"]))

    def test_artifact_named_lock_is_copied_without_lease_option(self):
        self.write("ordinary.lock")
        result = checkpoint.copy_immutable_tree(self.source, self.destination)
        self.assertIn("ordinary.lock", result["files"])

    def test_source_file_and_directory_symlinks_refused(self):
        self.write("real", b"do not follow")
        (self.source / "link").symlink_to("real")
        with self.assertRaises(checkpoint.CheckpointError):
            checkpoint.copy_immutable_tree(self.source, self.destination)
        link = self.root / "source-link"
        link.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(checkpoint.CheckpointError):
            checkpoint.copy_immutable_tree(link, self.root / "second-destination")

    def test_source_hardlink_is_refused(self):
        path = self.write("one")
        os.link(path, self.source / "two")
        with self.assertRaises(checkpoint.CheckpointError):
            checkpoint.copy_immutable_tree(self.source, self.destination)

    def test_clone_cannot_replace_independent_copy_with_a_hardlink(self):
        path = self.write("artifact")

        def unsafe_link(source_fd, destination_dir_fd, name):
            os.link(path, name, dst_dir_fd=destination_dir_fd)
            return True

        with patch.object(checkpoint, "_native_clone", side_effect=unsafe_link), \
                self.assertRaises(checkpoint.CheckpointError):
            checkpoint.copy_immutable_tree(self.source, self.destination)

    def test_concurrent_source_change_is_refused(self):
        path = self.write("artifact", b"initial")
        original = checkpoint._copy_bytes

        def changed(source_fd, destination_fd):
            original(source_fd, destination_fd)
            path.write_bytes(b"changed")

        with patch.object(checkpoint, "_native_clone", return_value=False), \
                patch.object(checkpoint, "_copy_bytes", side_effect=changed), \
                self.assertRaises(checkpoint.CheckpointError):
            checkpoint.copy_immutable_tree(self.source, self.destination)

    def test_destination_checksum_corruption_is_refused(self):
        self.write("artifact", b"original")
        result = checkpoint.copy_immutable_tree(self.source, self.destination)
        self.assertTrue(checkpoint.verify_tree(self.destination, result["files"]))
        (self.destination / "artifact").write_bytes(b"corrupt!")
        with self.assertRaises(checkpoint.CheckpointError):
            checkpoint.verify_tree(self.destination, result["files"])

    def test_destination_change_after_hash_is_not_accepted(self):
        self.write("artifact", b"original")
        original, calls = checkpoint._hash_fd, []

        def changed(fd):
            calls.append(fd)
            if len(calls) == 3:
                (self.destination / "artifact").write_bytes(b"corrupt!")
            return original(fd)

        with patch.object(checkpoint, "_hash_fd", side_effect=changed), \
                self.assertRaises(checkpoint.CheckpointError):
            checkpoint.copy_immutable_tree(self.source, self.destination)

    def test_incomplete_extra_and_unsafe_manifest_paths_are_refused(self):
        self.write("artifact")
        result = checkpoint.copy_immutable_tree(self.source, self.destination)
        with self.assertRaises(checkpoint.CheckpointError):
            checkpoint.verify_tree(self.destination, {})
        missing = {**result["files"], "missing": result["files"]["artifact"]}
        with self.assertRaises(checkpoint.CheckpointError):
            checkpoint.verify_tree(self.destination, missing)
        for bad in ["../outside", "/absolute", "a/../outside", "a//b", "./artifact"]:
            with self.subTest(path=bad), self.assertRaises(checkpoint.CheckpointError):
                checkpoint.verify_tree(self.destination, {bad: result["files"]["artifact"]})

    def test_nested_directories_and_files_are_private(self):
        self.write("nested/artifact", b"bytes")
        result = checkpoint.copy_immutable_tree(self.source, self.destination)
        self.assertEqual(result["files"]["nested/artifact"]["sha256"], hashlib.sha256(b"bytes").hexdigest())
        self.assertEqual(result["file_count"], 1)
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.destination / "nested").stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.destination / "nested/artifact").stat().st_mode), 0o600)
        self.assertTrue(checkpoint.verify_tree(self.destination, result["files"]))

    def test_existing_destination_is_never_overwritten(self):
        self.write("artifact")
        self.destination.mkdir()
        marker = self.destination / "keep"
        marker.write_bytes(b"untouched")
        with self.assertRaises(checkpoint.CheckpointError):
            checkpoint.copy_immutable_tree(self.source, self.destination)
        self.assertEqual(marker.read_bytes(), b"untouched")


if __name__ == "__main__":
    unittest.main()
