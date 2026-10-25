"""Concurrent publication, explicit verification scope and command-local reads."""
import contextlib
import io
import json
import sqlite3
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import registry
import registry_cache as cache
import registry_current
import squadra
from dashboard import VerifiedView
import test_registry_audit as support


class RegistryPerformance(unittest.TestCase):
    setUp = support.RegistryAuditTests.setUp
    rows = support.RegistryAuditTests.rows
    files = support.RegistryAuditTests.files
    reviewed = support.RegistryAuditTests.reviewed
    tamper_preanchor = support.RegistryAuditTests.tamper_preanchor

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = squadra.main(['--root', str(self.root), *argv])
            except SystemExit as exc:
                rc = exc.code
        return rc, out.getvalue(), err.getvalue()

    def absent_cache(self):
        self.store.release_views()
        for table in ('derived_state_meta', 'derived_radix_nodes', 'derived_radix_roots'):
            self.store.db.execute('DROP TABLE IF EXISTS ' + table)

    def forged_cache(self):
        value = registry_current.build(self.store.events(), registry.apply, registry.initial)
        value['pending_messages'].pop('old-message')
        tree = cache.Tree.build(self.store.db, value, domain='current')
        row = self.store.db.execute('SELECT body FROM derived_state_meta WHERE id=1').fetchone()
        metadata = json.loads(row[0]); metadata['current'] = tree.flush()
        self.store.db.execute('UPDATE derived_state_meta SET body=?,digest=? WHERE id=1',
                              (cache.canonical(metadata), cache.metadata_digest(metadata)))

    def concurrent_while_paused(self, hook, first):
        entered, release, second_done = threading.Event(), threading.Event(), threading.Event()
        errors = []
        original = hook[0]

        def paused(*args, **kwargs):
            if threading.current_thread().name == 'preparing-writer' and not entered.is_set():
                entered.set()
                if not release.wait(10):
                    raise AssertionError('The test did not release preparation')
            return original(*args, **kwargs)

        def worker(action, done=None):
            store = registry.Store(self.root)
            try:
                action(store)
            except BaseException as exc:
                errors.append(repr(exc))
            finally:
                store.close()
                if done:
                    done.set()

        primary = threading.Thread(target=worker, args=(first,), name='preparing-writer')
        secondary = threading.Thread(target=worker, args=(lambda s: s.append('diagnostic', 'system',
            {'reason': 'Concurrent writer positive control'}, 'concurrent-positive'), second_done))
        with patch.object(hook[1], hook[2], new=paused):
            primary.start()
            try:
                self.assertTrue(entered.wait(5), 'The expensive work must actually run')
                secondary.start()
                available = second_done.wait(2)
            finally:
                release.set()
                primary.join(15)
                if secondary.ident is not None:
                    secondary.join(15)
        self.assertFalse(primary.is_alive()); self.assertFalse(secondary.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(available, 'A concurrent writer waited for expensive preparation under the write lock')
        self.assertEqual(sum(e['id'] == 'concurrent-positive' for e in self.store.events()), 1)

    def test_cold_rebuild_does_not_block_a_second_writer(self):
        self.absent_cache()
        self.concurrent_while_paused((cache.Tree.build, cache.Tree, 'build'),
            lambda s: s.append('diagnostic', 'system', {'reason': 'Cold writer'}, 'cold-writer'))
        self.assertEqual(self.store.audit()['cache'], 'matched')

    def test_release_gate_full_audit_does_not_block_a_second_writer(self):
        name = self.reviewed(release_gate=True)
        self.concurrent_while_paused((registry.Store.audit, registry.Store, 'audit'),
                                    lambda s: s.append('close', 'C', {'task': name}))
        self.assertEqual(self.store.state()['tasks'][name]['status'], 'done')

    def test_full_audit_distinguishes_mismatch_from_missing_cache(self):
        self.forged_cache(); rows = self.rows()
        result = self.store.audit()
        self.assertEqual(result['cache'], 'mismatch')
        self.assertEqual(self.rows(), rows)

    def test_default_doctor_refuses_mismatch_without_writing_the_registry(self):
        self.forged_cache(); before = self.files()
        rc, out, err = self.cli('doctor')
        self.assertEqual(rc, 1, out + err)
        self.assertIn('--repair', err)
        self.assertNotIn('"integrity": "passed"', out)
        self.assertEqual(self.files(), before)

    def test_explicit_doctor_repair_persists_both_roots_without_events(self):
        self.forged_cache(); rows = self.rows()
        rc, out, err = self.cli('doctor', '--repair')
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.rows(), rows)
        self.assertEqual(self.store.audit()['cache'], 'matched')

    def test_dashboard_does_not_mark_a_proved_cache_mismatch_complete(self):
        self.forged_cache(); view = VerifiedView(self.root)
        try:
            try:
                view.read()
            except registry.Error as exc:
                self.assertIn('mismatch', str(exc).lower())
                return
            thread = view.audit_thread
            if thread is not None:
                thread.join(5)
            failure = None
            try:
                view.audit_info()
            except registry.Error as exc:
                failure = str(exc)
            self.assertIsNotNone(failure, 'A full audit accepted the mismatching cache')
            self.assertIn('mismatch', failure.lower())
        finally:
            view.close()

    def test_quick_doctor_reports_limited_scope_and_never_runs_full_audit(self):
        before = self.files()
        with patch.object(registry.Store, 'audit', side_effect=AssertionError('Quick ran a full audit')):
            rc, out, err = self.cli('doctor', '--quick')
        self.assertEqual(rc, 0, out + err)
        result = json.loads(out)
        self.assertEqual(result['verification'], 'quick')
        self.assertNotEqual(result.get('integrity'), 'passed')
        self.assertIn('anchor', result['scope'])
        self.assertEqual(self.files(), before)

    def test_healthy_full_doctor_still_passes_and_rejects_preanchor_tamper(self):
        self.assertEqual(self.cli('doctor')[0], 0)
        self.tamper_preanchor()
        self.assertEqual(self.cli('doctor')[0], 1)

    def test_status_computes_attention_once_in_both_presentations(self):
        for argv in (('status',), ('status', '--all')):
            with self.subTest(argv=argv), patch.object(registry, 'attention', wraps=registry.attention) as calls:
                rc, out, err = self.cli(*argv)
                self.assertEqual(rc, 0, err)
                self.assertEqual(calls.call_count, 1)
                self.assertIn('Serve a te', out)

    def test_an_ordinary_write_shares_attention_with_its_printed_summary(self):
        with patch.object(registry, 'attention', wraps=registry.attention) as calls:
            rc, out, err = self.cli('resource', 'acquire', '--actor', 'C', '--name', 'synthetic-summary', '--reason', 'Verify the shared summary')
            self.assertEqual(rc, 0, err)
            self.assertEqual(calls.call_count, 1)
        self.assertIn('Prossimo passo', out)
        self.assertTrue((self.root / 'stato.md').is_file())

    def test_old_registry_board_keeps_elapsed_time_before_any_cache_write(self):
        self.store.append('ack', 'A', {'message': 'assign-T'})
        self.store.append('start', 'A', {'task': 'T'})
        self.absent_cache(); rows = self.rows()
        rc, out, err = self.cli('status')
        self.assertEqual(rc, 0, err)
        self.assertRegex(out, r'Synthetic change[^\n]*circa [0-9]+ min')
        self.assertEqual(self.rows(), rows)

    def test_staging_is_external_private_and_removed_on_every_exit(self):
        import registry_prepare
        for outcome in ('success', 'refused', 'interrupted'):
            with self.subTest(outcome=outcome):
                self.absent_cache()
                paths, modes = [], []
                temporary = tempfile.TemporaryDirectory
                install = registry_prepare.PreparedCache.install

                def folder(*args, **kwargs):
                    value = temporary(*args, **kwargs)
                    paths.append(Path(value.name).resolve())
                    return value

                def checked(prepared, store):
                    path = Path(prepared.folder.name)
                    modes.append((stat.S_IMODE(path.stat().st_mode),
                                  stat.S_IMODE((path / 'derived.sqlite3').stat().st_mode)))
                    return install(prepared, store)

                with patch.object(registry_prepare.tempfile, 'TemporaryDirectory', side_effect=folder), \
                        patch.object(registry_prepare.PreparedCache, 'install', new=checked):
                    if outcome == 'success':
                        self.store.append('diagnostic', 'system', {'reason': outcome})
                    else:
                        error = registry.Error('Synthetic preparation refusal') if outcome == 'refused' else KeyboardInterrupt()
                        with patch.object(cache.Tree, 'build', side_effect=error), self.assertRaises(type(error)):
                            self.store.append('diagnostic', 'system', {'reason': outcome})
                self.assertTrue(paths)
                self.assertTrue(all(self.root not in path.parents and not path.exists() for path in paths))
                self.assertTrue(all(pair == (0o700, 0o600) for pair in modes))
                self.assertFalse(any('squadra-cache-' in str(path) for path in self.root.rglob('*')))

    def test_prepare_checks_appended_suffix_and_publishes_current_state(self):
        from registry_prepare import PreparedCache
        prepared = PreparedCache(self.store)
        self.addCleanup(prepared.close)
        other = registry.Store(self.root)
        try:
            other.append('diagnostic', 'system', {'reason': 'Append during preparation'}, 'suffix-positive')
        finally:
            other.close()
        self.store.db.execute('BEGIN IMMEDIATE')
        try:
            prepared.install(self.store)
            self.store._cached()
            self.store.db.execute('COMMIT')
        except BaseException:
            self.store.db.execute('ROLLBACK')
            raise
        self.assertEqual(self.store.audit()['cache'], 'matched')
        self.assertEqual(sum(event['id'] == 'suffix-positive' for event in self.store.events()), 1)

    def test_same_tip_change_invalidates_prepared_state_without_publication(self):
        from registry_prepare import PreparedCache, StalePreparation
        prepared = PreparedCache(self.store)
        self.addCleanup(prepared.close)
        other = sqlite3.connect(self.store.path, isolation_level=None)
        try:
            other.execute("UPDATE derived_state_meta SET body=body || ' ' WHERE id=1")
        finally:
            other.close()
        rows = self.rows()
        self.store.db.execute('BEGIN IMMEDIATE')
        try:
            with self.assertRaises(StalePreparation):
                prepared.install(self.store)
        finally:
            self.store.db.execute('ROLLBACK')
        self.assertEqual(self.rows(), rows)

    def test_repair_refuses_package_markers_and_default_doctor_preserves_bytes(self):
        self.forged_cache()
        (self.root / 'CHECKSUMS.json').write_text('{}\n')
        before = self.files()
        rc, out, err = self.cli('doctor')
        self.assertEqual(rc, 1, out + err)
        self.assertEqual(self.files(), before)
        rc, out, err = self.cli('doctor', '--repair')
        self.assertEqual(rc, 1, out + err)
        self.assertIn('saved package', err)
        self.assertEqual(self.files(), before)

    def test_quick_refuses_corrupt_anchor_without_writing(self):
        self.store.db.execute('DROP TRIGGER no_update')
        self.store.db.execute("UPDATE events SET data='{}' WHERE seq=(SELECT max(seq) FROM events)")
        rows = self.rows()
        rc, out, err = self.cli('doctor', '--quick')
        self.assertEqual(rc, 1, out + err)
        self.assertIn('integrity', err)
        self.assertEqual(self.rows(), rows)

    def test_continuous_change_refuses_gate_close_without_a_committed_event(self):
        name = self.reviewed(release_gate=True)
        original = registry.Store.audit
        calls = []

        def changing(store, *args, **kwargs):
            result = original(store, *args, **kwargs)
            other = registry.Store(self.root)
            try:
                other.append('diagnostic', 'system', {'reason': 'Concurrent append after audit'})
                calls.append(1)
            finally:
                other.close()
            return result

        before = getattr(self.store, 'commit_count', 0)
        with patch.object(registry.Store, 'audit', new=changing), self.assertRaisesRegex(sqlite3.OperationalError, 'registry busy, retry'):
            self.store.append('close', 'C', {'task': name})
        self.assertEqual(len(calls), 3)
        self.assertEqual(getattr(self.store, 'commit_count', 0), before)
        self.assertFalse(any(e['kind'] == 'close' and e['data']['task'] == name for e in self.store.events()))
        self.assertFalse(self.store.db.in_transaction)

    def test_unchanged_views_reuse_wait_facts_but_a_same_tip_change_invalidates(self):
        self.store.append('transport', 'C', {'message': 'old-message', 'delivered': False})
        self.store.append('diagnostic', 'system', {'reason': 'Keep the transport before the anchor'})
        with patch.object(registry, 'wait_facts', wraps=registry.wait_facts) as reads:
            self.store.view(); self.store.view()
            self.assertEqual(reads.call_count, 1)
            other = sqlite3.connect(self.store.path, isolation_level=None)
            try:
                other.execute("UPDATE derived_state_meta SET body=body || ' ' WHERE id=1")
            finally:
                other.close()
            self.store.view()
            self.assertEqual(reads.call_count, 2)

    def test_wait_facts_memo_never_hides_older_transport_tamper(self):
        self.store.append('transport', 'C', {'message': 'old-message', 'delivered': False})
        self.store.append('diagnostic', 'system', {'reason': 'Keep the transport before the anchor'})
        self.store.view()
        self.store.db.execute('DROP TRIGGER no_update')
        self.store.db.execute("UPDATE events SET data='{}' WHERE kind='transport'")
        with self.assertRaisesRegex(registry.Error, 'integrity'):
            self.store.view()

    def test_release_gate_also_checks_new_artifacts_in_the_same_batch_before_commit(self):
        name = self.reviewed(release_gate=True)
        missing = {'sha256': '0' * 64, 'name': 'missing-evidence', 'bytes': 1, 'original': 'synthetic'}
        rows = self.rows()
        with self.assertRaisesRegex(registry.Error, 'Missing/changed artifact'):
            self.store.batch([('output', 'A', {'task': name, 'text': 'Synthetic evidence', 'next': '-',
                                              'artifacts': [missing]}, None),
                              ('close', 'C', {'task': name}, None)])
        self.assertEqual(self.rows(), rows)

    def test_an_english_legacy_registry_has_elapsed_times_without_an_index_write(self):
        from test_recovery_seams import legacy
        root = self.base / 'english-legacy'
        old = legacy.Store(root, create=True)
        try:
            old.append('init', 'system', {'name': 'English legacy', 'project': str(self.repo),
                'version': '3.6.0', 'lang': 'en', 'limits': {'plan': 3, 'code': 2, 'build': 3}})
            for who, role in [('C', 'coordinator'), ('A', 'builder')]:
                old.append('agent', who, {'id': who, 'identity': who, 'role': role, 'client': 'manual',
                                         'repo': str(self.repo), 'skills': [], 'session': ''})
            old.append('task', 'C', {'id': 'T', 'title': 'English legacy work', 'owner': 'A',
                'repo': str(self.repo), 'base': registry.snapshot(self.repo)['head'],
                'scope': old.artifact(self.scope), 'proof': None, 'expected': None,
                'depends': [], 'full_gate': False})
            old.append('message', 'C', {'id': 'assign', 'to': 'A', 'task': 'T', 'body': 'Synthetic assignment'})
            old.append('ack', 'A', {'message': 'assign'})
            old.append('start', 'A', {'task': 'T'})
            before = old.events()
        finally:
            old.close()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = squadra.main(['--root', str(root), 'status'])
        self.assertEqual(rc, 0, err.getvalue())
        self.assertRegex(out.getvalue(), r'English legacy work[^\n]*about [0-9]+ min')
        reader = legacy.Store(root, read_only=True)
        try:
            self.assertEqual(reader.events(), before)
            self.assertNotIn('derived_state_meta', {row[0] for row in reader.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")})
        finally:
            reader.close()

    def test_quick_preserves_a_database_busy_refusal_instead_of_claiming_mismatch(self):
        from registry_prepare import quick
        with patch.object(cache, 'load', side_effect=sqlite3.OperationalError('database is locked')):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'database is locked'):
                quick(self.store)

    def test_full_audit_preserves_database_busy_instead_of_claiming_cache_mismatch(self):
        with patch.object(cache, 'load', side_effect=sqlite3.OperationalError('database is locked')):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'database is locked'):
                self.store.audit()

    def uncached_append(self):
        last = self.store.events()[-1]
        event = {'id': 'outside-cache', 'utc': registry.now(), 'kind': 'diagnostic', 'actor': 'system',
                 'data': {'reason': 'Synthetic valid suffix'}, 'previous': last['hash'], 'format': 2}
        sha = registry.digest(registry.canonical(event).encode())
        self.store.db.execute('INSERT INTO events(id,utc,kind,actor,data,previous,hash,hash_format) VALUES(?,?,?,?,?,?,?,?)',
            (event['id'], event['utc'], event['kind'], event['actor'], registry.canonical(event['data']), event['previous'], sha, 2))

    def test_a_stale_but_valid_cache_is_distinct_from_a_divergent_prefix(self):
        self.uncached_append()
        self.assertEqual(self.store.audit()['cache'], 'unavailable')
        self.store.append('diagnostic', 'system', {'reason': 'Fold the verified suffix'})
        self.assertEqual(self.store.audit()['cache'], 'matched')

    def test_a_divergent_cache_cannot_hide_behind_a_valid_newer_journal_event(self):
        self.forged_cache()
        self.uncached_append()
        self.assertEqual(self.store.audit()['cache'], 'mismatch')
        rc, out, err = self.cli('doctor')
        self.assertEqual(rc, 1, out + err)
        self.assertIn('--repair', err)

    def test_export_reuses_attention_between_the_saved_and_printed_tables(self):
        with patch.object(registry, 'attention', wraps=registry.attention) as calls:
            rc, out, err = self.cli('export')
            self.assertEqual(rc, 0, out + err)
            self.assertEqual(calls.call_count, 1)
        self.assertTrue((self.root / 'state.json').is_file())


class CacheMismatchHTTP(unittest.TestCase):
    def test_full_audit_mismatch_refuses_api_with_the_existing_content_free_code(self):
        import test_dashboard_background_audit as background
        fixture = background.BackgroundAudit('test_background_failure_latches_and_refuses_cached_state_and_details')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        original = registry.Store.audit

        def mismatch(store, cancelled=None):
            return {**original(store, cancelled=cancelled), 'cache': 'mismatch'}

        with patch.object(registry.Store, 'audit', new=mismatch):
            self.assertEqual(fixture.wait_for(409), {'error': 'registry_invalid'})


if __name__ == '__main__':
    unittest.main()
