"""Composite commands retain primitive events and real transport/ACK boundaries."""
import contextlib
import hashlib
import io
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import composite
import squadra
from registry import Store, latest_ping, plain

CLOCK = '2026-11-09T10:00:00+00:00'
SESSION = '12345678-1234-4567-8123-123456789abc'


class Composite(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sq-composite-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        home = self.base / 'home'; home.mkdir()
        env = patch.dict(os.environ, {'HOME': str(home), 'CODEX_HOME': str(home / 'codex'),
                                     'CLAUDE_CONFIG_DIR': str(home / 'claude'), 'GIT_CONFIG_NOSYSTEM': '1'})
        env.start(); self.addCleanup(env.stop)
        self.repo = self.base / 'repo with spaces'; self.repo.mkdir()
        for argv in [('init', '-q'), ('config', 'user.name', 'Synthetic'), ('config', 'user.email', 'test@example.invalid')]:
            self.git(*argv)
        (self.repo / 'code.py').write_text('VALUE = 1\n')
        self.git('add', '.'); self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'baseline')
        self.root = self.base / 'registry'
        self.scope = self.base / 'scope.txt'; self.scope.write_text('Preserve the synthetic value.\n')
        self.brief = self.base / 'brief.txt'; self.brief.write_text('Synthetic assignment\nA bounded useful change.\n')
        self.body = self.base / 'hello.txt'; self.body.write_text('I am the registered executor; awaiting an assignment.\n')
        self.proof = self.base / 'proof.json'; self.proof.write_text(json.dumps([sys.executable, '-B', '-c', 'print("ok")']))
        self.time = patch('registry.now', return_value=CLOCK); self.time.start(); self.addCleanup(self.time.stop)
        self.cli_time = patch('squadra.now', return_value=CLOCK); self.cli_time.start(); self.addCleanup(self.cli_time.stop)
        self.seed()

    def git(self, *argv):
        return subprocess.check_output(['git', *argv], cwd=self.repo, stderr=subprocess.PIPE)

    def seed(self):
        store = Store(self.root, create=True)
        try:
            store.append('init', 'system', {'name': 'Composite parity', 'project': str(self.repo), 'version': '3.14.0', 'lang': 'it',
                                          'limits': {'plan': 3, 'code': 2, 'build': 3}})
            for actor, role, client in [('C', 'coordinator', 'claude'), ('A', 'executor', 'codex'), ('R', 'reviewer', 'claude')]:
                store.append('agent', actor, {'id': actor, 'identity': actor + '-window', 'role': role, 'client': client,
                                             'repo': str(self.repo), 'session': '', 'model': '', 'skills': []})
            store.project()
        finally:
            store.close()

    def view(self):
        store = Store(self.root, read_only=True)
        try:
            return plain(store.view())
        finally:
            store.close()

    def events(self):
        store = Store(self.root, read_only=True)
        try:
            return [{key: row[key] for key in ('kind', 'actor', 'data')} for row in store.events()]
        finally:
            store.close()

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = squadra.main(['--root', str(self.root), *args])
        return rc, out.getvalue(), err.getvalue()

    @contextlib.contextmanager
    def identifiers(self):
        counter = itertools.count(1)
        with patch.object(uuid, 'uuid4', side_effect=lambda: uuid.UUID(int=next(counter))):
            yield

    def task_args(self):
        return ['--actor', 'C', '--id', 'Q', '--title', 'Synthetic change', '--owner', 'A', '--reviewer', 'R',
                '--repo', str(self.repo), '--scope-file', str(self.scope), '--proof-file', str(self.proof)]

    def join_args(self):
        return ['--id', 'B', '--identity', 'new-window', '--session', SESSION, '--role', 'executor',
                '--client', 'codex', '--repo', str(self.repo)]

    def assert_ok(self, *argv):
        rc, out, err = self.invoke(*argv)
        self.assertEqual(rc, 0, out + err)
        return out

    def fresh_second_registry(self):
        # Two new databases use the same path at different times, so root-bearing transport
        # instructions are comparable without normalising away any event data.
        self.root.rename(self.base / 'long-sequence-registry')
        self.seed()

    def test_assign_has_the_exact_events_of_task_brief_transport_checklist(self):
        with self.identifiers():
            self.assert_ok('task', *self.task_args())
            self.assert_ok('brief', '--actor', 'C', '--task', 'Q', '--file', str(self.brief))
            self.assert_ok('transport', '--actor', 'C', '--message', 'assign-Q', '--via', 'manual')
            self.assert_ok('checklist', '--moment', 'assign', '--actor', 'C', '--task', 'Q')
        expected = self.events()
        self.fresh_second_registry()
        with self.identifiers():
            out = self.assert_ok('assign', *self.task_args(), '--brief-file', str(self.brief), '--via', 'manual')
        self.assertEqual(self.events(), expected)
        self.assertTrue(json.loads(out)['ready'])
        self.assertLessEqual(len(out.encode()), 200)
        self.assertIsNone(self.view()['messages']['assign-Q']['ack'])

    def test_join_has_the_exact_events_of_registration_ping_and_transported_introduction(self):
        with self.identifiers():
            self.assert_ok('agent', *self.join_args())
            self.assert_ok('ping', '--actor', 'B', '--to', 'C')
            ping = latest_ping(self.view(), 'C')
            self.assert_ok('transport', '--actor', 'B', '--message', ping['id'], '--via', 'manual')
            self.assert_ok('send', '--actor', 'B', '--to', 'C', '--body-file', str(self.body))
            introduction = next(m['id'] for m in self.view()['messages'].values() if not m.get('ping'))
            self.assert_ok('transport', '--actor', 'B', '--message', introduction, '--via', 'manual')
        expected = self.events()
        self.fresh_second_registry()
        with self.identifiers():
            out = self.assert_ok('join', *self.join_args(), '--coordinator', 'C', '--body-file', str(self.body), '--via', 'manual')
        self.assertEqual(self.events(), expected)
        self.assertLessEqual(len(out.encode()), 200)
        state = self.view()
        self.assertEqual([a['id'] for a in state['agents'].values() if a['role'] == 'coordinator'], ['C'])
        self.assertTrue(all(m['ack'] is None for m in state['messages'].values()))
        self.assertEqual(state['agents']['B']['role'], 'executor')

    def test_pickup_preserves_registry_files_and_includes_real_sections_and_pending_alias_mail(self):
        store = Store(self.root)
        try:
            store.append('agent', 'A-read', {'id': 'A-read', 'identity': 'A-window', 'role': 'reviewer',
                                           'client': 'codex', 'repo': str(self.repo), 'skills': [], 'session': '', 'model': ''})
            store.append('message', 'C', {'id': 'pending', 'to': 'A-read', 'body': 'Pending recipient confirmation'})
            store.append('message', 'C', {'id': 'info', 'to': 'A', 'body': 'No ACK', 'info': True})
            store.project()
        finally:
            store.close()
        def files():
            return {str(p.relative_to(self.root)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                    for p in self.root.rglob('*') if p.is_file() and not p.name.endswith(('-wal', '-shm'))}
        before, events = files(), self.events()
        with patch('storage_policy.restore_projections', side_effect=AssertionError('pickup must not restore projections')):
            out = self.assert_ok('pickup', '--actor', 'A')
        self.assertEqual(self.events(), events)
        self.assertEqual(files(), before)
        self.assertIn('doctor --resume', out); self.assertIn('census', out)
        self.assertIn('In corso', out); self.assertIn('Da fare', out); self.assertIn('Fatti', out)
        self.assertIn('pending_mail', out); self.assertIn('Pending recipient confirmation', out)
        pending = json.loads(out[out.index('{"pending_mail"'):]) if '{"pending_mail"' in out else json.loads(out[out.rindex('{\n  "pending_mail"'):])
        self.assertEqual([m['id'] for m in pending['pending_mail']], ['pending'])

    def test_pickup_keeps_blockers_visible_and_does_not_resume_a_pause(self):
        self.assert_ok('pause', '--actor', 'C', '--reason', 'Owner stopped the workspace')
        before = self.events()
        rc, out, err = self.invoke('pickup', '--actor', 'A')
        self.assertIn('doctor --resume', out, err)
        self.assertEqual(self.events(), before)
        self.assertTrue(self.view()['paused'])
        self.assert_ok('resume', '--actor', 'C', '--reason', 'Owner resumed the workspace')
        self.assertFalse(self.view()['paused'])
        self.assertEqual(self.events()[-1]['kind'], 'resume')

    def test_pickup_unknown_actor_is_a_read_only_refusal(self):
        before = self.events()
        rc, out, err = self.invoke('pickup', '--actor', 'missing')
        self.assertEqual(rc, 1)
        self.assertIn('Registered pickup actor', err)
        self.assertEqual(self.events(), before)

    def test_join_refuses_a_missing_coordinator_before_registering_anyone(self):
        before = self.events()
        rc, out, err = self.invoke('join', *self.join_args(), '--coordinator', 'missing',
                                   '--body-file', str(self.body), '--via', 'manual')
        self.assertEqual(rc, 1)
        self.assertIn('existing active coordinator', err)
        self.assertEqual(self.events(), before)
        self.assertNotIn('B', self.view()['agents'])

    def test_assign_proof_tail_preserves_literal_task_named_options(self):
        args = self.task_args()
        at = args.index('--proof-file'); del args[at:at + 2]
        proof = [sys.executable, '-c', 'print("--owner task --scope-file")', '--owner', 'literal']
        self.assert_ok('assign', *args, '--brief-file', str(self.brief), '--via', 'manual', '--proof', '--', *proof)
        self.assertEqual(self.view()['tasks']['Q']['proof'], proof)

    def test_assign_keeps_the_primitive_guard_against_misplaced_task_options(self):
        args = self.task_args()
        at = args.index('--proof-file'); del args[at:at + 2]
        rc, out, err = self.invoke('assign', *args, '--brief-file', str(self.brief), '--via', 'manual',
                                   '--proof', sys.executable, '--owner', 'wrong-place')
        self.assertEqual(rc, 1)
        self.assertIn('Task option inside --proof', err)
        self.assertNotIn('Q', self.view()['tasks'])

    def test_assign_reports_a_transport_failure_without_retry_ack_or_rollback(self):
        self.assert_ok('agent', '--id', 'A', '--identity', 'A-window', '--role', 'executor',
                       '--client', 'codex', '--repo', str(self.repo), '--session', SESSION)
        with patch.object(squadra, 'codex_queue', return_value={'delivered': False, 'native_rc': 1,
                                                              'stage': 'queue', 'stderr': 'synthetic refusal'}) as transport:
            rc, out, err = self.invoke('assign', *self.task_args(), '--brief-file', str(self.brief), '--via', 'codex-queue')
        self.assertEqual(rc, 1)
        self.assertEqual(transport.call_count, 1)
        self.assertIn('"step": "transport"', out)
        self.assertIn('"completed": ["task", "brief"]', out)
        state = self.view()
        self.assertIn('Q', state['tasks'])
        self.assertIsNone(state['messages']['assign-Q']['ack'])
        self.assertIs(state['messages']['assign-Q']['transport']['delivered'], False)


if __name__ == '__main__':
    unittest.main()
