"""Reproduce the independent Claude findings at the public command boundaries."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_workflow_improvements as improved
from registry import Store


class ReviewFixes(unittest.TestCase):
    setUp = improved.Improvements.setUp
    cli = improved.Improvements.cli
    refuse = improved.Improvements.refuse
    state = improved.Improvements.state
    task = improved.Improvements.task
    start = improved.Improvements.start
    deliver = improved.Improvements.deliver
    review = improved.Improvements.review
    rule = improved.Improvements.rule

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.repo).decode().strip()

    def test_damaged_copy_is_readonly_scoped_and_explicitly_repaired(self):
        self.task(); self.start(); self.deliver()
        task = self.state()['tasks']['T']; frozen = Path(task['delivery']['frozen']['repo'])
        self.assertEqual(frozen.stat().st_mode & 0o222, 0)
        self.assertEqual((frozen / 'value.txt').stat().st_mode & 0o222, 0)
        frozen.chmod(0o755); (frozen / 'stray.txt').write_text('Reader pollution')
        refusal = self.refuse('review', '--actor', 'R', '--task', 'T', '--verdict', 'accepted', '--report', str(self.report))
        for expected in ('task T', task['candidate']['sha256'], 'stray.txt', str(frozen)):
            self.assertIn(expected, refusal)
        self.assertEqual(json.loads(self.cli('doctor'))['candidate_integrity'], 'degraded')
        self.cli('export')
        self.cli('checkpoint', '--to', str(self.base / 'damaged-checkpoint'))
        self.cli('save', '--actor', 'C', '--to', str(self.base / 'damaged-save'), '--temporary')
        self.cli('candidate', 'repair', '--actor', 'C', '--task', 'T', '--reason', 'Recover demonstrated reader pollution')
        output = self.state()['outputs'][-1]['candidate_action']
        self.assertTrue((Path(output['quarantine']) / 'repo/stray.txt').is_file())
        self.assertFalse((frozen / 'stray.txt').exists())
        self.assertEqual(json.loads(self.cli('doctor'))['candidate_integrity'], 'passed')
        self.review()

    def test_missing_and_retired_copies_have_consistent_continuity_reports(self):
        from frozen_candidate import remove
        self.task(); self.start(); self.deliver(); self.review()
        self.cli('close', '--actor', 'C', '--task', 'T')
        path = Path(self.state()['tasks']['T']['delivery']['frozen']['repo'])
        remove(path.parent)
        self.assertEqual(json.loads(self.cli('doctor'))['candidates'][0]['status'], 'missing')
        store = Store(self.root)
        try:
            report = {}; store.checkpoint(self.base / 'missing-checkpoint', report=report)
            self.assertEqual(report['candidates'][0]['status'], 'missing')
        finally:
            store.close()
        self.cli('candidate', 'repair', '--actor', 'C', '--task', 'T', '--reason', 'Restore exact source')
        self.cli('candidate', 'retire', '--actor', 'C', '--task', 'T', '--reason', 'Closed delivery retention')
        doctor = json.loads(self.cli('doctor'))
        self.assertEqual((doctor['candidate_integrity'], doctor['candidates'][0]['status']), ('passed', 'retired'))
        self.cli('checkpoint', '--to', str(self.base / 'retired-checkpoint'))

    def test_storage_limit_refuses_before_fetch_and_live_retirement_is_refused(self):
        self.task(); self.start()
        self.cli('prove', '--actor', 'A', '--task', 'T', '--', sys.executable, '-c', 'pass')
        with patch('frozen_candidate.MAX_COPY_BYTES', 1):
            self.assertIn('storage limit', self.refuse('deliver', '--actor', 'A', '--task', 'T'))
        self.cli('deliver', '--actor', 'A', '--task', 'T')
        self.assertIn('active tasks', self.refuse('candidate', 'retire', '--actor', 'C', '--task', 'T', '--reason', 'Too soon'))

    def test_storage_quota_is_explicitly_configurable_by_coordinator(self):
        self.assertIn('coordinator', self.refuse('candidate', 'limits', '--actor', 'A', '--reason', 'Invalid actor',
                      '--max-copy-mib', '1024', '--max-total-mib', '4096'))
        self.cli('candidate', 'limits', '--actor', 'C', '--reason', 'Larger synthetic project',
                 '--max-copy-mib', '1024', '--max-total-mib', '4096')
        self.assertEqual(self.state()['outputs'][-1]['candidate_action']['max_total_bytes'], 4096 * 1024 * 1024)

    def test_freeze_preserves_detached_old_head_linked_worktree_and_shallow_sources(self):
        from frozen_candidate import freeze, measured
        from registry import snapshot
        self.git('config', 'user.name', 'Synthetic'); self.git('config', 'user.email', 's@example.invalid')
        original = self.git('rev-parse', 'HEAD')
        self.git('checkout', '-qb', 'feature/odd-name.123')
        (self.repo / 'value.txt').write_text('Next head')
        self.git('-c', 'commit.gpgsign=false', 'commit', '-qam', 'Next')
        self.git('checkout', '--detach', original)
        linked = self.base / 'linked'; self.git('worktree', 'add', '--detach', str(linked), original)
        shallow = self.base / 'shallow'
        subprocess.run(['git', 'clone', '--quiet', '--depth=1', self.repo.as_uri(), str(shallow)], check=True, capture_output=True)
        store = Store(self.root)
        try:
            for source in (self.repo, linked, shallow):
                expected = snapshot(source)
                record = freeze(store, {'id': 'edge', 'repo': str(source)}, expected)
                self.assertEqual(measured({'repo': str(source)}, Path(record['repo'])), expected)
        finally:
            store.close()

    def test_transfer_recomputes_same_client_reason_in_both_directions(self):
        store = Store(self.root)
        try:
            for actor, client in [('A', 'codex'), ('R', 'codex'), ('Other', 'claude')]:
                data = dict(store.view()['agents'][actor]); data['client'] = client
                store.append('agent', actor, data)
        finally:
            store.close()
        self.cli('task', '--actor', 'C', '--id', 'T', '--title', 'Cross-client transfer', '--owner', 'A',
                 '--repo', str(self.repo), '--scope-file', str(self.scope), '--proof-file', str(self.proof),
                 '--reviewer', 'R', '--same-client-reason', 'Independent Claude temporarily unavailable')
        self.start(); self.deliver()
        self.cli('reassign', '--actor', 'C', '--task', 'T', '--reviewer', 'Other', '--reason', 'Claude available')
        self.assertIsNone(self.state()['tasks']['T'].get('same_client_reason'))
        self.assertTrue(any(m['to'] == 'R' and m.get('info') and 'reassigned' in m['body'] for m in self.state()['messages'].values()))
        self.assertIn('--same-client-reason', self.refuse('reassign', '--actor', 'C', '--task', 'T', '--reviewer', 'R', '--reason', 'Claude occupied'))
        self.cli('reassign', '--actor', 'C', '--task', 'T', '--reviewer', 'R', '--reason', 'Claude occupied',
                 '--same-client-reason', 'Only available independent reader')
        self.assertEqual(self.state()['tasks']['T']['same_client_reason'], 'Only available independent reader')

    def test_refused_commit_never_stages_own_rule(self):
        rule = self.rule()
        (self.repo / 'foreign.txt').write_text('Other seat')
        self.git('add', 'foreign.txt')
        hook = self.repo / '.git/hooks/pre-commit'; hook.write_text('#!/bin/sh\nexit 1\n'); hook.chmod(0o755)
        self.refuse('migliora', 'apply', '--actor', 'A', '--task', 'T', '--rule-file', str(rule))
        self.assertEqual(self.git('diff', '--cached', '--name-only'), 'foreign.txt')
        self.assertTrue((self.repo / 'AGENTS.md').is_file())
        self.assertEqual(self.state()['outputs'][-1]['migliora']['status'], 'refused')

    def test_hook_generated_commit_is_recorded_without_staged_deletion(self):
        rule = self.rule()
        (self.repo / 'foreign.txt').write_text('Other seat'); self.git('add', 'foreign.txt')
        hook = self.repo / '.git/hooks/pre-commit'
        hook.write_text('#!/bin/sh\nprintf generated > generated.txt\ngit add generated.txt\n'); hook.chmod(0o755)
        self.assertIn('needs reconciliation', self.refuse('migliora', 'apply', '--actor', 'A', '--task', 'T', '--rule-file', str(rule)))
        result = self.state()['outputs'][-1]['migliora']
        self.assertEqual(result['commit'], self.git('rev-parse', 'HEAD'))
        self.assertEqual(result['files'], ['AGENTS.md', 'generated.txt'])
        self.assertTrue(result['committed']); self.assertFalse(result['applied'])
        self.assertEqual(self.git('diff', '--cached', '--name-only'), 'foreign.txt')

    def test_rule_markers_in_check_argv_are_rejected(self):
        rule = self.rule([sys.executable, '-c', "print('<!-- squadra:migliora:end -->')"])
        self.assertIn('markers', self.refuse('migliora', 'apply', '--actor', 'A', '--task', 'T', '--rule-file', str(rule)))
        self.assertFalse((self.repo / 'AGENTS.md').exists())

    def test_check_that_moves_head_restores_own_unchanged_instruction(self):
        rule = self.rule(['git', '-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '-qm', 'Check moves HEAD'])
        self.assertIn('HEAD changed', self.refuse('migliora', 'apply', '--actor', 'A', '--task', 'T', '--rule-file', str(rule)))
        self.assertFalse((self.repo / 'AGENTS.md').exists())

    def test_restarted_follower_reports_pending_ids_without_a_wake_loop(self):
        body = self.base / 'mail.txt'; body.write_text('Read and ACK')
        self.cli('send', '--actor', 'C', '--to', 'R', '--id', 'unacked', '--body-file', str(body))
        with patch.dict(os.environ, {'XDG_STATE_HOME': str(self.base / 'state')}):
            self.cli('inbox', '--agent', 'R', '--follow', '--until-first')
            rows = [json.loads(line) for line in self.cli('inbox', '--agent', 'R', '--follow', '--until-first', '--timeout', '0.1').splitlines()]
        self.assertEqual(rows[0]['ids'], ['unacked'])
        self.assertEqual(rows[0]['count'], 1)
        self.assertFalse(any(row['kind'] == 'message' for row in rows))
        self.assertIsNone(self.state()['messages']['unacked']['ack'])

    def test_task_options_swallowed_by_proof_are_refused(self):
        self.assertIn('Task option inside', self.refuse('task', '--actor', 'C', '--id', 'T', '--title', 'Misordered',
            '--owner', 'A', '--repo', str(self.repo), '--scope-file', str(self.scope), '--proof',
            sys.executable, '-c', 'pass', '--reviewer', 'R'))
        self.assertNotIn('T', self.state()['tasks'])
