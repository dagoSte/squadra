"""Check complete, independent checkpoint files and unchanged recovery packages."""
import json
from pathlib import Path
import shutil
import sqlite3
import unittest
from unittest.mock import patch

import test_squadra as support
import registry_checkpoint
import recovery
from recovery import restore_check, save, verify_package
from registry import Error, Store, atomic, canonical, digest, run_lease_paths


class CheckpointIntegration(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task

    def leases(self):
        folder, lock, metadata, result = run_lease_paths(self.root, "synthetic-run")
        folder.mkdir(mode=0o700)
        atomic(lock, b"lock is not a receipt")
        atomic(metadata, b'{"run":"synthetic-run","phase":"disposed","pgid":null}\n')
        atomic(result, b'{"run":"synthetic-run","status":"passed","exit_code":0}\n')
        return lock, metadata, result

    def target(self, name="checkpoint"):
        return self.base / name

    def test_full_source_and_copied_database_are_audited_before_complete_exports(self):
        target = self.target()
        original = Store.audit
        original_project = Store.project
        audited = []

        def track(store):
            audited.append(store.root)
            return original(store)

        def export(store, *args, **kwargs):
            self.assertIn(store.root, audited, "The copied database must be audited before export")
            return original_project(store, *args, **kwargs)

        with patch.object(Store, "audit", track), patch.object(Store, "project", export):
            self.assertEqual(self.store.checkpoint(target), str(target))
        self.assertEqual(audited[0], self.root)
        self.assertIn(target, audited)
        expected = self.store.events()
        self.assertEqual([json.loads(line) for line in (target / "events.jsonl").read_text().splitlines()], expected)
        expected_state = self.store.state()
        expected_state.pop("root")
        self.assertEqual(json.loads((target / "state.json").read_text()), expected_state)
        self.assertTrue((target / "stato.md").is_file())
        self.assertTrue((target / "squadra-posta.md").is_file())
        checks = json.loads((target / "CHECKSUMS.json").read_text())
        actual = {str(path.relative_to(target)): digest(path.read_bytes()) for path in target.rglob("*")
                  if path.is_file() and path.name not in {"CHECKSUMS.json", "registry.sqlite3-wal", "registry.sqlite3-shm"}}
        self.assertEqual(checks, actual)

    def test_old_row_tamper_refused_even_when_cache_was_warm(self):
        self.store.view()
        self.store.db.execute("DROP TRIGGER no_update")
        self.store.db.execute("UPDATE events SET actor='tampered' WHERE seq=1")
        with self.assertRaisesRegex(Error, "integrity"):
            self.store.checkpoint(self.target())
        self.assertFalse(self.target().exists())

    def test_copied_database_tamper_after_source_audit_is_refused(self):
        original = Store.audit

        def race(store):
            result = original(store)
            if store is self.store:
                store.db.execute("DROP TRIGGER no_update")
                store.db.execute("UPDATE events SET actor='tampered' WHERE seq=1")
            return result

        with patch.object(Store, "audit", race):
            with self.assertRaisesRegex(Error, "integrity"):
                self.store.checkpoint(self.target())
        self.assertFalse((self.target() / "CHECKSUMS.json").exists())

    def test_artifacts_and_run_metadata_are_private_independent_files_without_locks(self):
        lock, metadata, result = self.leases()
        target = self.target()
        original_rows = self.store.events()
        copied = [path for path in (self.root / "artifacts").iterdir()] + [metadata, result]
        original_bytes = {path.relative_to(self.root): path.read_bytes() for path in copied}
        self.store.checkpoint(target)
        self.assertFalse((target / "run-leases" / lock.name).exists())
        self.assertTrue(lock.is_file())
        for relative, data in original_bytes.items():
            source, destination = self.root / relative, target / relative
            self.assertTrue(destination.is_file(), str(relative))
            self.assertEqual(destination.read_bytes(), data)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            self.assertEqual(destination.stat().st_nlink, 1)
            self.assertNotEqual((source.stat().st_dev, source.stat().st_ino),
                                (destination.stat().st_dev, destination.stat().st_ino))
            source.write_bytes(b"source changed after checkpoint")
            self.assertEqual(destination.read_bytes(), data)
            source.unlink()
            self.assertEqual(destination.read_bytes(), data)
        self.store.append("diagnostic", "system", {"after_checkpoint": True})
        restored = Store(target)
        try:
            self.assertEqual(restored.audit()["events"], original_rows)
        finally:
            restored.close()

    def test_failed_native_clone_falls_back_for_whole_checkpoint(self):
        self.leases()
        with patch.object(registry_checkpoint, "_native_clone", side_effect=OSError("unsupported")) as clone:
            self.store.checkpoint(self.target())
        self.assertGreaterEqual(clone.call_count, 3)
        restored = Store(self.target())
        try:
            self.assertEqual(restored.audit()["events"], self.store.events())
        finally:
            restored.close()

    def test_copy_corruption_is_refused_before_final_checksum_is_written(self):
        _, metadata, _ = self.leases()
        original = registry_checkpoint.copy_immutable_tree

        def corrupt(source, destination, **kwargs):
            result = original(source, destination, **kwargs)
            if Path(source).name == "run-leases":
                (Path(destination) / metadata.name).write_bytes(b"tampered receipt")
            return result

        with patch.object(registry_checkpoint, "copy_immutable_tree", corrupt):
            with self.assertRaisesRegex(Error, "verification refused"):
                self.store.checkpoint(self.target())
        self.assertFalse((self.target() / "CHECKSUMS.json").exists())

    def test_unsafe_run_metadata_is_refused_and_failure_evidence_is_retained(self):
        _, metadata, _ = self.leases()
        metadata.unlink()
        metadata.symlink_to(self.scope)
        with self.assertRaisesRegex(Error, "copy refused"):
            self.store.checkpoint(self.target())
        self.assertTrue((self.target() / "registry.sqlite3").is_file())
        self.assertFalse((self.target() / "CHECKSUMS.json").exists())

    def test_new_save_restore_package_keeps_leases_and_remains_self_contained(self):
        lock, metadata, result = self.leases()
        original = {p.name: p.read_bytes() for p in (metadata, result)}
        package = self.target("saved")
        audited = []
        original_audit = Store.audit
        original_checkpoint = Store.checkpoint
        checkpoint_complete = False
        audited_after_checkpoint = []

        def track(store):
            audited.append(store.root)
            if checkpoint_complete:
                audited_after_checkpoint.append(store.root)
            return original_audit(store)

        def checkpoint(store, destination):
            nonlocal checkpoint_complete
            result = original_checkpoint(store, destination)
            checkpoint_complete = True
            return result

        with patch.object(Store, "audit", track), patch.object(Store, "checkpoint", checkpoint):
            save(self.store, package, temporary=True)
        self.assertIn(package / "registry", audited_after_checkpoint)
        self.assertEqual(verify_package(package)["schema"], 1)
        for path in (self.root / "artifacts").iterdir():
            path.unlink()
        metadata.unlink()
        result.unlink()
        audited.clear()
        original_git = recovery.git

        def checked_git(*args, **kwargs):
            if "clone" in args:
                self.assertIn(self.target("restored") / "registry", audited)
            return original_git(*args, **kwargs)

        with patch.object(Store, "audit", track), patch.object(recovery, "git", checked_git):
            report = restore_check(package, self.target("restored"))
        self.assertIn(self.target("restored") / "registry", audited)
        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["restored_paused"])
        copied = self.target("restored") / "registry" / "run-leases"
        self.assertFalse((copied / lock.name).exists())
        self.assertEqual({p.name: p.read_bytes() for p in copied.iterdir()}, original)

    def test_old_schema_one_package_without_leases_still_restores(self):
        # Historical packages contain a complete SQLite copy and artifacts only.
        def legacy_checkpoint(store, destination):
            destination = Path(destination)
            destination.mkdir(mode=0o700)
            target = sqlite3.connect(destination / "registry.sqlite3")
            try:
                store.db.backup(target)
            finally:
                target.close()
            shutil.copytree(store.root / "artifacts", destination / "artifacts")
            check = Store(destination)
            try:
                check.verify_artifacts()
                check.project()
            finally:
                check.close()
            hashes = {str(p.relative_to(destination)): digest(p.read_bytes()) for p in destination.rglob("*")
                      if p.is_file() and p.name not in {"registry.sqlite3-wal", "registry.sqlite3-shm"}}
            atomic(destination / "CHECKSUMS.json", (canonical(hashes) + "\n").encode())
            return str(destination)

        self.leases()
        package = self.target("legacy")
        with patch.object(Store, "checkpoint", legacy_checkpoint):
            save(self.store, package, temporary=True)
        self.assertFalse((package / "registry" / "run-leases").exists())
        self.assertEqual(verify_package(package)["schema"], 1)
        report = restore_check(package, self.target("restored-legacy"))
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["registry_integrity"], "passed")

    def test_corrupt_run_receipt_in_package_is_refused_before_sqlite_or_git(self):
        _, metadata, _ = self.leases()
        package = self.target("saved")
        save(self.store, package, temporary=True)
        (package / "registry" / "run-leases" / metadata.name).write_bytes(b"tampered")
        with patch("recovery.Store", side_effect=AssertionError("SQLite opened")), \
                patch("recovery.git", side_effect=AssertionError("Git opened")):
            with self.assertRaisesRegex(Error, "tampered"):
                restore_check(package, self.target("refused"))
        self.assertFalse(self.target("refused").exists())


if __name__ == "__main__":
    unittest.main()
