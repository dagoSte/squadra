"""Candidate maintenance releases readers and checkpoints one locked journal boundary."""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_workflow_improvements as support
import frozen_candidate as frozen
import registry_checkpoint
from registry import Store


class CandidateCheckpoint(unittest.TestCase):
    cli = support.Improvements.cli
    state = support.Improvements.state
    task = support.Improvements.task
    start = support.Improvements.start
    deliver = support.Improvements.deliver
    review = support.Improvements.review

    def setUp(self):
        support.Improvements.setUp(self)
        def writable():
            for path in [self.base, *self.base.rglob('*')]:
                if not path.is_symlink():
                    path.chmod(path.stat().st_mode | 0o700)
        self.addCleanup(writable)

    def write_and_checkpoint(self, name):
        writer = Store(self.root)
        try:
            writer.append('decision', 'C', {'id': name, 'reason': 'Synthetic concurrent checkpoint guard'})
            return tuple(writer.db.execute('PRAGMA wal_checkpoint(PASSIVE)').fetchone())
        finally:
            writer.close()

    def cache_lock_status(self):
        probe = subprocess.run([sys.executable, '-B', '-c',
            'import fcntl,sys; f=open(sys.argv[1],"a+");\n'
            'try: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\n'
            'except BlockingIOError: print("blocked")\n'
            'else: print("acquired")', str(self.root / 'candidates/.lock')],
            capture_output=True, check=True, timeout=15)
        return probe.stdout.strip()

    def test_limits_releases_the_read_snapshot(self):
        store = Store(self.root)
        try:
            store.view()
            self.assertEqual(frozen.limits(store), (frozen.MAX_COPY_BYTES, frozen.MAX_TOTAL_BYTES))
            _, frames, copied = self.write_and_checkpoint('after-limits')
            self.assertEqual(frames, copied)
        finally:
            store.close()

    def test_inventory_releases_views_before_measuring_the_copy(self):
        self.task(); self.start(); self.deliver()
        store = Store(self.root); original = frozen.current; observations = []
        def measured(*args, **kwargs):
            observations.append(self.write_and_checkpoint('during-inventory'))
            return original(*args, **kwargs)
        try:
            store.view()
            with patch.object(frozen, 'current', side_effect=measured):
                rows = frozen.inventory(store)
            self.assertEqual(rows[0]['status'], 'available')
            self.assertTrue(observations)
            for _, frames, copied in observations:
                self.assertEqual(frames, copied)
        finally:
            store.close()

    def test_checkpoint_holds_storage_lock_but_no_source_read_snapshot_while_copying(self):
        self.task(); self.start(); self.deliver()
        store = Store(self.root); original = frozen.shutil.copytree; observed = []
        def copied(source, destination, *args, **kwargs):
            if not observed:
                observed.append((self.cache_lock_status(), self.write_and_checkpoint('during-copy')))
            return original(source, destination, *args, **kwargs)
        try:
            store.view()
            with patch.object(frozen.shutil, 'copytree', side_effect=copied):
                store.checkpoint(self.base / 'checkpoint')
            self.assertTrue(observed)
            for lock, (_, frames, copied_frames) in observed:
                self.assertEqual(lock, b'blocked')
                self.assertEqual(frames, copied_frames)
        finally:
            store.close()

    def test_checkpoint_acquires_storage_lock_before_database_backup(self):
        store = Store(self.root); database = store.db; observed = []; owner = self
        with frozen.cache_lock(store):
            pass
        class BackupProbe:
            def __getattr__(self, name):
                return getattr(database, name)
            def backup(self, target):
                observed.append(owner.cache_lock_status())
                return database.backup(target)
        store.db = BackupProbe()
        try:
            store.checkpoint(self.base / 'checkpoint', audit_source=False)
            self.assertEqual(observed, [b'blocked'])
        finally:
            store.db = database
            store.close()

    def test_candidate_metadata_comes_from_the_database_backup(self):
        self.task(); self.start(); self.deliver(); self.review()
        self.cli('close', '--actor', 'C', '--task', 'T')
        sha = self.state()['tasks']['T']['delivery']['frozen']['sha256']
        source = self.root / 'candidates' / sha
        original = registry_checkpoint.copy_immutable_tree; changed = []
        def copied(path, destination, **kwargs):
            value = original(path, destination, **kwargs)
            if not changed:
                changed.append(True)
                writer = Store(self.root)
                try:
                    writer.append('output', 'C', {'task': 'T', 'text': 'Late retirement',
                        'next': 'Synthetic backup-boundary probe', 'artifacts': [],
                        'candidate_action': {'action': 'retire', 'sha256': sha, 'reason': 'After DB backup'}})
                finally:
                    writer.close()
                # Simulate external cache loss after the backup, independent of the journal boundary.
                source.chmod(0o755)
                source.rename(self.base / 'late-cache-loss')
            return value
        store = Store(self.root); report = {}; destination = self.base / 'checkpoint'
        try:
            with patch.object(registry_checkpoint, 'copy_immutable_tree', side_effect=copied):
                store.checkpoint(destination, report=report)
            self.assertTrue(changed)
            self.assertEqual(report['candidates'][0]['status'], 'missing')
            backup = Store(destination, read_only=True)
            try:
                self.assertFalse(any(e['data'].get('candidate_action', {}).get('action') == 'retire'
                                     for e in backup.events()))
            finally:
                backup.close()
        finally:
            store.close()
