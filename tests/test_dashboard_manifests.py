"""Keep source details complete while compact dashboard reads stay compact."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import dashboard
import source_manifests
from registry import Store, canonical, digest
import test_dashboard_performance as performance


class DashboardManifests(unittest.TestCase):
    stamp = performance.DashboardPerformance.stamp
    task_id = performance.DashboardPerformance.task_id
    run_id = performance.DashboardPerformance.run_id
    source_path = performance.DashboardPerformance.source_path
    source_hash = performance.DashboardPerformance.source_hash
    get = performance.DashboardPerformance.get
    state = performance.DashboardPerformance.state
    compact = performance.DashboardPerformance.compact
    detail = performance.DashboardPerformance.detail
    stop_server = performance.DashboardPerformance.stop_server

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sq-dashboard-manifests-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.home = self.base / 'home'
        self.home.mkdir(mode=0o700)
        self.root = self.base / 'registry'
        for target in ('registry.now', 'dashboard.now'):
            clock = patch(target, return_value=self.stamp)
            clock.start()
            self.addCleanup(clock.stop)
        self.store = Store(self.root, create=True)
        self.addCleanup(self.store.close)
        self.store.append('init', 'system', {'name': 'Manifest fixture', 'project': str(self.base),
                          'limits': {'plan': 3, 'code': 2, 'build': 3}})
        for actor, role in (('C', 'coordinator'), ('A', 'builder'), ('R', 'reviewer')):
            self.store.append('agent', actor, {'id': actor, 'identity': actor, 'role': role,
                              'client': 'manual', 'repo': str(self.base), 'skills': [], 'session': ''})
        self.artifact = self.store.artifact_bytes(b'Synthetic scope', 'scope.txt', 'synthetic')
        self.fingerprint = self.fingerprint_for(self.source_path)
        self.add_task(self.task_id, self.run_id, self.fingerprint)
        with patch.object(Path, 'home', return_value=self.home):
            self.server, self.token = dashboard.make_server(self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def fingerprint_for(self, path):
        value = {'repo': str(self.base), 'head': 'b' * 40, 'files': [
            {'path': path, 'kind': 'file', 'sha256': self.source_hash, 'executable': False}]}
        return {**value, 'sha256': digest(canonical(value).encode())}

    def add_task(self, task, run, fingerprint):
        self.store.append('task', 'C', {'id': task, 'owner': 'A', 'title': 'Check source details',
                          'base': 'b' * 40, 'repo': str(self.base), 'depends': [], 'scope': self.artifact,
                          'proof': None, 'metadata': {'files': ['ordinary metadata']}})
        message = 'assign-' + run
        self.store.append('message', 'C', {'id': message, 'to': 'A', 'task': task, 'body': 'Assigned'})
        self.store.append('ack', 'A', {'message': message})
        self.store.append('run.start', 'A', {'id': run, 'task': task, 'mode': 'proof',
                          'before': deepcopy(fingerprint), 'argv': ['python3', '-B', 'fixture.py']})
        self.store.append('run.end', 'A', {'id': run, 'status': 'passed', 'native_rc': 0,
                          'seconds': 1, 'counts': {'passed': 1, 'total': 1}, 'covers': [],
                          'after': deepcopy(fingerprint), 'output': self.artifact})
        self.store.append('deliver', 'A', {'task': task, 'snapshot': deepcopy(fingerprint)})

    def append(self, label='new'):
        self.store.append('diagnostic', 'system', {'marker': label})

    def assert_full(self, value):
        task = value['tasks'][self.task_id]
        for fingerprint in (task['candidate'], task['delivery']['snapshot'],
                            task['proofs'][0]['before'], task['proofs'][0]['after'],
                            value['runs'][self.run_id]['before'], value['runs'][self.run_id]['after']):
            self.assertEqual(fingerprint, self.fingerprint)
            self.assertNotIn('manifest', fingerprint)

    def test_full_state_and_details_keep_inventory_across_cold_warm_and_append(self):
        first = self.state()
        self.assert_full(first)
        self.assert_full(self.state())
        self.append()
        latest = self.state()
        self.assert_full(latest)
        self.assertEqual(latest['events'], self.store.events()[-120:], 'Recorded events remain byte-bound projections')
        for kind, plural, key in (('task', 'tasks', self.task_id), ('run', 'runs', self.run_id)):
            status, _, body = self.detail(kind, key)
            self.assertEqual(status, 200, body)
            self.assertEqual(json.loads(body), latest[plural][key])

    def test_empty_compact_requests_never_materialize_bulk_inventory(self):
        first = self.compact()
        with patch.object(source_manifests, '_read', wraps=source_manifests._read) as inventory:
            self.assertEqual(self.compact(), first)
            self.append()
            latest = self.compact()
        self.assertEqual(inventory.call_count, 0, 'Compact requests do not resolve bulk inventory')
        self.assertEqual(first['tasks'], latest['tasks'])
        self.assertEqual(latest['tasks'][self.task_id]['metadata']['files'], ['ordinary metadata'])
        self.assertNotIn('files', latest['tasks'][self.task_id]['candidate'])
        self.assertNotIn('files', latest['runs'][self.run_id]['before'])
        self.assertEqual(latest['search'], {'query': '', **{kind: [] for kind in ('tasks', 'runs', 'messages', 'outputs', 'decisions', 'asks', 'reviews')}, 'results': [], 'total': 0})

    def test_compact_search_resolves_hidden_paths_and_digests_only_when_requested(self):
        self.compact()
        self.append()
        for query in (self.source_path.upper(), self.source_hash.upper()):
            with self.subTest(query=query):
                # D-SEARCH-WITHDRAW: a search reads no source manifest.
                with patch.object(source_manifests, '_read', side_effect=AssertionError('search hydrated inventory')):
                    result = self.compact(query)
                self.assertEqual(result['search']['query'], query.lower())
                self.assertEqual(result['search']['tasks'], [])
                self.assertEqual(result['search']['runs'], [])
                self.assertEqual(result['search']['total'], 0)
                self.assertNotIn('files', result['tasks'][self.task_id]['candidate'])

    def test_detail_materializes_only_the_selected_record(self):
        other = self.fingerprint_for('other/second-source.py')
        self.add_task('OTHER', '1' * 32, other)
        self.compact()
        original = source_manifests._read
        read = []

        def track(ref, root):
            read.append(ref['sha256'])
            return original(ref, root)

        with patch.object(source_manifests, '_read', track):
            status, _, body = self.detail('task', self.task_id)
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)['candidate'], self.fingerprint)
        self.assertEqual(set(read), {digest(canonical(self.fingerprint['files']).encode())})
        self.assertNotIn(digest(canonical(other['files']).encode()), read)

    def test_warm_full_detail_and_search_recheck_corrupted_manifest(self):
        self.compact()
        path = self.root / 'artifacts' / digest(canonical(self.fingerprint['files']).encode())
        original = path.read_bytes()
        path.write_bytes(b'[]')
        # D-SEARCH-WITHDRAW keeps FULL/DETAIL refusals; search reads no manifest.
        with patch.object(source_manifests, '_read', side_effect=AssertionError('search hydrated inventory')):
            status, _, body = self.get('/api/state', {'X-Squadra-View': 'compact', 'X-Squadra-Search': 'missing-source-needle'})
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)['search']['total'], 0)
        for route, headers in (('/api/state', {}),
                               ('/api/detail/task', {'X-Squadra-Record': quote(self.task_id, safe='')})):
            with self.subTest(route=route):
                status, _, body = self.get(route, headers)
                self.assertEqual(status, 409, body)
                self.assertEqual(json.loads(body), {'error': 'registry_invalid'})
        path.write_bytes(original)
        self.assert_full(self.state())

    def test_concurrent_append_falls_back_to_one_consistent_authoritative_snapshot(self):
        view = dashboard.VerifiedView(self.root)
        self.addCleanup(view.close)
        view.read()
        self.append('suffix')
        original = Store.view
        raced = False

        def racing(store):
            nonlocal raced
            if not raced:
                raced = True
                self.append('between-suffix-and-view')
            return original(store)

        with patch.object(Store, 'view', racing):
            events, state, _ = view.read()
        self.assertTrue(raced)
        self.assertEqual(state['event_count'], events[-1]['seq'])
        self.assertEqual(events[-1]['data']['marker'], 'between-suffix-and-view')
        self.assertEqual(events, self.store.events())

    def test_detached_cached_state_outlives_closed_owned_sqlite_readers(self):
        view = dashboard.VerifiedView(self.root)
        self.addCleanup(view.close)
        original = Store.close
        original_init = Store.__init__
        opened = []
        closed = []

        def initialize(store, *args, **kwargs):
            original_init(store, *args, **kwargs)
            opened.append(store)

        def track(store):
            closed.append(store)
            return original(store)

        with patch.object(Store, '__init__', initialize), patch.object(Store, 'close', track):
            _, state, _ = view.read()
            worker = view.audit_thread
            if worker is not None:
                worker.join(5)
                self.assertFalse(worker.is_alive())
            self.assertEqual(view.audit_info(), {'status': 'complete'})
        self.assertTrue(opened)
        self.assertEqual(set(opened), set(closed))
        for store in opened:
            with self.assertRaises(sqlite3.ProgrammingError):
                store.db.execute('SELECT 1')
        serialized = json.dumps(state, ensure_ascii=False)
        self.assertIn(self.task_id, serialized)
        record = view.record('tasks', state['tasks'][self.task_id])
        self.assertEqual(record['candidate'], self.fingerprint)


if __name__ == '__main__':
    unittest.main()
