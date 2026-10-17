"""The registry WAL stays bounded while a dashboard is open, and doctor reports and compacts it."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import dashboard
import registry
import registry_prepare
import squadra
from registry import Error, Store

AUDIT_THREAD = 'squadra-registry-audit'
LIMIT = 64 * 1024 * 1024


class WalCompact(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sq-wal-compact-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.home = self.base / 'home'
        self.home.mkdir()
        # The audit copy goes to this private temporary folder, so leftovers are attributable to this test.
        self.scratch = self.base / 'tmp'
        self.scratch.mkdir()
        for patcher in (patch.dict(os.environ, {'HOME': str(self.home), 'CODEX_HOME': str(self.home / 'codex'),
                                                'CLAUDE_CONFIG_DIR': str(self.home / 'claude')}),
                        patch.object(tempfile, 'tempdir', str(self.scratch))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.root = self.base / 'registry'
        self.db_path = self.root / 'registry.sqlite3'
        self.wal = Path(str(self.db_path) + '-wal')
        self.store = Store(self.root, create=True)
        self.addCleanup(self.store.close)
        self.store.append('init', 'system', {'name': 'WAL fixture', 'project': str(self.base),
                                             'limits': {'plan': 3, 'code': 2, 'build': 3}})
        for actor, role in (('C', 'coordinator'), ('A', 'builder'), ('R', 'reviewer')):
            self.store.append('agent', actor, {'id': actor, 'identity': actor, 'role': role, 'client': 'manual',
                                               'repo': str(self.base), 'skills': [], 'session': ''})
        self.serial = 0
        self.write_batch(30)

    # Helpers -------------------------------------------------------------------------------------------------

    def write_batch(self, count, store=None):
        for _ in range(count):
            self.serial += 1
            (store or self.store).append('message', 'C', {'id': 'M%d' % self.serial, 'to': 'A',
                                                          'body': 'WAL fixture message %d' % self.serial})

    def passive(self):
        """(busy, frames, backfilled) of a PASSIVE checkpoint from a fresh connection."""
        probe = sqlite3.connect(self.db_path, isolation_level=None)
        try:
            return tuple(probe.execute('PRAGMA wal_checkpoint(PASSIVE)').fetchone())
        finally:
            probe.close()

    def assert_backfilled(self, label):
        busy, frames, backfilled = self.passive()
        self.assertEqual(backfilled, frames, '%s: a reader pins the live WAL (%d of %d frames backfilled)'
                         % (label, backfilled, frames))

    def view(self):
        view = dashboard.VerifiedView(self.root)
        self.addCleanup(view.close)
        return view

    def start_failing_audit(self, view):
        """A first read; the audit may already have failed, and latched, by the time it returns."""
        try:
            view.read()
        except Error:
            pass
        self.join_audit(view)

    def join_audit(self, view):
        worker = view.audit_thread
        if worker is not None:
            worker.join(20)
            self.assertFalse(worker.is_alive(), 'the audit worker must end')

    @contextmanager
    def audit_held(self):
        """Hold the background audit inside its read, after any copy it makes."""
        entered, release = threading.Event(), threading.Event()
        original = Store.verify_artifacts

        def held(store, value=None, scrubbed=None):
            if threading.current_thread().name == AUDIT_THREAD:
                entered.set()
                if not release.wait(30):
                    raise AssertionError('The test did not release the audit')
            return original(store, value, scrubbed=scrubbed)

        with patch.object(Store, 'verify_artifacts', held):
            try:
                yield entered, release
            finally:
                release.set()

    @contextmanager
    def recorded_connections(self):
        made, real = [], sqlite3.connect

        def recording(*args, **kwargs):
            connection = real(*args, **kwargs)
            target = args[0] if args else kwargs.get('database')
            made.append((connection, unquote(str(target))))
            return connection

        with patch.object(sqlite3, 'connect', recording):
            yield made

    def live_reads(self, made):
        """Connections opened on the live registry that are still inside a transaction."""
        held = []
        for connection, target in made:
            if str(self.db_path) not in target:
                continue
            try:
                if connection.in_transaction:
                    held.append(target)
            except sqlite3.ProgrammingError:
                pass  # closed
        return held

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                rc = squadra.main(['--root', str(self.root), *argv])
            except SystemExit as exc:
                rc = exc.code
        return rc, out.getvalue(), err.getvalue()

    def doctor(self, *argv):
        rc, out, err = self.cli('doctor', *argv)
        self.assertEqual(rc, 0, out + err)
        return json.loads(out)

    def chain(self):
        reader = Store(self.root, read_only=True)
        try:
            return [(event['seq'], event['hash']) for event in reader.events()]
        finally:
            reader.close()

    def hold_reader(self):
        reader = sqlite3.connect(self.db_path, isolation_level=None)
        self.addCleanup(reader.close)
        reader.execute('BEGIN')
        reader.execute('SELECT count(*) FROM events').fetchone()
        return reader

    def replacement(self):
        """A different registry file, ready to take the live file's place."""
        other_root = self.base / 'replacement'
        other = Store(other_root, create=True)
        other.append('init', 'system', {'name': 'Replacement registry', 'project': str(self.base),
                                        'limits': {'plan': 3, 'code': 2, 'build': 3}})
        other.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        other.close()
        return other_root / 'registry.sqlite3'

    # WC1 ------------------------------------------------------------------------------------------------------

    def test_wc1_writes_during_a_background_audit_are_fully_backfilled(self):
        view = self.view()
        with self.audit_held() as (entered, release):
            view.read()
            self.assertTrue(entered.wait(10), 'the background audit must start and pass any copy phase')
            for batch in range(3):
                self.write_batch(10)
                view.read()
                self.assert_backfilled('batch %d while the audit runs' % batch)
            release.set()
            self.join_audit(view)
        self.assertEqual(view.audit_info(), {'status': 'complete'})
        self.assertEqual(list(self.scratch.iterdir()), [], 'the audit copy is removed')

    def test_wc1_a_wal_above_the_limit_returns_under_it_after_a_commit_once_no_reader_holds_it(self):
        reader = self.hold_reader()
        filler = sqlite3.connect(self.db_path, isolation_level=None)
        try:
            filler.execute('CREATE TABLE wal_filler(body BLOB)')
            for _ in range(70):
                filler.execute('INSERT INTO wal_filler VALUES (randomblob(1048576))')
            filler.execute('DROP TABLE wal_filler')
        finally:
            filler.close()
        self.assertGreater(self.wal.stat().st_size, LIMIT, 'precondition: a WAL above the limit')
        started = time.monotonic()
        self.write_batch(1)
        self.assertLess(time.monotonic() - started, 5, 'a held reader must not stall a writer')
        self.assertGreater(self.wal.stat().st_size, LIMIT, 'a reader still holds the WAL')
        reader.execute('COMMIT')
        reader.close()
        self.write_batch(1)
        self.assertLessEqual(self.wal.stat().st_size, LIMIT, 'the WAL returns under the limit')
        self.assert_backfilled('after the trimming commit')

    # WC2 ------------------------------------------------------------------------------------------------------

    def test_wc2_no_live_connection_the_dashboard_opened_holds_a_read_between_polls(self):
        view = self.view()
        with self.recorded_connections() as made, self.audit_held() as (entered, release):
            view.read()
            self.assertTrue(entered.wait(10), 'the background audit must be running')
            for batch in range(3):
                self.write_batch(5)
                view.read()
                self.assertEqual(self.live_reads(made), [], 'batch %d: a live read outlived its poll' % batch)
                self.assert_backfilled('batch %d between polls' % batch)
            release.set()
            self.join_audit(view)
        self.assertTrue(any(str(self.db_path) in target for _, target in made), 'connections were recorded')
        self.assertEqual(self.live_reads(made), [])

    def test_wc2_a_cursor_kept_after_close_does_not_keep_the_store_reading(self):
        escaped = []
        for opener in ('execute', 'cursor'):
            reader = Store(self.root, read_only=True)
            if opener == 'execute':
                cursor = reader.db.execute('SELECT seq FROM events ORDER BY seq')
            else:
                cursor = reader.db.cursor()
                cursor.execute('SELECT seq FROM events ORDER BY seq')
            self.assertIsNotNone(cursor.fetchone())
            reader.close()
            escaped.append(cursor)
            self.write_batch(5)
            self.assert_backfilled('cursor from %s kept after close' % opener)
        self.assertEqual(len(escaped), 2)

    def test_wc2_store_close_closes_everything_and_reraises_the_first_error(self):
        reader = Store(self.root, read_only=True)
        reader.view()
        views = list(reader._views)
        self.assertEqual(len(views), 3)
        calls = []

        def failing(view, message, close_first):
            original = view.close

            def close():
                calls.append(message)
                del view.close  # restore the class method for later finalizers
                if close_first:
                    original()
                raise RuntimeError(message)
            return close

        views[0].close = failing(views[0], 'first view failed', True)
        views[1].close = failing(views[1], 'second view failed', True)
        last = views[2].close

        def recorded():
            calls.append('last view closed')
            del views[2].close
            last()
        views[2].close = recorded
        real = reader.db

        class Database:
            def __getattr__(self, name):
                return getattr(real, name)

            def close(self):
                real.close()
                raise sqlite3.OperationalError('database close failed')

        reader.db = Database()
        reader._summary_context = reader._wait_memo = ('kept', 'memo')
        with self.assertRaisesRegex(RuntimeError, 'first view failed'):
            reader.close()
        self.assertEqual(calls, ['first view failed', 'second view failed', 'last view closed'])
        with self.assertRaises(sqlite3.ProgrammingError):
            real.execute('SELECT 1')
        with self.assertRaises(sqlite3.ProgrammingError):
            views[0].db.execute('SELECT 1')
        self.assertEqual((reader._views, reader._summary_context, reader._wait_memo), ([], None, None))
        self.write_batch(5)
        self.assert_backfilled('after a failing close')

    def test_wc2_the_audit_ends_its_read_when_commit_fails(self):
        reader = Store(self.root, read_only=True)
        self.addCleanup(reader.close)
        real = reader.db

        class Database:
            def __getattr__(self, name):
                return getattr(real, name)

            def execute(self, sql, *args):
                if sql == 'COMMIT':
                    raise sqlite3.OperationalError('synthetic commit failure')
                return real.execute(sql, *args)

        reader.db = Database()
        with self.assertRaisesRegex(sqlite3.OperationalError, 'synthetic commit failure'):
            reader.audit()
        self.assertFalse(real.in_transaction, 'a failed COMMIT must fall back to ROLLBACK')
        reader.db = real
        self.write_batch(5)
        self.assert_backfilled('after a failed audit COMMIT')

    def copying_connection(self):
        """The Store's connection class, through which the audit copies the registry."""
        owner = getattr(registry, 'Connection', None)
        self.assertIsNotNone(owner, 'the background audit must read a private copy, made by the Store connection')
        return owner

    def test_wc2_closing_the_dashboard_during_the_copy_releases_it_within_the_bound(self):
        owner = self.copying_connection()
        view = self.view()
        entered = threading.Event()
        original = owner.backup

        def slow(source, target, *, pages=-1, progress=None, name='main', sleep=0.25):
            entered.set()

            def step(status, remaining, total):
                if progress is not None:
                    progress(status, remaining, total)
                time.sleep(.05)
            return original(source, target, pages=1, progress=step, name=name, sleep=sleep)

        with self.recorded_connections() as made, patch.object(owner, 'backup', slow):
            view.read()
            self.assertTrue(entered.wait(10), 'the audit must copy the registry before reading it')
            started = time.monotonic()
            view.close()
            self.assertLess(time.monotonic() - started, 5)
        self.assertIsNone(view.audit_thread)
        self.assertEqual(list(self.scratch.iterdir()), [], 'the partial copy is removed')
        for connection, target in made:
            if target != ':memory:':
                with self.assertRaises(sqlite3.ProgrammingError, msg=target):
                    connection.execute('SELECT 1')
        self.assertNotEqual(view.audit_state, 'complete')

    def test_wc2_a_failed_copy_is_a_visible_audit_failure_and_leaves_nothing_behind(self):
        owner = self.copying_connection()
        view = self.view()

        def broken(source, target, *args, **kwargs):
            raise sqlite3.OperationalError('synthetic copy failure')

        with patch.object(owner, 'backup', broken):
            self.start_failing_audit(view)
        with self.assertRaisesRegex(Error, 'copy failure'):
            view.audit_info()
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_wc2_a_failing_close_of_the_audit_store_still_finishes_bookkeeping(self):
        view = self.view()
        original = Store.close

        def failing(store):
            original(store)
            if threading.current_thread().name == AUDIT_THREAD:
                raise sqlite3.OperationalError('synthetic close failure')

        with patch.object(Store, 'close', failing):
            self.start_failing_audit(view)
        self.assertIsNone(view.audit_thread, 'the worker must finish its bookkeeping')
        with self.assertRaisesRegex(Error, 'close failure'):
            view.audit_info()
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_wc2_a_registry_replaced_during_the_copy_is_never_audited_from_that_copy(self):
        owner = self.copying_connection()
        view = self.view()
        replacement = self.replacement()
        # Swapping a file under SQLite is safe only with an empty WAL, as in the existing replacement test.
        self.store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        self.store.close()
        replaced = threading.Event()
        audited = []
        original_audit = Store._audit
        original_backup = owner.backup

        def replacing(source, target, *args, **kwargs):
            if not replaced.is_set():
                os.replace(replacement, self.db_path)
                replaced.set()
            return original_backup(source, target, *args, **kwargs)

        def counting(store, cancelled=None):
            audited.append(store.path)
            return original_audit(store, cancelled)

        with patch.object(Store, '_audit', counting), patch.object(owner, 'backup', replacing):
            view.read()
            self.join_audit(view)
            self.assertTrue(replaced.is_set(), 'the audit must copy the registry before reading it')
            self.assertEqual(audited, [], 'a copy of a replaced file is discarded, not audited')
            self.assertEqual(view.audit_info(), {'status': 'running'})
            self.assertEqual(list(self.scratch.iterdir()), [])
            _, state, _ = view.read()
            self.join_audit(view)
        self.assertEqual(state['workspace']['name'], 'Replacement registry')
        self.assertEqual(view.audit_info(), {'status': 'complete'})
        self.assertEqual(len(audited), 1)
        self.assertNotEqual(audited[0], self.db_path, 'the audit reads a private copy')

    def test_wc2_the_live_identity_belongs_only_to_a_completed_copy(self):
        live = registry_prepare.identity(self.store)
        copy = self.base / 'copy.sqlite3'
        target = sqlite3.connect(copy)
        self.store.db.backup(target)
        target.execute('PRAGMA journal_mode=DELETE')
        target.close()
        self.assertTrue(hasattr(registry_prepare, 'AuditSnapshot'), 'no private audit copy exists')
        direct = Store(self.root, read_only=True, database=copy)
        try:
            self.assertNotEqual(registry_prepare.identity(direct), live, 'an arbitrary copy is not the registry')
        finally:
            direct.close()
        snapshot = registry_prepare.AuditSnapshot(self.root, live)
        try:
            self.assertEqual(registry_prepare.identity(snapshot.store), live)
            self.assertEqual(snapshot.store.root, self.root)
            self.assertFalse(Path(snapshot.store.path).is_relative_to(self.root), 'the copy lives outside the registry')
            self.assertEqual(snapshot.store.audit()['cache'], 'matched')
        finally:
            snapshot.close()
        self.assertEqual(list(self.scratch.iterdir()), [])
        with self.assertRaises(registry_prepare.StaleSnapshot):
            registry_prepare.AuditSnapshot(self.root, (live[0], live[1] + 1))
        self.assertEqual(list(self.scratch.iterdir()), [])

    def corrupted_copy_refuses(self, corrupt, failure):
        """Corrupt whatever file the background audit's body reads; data stops, the live registry is untouched."""
        view = self.view()
        original = getattr(Store, '_audit', None)

        def tampered(store, cancelled=None):
            editor = sqlite3.connect(store.path, isolation_level=None)
            try:
                corrupt(editor)
            finally:
                editor.close()
            return original(store, cancelled)

        with patch.object(Store, '_audit', tampered, create=True):
            self.start_failing_audit(view)
        with self.assertRaisesRegex(Error, failure):
            view.audit_info()
        with self.assertRaises(Error):
            view.read()
        self.assertEqual(list(self.scratch.iterdir()), [], 'the corrupted copy is removed')
        checked = Store(self.root, read_only=True)
        try:
            status = checked.audit()['cache']
        except Error as exc:
            status = 'audit failed: ' + str(exc)
        finally:
            checked.close()
        self.assertEqual(status, 'matched', 'the live registry is intact')

    def test_wc2_a_corrupted_event_in_the_copy_stops_data_and_never_touches_the_live_registry(self):
        def old_event(editor):
            editor.execute('DROP TRIGGER no_update')
            editor.execute("UPDATE events SET data='{}' WHERE seq=2")
        self.corrupted_copy_refuses(old_event, 'integrity')

    def test_wc2_a_corrupted_cache_in_the_copy_stops_data_and_never_touches_the_live_registry(self):
        def stale_anchor(editor):
            # A well-formed anchor one event back over leaves that already include the last event.
            import registry_cache
            meta = json.loads(editor.execute('SELECT body FROM derived_state_meta WHERE id=1').fetchone()[0])
            meta['seq'] -= 1
            meta['head'] = editor.execute('SELECT hash FROM events WHERE seq=?', (meta['seq'],)).fetchone()[0]
            editor.execute('UPDATE derived_state_meta SET body=?, digest=? WHERE id=1',
                           (registry_cache.canonical(meta), registry_cache.metadata_digest(meta)))
        self.corrupted_copy_refuses(stale_anchor, 'Cache mismatch')

    # WC3 ------------------------------------------------------------------------------------------------------

    def test_wc3_doctor_reports_the_wal_and_a_reader_held_behind(self):
        reader = self.hold_reader()
        self.write_batch(5)
        for argv in ((), ('--quick',)):
            line = self.doctor(*argv).get('wal')
            self.assertIsInstance(line, dict, argv)
            self.assertEqual(line['bytes'], self.wal.stat().st_size, argv)
            self.assertIsInstance(line['frames'], int, argv)
            self.assertIsInstance(line['backfilled'], int, argv)
            self.assertLess(line['oldest_held_read_mark'], line['frames'], argv)
            self.assertIn('frame %d of %d' % (line['oldest_held_read_mark'], line['frames']), line['note'])
        reader.execute('COMMIT')
        reader.close()
        self.write_batch(1)
        line = self.doctor()['wal']
        self.assertNotIn('oldest_held_read_mark', line)
        self.assertNotIn('note', line)

    def test_wc3_the_wal_index_is_unknown_unless_a_whole_consistent_header_is_read(self):
        index = getattr(registry_prepare, 'wal_index', None)
        probe = getattr(registry_prepare, 'wal_probe', None)
        self.assertTrue(callable(index) and callable(probe), 'no read-only WAL index reader exists')
        self.write_batch(3)
        shm = Path(str(self.db_path) + '-shm')
        samples, held = probe(shm)
        size = self.wal.stat().st_size
        parsed = index(samples, size)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed['backfilled'] <= parsed['frames'], True)
        header = bytearray(samples[0])
        bad_checksum = bytearray(header)
        bad_checksum[16] ^= 1  # mxFrame changes without its checksum
        copies_differ = bytearray(header)
        copies_differ[48 + 16] ^= 1
        not_initialized = bytearray(header)
        not_initialized[12] = not_initialized[48 + 12] = 0
        for label, value in (('truncated', [bytes(header[:100])] * 2), ('samples differ', [bytes(header), bytes(copies_differ)]),
                             ('checksum', [bytes(bad_checksum)] * 2), ('copies differ', [bytes(copies_differ)] * 2),
                             ('not initialized', [bytes(not_initialized)] * 2), ('one sample', [bytes(header)]),
                             ('WAL shorter than its frames', samples)):
            self.assertIsNone(index(value, 0 if label.startswith('WAL') else size), label)
        orphan = self.base / 'orphan'
        orphan.mkdir()
        (orphan / 'registry.sqlite3-wal').write_bytes(b'\0' * 4096)
        self.assertEqual(registry_prepare.wal_state(orphan), {'bytes': 4096, 'frames': 'unknown', 'backfilled': 'unknown'})
        empty = self.base / 'empty'
        empty.mkdir()
        self.assertEqual(registry_prepare.wal_state(empty), {'bytes': 0, 'frames': 0, 'backfilled': 0})
        self.assertEqual(probe(self.base / 'missing-shm'), None)

    def test_wc3_doctor_repair_truncates_the_wal_and_keeps_every_event(self):
        self.write_batch(20)
        self.assertGreater(self.wal.stat().st_size, 0)
        before = self.chain()
        result = self.doctor('--repair')
        compaction = result.get('wal_compaction')
        self.assertIsInstance(compaction, dict, 'doctor --repair reports its compaction')
        self.assertEqual((compaction['truncated'], compaction['readers_prevented']), (True, False))
        self.assertEqual(compaction['bytes_after'], 0)
        self.assertEqual(self.wal.stat().st_size, 0)
        self.assertEqual(self.chain(), before)
        self.assertEqual(result['events'], len(before))
        reader = self.hold_reader()
        self.write_batch(3)
        before = self.chain()
        with patch.object(squadra, 'REPAIR_BUSY_MS', 200):
            started = time.monotonic()
            compaction = self.doctor('--repair')['wal_compaction']
            self.assertLess(time.monotonic() - started, 10)
        self.assertEqual((compaction['truncated'], compaction['readers_prevented']), (False, True))
        self.assertGreater(compaction['bytes_after'], 0)
        reader.execute('COMMIT')
        self.assertEqual(self.chain(), before)
        checked = Store(self.root, read_only=True)
        try:
            self.assertEqual(checked.audit()['cache'], 'matched')
        finally:
            checked.close()

    def close_during_snapshot_setup(self, busy):
        """Close the dashboard while the copy's first read meets a writer's lock (busy) or no lock (control)."""
        self.store.close()
        change = sqlite3.connect(self.db_path, isolation_level=None)
        try:
            self.assertEqual(change.execute('PRAGMA journal_mode=DELETE').fetchone()[0], 'delete')
        finally:
            change.close()
        entered, release = threading.Event(), threading.Event()
        original = registry.Connection.execute

        def at_copy(connection, sql, *args):
            if threading.current_thread().name == AUDIT_THREAD and sql == 'SELECT count(*) FROM sqlite_master':
                entered.set()
                release.wait(8)
            return original(connection, sql, *args)

        view, locker = self.view(), None
        try:
            with patch.object(registry.Connection, 'execute', at_copy):
                view.read()
                self.assertTrue(entered.wait(5), 'the copy reaches its first real read')
                if busy:
                    locker = sqlite3.connect(self.db_path, timeout=.2, isolation_level=None)
                    locker.execute('BEGIN EXCLUSIVE')
                release.set()
                time.sleep(.05)
                started = time.monotonic()
                try:
                    view.close()
                except Error as exc:
                    self.fail('closing must cancel the copy within the cleanup bound: %s' % exc)
                elapsed = time.monotonic() - started
                self.assertLess(elapsed, 5, 'closing cancels the copy within the cleanup bound')
                self.assertTrue(view.audit_thread is None or not view.audit_thread.is_alive())
                self.assertEqual(list(self.scratch.iterdir()), [], 'the private copy is removed')
        finally:
            release.set()
            if locker is not None:
                if locker.in_transaction:
                    locker.execute('ROLLBACK')
                locker.close()
            if view.audit_thread is not None:
                view.audit_thread.join(10)
            view.close()

    def test_wc2_a_writer_lock_during_the_copy_cannot_hold_dashboard_close(self):
        self.close_during_snapshot_setup(busy=True)

    def test_wc2_closing_during_an_unlocked_copy_setup_is_prompt(self):
        self.close_during_snapshot_setup(busy=False)


if __name__ == '__main__':
    unittest.main()
