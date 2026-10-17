"""Opt-in resource waiting uses real processes and the existing reservation events."""
from contextlib import closing
import os
import hashlib
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import test_squadra as support
from registry import Store

CLI = Path(__file__).resolve().parents[1] / 'scripts/squadra.py'


class GateWait(unittest.TestCase):
    git = support.Workflow.git
    task = support.Workflow.task

    def setUp(self):
        support.Workflow.setUp(self)

    def call(self, *args):
        return subprocess.run([sys.executable, '-B', str(CLI), '--root', str(self.root), *args],
                              env=self.env, capture_output=True, text=True, timeout=20)

    def resource(self, actor='A', wait=None, action='acquire', name='gate'):
        args = ['resource', action, '--actor', actor, '--name', name, '--reason', 'synthetic queue']
        return args + ([] if wait is None else ['--wait', str(wait)])

    def test_wait_zero_acquires_free_resource_through_the_cli(self):
        result = self.call(*self.resource(wait=0))
        self.assertEqual(result.returncode, 0, result.stderr)
        resources = self.store.state()['resources']
        self.assertEqual(resources['gate']['owner'], 'A')
        self.assertEqual([e['kind'] for e in self.store.events()].count('resource.acquire'), 1)

    def test_large_finite_wait_is_capped_before_milliseconds(self):
        result = self.call(*self.resource(wait=1e308))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.store.state()['resources']['gate']['owner'], 'A')

    def until(self, condition, label):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(0.02)
        self.fail('timed out: ' + label)

    def waiter(self, actor, seconds=10, name='gate'):
        log = self.base / (actor + '-' + name + '.stderr')
        handle = log.open('w')
        self.addCleanup(handle.close)
        proc = subprocess.Popen([sys.executable, '-B', str(CLI), '--root', str(self.root),
                                 *self.resource(actor, seconds, name=name)], env=self.env,
                                stdout=subprocess.PIPE, stderr=handle, text=True)
        def cleanup():
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=15)
        self.addCleanup(cleanup)
        self.until(lambda: 'posizione in coda' in log.read_text() or proc.poll() is not None, 'queue enrolment')
        self.assertIsNone(proc.poll(), log.read_text())
        return proc, log

    def hold(self, actor='C', name='gate'):
        self.store.append('resource.acquire', actor, {'name': name, 'reason': 'held'})
        self.store.release_views()

    def release(self, actor='C', name='gate'):
        self.store.append('resource.release', actor, {'name': name, 'reason': 'actually released'})
        self.store.release_views()

    def test_fifo_and_one_event_per_success(self):
        self.hold()
        first, first_log = self.waiter('A')
        second, second_log = self.waiter('R')
        self.assertIn('posizione in coda 1', first_log.read_text())
        self.assertIn('posizione in coda 2', second_log.read_text())
        before = len(self.store.events())
        time.sleep(0.45)
        self.assertEqual(len(self.store.events()), before, 'waiting appends no event or diagnostic')
        self.release()
        first.communicate(timeout=15)
        self.assertEqual(first.returncode, 0, first_log.read_text())
        self.assertIsNone(second.poll(), 'the next waiter cannot acquire before actual release')
        self.assertEqual(self.store.state()['resources']['gate']['owner'], 'A')
        self.release('A')
        second.communicate(timeout=15)
        self.assertEqual(second.returncode, 0, second_log.read_text())
        acquired = [e['actor'] for e in self.store.events() if e['kind'] == 'resource.acquire']
        self.assertEqual(acquired, ['C', 'A', 'R'])
        self.assertFalse(any(e['kind'] == 'diagnostic' for e in self.store.events()))

    def test_dead_head_and_interrupted_nonhead_do_not_block_the_line(self):
        for signal_to_send, kill_head in ((signal.SIGKILL, True), (signal.SIGTERM, False)):
            with self.subTest(signal=signal_to_send):
                self.hold()
                first, first_log = self.waiter('A')
                second, second_log = self.waiter('R')
                dead, survivor, log = (first, second, second_log) if kill_head else (second, first, first_log)
                dead.send_signal(signal_to_send); dead.communicate(timeout=15)
                self.assertEqual(dead.returncode, -signal_to_send)
                self.release()
                survivor.communicate(timeout=15)
                self.assertEqual(survivor.returncode, 0, log.read_text())
                self.release('R' if kill_head else 'A')

    def test_zero_wait_never_passes_live_waiters(self):
        self.hold()
        first, log = self.waiter('A')
        # Stop the head outside its critical section, so even the order-bypass mutant can enter.
        from resource_wait import queue_folder, queue_file, queue_lock
        with queue_folder(self.root, 'gate') as folder:
            mutex = queue_file(folder, 'mutex')
            try:
                with queue_lock(mutex, time.monotonic() + 5):
                    first.send_signal(signal.SIGSTOP)
                    self.assertTrue(os.WIFSTOPPED(os.waitpid(first.pid, os.WUNTRACED)[1]))
            finally:
                os.close(mutex)
        self.release()
        # The existing ticket is still authoritative even while its owner is between polls.
        try:
            result = self.call(*self.resource('R', wait=0))
        finally:
            first.send_signal(signal.SIGCONT)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('Resource already reserved', result.stderr)
        first.communicate(timeout=15)
        self.assertEqual(first.returncode, 0, log.read_text())
        self.assertEqual(self.store.state()['resources']['gate']['owner'], 'A')

    def test_timeout_is_one_refusal_without_stealing_a_reservation(self):
        self.hold()
        start = time.monotonic()
        result = self.call(*self.resource(wait=0.5))
        elapsed = time.monotonic() - start
        self.assertEqual(result.returncode, 1)
        self.assertIn('Resource already reserved; expiry is not release', result.stderr)
        self.assertLess(elapsed, 4)
        self.assertGreaterEqual(elapsed, 0.5)
        self.assertEqual(self.store.state()['resources']['gate']['owner'], 'C')
        self.assertEqual(sum(e['kind'] == 'diagnostic' for e in self.store.events()), 1)

    def test_holder_and_its_alias_refuse_immediately(self):
        self.hold('A')
        for actor in ('A', 'A-alias'):
            start = time.monotonic()
            result = self.call(*self.resource(actor, wait=20))
            self.assertEqual(result.returncode, 1)
            self.assertIn('holder: A', result.stderr)
            self.assertLess(time.monotonic() - start, 4)
        self.assertFalse((self.root / 'resource-waits').exists())

    def test_waiting_releases_sqlite_snapshots_and_writes_no_projections(self):
        self.hold()
        proc, log = self.waiter('A')
        files = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in self.root.rglob('*')
                 if p.is_file() and p.suffix in {'.md', '.json', '.jsonl'}}
        self.store.append('diagnostic', 'system', {'control': 'concurrent append'})
        self.store.release_views()
        time.sleep(0.5)
        with closing(sqlite3.connect(self.root / 'registry.sqlite3', timeout=2)) as db:
            checkpoint = db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        self.assertEqual(checkpoint[0], 0, checkpoint)
        self.assertEqual(files, {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in files})
        self.release()
        proc.communicate(timeout=15)
        self.assertEqual(proc.returncode, 0, log.read_text())

    def test_resources_are_independent_and_immediate_calls_are_unchanged(self):
        self.hold()
        proc, log = self.waiter('A')
        immediate = self.call(*self.resource('R'))
        self.assertEqual(immediate.returncode, 1)
        self.assertIn('Resource already reserved; expiry is not release', immediate.stderr)
        other = self.call(*self.resource('R', 0, name='different/resource'))
        self.assertEqual(other.returncode, 0, other.stderr)
        self.assertIsNone(proc.poll())
        self.release()
        proc.communicate(timeout=15)
        self.assertEqual(proc.returncode, 0, log.read_text())

    def test_argument_platform_and_private_queue_refusals(self):
        for args in (self.resource(wait=-1), self.resource(wait='nan'), self.resource(wait='inf'),
                     self.resource(wait=1, action='release')):
            result = self.call(*args)
            self.assertEqual(result.returncode, 1)
            self.assertIn('--wait', result.stderr)
        from resource_wait import acquire
        with patch('resource_wait.os.name', 'nt'):
            with self.assertRaisesRegex(Exception, 'Windows'):
                acquire(self.store, 'A', 'gate', 'unsupported', 0)
        folder = self.root / 'resource-waits'
        folder.mkdir(mode=0o777); folder.chmod(0o777)
        refused = self.call(*self.resource(wait=0))
        self.assertEqual(refused.returncode, 1)
        self.assertIn('one OS user', refused.stderr)

    def test_queue_mutex_contention_is_in_the_wait_budget(self):
        import fcntl
        from resource_wait import queue_folder, queue_file
        with queue_folder(self.root, 'gate') as folder:
            fd = queue_file(folder, 'mutex')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                start = time.monotonic()
                result = self.call(*self.resource(wait=0.25))
                self.assertEqual(result.returncode, 1)
                self.assertIn('Resource queue mutex busy; wait deadline expired', result.stderr)
                self.assertLess(time.monotonic() - start, 4)
                self.assertNotIn('gate', self.store.state()['resources'])
                self.assertFalse(any(e['kind'] == 'resource.acquire' for e in self.store.events()))
            finally:
                os.close(fd)

    def test_foreign_cache_cli_refusal_writes_nothing_and_prepared_copy_works(self):
        copied = self.base / 'moved'; copied.mkdir()
        with closing(sqlite3.connect(copied / 'registry.sqlite3')) as db:
            self.store.db.backup(db)
        original = self.root
        self.root = copied
        try:
            with closing(sqlite3.connect(copied / 'registry.sqlite3')) as db:
                before = db.execute('SELECT count(*) FROM events').fetchone()[0]
            result = self.call(*self.resource(wait=0))
            self.assertEqual(result.returncode, 1)
            self.assertIn('cache unavailable', result.stderr)
            with closing(sqlite3.connect(copied / 'registry.sqlite3')) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], before)
            # A normal write prepares the foreign anchor outside polling.
            moved = Store(copied)
            try:
                moved.append('resource.acquire', 'C', {'name': 'prepare', 'reason': 'ordinary write'})
            finally:
                moved.close()
            result = self.call(*self.resource(wait=0))
            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            self.root = original

    def test_symlinks_and_linked_queue_files_are_refused_without_following_them(self):
        folder = self.root / 'resource-waits'
        outside = self.base / 'outside'; outside.mkdir()
        folder.symlink_to(outside, target_is_directory=True)
        result = self.call(*self.resource(wait=0))
        self.assertEqual(result.returncode, 1)
        self.assertFalse(list(outside.iterdir()))
        folder.unlink(); folder.mkdir(mode=0o700)
        resource = folder / hashlib.sha256(b'gate').hexdigest(); resource.mkdir(mode=0o700)
        sentinel = self.base / 'sentinel'; sentinel.write_text('unchanged'); sentinel.chmod(0o600)
        os.link(sentinel, resource / 'mutex')
        result = self.call(*self.resource(wait=0))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(sentinel.read_text(), 'unchanged')

    def test_polls_demand_only_cache_leaves_and_refuse_foreign_cache_without_replay(self):
        from resource_wait import resource_state, CacheUnavailable
        self.store.batch([('diagnostic', 'system', {'row': i}, None) for i in range(3000)])
        self.store.release_views()
        with patch.object(Store, 'state', side_effect=AssertionError('poll must not replay')):
            start = time.monotonic()
            for _ in range(10):
                self.assertIsNone(resource_state(self.root, 'gate', 'A')['holder'])
            self.assertLess((time.monotonic() - start) / 10, 0.2)
            copied = self.base / 'copy'; copied.mkdir()
            with closing(sqlite3.connect(copied / 'registry.sqlite3')) as db:
                self.store.db.backup(db)
            with self.assertRaisesRegex(CacheUnavailable, 'cache unavailable'):
                resource_state(copied, 'gate', 'A')

    def test_non_ticket_entries_are_ignored_without_following_or_removing_them(self):
        from resource_wait import queue_folder
        with queue_folder(self.root, 'gate'):
            pass
        folder = self.root / 'resource-waits' / hashlib.sha256(b'gate').hexdigest()
        stray = folder / '.DS_Store'
        stray.write_bytes(b'Finder metadata')
        (folder / 'notes').mkdir()
        sentinel = self.base / 'outside-note'
        sentinel.write_text('unchanged')
        (folder / 'shortcut').symlink_to(sentinel)
        result = self.call(*self.resource(wait=0))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.store.state()['resources']['gate']['owner'], 'A')
        self.assertEqual(stray.read_bytes(), b'Finder metadata')
        self.assertTrue((folder / 'notes').is_dir())
        self.assertTrue((folder / 'shortcut').is_symlink())
        self.assertEqual(sentinel.read_text(), 'unchanged')
        self.assertFalse(list(folder.glob('*.ticket')), 'the successful waiter cleans only its own ticket')

    def test_busy_mutex_is_named_without_claiming_the_resource_is_reserved(self):
        import fcntl
        from resource_wait import queue_folder, queue_file
        with queue_folder(self.root, 'gate') as folder:
            fd = queue_file(folder, 'mutex')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                result = self.call(*self.resource(wait=0.25))
            finally:
                os.close(fd)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('Resource queue mutex busy; wait deadline expired', result.stderr)
        self.assertNotIn('Resource already reserved', result.stderr)
        self.assertNotIn('gate', self.store.state()['resources'])
        self.assertFalse(any(e['kind'] == 'resource.acquire' for e in self.store.events()))
        self.assertEqual(sum(e['kind'] == 'diagnostic' for e in self.store.events()), 1)

    def test_ticket_shaped_symlink_is_still_refused(self):
        from resource_wait import queue_folder
        with queue_folder(self.root, 'gate'):
            pass
        folder = self.root / 'resource-waits' / hashlib.sha256(b'gate').hexdigest()
        sentinel = self.base / 'ticket-target'
        sentinel.write_text('unchanged')
        ticket = folder / ('0' * 20 + '-' + 'a' * 32 + '.ticket')
        ticket.symlink_to(sentinel)
        result = self.call(*self.resource(wait=0))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('private queue', result.stderr)
        self.assertTrue(ticket.is_symlink())
        self.assertEqual(sentinel.read_text(), 'unchanged')
        self.assertNotIn('gate', self.store.state()['resources'])
