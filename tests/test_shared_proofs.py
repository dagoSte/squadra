"""One native execution, separate task contracts, and raw replay compatibility."""
from copy import deepcopy
import inspect
import io
import json
import os
from pathlib import Path
import sqlite3
import shutil
import shlex
import subprocess
import sys
import tarfile
import unittest
import threading
import urllib.request
from unittest.mock import patch

from test_review_pool_gate_refusals import Fixture
import registry
from registry import Error, Store, plain, snapshot
from runtime import execute
from squadra import deliver


class SharedProofs(Fixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, self.env)
        env.start()
        self.addCleanup(env.stop)
        self.argv = [sys.executable, '-c', "print('one native execution')"]
        self.full_task('ONE', proof=self.argv, reviewer='R', full_gate=True,
                       requirements=[{'id': 'A1', 'text': 'First contract'}])
        self.full_task('TWO', proof=self.argv, reviewer='R2', full_gate=True,
                       requirements=[{'id': 'B1', 'text': 'Second contract'}])
        self.store.append('resource.acquire', 'A', {'name': 'gate', 'reason': 'Synthetic shared proof'})

    def shared(self, **kwargs):
        self.assertIn('also', inspect.signature(execute).parameters,
                      'The runtime must support a proof shared by several tasks')
        args = dict(argv=self.argv, gate=True, also=['TWO'], covers=['A1', 'B1'])
        args.update(kwargs)
        return execute(self.store, 'A', 'ONE', **args)

    def close_task(self, name, reader, requirement):
        handoff = deliver(self.store, 'A', name)['handoff']
        self.store.append('ack', reader, {'message': handoff})
        rc, err = self.run_cli('review', '--actor', reader, '--task', name, '--verdict', 'accepted',
                               '--report', str(self.report), '--covers', requirement)
        self.assertEqual(rc, 0, err)
        rc, err = self.run_cli('check', '--actor', 'C', '--task', name)
        self.assertEqual(rc, 0, err)
        rc, err = self.run_cli('close', '--actor', 'C', '--task', name)
        self.assertEqual(rc, 0, err)
        return handoff

    def test_one_process_serves_independent_deliveries_reads_checks_and_closes(self):
        import runtime
        original = runtime.subprocess.Popen
        launched = []
        def start(argv, *args, **kwargs):
            if argv == self.argv:
                launched.append(argv)
            return original(argv, *args, **kwargs)
        with patch.object(runtime.subprocess, 'Popen', side_effect=start):
            result = self.shared()
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(len(launched), 1)
        self.assertEqual(self.store.state()['tasks']['TWO']['proofs'], [])
        alias = self.store.task('TWO')['proofs']
        self.assertEqual([(p['id'], p['shared_from'], p['covers']) for p in alias],
                         [(result['id'], 'ONE', ['B1'])])
        self.assertEqual(self.store.task('ONE')['proofs'][0]['covers'], ['A1'])
        self.store.append('resource.release', 'A', {'name': 'gate'})
        first = self.close_task('ONE', 'R', 'A1')
        second = self.close_task('TWO', 'R2', 'B1')
        self.assertNotEqual(first, second)
        self.assertIn(result['id'], self.store.state()['messages'][second]['body'])
        self.assertIn('--also', self.store.state()['messages'][second]['body'])
        self.assertEqual(len(self.store.state()['runs']), 1)
        self.assertEqual(self.store.audit()['cache'], 'matched')
        self.store.project()
        exported = json.loads((self.root / 'state.json').read_text())
        self.assertEqual(exported['tasks']['TWO']['proofs'][0]['id'], result['id'])
        self.assertEqual(len(exported['runs']), 1)

    def test_cli_accepts_repeated_also_and_shared_requirement_ids(self):
        self.full_task('THREE', proof=self.argv, requirements=[{'id': 'A1', 'text': 'Same ID, own contract'}])
        rc, err = self.run_cli('prove', '--actor', 'A', '--task', 'ONE', '--also', 'TWO', '--also', 'THREE',
                               '--gate', '--covers', 'A1', '--covers', 'B1', '--', *self.argv)
        self.assertEqual(rc, 0, err)
        ids = [self.store.task(name)['proofs'][0]['id'] for name in ('ONE', 'TWO', 'THREE')]
        self.assertEqual(len(set(ids)), 1)

    def test_invalid_coverage_starts_neither_precheck_nor_proof(self):
        import precheck
        cases = (([], 'prove requires --covers for requirement IDs: A1, B1'),
                 (['A1'], 'Shared prove requires --covers for TWO: B1'),
                 (['A1', 'B1', 'NOPE'], 'Unknown requirement IDs in --covers: NOPE'))
        for covers, sibling_refusal in cases:
            with self.subTest(covers=covers), patch.object(precheck, 'run') as checked:
                rc, err = self.run_cli('prove', '--actor', 'A', '--task', 'ONE', '--also', 'TWO', '--gate',
                                       *[arg for key in covers for arg in ('--covers', key)], '--', *self.argv)
                self.assertEqual(rc, 1, err)
                self.assertTrue('shared_proof_invalid' in err or
                                err.strip() == 'Squadra: ' + sibling_refusal, err)
                checked.assert_not_called()
                self.assertEqual(self.store.state()['runs'], {})

    def test_duplicate_unknown_and_primary_as_extra_refuse_before_start(self):
        for also in (['ONE'], ['TWO', 'TWO'], ['NOPE']):
            with self.subTest(also=also), self.assertRaisesRegex(Error, 'shared_proof_invalid'):
                self.shared(also=also)
            self.assertEqual(self.store.state()['runs'], {})
            self.assertFalse((self.root / 'runs').exists())

    def test_each_contract_is_checked_before_start(self):
        for fields, message in (({'proof': ['false']}, 'argv'), ({'expected': 99}, 'count'),
                                ({'repo': str(self.base)}, 'repository')):
            with self.subTest(fields=fields):
                state = self.store.resolved()
                tasks = plain(state['tasks'])
                tasks['TWO'].update(fields)
                state['tasks'] = tasks
                with patch.object(self.store, 'resolved', return_value=state), self.assertRaisesRegex(Error, message):
                    self.shared()
                self.assertEqual(self.store.state()['runs'], {})
        with self.assertRaisesRegex(Error, 'gate'):
            self.shared(gate=False)

    def test_secondary_assignment_and_dependencies_are_not_bypassed(self):
        self.task('UNACK')
        with self.assertRaisesRegex(Error, 'unacknowledged'):
            self.shared(also=['UNACK'], covers=['A1'])
        self.full_task('BLOCKED', depends=['T'], proof=self.argv)
        with self.assertRaisesRegex(Error, 'Dependenc'):
            self.shared(also=['BLOCKED'], covers=['A1'])
        self.assertEqual(self.store.state()['runs'], {})

    def test_failure_timeout_cleanup_and_source_change_never_attribute_a_pass(self):
        cases = [([sys.executable, '-c', 'raise SystemExit(7)'], {}, 'failed'),
                 ([sys.executable, '-c', 'import time;time.sleep(5)'], {'timeout': 1}, 'timeout'),
                 ([sys.executable, '-c', "from pathlib import Path;Path('code.py').write_text('VALUE=2\\n')"], {}, 'failed')]
        for number, (argv, options, status) in enumerate(cases):
            with self.subTest(status=status, argv=argv):
                first, second = 'FAIL-A-' + str(number), 'FAIL-B-' + str(number)
                self.full_task(first, proof=argv)
                self.full_task(second, proof=argv)
                self.assertIn('also', inspect.signature(execute).parameters)
                result = execute(self.store, 'A', first, argv=argv, also=[second], **options)
                self.assertEqual(result['status'], status, result)
                self.assertEqual(self.store.task(second)['proofs'], [])
                (self.repo / 'code.py').write_text('VALUE = 1\n')

    def test_failed_cleanup_cannot_leave_a_passing_alias(self):
        with patch('runtime.terminate', side_effect=RuntimeError('synthetic cleanup uncertainty')):
            result = self.shared()
        self.assertEqual(result['status'], 'cleanup-unknown')
        self.assertEqual(self.store.task('TWO')['proofs'], [])

    def test_terminal_contract_override_is_rejected(self):
        original = self.store.append
        def append(kind, actor, data, event_id=None):
            if kind == 'run.end' and data['status'] == 'passed':
                for key, value in [('stage', 'plan'), ('folder', '/unrelated'), ('timeout', 0), ('agent', 'C')]:
                    with self.subTest(key=key), self.assertRaisesRegex(Error, 'immutable'):
                        original(kind, actor, {**data, key: value}, event_id)
            return original(kind, actor, data, event_id)
        with patch.object(self.store, 'append', side_effect=append):
            self.assertEqual(self.shared()['status'], 'passed')

    def test_task_changed_at_the_terminal_boundary_fails_and_ends_the_run(self):
        original = self.store.append
        def append(kind, actor, data, event_id=None):
            if kind == 'run.end' and data['status'] == 'passed':
                original('plan', 'A', {'task': 'TWO', 'artifact': self.store.artifact(self.report)})
            return original(kind, actor, data, event_id)
        with patch.object(self.store, 'append', side_effect=append):
            result = self.shared()
        self.assertEqual(result['status'], 'failed', result)
        self.assertIn('terminal binding refused', result['error'])
        self.assertEqual(self.store.task('TWO')['proofs'], [])
        self.assertEqual(self.store.state()['runs'][result['id']]['status'], 'failed')

    def test_checked_source_drift_and_expected_nonzero_refuse_before_start(self):
        with self.assertRaisesRegex(Error, 'expect exit zero'):
            self.shared(expected_rc=1)
        before = snapshot(self.repo)
        (self.repo / 'code.py').write_text('VALUE = 2\n')
        with self.assertRaisesRegex(Error, 'precheck_failed'):
            self.shared(checked=before)
        self.assertEqual(self.store.state()['runs'], {})
        self.assertFalse((self.root / 'runs').exists())

    def test_common_native_test_count_is_required_for_every_member(self):
        argv = [sys.executable, '-c', "print('Ran 2 tests in 0.001s\\n\\nOK')"]
        for name in ('COUNT-A', 'COUNT-B'):
            self.full_task(name, proof=argv, expected=2)
        self.assertIn('also', inspect.signature(execute).parameters)
        result = execute(self.store, 'A', 'COUNT-A', argv=argv, also=['COUNT-B'])
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(self.store.task('COUNT-B')['proofs'][0]['counts'], {'passed': 2})

    def test_compact_view_preserves_manifests_and_the_previous_view(self):
        from dashboard import VerifiedView
        verified = VerifiedView(self.root)
        before_events, before, before_raw, cached = verified.rebuild(self.store, None, None, None)
        saved = deepcopy(before)
        result = self.shared()
        events = self.store.events()
        prior = {'events': before_events, 'state': before, 'raw': before_raw, 'cached': cached}
        _, changed, _, _ = verified.rebuild(self.store, before_events, events[len(before_events):], prior)
        expected = plain(self.store.resolved(compact=True))
        self.assertEqual(changed, expected)
        self.assertEqual(before, saved)
        self.assertEqual(changed['tasks']['TWO']['proofs'][0]['id'], result['id'])
        self.assertEqual(len(changed['runs']), 1)
        progress = registry.task_progress(events)
        for name in ('ONE', 'TWO'):
            proof = next(step for step in progress[name]['steps'] if step['id'] == 'proof')
            self.assertTrue(proof['reached'])
        graph = registry.decision_graph(events)
        nodes = [node for node in graph['nodes'] if node['kind'] == 'proof']
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]['tasks'], ['ONE', 'TWO'])

    def test_index_matches_canonical_start_and_memo_invalidates_on_new_head(self):
        self.shared()
        rows = self.store.db.execute('SELECT data FROM events WHERE ' + registry.SHARED_START_PREDICATE).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertIn('"shared_proof":', rows[0]['data'])
        plan = self.store.db.execute('EXPLAIN QUERY PLAN SELECT seq FROM events WHERE ' +
                                     registry.SHARED_START_PREDICATE).fetchall()
        self.assertIn('shared_proof_starts', str([tuple(row) for row in plan]))
        self.store._shared_start_memo = None
        queries = []
        self.store.db.set_trace_callback(queries.append)
        try:
            for _ in range(2):
                self.store.task('TWO')
            reads = lambda: sum('SELECT actor,data FROM events WHERE' in query for query in queries)
            self.assertEqual(reads(), 1)
            self.store.append('diagnostic', 'system', {'error': 'New head'})
            self.store.task('TWO')
            self.assertEqual(reads(), 2)
        finally:
            self.store.db.set_trace_callback(None)

    def test_dashboard_lists_one_physical_run_for_each_task_in_italian_and_english(self):
        from dashboard_test_support import make_server
        self.shared()
        server, token = make_server(self.root)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            request = urllib.request.Request('http://127.0.0.1:' + str(server.server_port) + '/api/state',
                                             headers={'X-Squadra-Token': token})
            with urllib.request.urlopen(request, timeout=15) as response:
                body = response.read()
        finally:
            server.shutdown(); server.server_close(); worker.join(timeout=5)
        fixture = self.base / 'ui.json'
        fixture.write_bytes(body)
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node required')
        source = Path(__file__).resolve().parents[1]
        result = subprocess.run([node, str(source / 'tests/shared_proofs_contract.js'),
                                 str(source / 'assets/dashboard.html'), str(fixture), 'shared'],
                                env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_read_only_discovery_without_index_and_false_positive(self):
        result = self.shared()
        fake = {'id': 'false-positive', 'task': 'ONE', 'mode': 'proof', 'argv': self.argv,
                'before': snapshot(self.repo), 'gate': True, 'expected_rc': 0, 'covers': ['A1'],
                'metadata': {'shared_proof': {'tasks': [{'task': 'TWO'}]}}}
        self.store.append('run.start', 'A', fake)
        self.store.append('run.end', 'A', {'id': fake['id'], 'status': 'passed', 'native_rc': 0,
                                          'after': fake['before']})
        self.store.db.execute('DROP INDEX shared_proof_starts')
        reader = Store(self.root, read_only=True)
        try:
            self.assertEqual([p['id'] for p in reader.task('TWO')['proofs']], [result['id']])
            self.assertIsNone(reader.db.execute("SELECT 1 FROM sqlite_master WHERE name='shared_proof_starts'").fetchone())
        finally:
            reader.close()
        self.assertEqual(self.store.db.execute('PRAGMA quick_check').fetchone()[0], 'ok')

    def test_failed_optional_index_creation_still_allows_discovery(self):
        self.shared()
        self.store.db.execute('DROP INDEX shared_proof_starts')
        connect = sqlite3.connect
        class NoIndex(getattr(registry, 'Connection', sqlite3.Connection)):
            def execute(self, sql, *args, **kwargs):
                if sql.startswith('CREATE INDEX IF NOT EXISTS shared_proof_starts'):
                    raise sqlite3.OperationalError('database is locked')
                return super().execute(sql, *args, **kwargs)
        with patch('registry.sqlite3.connect', side_effect=lambda *a, **k: connect(*a, **{**k, 'factory': NoIndex})):
            other = Store(self.root)
        try:
            self.assertEqual(len(other.task('TWO')['proofs']), 1)
            self.assertEqual(other.db.execute('PRAGMA busy_timeout').fetchone()[0], 30000)
        finally:
            other.close()

    def test_unparseable_old_writer_metadata_does_not_block_other_work(self):
        from dashboard_test_support import make_server
        self.full_task('OTHER', proof=self.argv)
        source = Path(__file__).resolve().parents[1]
        old = self.base / 'original'
        old.mkdir()
        archive = subprocess.check_output(['git', 'archive', '8558291', 'scripts'], cwd=source)
        with tarfile.open(fileobj=io.BytesIO(archive)) as packed:
            packed.extractall(old, filter='data')
        before = snapshot(self.repo)
        start = {'id': 'future-run', 'task': 'ONE', 'mode': 'proof', 'argv': self.argv,
                 'before': before, 'gate': True, 'expected_rc': 0, 'covers': ['A1'],
                 'shared_proof': {'v': 2, 'tasks': [{'task': 'TWO', 'covers': ['B1']}]}}
        # The current writer still refuses unknown metadata before appending anything.
        count = len(self.store.events())
        with self.assertRaisesRegex(Error, 'shared_proof_invalid'):
            self.store.append('run.start', 'A', start)
        self.assertEqual(len(self.store.events()), count)
        writer = """
import json, sys
sys.path.insert(0, sys.argv[1])
from registry import Store
store = Store(sys.argv[2])
try:
    start = json.loads(sys.argv[3])
    store.append('run.start', 'A', start)
    store.append('run.end', 'A', {'id': start['id'], 'status': 'passed', 'native_rc': 0,
                                 'after': start['before']})
finally:
    store.close()
"""
        result = subprocess.run([sys.executable, '-B', '-c', writer, str(old / 'scripts'),
                                 str(self.root), json.dumps(start)], env=self.env,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        try:
            tasks = {name: self.store.task(name) for name in ('ONE', 'TWO', 'OTHER')}
            events = self.store.events()
            progress = registry.task_progress(events)
            graph = registry.decision_graph(events)
        except Error as exc:
            self.fail('Unknown shared metadata must not block reads: ' + str(exc))
        self.assertEqual(tasks['TWO']['proofs'], [])
        self.assertEqual(tasks['OTHER']['proofs'], [])
        self.assertFalse(next(step for step in progress['TWO']['steps'] if step['id'] == 'proof')['reached'])
        node = next(node for node in graph['nodes'] if node['kind'] == 'proof')
        self.assertEqual(node['tasks'], ['ONE'])
        for args in (('prove', '--actor', 'A', '--task', 'OTHER', '--', *self.argv),
                     ('deliver', '--actor', 'A', '--task', 'OTHER')):
            rc, err = self.run_cli(*args)
            self.assertEqual(rc, 0, err)
        self.assertEqual(self.store.task('TWO')['proofs'], [])
        server, token = make_server(self.root)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            request = urllib.request.Request('http://127.0.0.1:' + str(server.server_port) + '/api/state',
                                             headers={'X-Squadra-Token': token})
            with urllib.request.urlopen(request, timeout=15) as response:
                data = json.load(response)
            self.assertIn('OTHER', data['tasks'])
            self.assertEqual(data['tasks']['TWO']['proofs'], [])
        finally:
            server.shutdown(); server.server_close(); worker.join(timeout=5)

    def test_documented_shared_proof_gate_release_releases_the_gate(self):
        source = Path(__file__).resolve().parents[1]
        text = (source / 'references/work.md').read_text()
        section = text.split('### One execution for the two final reads', 1)[1].split('```sh', 1)[1].split('```', 1)[0]
        command = next(line for line in section.splitlines() if line.startswith('squadra resource release '))
        argv = ['A' if word == 'builder' else word for word in shlex.split(command)[1:]]
        rc, err = self.run_cli(*argv)
        self.assertEqual(rc, 0, err)
        self.assertNotIn('gate', self.store.state()['resources'])

    def test_original_build_fails_closed_and_keeps_audit_write_save_restore_compatibility(self):
        source = Path(__file__).resolve().parents[1]
        archive = subprocess.check_output(['git', 'archive', '8558291', 'scripts'], cwd=source)
        old = self.base / 'original'
        old.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as packed:
            packed.extractall(old, filter='data')
        def cli(*args):
            return subprocess.run([sys.executable, '-B', str(old / 'scripts/squadra.py'), '--root', str(self.root),
                                   *args], env=self.env, capture_output=True, text=True, timeout=45)
        result = self.shared()
        self.store.append('resource.release', 'A', {'name': 'gate'})
        handoff = deliver(self.store, 'A', 'TWO')['handoff']
        self.store.append('ack', 'R2', {'message': handoff})
        before = len(self.store.events())
        for args in [('deliver', '--actor', 'A', '--task', 'TWO'),
                     ('review', '--actor', 'R2', '--task', 'TWO', '--verdict', 'accepted',
                      '--report', str(self.report), '--covers', 'B1'),
                     ('check', '--actor', 'C', '--task', 'TWO'), ('close', '--actor', 'C', '--task', 'TWO')]:
            with self.subTest(args=args):
                reply = cli(*args)
                self.assertEqual(reply.returncode, 1, reply.stdout + reply.stderr)
                self.assertIn('proof', reply.stderr.lower())
        self.assertEqual({e['kind'] for e in self.store.events()[before:]}, {'diagnostic'})
        self.assertEqual(self.store.state()['tasks']['TWO']['proofs'], [])
        self.assertEqual(self.store.state()['tasks']['TWO']['reviews'], [])
        for args in [('doctor',), ('status',), ('send', '--actor', 'C', '--to', 'A',
                     '--body-file', str(self.report), '--info', '--id', 'OLD-WRITE')]:
            with self.subTest(args=args):
                reply = cli(*args)
                self.assertEqual(reply.returncode, 0, reply.stdout + reply.stderr)
        saved = self.base / 'saved'
        reply = cli('save', '--actor', 'C', '--to', str(saved), '--temporary')
        self.assertEqual(reply.returncode, 0, reply.stdout + reply.stderr)
        reply = cli('restore-check', '--from', str(saved), '--to', str(self.base / 'restored'))
        self.assertEqual(reply.returncode, 0, reply.stdout + reply.stderr)
        self.assertEqual(self.store.db.execute('PRAGMA quick_check').fetchone()[0], 'ok')
        self.assertEqual(self.store.audit()['cache'], 'matched')
        self.assertEqual(self.store.task('TWO')['proofs'][0]['id'], result['id'])
        for name, reader, cover in [('ONE', 'R', 'A1'), ('TWO', 'R2', 'B1')]:
            # TWO already has an ACKed candidate; record its independent read without a duplicate delivery.
            if name == 'TWO':
                rc, err = self.run_cli('review', '--actor', reader, '--task', name, '--verdict', 'accepted',
                                       '--report', str(self.report), '--covers', cover)
                self.assertEqual(rc, 0, err)
                self.store.append('close', 'C', {'task': name})
            else:
                self.close_task(name, reader, cover)
        self.assertEqual(cli('doctor').returncode, 0)


if __name__ == '__main__':
    unittest.main()
