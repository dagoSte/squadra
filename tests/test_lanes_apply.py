"""Lane assignment uses real Store transactions and synthetic analysis, never real queues."""
import contextlib
import concurrent.futures
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import lanes_apply
import registry
import squadra
from registry import Error, Store, plain


class LaneApplication(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='lane-apply-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.repo = self.base / 'integration'
        self.repo.mkdir()
        self.env = os.environ | {'HOME': str(self.base), 'GIT_CONFIG_NOSYSTEM': '1'}
        self.git(self.repo, 'init', '-q')
        self.git(self.repo, 'config', 'user.name', 'Synthetic')
        self.git(self.repo, 'config', 'user.email', 'test@example.invalid')
        for name in ('a.py', 'b.py', 'c.py', 'hub.py', 'tests/test_a.py', 'tests/test_b.py'):
            path = self.repo / name
            path.parent.mkdir(exist_ok=True)
            path.write_text('VALUE = 1\n')
        self.git(self.repo, 'add', '.')
        self.git(self.repo, '-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture')
        self.head = self.git(self.repo, 'rev-parse', 'HEAD').strip()
        self.store = Store(self.base / 'registry', create=True)
        self.addCleanup(self.store.close)
        self.store.append('init', 'system', {'name': 'Synthetic lanes', 'project': str(self.repo),
                          'version': registry.VERSION, 'limits': {'plan': 3, 'code': 2, 'build': 3}})
        self.agent('C', 'coordinator', 'coord', 'claude', self.repo)
        self.agent('R', 'reviewer', 'reader', 'claude', self.repo)
        for name in ('A', 'B'):
            target = self.base / name
            self.git(self.base, 'clone', '-q', '--no-hardlinks', str(self.repo), str(target))
            self.agent(name, 'builder', name, 'codex', target)
            self.agent(name + '-read', 'reviewer', name, 'codex', target)
        self.scope = self.base / 'scope.txt'
        self.scope.write_text('Synthetic parent contract\n')
        self.task = {'id': 'P', 'title': 'Synthetic parent', 'owner': 'C', 'repo': str(self.repo),
                     'base': self.head, 'scope': self.store.artifact(self.scope), 'proof': [sys.executable, '-B', '-c', 'print(1)'],
                     'expected': 0, 'full_gate': False, 'depends': [], 'requirements': [],
                     'reviewer': 'A-read', 'delivery_policy': 'frozen-v1', 'cross_client': True}
        self.store.batch([('task', 'C', self.task, None),
                          ('message', 'C', lanes_apply.assignment(self.task), None)])
        self.native = patch.object(squadra, 'codex_queue', return_value={'delivered': True, 'native_rc': 0})
        self.queue = self.native.start()
        self.addCleanup(self.native.stop)

    def git(self, repo, *args):
        return subprocess.check_output(['git', *args], cwd=repo, env=self.env, stderr=subprocess.PIPE).decode()

    def agent(self, name, role, identity, client, repo):
        self.store.append('agent', name, {'id': name, 'role': role, 'identity': identity, 'client': client,
                          'repo': str(repo), 'skills': [], 'session': 'synthetic-' + name})

    def partition(self, shared=True, third=False):
        rows = [{'id': 'L1', 'files': ['a.py', 'tests/test_a.py'], 'tests': ['tests/test_a.py'], 'lines': 100},
                {'id': 'L2', 'files': ['b.py', 'tests/test_b.py'], 'tests': ['tests/test_b.py'], 'lines': 80}]
        if third:
            rows.append({'id': 'L3', 'files': ['c.py'], 'tests': [], 'lines': 10})
        common = ['hub.py'] if shared else []
        return {'form': 'squadra-lanes/1', 'repo': str(self.repo), 'base': self.head,
                'files': sorted(common + [p for row in rows for p in row['files']]), 'shared': common,
                'lanes': rows, 'depends': {}, 'cross_edges': [], 'reasons': ['Synthetic independent groups']}

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = squadra.main(['--root', str(self.store.root), *argv])
        return code, out.getvalue(), err.getvalue()

    def analysis(self, value=None):
        module = types.ModuleType('lanes')
        module.analyze = unittest.mock.Mock(return_value={'repo': str(self.repo)})
        module.partition = unittest.mock.Mock(return_value=value or self.partition())
        return module

    def test_LB2_LB4_assigns_other_model_readers_and_atomic_contract_dependencies(self):
        before = self.store.events()
        planned = lanes_apply.plan(self.store, 'P', self.partition())
        self.assertEqual(self.store.events(), before)
        self.assertEqual(planned, lanes_apply.plan(self.store, 'P', self.partition()))
        result = lanes_apply.apply(self.store, 'C', 'P', planned)
        self.assertEqual(result['created'], ['P.L0', 'P.L1', 'P.L2'])
        state = plain(self.store.view())
        owners = []
        for name in result['created']:
            task = state['tasks'][name]
            builder, reader = [state['agents'][task[field]] for field in ['owner', 'reviewer']]
            self.assertNotEqual(builder['client'], reader['client'])
            self.assertNotEqual(builder['identity'], reader['identity'])
            self.assertEqual(task['proof'], self.task['proof'])
            self.assertEqual(task['base'], self.head)
            self.assertIsNone(state['messages']['assign-' + name]['ack'])
            if name != 'P.L0':
                owners.append(builder['identity'])
                self.assertEqual(task['depends'], ['P.L0'])
                body = (self.store.root / 'artifacts' / task['scope']['sha256']).read_text()
                self.assertIn('tests/test_', body)
        self.assertEqual(len(owners), len(set(owners)))
        decision = state['decisions'][-1]
        self.assertEqual(decision['id'], 'lanes-P')
        self.assertEqual(json.loads((self.store.root / 'artifacts' / decision['plan']['sha256']).read_text()), planned)
        transports = [e['data'] for e in self.store.events() if e['kind'] == 'transport']
        self.assertEqual([r['via'] for r in transports].count('codex-queue'), 2)
        self.assertEqual(self.queue.call_count, 2)
        with self.assertRaisesRegex(Error, 'plan changed|already exist'):
            lanes_apply.apply(self.store, 'C', 'P', planned)
        self.assertEqual(self.queue.call_count, 2)

    def test_LB3_merges_the_smallest_groups_and_keeps_tests_with_their_module(self):
        planned = lanes_apply.plan(self.store, 'P', self.partition(shared=False, third=True))
        self.assertEqual(len(planned['lanes']), 2)
        merged = next(r for r in planned['lanes'] if 'c.py' in r['files'])
        self.assertEqual(merged['files'], ['b.py', 'c.py', 'tests/test_b.py'])
        self.assertEqual(merged['tests'], ['tests/test_b.py'])

    def test_LB2_no_reader_or_author_alias_never_becomes_an_implicit_same_client_read(self):
        self.store.append('availability', 'C', {'agent': 'R', 'status': 'stand-by', 'reason': 'Synthetic'})
        self.store.append('availability', 'C', {'agent': 'C', 'status': 'stand-by', 'reason': 'Synthetic'})
        planned = lanes_apply.plan(self.store, 'P', self.partition())
        self.assertEqual(planned['lanes'], [])
        self.assertTrue(any('no independent reader' in r for r in planned['reasons']))

    def test_LB3_busy_identity_aliases_standby_and_same_checkout_are_excluded(self):
        busy = {**self.task, 'id': 'busy', 'owner': 'A', 'reviewer': 'R'}
        self.store.append('task', 'C', busy)
        planned = lanes_apply.plan(self.store, 'P', self.partition())
        self.assertEqual(planned['lanes'], [])
        self.assertTrue(any('already owns' in r for r in planned['reasons']))
        # A second builder sharing B's checkout cannot create an additional simultaneous lane.
        self.agent('D', 'builder', 'D', 'codex', self.base / 'B')
        planned = lanes_apply.plan(self.store, 'P', self.partition())
        self.assertEqual(planned['lanes'], [])
        self.assertTrue(any('Checkout already used' in r for r in planned['reasons']))

    def test_LB3_an_unrelated_checkout_is_not_a_builder_for_this_project(self):
        other = self.base / 'other'
        other.mkdir()
        self.git(other, 'init', '-q')
        self.git(other, '-c', 'user.name=Other', '-c', 'user.email=other@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '-qm', 'other project')
        self.agent('B', 'builder', 'B', 'codex', other)
        planned = lanes_apply.plan(self.store, 'P', self.partition())
        self.assertEqual(planned['lanes'], [])
        self.assertTrue(any('another project' in r for r in planned['reasons']))

    def test_LB3_standby_covers_every_seat_of_the_builder_identity(self):
        self.store.append('availability', 'C', {'agent': 'A-read', 'status': 'stand-by', 'reason': 'Synthetic'})
        planned = lanes_apply.plan(self.store, 'P', self.partition(shared=False))
        self.assertEqual(planned['lanes'], [])
        self.assertTrue(any('Fewer than two available' in r for r in planned['reasons']))

    def test_LB4_failure_in_the_middle_of_a_batch_leaves_no_tasks_or_messages(self):
        planned = lanes_apply.plan(self.store, 'P', self.partition())
        before = self.store.events()
        original = registry.validate

        def fail(state, kind, actor, data, **kwargs):
            if kind == 'task' and data['id'] == 'P.L2':
                raise Error('Synthetic middle-of-batch failure')
            return original(state, kind, actor, data, **kwargs)

        with patch.object(registry, 'validate', side_effect=fail):
            with self.assertRaisesRegex(Error, 'middle-of-batch'):
                lanes_apply.apply(self.store, 'C', 'P', planned)
        self.assertEqual(self.store.events(), before)
        self.assertEqual(set(self.store.view()['tasks']), {'P'})
        self.queue.assert_not_called()

    def test_LB4_changed_assignment_is_refused_before_delivery(self):
        planned = lanes_apply.plan(self.store, 'P', self.partition())
        planned['lanes'][-1]['owner'] = 'C'
        with self.assertRaisesRegex(Error, 'plan changed'):
            lanes_apply.apply(self.store, 'C', 'P', planned)
        self.queue.assert_not_called()

    def test_LB1_task_paths_creates_parent_and_lanes_with_a_stub_analysis(self):
        with patch.dict(sys.modules, {'lanes': self.analysis()}):
            code, out, err = self.cli('task', '--actor', 'C', '--id', 'NEW', '--title', 'New parent',
                '--owner', 'C', '--reviewer', 'A-read', '--repo', str(self.repo), '--scope-file', str(self.scope),
                '--paths', '*.py', '--paths', 'tests/*.py')
        self.assertEqual(code, 0, err)
        self.assertTrue({'NEW', 'NEW.L0', 'NEW.L1', 'NEW.L2'} <= set(self.store.view()['tasks']))
        self.assertEqual(json.JSONDecoder().raw_decode(out)[0]['created'], ['NEW.L0', 'NEW.L1', 'NEW.L2'])

    def test_LB1_lanes_none_does_not_load_analysis_or_create_children(self):
        with patch.dict(sys.modules, {'lanes': None}):
            code, _, err = self.cli('task', '--actor', 'C', '--id', 'single', '--title', 'Single task',
                '--owner', 'A', '--reviewer', 'R', '--repo', str(self.repo), '--scope-file', str(self.scope),
                '--paths', '*.py', '--lanes', 'none')
        self.assertEqual(code, 0, err)
        self.assertEqual(set(self.store.view()['tasks']), {'P', 'single'})
        self.queue.assert_not_called()

    def test_LB1_task_cannot_reuse_an_existing_parent_id_to_apply_lanes(self):
        with patch.dict(sys.modules, {'lanes': self.analysis()}):
            code, _, err = self.cli('task', '--actor', 'C', '--id', 'P', '--title', 'Duplicate parent',
                '--owner', 'C', '--reviewer', 'A-read', '--repo', str(self.repo), '--scope-file', str(self.scope), '--paths', '*.py')
        self.assertEqual(code, 1)
        self.assertIn('Task ID already exists', err)
        self.assertEqual(set(self.store.view()['tasks']), {'P'})
        self.queue.assert_not_called()

    def test_LB4_automatic_parent_also_rolls_back_when_a_later_lane_fails(self):
        original = registry.validate

        def fail(state, kind, actor, data, **kwargs):
            if kind == 'task' and data['id'] == 'NEW.L2':
                raise Error('Synthetic later lane fails')
            return original(state, kind, actor, data, **kwargs)

        with patch.dict(sys.modules, {'lanes': self.analysis()}), patch.object(registry, 'validate', side_effect=fail):
            code, _, err = self.cli('task', '--actor', 'C', '--id', 'NEW', '--title', 'New parent',
                '--owner', 'C', '--reviewer', 'A-read', '--repo', str(self.repo), '--scope-file', str(self.scope), '--paths', '*.py')
        self.assertEqual(code, 1)
        self.assertIn('later lane fails', err)
        self.assertEqual(set(self.store.view()['tasks']), {'P'})
        self.assertEqual(set(self.store.view()['messages']), {'assign-P'})
        self.queue.assert_not_called()

    def test_LB4_queue_failure_is_recorded_after_committed_tasks_and_keeps_ack_pending(self):
        self.queue.side_effect = [{'delivered': False, 'native_rc': 1}, {'delivered': True, 'native_rc': 0}]
        planned = lanes_apply.plan(self.store, 'P', self.partition(shared=False))
        result = lanes_apply.apply(self.store, 'C', 'P', planned)
        self.assertEqual(result['created'], ['P.L1', 'P.L2'])
        self.assertFalse(result['transport'][0]['delivered'])
        state = plain(self.store.view())
        for name in result['created']:
            self.assertIsNone(state['messages']['assign-' + name]['ack'])
        self.assertFalse(state['messages']['assign-P.L1']['transport']['delivered'])

    def test_LB3_concurrent_applications_cannot_assign_the_same_free_builders_twice(self):
        self.store.append('task', 'C', {**self.task, 'id': 'Q'})
        plans = {name: lanes_apply.plan(self.store, name, self.partition(shared=False)) for name in ['P', 'Q']}
        start = threading.Barrier(2, timeout=15)

        def worker(name):
            connection = Store(self.store.root)
            try:
                start.wait()
                try:
                    lanes_apply.apply(connection, 'C', name, plans[name])
                    return 'applied'
                except Error as exc:
                    return str(exc)
            finally:
                connection.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, ['P', 'Q']))
        self.assertEqual(results.count('applied'), 1, results)
        self.assertTrue(any('plan changed' in r for r in results), results)
        children = [t for name, t in self.store.view()['tasks'].items() if '.L' in name]
        self.assertEqual(len(children), 2)
        self.assertEqual(len({t['owner'] for t in children}), 2)

    def test_LB5_preview_is_read_only_and_apply_requires_the_parent_coordinator(self):
        before = self.store.events()
        with patch.dict(sys.modules, {'lanes': self.analysis()}):
            code, out, err = self.cli('lanes', '--task', 'P', '--paths', '*.py')
            self.assertEqual(code, 0, err)
            self.assertTrue(json.JSONDecoder().raw_decode(out)[0]['dry_run'])
            self.assertEqual(self.store.events(), before)
            code, _, _ = self.cli('lanes', '--actor', 'A', '--task', 'P', '--paths', '*.py', '--apply')
            self.assertEqual(code, 1)
            code, _, _ = self.cli('lanes', '--task', 'P', '--paths', '*.py', '--apply')
            self.assertEqual(code, 1)
            self.assertEqual(set(self.store.view()['tasks']), {'P'})
            self.assertTrue(all(e['kind'] == 'diagnostic' for e in self.store.events()[len(before):]))
            code, _, err = self.cli('lanes', '--actor', 'C', '--task', 'P', '--paths', '*.py', '--apply')
            self.assertEqual(code, 0, err)

    def test_LB1_non_coordinator_parent_stays_one_task_with_a_reason(self):
        with patch.dict(sys.modules, {'lanes': self.analysis()}):
            code, out, err = self.cli('task', '--actor', 'C', '--id', 'single', '--title', 'Single task',
                '--owner', 'A', '--reviewer', 'R', '--repo', str(self.repo), '--scope-file', str(self.scope), '--paths', '*.py')
        self.assertEqual(code, 0, err)
        result = json.JSONDecoder().raw_decode(out)[0]
        self.assertEqual(result['created'], [])
        self.assertTrue(any('not owned by the coordinator' in r for r in result['reasons']))
        self.assertEqual(self.store.view()['tasks']['single']['owner'], 'A')

    def test_LB5_missing_analysis_has_a_plain_refusal_and_never_creates_a_parent(self):
        with patch.dict(sys.modules, {'lanes': None}):
            code, _, err = self.cli('task', '--actor', 'C', '--id', 'bad', '--title', 'Missing analysis',
                '--owner', 'C', '--reviewer', 'A-read', '--repo', str(self.repo), '--scope-file', str(self.scope), '--paths', '*.py')
        self.assertEqual(code, 1)
        self.assertIn('analysis module', err)
        self.assertNotIn('Traceback', err)
        self.assertNotIn('bad', self.store.view()['tasks'])

    def test_LB1_unknown_parent_owner_has_a_plain_refusal_and_no_partial_tasks(self):
        with patch.dict(sys.modules, {'lanes': self.analysis()}):
            code, _, err = self.cli('task', '--actor', 'C', '--id', 'bad', '--title', 'Unknown owner',
                '--owner', 'missing', '--repo', str(self.repo), '--scope-file', str(self.scope), '--paths', '*.py')
        self.assertEqual(code, 1)
        self.assertIn('Task owner not registered', err)
        self.assertNotIn('Traceback', err)
        self.assertEqual(set(self.store.view()['tasks']), {'P'})
        self.queue.assert_not_called()

    def test_LB4_overlap_and_fixture_paths_are_rejected(self):
        for filename in ('a.py', 'tests/fixtures/old.py', './tests/fixtures/old.py', '../outside.py'):
            value = self.partition()
            value['shared'].append(filename)
            with self.assertRaisesRegex(Error, 'overlap|fixture'):
                lanes_apply.plan(self.store, 'P', value)


if __name__ == '__main__':
    unittest.main()
