"""Keep cached dashboard views complete, current and bound to verified records."""
from copy import deepcopy
import datetime as dt
import http.client
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
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import dashboard
import registry
from registry import Store, canonical


class DashboardPerformance(unittest.TestCase):
    stamp = '2026-10-12T12:00:00+00:00'
    task_id = 'TASK-caffè/1'
    run_id = '0123456789abcdef0123456789abcdef'
    source_path = 'src/caffè + directory/unique-source.py'
    source_hash = 'abcdef0135792468' * 4

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sq-dashboard-performance-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.home = self.base / 'home'
        self.home.mkdir()
        self.root = self.base / 'registry'
        clock = patch('registry.now', return_value=self.stamp)
        clock.start()
        self.addCleanup(clock.stop)
        display_clock = patch.object(dashboard, 'now', return_value=self.stamp)
        display_clock.start()
        self.addCleanup(display_clock.stop)
        self.store = Store(self.root, create=True)
        self.addCleanup(self.store.close)
        self.store.append('init', 'system', {'name': 'Performance fixture', 'project': str(self.base),
                          'limits': {'plan': 3, 'code': 2, 'build': 3}})
        for actor, role in (('C', 'coordinator'), ('A', 'builder'), ('R', 'reviewer')):
            self.store.append('agent', actor, {'id': actor, 'identity': actor, 'role': role,
                              'client': 'manual', 'repo': str(self.base), 'skills': [], 'session': ''})
        self.artifact_body = b'Registered dashboard performance evidence.'
        self.artifact = self.store.artifact_bytes(self.artifact_body, 'evidence.txt', 'synthetic')
        # Recorded history may hold fields a declaration no longer takes; compaction keeps them.
        with patch.object(registry, 'TASK_FIELDS', registry.TASK_FIELDS | {'metadata'}):
            self.store.append('task', 'C', {'id': self.task_id, 'owner': 'A', 'title': 'Inspect the retained source inventory',
                              'base': 'b' * 40, 'repo': str(self.base), 'depends': [], 'scope': self.artifact,
                              'proof': None, 'metadata': {'files': ['ordinary metadata must remain']}})
        self.store.append('message', 'C', {'id': 'assign-' + self.task_id, 'to': 'A', 'task': self.task_id,
                          'body': 'Assigned check'})
        self.store.append('ack', 'A', {'message': 'assign-' + self.task_id})
        self.fingerprint = {'head': 'b' * 40, 'sha256': 'c' * 64, 'files': {
            self.source_path: {'sha256': self.source_hash, 'bytes': 321},
            'tests/retained_test.py': {'sha256': 'e' * 64, 'bytes': 123}},
            'file_count': 2, 'dirty': False}
        self.store.append('run.start', 'A', {'id': self.run_id, 'task': self.task_id, 'mode': 'proof',
                          'before': deepcopy(self.fingerprint), 'argv': ['python3', '-B', 'fixture.py'],
                          'metadata': {'files': ['run metadata must remain']}})
        self.store.append('run.end', 'A', {'id': self.run_id, 'status': 'passed', 'native_rc': 0,
                          'seconds': 1, 'counts': {'passed': 1, 'total': 1}, 'covers': [],
                          'after': deepcopy(self.fingerprint), 'output': self.artifact})
        self.store.append('deliver', 'A', {'task': self.task_id, 'snapshot': deepcopy(self.fingerprint)})
        with patch.object(Path, 'home', return_value=self.home):
            self.server, self.token = dashboard.make_server(self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())

    def get(self, route='/api/state', headers=None, authenticated=True):
        request_headers = {'X-Squadra-Token': self.token} if authenticated else {}
        request_headers.update(headers or {})
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request('GET', route, headers=request_headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def state(self, **headers):
        deadline = time.monotonic() + 10
        while True:
            status, _, body = self.get(headers=headers)
            self.assertEqual(status, 200, body)
            result = json.loads(body)
            if result.get('audit', {}).get('status') != 'running':
                return result
            self.assertLess(time.monotonic(), deadline, 'The background audit must complete')
            time.sleep(.01)

    def compact(self, query=''):
        return self.state(**{'X-Squadra-View': 'compact', 'X-Squadra-Search': quote(query, safe='')})

    def detail(self, kind, identity):
        return self.get('/api/detail/' + kind, {'X-Squadra-Record': quote(identity, safe='')})

    def test_unchanged_hits_reuse_verified_events_and_fold_without_losing_raw_state(self):
        original = Store.events
        original_audit = Store.audit
        with patch.object(Store, 'events', autospec=True, side_effect=original) as events, \
                patch.object(Store, 'audit', autospec=True, side_effect=original_audit) as audit, \
                patch.object(registry, 'apply', wraps=registry.apply) as fold:
            first = self.state()
            cold_calls, cold_folds = events.call_count, fold.call_count
            self.assertEqual(cold_calls, 2)
            self.assertEqual(audit.call_count, 1)
            self.assertEqual([call.args[1]['seq'] for call in fold.call_args_list[:first['event_count']]],
                             list(range(1, first['event_count'] + 1)))
            self.assertGreaterEqual(cold_folds, first['event_count'])
            for _ in range(3):
                self.assertEqual(self.state(), first)
            self.assertEqual((events.call_count, fold.call_count), (cold_calls, cold_folds))
            self.assertEqual(audit.call_count, 1)
            self.assertEqual(first['tasks'][self.task_id]['candidate']['files'], self.fingerprint['files'])

    def test_append_invalidates_once_and_returns_the_new_verified_state(self):
        first = self.state()
        self.store.append('output', 'A', {'task': self.task_id, 'text': 'Newly recorded output',
                          'next': 'Review', 'artifacts': [self.artifact]})
        original = Store.events
        original_suffix = Store.events_since
        with patch.object(Store, 'events', autospec=True, side_effect=original) as events, \
                patch.object(Store, 'events_since', autospec=True, side_effect=original_suffix) as suffix:
            later = self.state()
            self.assertEqual(events.call_count, 0)
            self.assertEqual(suffix.call_count, 1)
            self.assertEqual(suffix.call_args.args[1:], (first['event_count'], first['registry_head']['hash']))
            self.assertEqual(later['event_count'], first['event_count'] + 1)
            self.assertEqual(later['outputs'][-1]['text'], 'Newly recorded output')
            self.assertEqual(self.state(), later)
            self.assertEqual(events.call_count, 0)
            self.assertEqual(suffix.call_count, 1)

    def test_changed_old_row_with_unchanged_head_is_refused_until_integrity_is_restored(self):
        original_state = self.state()
        original_data = self.store.db.execute('SELECT data FROM events WHERE seq=1').fetchone()[0]
        head = tuple(self.store.db.execute('SELECT seq,hash FROM events ORDER BY seq DESC LIMIT 1').fetchone())
        tampered = json.loads(original_data)
        tampered['name'] = 'Changed without updating its hash'
        self.store.db.execute('DROP TRIGGER no_update')
        self.store.db.execute('UPDATE events SET data=? WHERE seq=1', (canonical(tampered),))
        self.assertEqual(tuple(self.store.db.execute('SELECT seq,hash FROM events ORDER BY seq DESC LIMIT 1').fetchone()), head)
        for route, headers in (('/api/state', {}), ('/api/state', {'X-Squadra-View': 'compact'}),
                               ('/api/detail/task', {'X-Squadra-Record': quote(self.task_id, safe='')})):
            with self.subTest(route=route, headers=headers):
                status, _, body = self.get(route, headers)
                self.assertEqual(status, 409, body)
                self.assertEqual(json.loads(body), {'error': 'registry_invalid'})
                self.assertNotIn(b'"tasks"', body)
        self.store.db.execute('UPDATE events SET data=? WHERE seq=1', (original_data,))
        self.assertEqual(self.state(), original_state)

    def test_atomic_database_replacement_reopens_monitor_and_verifies_replacement(self):
        original_state = self.state()
        self.store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        replacement_root = self.base / 'replacement'
        replacement = Store(replacement_root, create=True)
        replacement.append('init', 'system', {'name': 'Replacement registry', 'project': str(self.base),
                           'limits': {'plan': 3, 'code': 2, 'build': 3}})
        expected = replacement.events()[0]['hash']
        replacement.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        replacement.close()
        self.store.close()
        path = self.root / 'registry.sqlite3'
        inode = path.stat().st_ino
        os.replace(replacement_root / 'registry.sqlite3', path)
        self.assertNotEqual(path.stat().st_ino, inode)
        original = Store.events
        with patch.object(Store, 'events', autospec=True, side_effect=original) as events:
            current = self.state()
            # Cold history, foreign-path cache recovery, and the separate full audit.
            self.assertEqual(events.call_count, 3)
            self.assertEqual(current['workspace']['name'], 'Replacement registry')
            self.assertEqual(current['plugins']['key'], expected)
            self.assertNotEqual(current['plugins']['key'], original_state['plugins']['key'])
            self.assertEqual(current['tasks'], {})
            self.assertEqual(self.state(), current)
            self.assertEqual(events.call_count, 3)

    def test_ctime_only_raw_edit_invalidates_warm_cache_and_restore_recovers(self):
        needle = b'Only ctime reveals this live event edit'
        event_id = self.store.append('output', 'A', {'task': self.task_id, 'text': needle.decode(),
                                 'next': 'Review', 'artifacts': []})
        seq = self.store.db.execute('SELECT seq FROM events WHERE id=?', (event_id,)).fetchone()[0]
        # Only the authoritative row carries the marker in this raw-page fixture.
        self.store.db.execute('PRAGMA secure_delete=ON')
        for table in ('derived_radix_nodes', 'derived_radix_roots', 'derived_state_meta'):
            self.store.db.execute('DELETE FROM ' + table)
        self.assertEqual(self.store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0], 0)
        original = self.state()
        self.assertEqual(self.state(), original)
        path = self.root / 'registry.sqlite3'
        raw = path.read_bytes()
        self.assertEqual(raw.count(needle), 1, 'the marker identifies one live payload')
        offset = raw.index(needle)
        observer = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, isolation_level=None)
        self.addCleanup(observer.close)
        version = observer.execute('PRAGMA data_version').fetchone()[0]
        before = path.stat()
        fd = os.open(path, os.O_WRONLY)
        try:
            self.assertEqual(os.pwrite(fd, b'X', offset), 1)
            os.fsync(fd)
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            after = path.stat()
            self.assertEqual((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
                             (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns))
            self.assertNotEqual(after.st_ctime_ns, before.st_ctime_ns)
            self.assertEqual(observer.execute('PRAGMA data_version').fetchone()[0], version)
            live = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
            try:
                data = live.execute('SELECT data FROM events WHERE seq=?', (seq,)).fetchone()[0]
                self.assertEqual(json.loads(data)['text'], 'X' + needle.decode()[1:],
                                 'the raw edit must affect a live event, not a free page')
            finally:
                live.close()
            status, _, body = self.get()
            self.assertEqual(status, 409, body[:200])
            self.assertEqual(json.loads(body), {'error': 'registry_invalid'})
        finally:
            os.pwrite(fd, needle[:1], offset)
            os.fsync(fd)
            os.close(fd)
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(observer.execute('PRAGMA data_version').fetchone()[0], version)
        self.assertEqual(self.state(), original)

    def test_missing_database_or_symlink_never_serves_cached_state(self):
        original = self.state()
        path = self.root / 'registry.sqlite3'
        moved = self.root / 'registry-kept.sqlite3'
        path.rename(moved)
        status, _, body = self.get()
        self.assertEqual(status, 409, body)
        self.assertNotIn(b'"tasks"', body)
        path.symlink_to(moved)
        status, _, body = self.get()
        self.assertEqual(status, 409, body)
        self.assertEqual(json.loads(body), {'error': 'registry_invalid'})
        path.unlink()
        moved.rename(path)
        self.assertEqual(self.state(), original)

    def test_compact_opt_in_removes_only_nested_fingerprint_files_from_tasks_and_runs(self):
        full = self.state()
        compact = self.compact()
        expected = deepcopy(full)
        task = expected['tasks'][self.task_id]
        del task['candidate']['files']
        del task['delivery']['snapshot']['files']
        for record in (task['proofs'][0], expected['runs'][self.run_id]):
            del record['before']['files']
            del record['after']['files']
        expected['search'] = {'query': '', **{kind: [] for kind in ('tasks', 'runs', 'messages', 'outputs', 'decisions', 'asks', 'reviews')}, 'results': [], 'total': 0}
        self.assertEqual(compact, expected)
        self.assertIn('files', compact['tasks'][self.task_id]['metadata'])
        self.assertIn('files', compact['runs'][self.run_id]['metadata'])
        self.assertEqual(compact['events'], full['events'])
        self.assertEqual(compact['graph'], full['graph'])
        self.assertEqual(compact['requirements'], full['requirements'])
        self.assertEqual(self.state(**{'X-Squadra-View': 'unknown', 'X-Squadra-Search': 'ignored'}), full)
        self.assertEqual(self.state(), full)

    def test_compact_search_matches_hidden_unicode_paths_and_hashes_in_full_records(self):
        # D-SEARCH-WITHDRAW: inventory paths/digests are no longer human search fields.
        for query in (self.source_path.upper(), self.source_hash.upper()):
            with self.subTest(query=query):
                result = self.compact(query)
                self.assertEqual(result['search']['query'], query.lower())
                self.assertEqual(result['search']['tasks'], [])
                self.assertEqual(result['search']['runs'], [])
                self.assertEqual(result['search']['total'], 0)
                self.assertNotIn('files', result['tasks'][self.task_id]['candidate'])
        result = self.compact(self.task_id.upper())
        self.assertEqual(result['search']['tasks'], [self.task_id])
        self.assertEqual(result['search']['runs'], [self.run_id])
        self.assertNotIn('files', result['tasks'][self.task_id]['candidate'])
        self.assertEqual(self.compact('absent-needle')['search']['total'], 0)
        self.assertEqual(self.compact()['search'], {'query': '', **{kind: [] for kind in ('tasks', 'runs', 'messages', 'outputs', 'decisions', 'asks', 'reviews')}, 'results': [], 'total': 0})
        self.assertNotIn('search', self.state(**{'X-Squadra-Search': quote(self.source_path, safe='')}))

    def test_details_return_exact_full_record_after_compact_hit_with_no_path_access(self):
        full = self.state()
        self.compact()
        original = Store.events
        with patch.object(Store, 'events', autospec=True, side_effect=original) as events:
            for singular, plural, identity in (('task', 'tasks', self.task_id), ('run', 'runs', self.run_id)):
                status, headers, body = self.detail(singular, identity)
                self.assertEqual(status, 200, body)
                self.assertEqual(json.loads(body), full[plural][identity])
                self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertEqual(events.call_count, 0)
        outside = self.base / 'not-a-record.txt'
        outside.write_text('Private outside sentinel')
        for identity in ('', 'unknown', str(outside), '../not-a-record.txt', '__proto__'):
            for kind in ('task', 'run'):
                with self.subTest(kind=kind, identity=identity):
                    status, _, body = self.detail(kind, identity)
                    self.assertEqual(status, 404, body)
                    self.assertNotIn(b'Private outside sentinel', body)

    def test_state_and_detail_authorization_and_policy_stay_in_front_of_cache(self):
        _, baseline_headers, _ = self.get('/')
        self.state()
        with patch.object(Store, 'events', side_effect=AssertionError('Unauthorized request reached registry')):
            for route in ('/api/state', '/api/detail/task', '/api/detail/run'):
                with self.subTest(route=route):
                    status, headers, _ = self.get(route, {'X-Squadra-View': 'compact'}, authenticated=False)
                    self.assertEqual(status, 401)
                    self.assertEqual(headers['Content-Security-Policy'], baseline_headers['Content-Security-Policy'])
                    status, _, _ = self.get(route, {'Host': 'evil.example:' + str(self.server.server_port)})
                    self.assertEqual(status, 403)
                    status, _, _ = self.get(route, {'X-Squadra-Token': 'wrong'})
                    self.assertEqual(status, 401)

    def test_hot_cache_keeps_artifact_whitelist_and_rechecks_artifact_bytes(self):
        self.state()
        route = '/api/artifact/' + self.artifact['sha256']
        status, _, body = self.get(route)
        self.assertEqual((status, body), (200, self.artifact_body))
        unregistered = self.store.artifact_bytes(b'Unregistered file', 'unregistered.txt', 'synthetic')
        status, _, body = self.get('/api/artifact/' + unregistered['sha256'])
        self.assertEqual(status, 404)
        self.assertIn(b'unregistered artifact', body)
        target = self.root / 'artifacts' / self.artifact['sha256']
        target.write_bytes(b'Changed after the state cache was filled')
        status, _, body = self.get(route)
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body), {'error': 'registry_invalid'})
        target.write_bytes(self.artifact_body)
        self.assertEqual(self.get(route)[2], self.artifact_body)
        self.assertEqual(self.get(route, authenticated=False)[0], 401)

    def test_cached_events_still_recompute_presence_and_waits_at_read_time(self):
        self.store.append('settings', 'C', {'wait_seconds': {'alive': 300, 'ack': 300}})
        self.store.append('message', 'C', {'id': 'PING', 'to': 'A', 'body': 'Are you available?', 'ping': True,
                          'availability_event': None})
        self.store.append('transport', 'C', {'message': 'PING', 'via': 'codex-queue', 'delivered': True})
        original = Store.events
        clock = dt.datetime.fromisoformat(self.stamp)
        with patch.object(Store, 'events', autospec=True, side_effect=original) as events:
            with patch.object(dashboard, 'now', return_value=(clock + dt.timedelta(seconds=299)).isoformat()):
                early = self.state()
            with patch.object(dashboard, 'now', return_value=(clock + dt.timedelta(seconds=301)).isoformat()):
                late = self.state()
            self.assertEqual(events.call_count, 2)
        before = next(row for row in early['presence'] if row['agent'] == 'A')
        after = next(row for row in late['presence'] if row['agent'] == 'A')
        self.assertEqual((before['seconds'], before['due']), (299, False))
        self.assertEqual((after['seconds'], after['due']), (301, True))
        self.assertFalse(any(row['kind'] == 'alive' for row in early['waits']))
        self.assertEqual(next(row['seconds'] for row in late['waits'] if row['kind'] == 'alive'), 301)
        self.assertEqual(after['availability'], 'active')
        for key in ('event_count', 'events', 'tasks', 'runs', 'agents'):
            self.assertEqual(early[key], late[key], key)


if __name__ == '__main__':
    unittest.main()
