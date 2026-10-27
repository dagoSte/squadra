"""Recipient-local permission evidence, including refusal and uncertain context."""
import contextlib
import copy
import errno
import io
import json
import os
import subprocess
from pathlib import Path
import unittest
from unittest.mock import patch

import test_squadra as support
from access import run, write_probe
from registry import Error, access_view, board, presence, now
from squadra import main


class AccessProbe(unittest.TestCase):
    git = support.Workflow.git
    task = support.Workflow.task

    def setUp(self):
        support.Workflow.setUp(self)
        agent = dict(self.store.state()['agents']['A'])
        agent.pop('updated', None)
        agent['session'] = 'recipient-session'
        self.store.append('agent', 'A', agent)
        self.environment = patch.dict(os.environ, {'CODEX_THREAD_ID': 'recipient-session'})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def probe(self, **kw):
        return run(self.root, 'A', 'probe', str(self.repo), 'recipient-session', kw.get('context', 'sandbox'))

    def test_both_writable_real_probe_and_cleanup_format_replay_cache(self):
        before = sorted(self.repo.iterdir())
        result = self.probe()
        self.assertTrue(result['recorded'])
        self.assertEqual(result['access']['registry']['status'], 'verified')
        self.assertEqual(result['access']['clone']['status'], 'verified')
        self.assertEqual(before, sorted(self.repo.iterdir()))
        self.assertFalse(list(self.root.glob('.squadra-access-*')))
        self.assertEqual(self.store.events()[-1]['format'], 4)
        self.assertEqual(self.store.state(), dict(self.store.resolved()))
        self.assertEqual(access_view(self.store.view(), 'A'), access_view(self.store.state(), 'A'))
        self.store.audit()

    def test_registry_writable_clone_denied_is_visible_without_blocking_reader(self):
        original = write_probe
        def probe(path):
            if Path(path) == self.repo:
                return {'path': str(path), 'outcome': 'denied', 'error': 'EACCES'}
            return original(path)
        with patch('access.write_probe', side_effect=probe):
            result = self.probe()
        self.assertEqual(result['access']['registry']['status'], 'verified')
        self.assertEqual(result['access']['clone']['status'], 'denied')
        self.assertIn('clone: denied', board(self.store.view(), lang='en'))
        self.assertIn('clone: negato', board(self.store.view(), lang='it'))
        self.assertEqual(next(row for row in presence(self.store.view(), now()) if row['agent'] == 'A')['access']['clone']['status'], 'denied')
        self.assertEqual(self.store.state()['tasks']['T']['status'], 'waiting-ack')

    def test_registry_denied_returns_receipt_without_inventing_a_save(self):
        before = self.store.events()
        original = write_probe
        def probe(path):
            if Path(path) == self.root:
                return {'path': str(path), 'outcome': 'denied', 'error': 'EPERM'}
            return original(path)
        with patch('access.write_probe', side_effect=probe):
            result = self.probe()
        self.assertFalse(result['recorded'])
        self.assertEqual(result['receipt']['registry']['outcome'], 'denied')
        self.assertEqual(before, self.store.events())

    def test_missing_probe_status_is_read_only_and_not_verified(self):
        before = self.store.events()
        result = run(self.root, 'A', 'status', str(self.repo))
        self.assertEqual(result['clone']['status'], 'not-verified')
        self.assertEqual(result['registry']['status'], 'not-verified')
        self.assertEqual(before, self.store.events())
        self.assertIn('not verified', board(self.store.view(), lang='en'))

    def test_escalated_or_unknown_context_never_claims_sandbox_access(self):
        for context in ('escalated', 'unknown'):
            with self.subTest(context=context):
                result = self.probe(context=context)
                self.assertEqual(result['receipt']['clone']['outcome'], 'writable')
                self.assertEqual(result['access']['clone']['status'], 'not-verified')
                self.assertEqual(result['access']['registry']['status'], 'not-verified')

    def test_unobserved_session_is_not_verified_and_other_window_is_refused(self):
        with patch.dict(os.environ, {'CODEX_THREAD_ID': ''}):
            self.assertEqual(self.probe()['access']['clone']['status'], 'not-verified')
        before = self.store.events()
        with patch.dict(os.environ, {'CODEX_THREAD_ID': 'coordinator-session'}):
            with self.assertRaisesRegex(Error, 'recipient session'):
                self.probe()
        self.assertEqual(before, self.store.events())

    def test_changed_session_repo_or_requested_path_invalidates_claim(self):
        self.probe()
        state = self.store.state()
        self.assertEqual(access_view(state, 'A', repo=str(self.base))['clone']['status'], 'not-verified')
        self.assertEqual(access_view(state, 'A', root=str(self.base))['registry']['status'], 'not-verified')
        state['agents']['A']['session'] = 'replacement-session'
        self.assertEqual(access_view(state, 'A')['clone']['status'], 'not-verified')
        state['agents']['A']['session'] = 'recipient-session'
        state['agents']['A']['repo'] = str(self.base)
        self.assertEqual(access_view(state, 'A')['clone']['status'], 'not-verified')
        self.assertEqual(access_view(state, 'R')['clone']['status'], 'not-verified')

    def test_alias_with_same_session_sees_evidence_without_changing_role(self):
        agent = dict(self.store.state()['agents']['A'])
        agent.pop('updated', None)
        agent.update(id='reader-alias', role='reviewer')
        self.store.append('agent', 'reader-alias', agent)
        self.probe()
        self.assertEqual(access_view(self.store.view(), 'reader-alias')['clone']['status'], 'verified')
        self.assertEqual(self.store.view()['agents']['reader-alias']['role'], 'reviewer')

    def test_actual_permission_error_and_unknown_io_error_are_distinct(self):
        for code, expected in ((errno.EACCES, 'denied'), (errno.EPERM, 'denied'),
                               (errno.EROFS, 'denied'), (errno.EIO, 'unknown')):
            with patch('access.tempfile.mkstemp', side_effect=OSError(code, 'synthetic')):
                self.assertEqual(write_probe(self.repo)['outcome'], expected)
        self.assertEqual(write_probe(self.repo / 'missing')['outcome'], 'unknown')

    def test_cleanup_error_prevents_success(self):
        with patch('access.os.unlink', side_effect=PermissionError(errno.EACCES, 'synthetic')):
            result = write_probe(self.repo)
        self.assertEqual(result['outcome'], 'unknown')
        self.assertEqual(result['error'], 'cleanup-failed')
        for path in self.repo.glob('.squadra-access-*'):
            path.unlink()

    def test_invalid_receipts_refused_by_store(self):
        receipt = self.probe()['receipt']
        for change in ({'session': 'someone-else'}, {'observed_session': 'someone-else'},
                       {'context': 'trusted'}, {'identity': 'coord'}):
            with self.subTest(change=change), self.assertRaises(Error):
                self.store.append('access.probe', 'A', {**receipt, **change})
        forged = copy.deepcopy(receipt)
        forged['clone']['status'] = 'verified'
        with self.assertRaises(Error):
            self.store.append('access.probe', 'A', forged)

    def test_dashboard_renders_all_access_states_in_both_languages(self):
        self.probe()
        state = self.store.state()
        state['presence'] = presence(state, now())
        fixture = self.base / 'access-ui.json'
        fixture.write_text(json.dumps([state]))
        source = Path(__file__).resolve().parents[1]
        harness = (source / 'tests/presence_dashboard.js').read_text().split('(async()=>{', 1)[0]
        assertions = r"""
(async()=>{
 for(const block of blocks)vm.runInContext(block.script,context,{timeout:1000});
 await new Promise(r=>setTimeout(r,20));
 const panel=()=>text(vm.runInContext('presencePanel()',context));
 assert.match(panel(),/scrittura registro: verificato/);
 assert.match(panel(),/scrittura clone: verificato/);
 assert.match(panel(),/scrittura clone: non verificato/);
 fixtures[0].presence.find(a=>a.agent==='A').access.clone.status='denied';
 await timers.shift()();
 assert.match(panel(),/scrittura clone: negato/);
 vm.runInContext("UI_LANG='en';render()",context);
 assert.match(panel(),/registry write: verified/);
 assert.match(panel(),/clone write: denied/);
 assert.match(panel(),/clone write: not verified/);
 assert.match(panel(),/observed/);
 console.log('access UI verified');
})().catch(e=>{console.error(e);process.exitCode=1});
"""
        script = self.base / 'access-ui.js'
        script.write_text(harness + assertions)
        result = subprocess.run(['node', str(script), str(source / 'assets/dashboard.html'), str(fixture)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cli_status_and_probe_return_json(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = main(['access', 'probe', '--actor', 'A', '--session', 'recipient-session',
                       '--context', 'sandbox', '--repo', str(self.repo), '--root', str(self.root)])
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(output.getvalue())['recorded'])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = main(['access', 'status', '--actor', 'A', '--repo', str(self.repo), '--root', str(self.root)])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(output.getvalue())['clone']['status'], 'verified')


if __name__ == '__main__':
    unittest.main()
