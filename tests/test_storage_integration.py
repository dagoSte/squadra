"""Seams between verified reuse (lane A) and recorded checkpoints (lane B)."""
import os
import errno
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import registry_checkpoint as checkpoint
import storage_reuse
import runtime


class ProofBindPreflight(unittest.TestCase):
    def test_ephemeral_loopback_probe_closes_its_socket(self):
        with patch.object(runtime.socket, 'socket') as factory:
            self.assertEqual(runtime.local_bind_available(), (True, ''))
        factory.assert_called_once_with(runtime.socket.AF_INET, runtime.socket.SOCK_STREAM)
        probe = factory.return_value.__enter__.return_value
        probe.bind.assert_called_once_with(('127.0.0.1', 0))
        factory.return_value.__exit__.assert_called_once()

    def test_denied_bind_reports_the_environment_limit_and_closes_the_socket(self):
        for code in (errno.EPERM, errno.EACCES):
            with self.subTest(errno=code), patch.object(runtime.socket, 'socket') as factory:
                probe = factory.return_value.__enter__.return_value
                probe.bind.side_effect = OSError(code, 'synthetic denied bind')
                available, message = runtime.local_bind_available()
                self.assertFalse(available)
                self.assertIn('limite del sandbox: esegui il processo della prova fuori dal sandbox', message)
                factory.return_value.__exit__.assert_called_once()

    def test_denied_socket_creation_has_the_same_actionable_error(self):
        with patch.object(runtime.socket, 'socket', side_effect=PermissionError(errno.EPERM, 'synthetic denied socket')):
            available, message = runtime.local_bind_available()
            self.assertFalse(available)
            self.assertIn('limite del sandbox: esegui il processo della prova fuori dal sandbox', message)


class InFlightTemporaries(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.source = self.root / "artifacts"
        self.source.mkdir(mode=0o700)

    def write(self, name, data):
        path = self.source / name
        path.write_bytes(data)
        path.chmod(0o600)
        return path

    def test_unpublished_atomic_temporary_is_not_copied(self):
        self.write("ab" * 32, b"published")
        self.write("tmpk2x9q1", b"half written")
        result = checkpoint.copy_immutable_tree(self.source, self.root / "copy")
        self.assertEqual(sorted(result["files"]), ["ab" * 32])
        self.assertFalse((self.root / "copy" / "tmpk2x9q1").exists())

    def test_temporary_renamed_away_during_listing_does_not_refuse_the_checkpoint(self):
        self.write("cd" * 32, b"published")
        listing = os.listdir

        def with_vanished(fd):
            return listing(fd) + ["tmpgone123"]

        with patch.object(checkpoint.os, "listdir", side_effect=with_vanished):
            result = checkpoint.copy_immutable_tree(self.source, self.root / "copy")
        self.assertEqual(sorted(result["files"]), ["cd" * 32])

    def test_a_published_name_that_vanishes_is_still_refused(self):
        self.write("ef" * 32, b"published")
        listing = os.listdir

        def with_vanished(fd):
            return listing(fd) + ["01" * 32]

        with patch.object(checkpoint.os, "listdir", side_effect=with_vanished), \
                self.assertRaises(FileNotFoundError):
            checkpoint.copy_immutable_tree(self.source, self.root / "copy")


class ReuseRoots(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        if not storage_reuse.capabilities(self.root)["clone"]:
            self.skipTest("Volume without native clone")
        self.source = self.root / "registry" / "artifacts"
        self.source.mkdir(parents=True, mode=0o700)
        name = "12" * 32
        (self.source / name).write_bytes(b"artifact bytes" * 4096)
        (self.source / name).chmod(0o600)
        self.previous = self.root / "cp1"
        self.previous.mkdir()
        checkpoint.copy_immutable_tree(self.source, self.previous / "artifacts")

    def copy_without_source_clone(self, reuse, target):
        with patch.object(checkpoint, "_native_clone", return_value=False):
            return checkpoint.copy_immutable_tree(self.source, target / "artifacts", reuse=reuse)

    def test_previous_checkpoint_root_is_the_reuse_unit(self):
        target = self.root / "cp2"
        target.mkdir()
        result = self.copy_without_source_clone([self.previous], target)
        self.assertEqual(result["reuse"]["clone"], 1)
        self.assertTrue(checkpoint.verify_tree(target / "artifacts", result["files"]))
        self.assertEqual(os.stat(next((target / "artifacts").iterdir())).st_nlink, 1)

    def test_a_subfolder_is_not_a_reuse_root(self):
        target = self.root / "cp3"
        target.mkdir()
        result = self.copy_without_source_clone([self.previous / "artifacts"], target)
        self.assertEqual(result["reuse"]["clone"], 0)


class RealCompactLock(unittest.TestCase):
    """storage compact --apply with the real lane A primitive: one owner of candidates/.lock."""

    def test_policy_compact_apply_finishes_with_the_real_primitive(self):
        import json
        import subprocess
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import test_storage_policy as support

        case = support.StoragePolicy("test_retention_keeps_three_recent_age_keep_and_never_unknown")
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.checkpoint("compact-me")
        script = Path(__file__).resolve().parents[1] / "scripts" / "squadra.py"
        try:
            done = subprocess.run([sys.executable, "-B", str(script), "--verbose", "--root", str(case.store.root), "storage", "compact",
                                   "--apply", "--actor", "C", "--reason", "Integrated compact"],
                                  capture_output=True, timeout=90, start_new_session=True)
        except subprocess.TimeoutExpired:
            self.fail("storage compact --apply waited on its own candidate lock")
        self.assertEqual(done.returncode, 0, done.stderr.decode())
        result, _ = json.JSONDecoder().raw_decode(done.stdout.decode())
        self.assertFalse(result["dry_run"])


if __name__ == "__main__":
    unittest.main()
