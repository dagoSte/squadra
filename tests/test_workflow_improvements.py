"""Reader transfers keep the original proof and one verdict consumes one round."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_board as support
from registry import Store, attention
from squadra import main


class Improvements(unittest.TestCase):
    setUp = support.Board.setUp
    cli = support.Board.cli
    state = support.Board.state
    task = support.Board.task
    start = support.Board.start
    deliver = support.Board.deliver
    review = support.Board.review

    def refuse(self, *args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            result = main(['--root', str(self.root), *args])
        self.assertEqual(result, 1, err.getvalue())
        return err.getvalue()

    def test_builder_can_continue_after_delivery_and_frozen_drift_is_refused(self):
        self.task(); self.start(); self.deliver()
        task = self.state()['tasks']['T']
        frozen = Path(task['delivery']['frozen']['repo'])
        self.assertEqual((frozen / 'value.txt').read_text(), 'Synthetic fixture\n')
        (self.repo / 'value.txt').write_text('Next task\n')
        self.review()
        self.cli('check', '--actor', 'C', '--task', 'T')
        (frozen / 'value.txt').write_text('Tampered\n')
        self.assertIn('Frozen candidate', self.refuse('close', '--actor', 'C', '--task', 'T'))

    def test_frozen_copy_contains_uncommitted_and_untracked_bytes(self):
        self.task(); self.start()
        (self.repo / 'value.txt').write_text('Changed\n')
        (self.repo / 'new.txt').write_text('Untracked\n')
        self.deliver()
        frozen = Path(self.state()['tasks']['T']['delivery']['frozen']['repo'])
        self.assertEqual((frozen / 'value.txt').read_text(), 'Changed\n')
        self.assertEqual((frozen / 'new.txt').read_text(), 'Untracked\n')

    def test_reassign_needs_new_ack_keeps_proof_budget_and_rejects_old_reader(self):
        self.task(); self.start(); self.deliver()
        before = self.state()['tasks']['T']
        (self.repo / 'value.txt').write_text('Builder continues\n')
        self.cli('reassign', '--actor', 'C', '--task', 'T', '--reviewer', 'Other', '--reason', 'Reader occupied')
        after = self.state()['tasks']['T']
        self.assertEqual(before['proofs'], after['proofs'])
        self.assertEqual(before['rounds'], after['rounds'])
        self.assertEqual(before['candidate'], after['candidate'])
        self.assertIn('ACK', self.refuse('review', '--actor', 'Other', '--task', 'T', '--verdict', 'accepted', '--report', str(self.report)))
        self.cli('ack', '--actor', 'Other', '--message', after['delivery']['handoff'])
        self.assertIn('reviewer', self.refuse('review', '--actor', 'R', '--task', 'T', '--verdict', 'accepted', '--report', str(self.report)))
        self.cli('review', '--actor', 'Other', '--task', 'T', '--verdict', 'accepted', '--report', str(self.report))
        self.cli('close', '--actor', 'C', '--task', 'T', '--note', 'Reviewed frozen delivery')
        self.assertEqual(self.state()['tasks']['T']['close_note'], 'Reviewed frozen delivery')

    def test_duplicate_review_keeps_one_round_and_same_receipt(self):
        self.task(); self.start(); self.deliver()
        self.review(); before = self.state()['event_count']
        out = self.cli('review', '--actor', 'R', '--task', 'T', '--verdict', 'accepted', '--report', str(self.report))
        task = self.state()['tasks']['T']
        self.assertEqual(task['rounds']['code'], 1)
        self.assertEqual(len(task['reviews']), 1)
        self.assertEqual(self.state()['event_count'], before)
        self.assertIn('already_recorded', out)

    def test_direct_store_review_retry_is_idempotent_on_new_tasks(self):
        self.task(); self.start(); self.deliver()
        store = Store(self.root)
        try:
            task = store.task('T')
            data = {'task': 'T', 'stage': 'code', 'verdict': 'accepted',
                    'binding': task['candidate']['sha256'], 'report': store.artifact(self.report), 'covers': []}
            first = store.append('review', 'R', data)
            second = store.append('review', 'R', data)
            self.assertEqual(first, second)
            self.assertEqual(store.task('T')['rounds']['code'], 1)
        finally:
            store.close()

    def test_new_workflow_envelope_persists_and_doctor_names_compatible_version(self):
        self.task()
        store = Store(self.root, read_only=True)
        try:
            events = store.events()
            task_event = next(e for e in events if e['kind'] == 'task')
            self.assertEqual(task_event['format'], 3)
            self.assertEqual(events[-1]['format'], 3)
            self.assertTrue(all(e.get('format') != 3 for e in events if e['seq'] < task_event['seq']))
        finally:
            store.close()
        self.assertIn('3.11.0 or later', self.cli('doctor', '--quick'))

    def test_checkpoint_and_restore_keep_the_delivered_source_after_relocation(self):
        from frozen_candidate import current
        from recovery import save, restore_check
        self.task(); self.start(); self.deliver()
        store = Store(self.root)
        try:
            expected = store.task('T')['candidate']
            copied = self.base / 'checkpoint'
            store.checkpoint(copied)
            reader = Store(copied, read_only=True)
            try:
                self.assertEqual(current(reader.task('T'), copied), expected)
            finally:
                reader.close()
            package = self.base / 'save'
            save(store, package, temporary=True)
            restored = self.base / 'restored'
            restore_check(package, restored)
            reader = Store(restored / 'registry', read_only=True)
            try:
                self.assertEqual(current(reader.task('T'), restored / 'registry'), expected)
            finally:
                reader.close()
        finally:
            store.close()

    def test_changed_verdict_needs_explicit_amendment(self):
        self.task(); self.start(); self.deliver(); self.review()
        self.assertIn('--amend', self.refuse('review', '--actor', 'R', '--task', 'T', '--verdict', 'blocked', '--report', str(self.report)))
        receipt = self.state()['tasks']['T']['reviews'][-1]['request']
        self.cli('review', '--actor', 'R', '--task', 'T', '--verdict', 'blocked', '--report', str(self.report), '--amend', receipt)
        self.assertEqual(self.state()['tasks']['T']['rounds']['code'], 2)

    def test_author_cannot_take_transferred_read(self):
        self.task(); self.start(); self.deliver()
        self.assertIn('reviewer', self.refuse('reassign', '--actor', 'C', '--task', 'T', '--reviewer', 'A', '--reason', 'Invalid'))

    def test_send_is_short_and_resource_status_does_not_write(self):
        text = self.base / 'mail.txt'; text.write_text('A short notice')
        out = self.cli('send', '--actor', 'C', '--to', 'R', '--body-file', str(text))
        self.assertNotIn('In corso', out)
        before = self.state()['event_count']
        self.cli('resource', 'status', '--name', 'cpu-heavy')
        self.assertEqual(before, self.state()['event_count'])

    def test_unrelated_task_reservation_does_not_prevent_supersede(self):
        self.task('Old'); self.task('New'); self.task('OtherTask')
        self.cli('resource', 'acquire', '--actor', 'A', '--name', 'port', '--reason', 'Other work', '--task', 'OtherTask')
        self.cli('supersede', '--actor', 'C', '--task', 'Old', '--by', 'New', '--reason', 'Replacement')
        self.assertIn('port', self.state()['resources'])

    def test_cursor_survives_restart_without_acknowledging_mail(self):
        body = self.base / 'mail.txt'; body.write_text('First')
        self.cli('send', '--actor', 'C', '--to', 'R', '--id', 'first', '--body-file', str(body))
        with patch.dict(os.environ, {'XDG_STATE_HOME': str(self.base / 'state')}):
            first = self.cli('inbox', '--agent', 'R', '--follow', '--until-first')
            self.assertIn('"id": "first"', first)
            body.write_text('Second')
            self.cli('send', '--actor', 'C', '--to', 'R', '--id', 'second', '--body-file', str(body))
            second = self.cli('inbox', '--agent', 'R', '--follow', '--until-first')
            self.assertNotIn('"id": "first"', second)
            self.assertIn('"id": "second"', second)
            self.assertIsNone(self.state()['messages']['first']['ack'])

    def test_resource_status_names_live_waiters_and_leaves_stale_tickets(self):
        import fcntl
        from resource_wait import queue_folder, queue_file, status
        store = Store(self.root)
        try:
            with queue_folder(self.root, 'cpu-heavy') as folder:
                name = '00000000000000000001-' + 'a' * 32 + '.ticket'
                fd = queue_file(folder, name, exclusive=True)
                try:
                    os.write(fd, json.dumps({'actor': 'R', 'task': None, 'reason': 'Read probe'}).encode())
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    found = status(store, 'cpu-heavy')
                    self.assertEqual(found['waiting'][0]['actor'], 'R')
                    self.assertEqual(found['waiting'][0]['position'], 1)
                finally:
                    os.close(fd)
                self.assertEqual(status(store, 'cpu-heavy')['waiting'], [])
                self.assertIn(name, os.listdir(folder))
        finally:
            store.close()

    def rule(self, check=None):
        self.task(); self.start()
        task = self.state()['tasks']['T']
        store = Store(self.root)
        try:
            evidence = next(e['id'] for e in store.events() if e['kind'] == 'start')
        finally:
            store.close()
        rule = {'id': 'check-before-commit', 'evidence': [evidence], 'trigger': 'Before a workflow commit',
                'action': 'Check the managed instruction before committing.',
                'check': check or [sys.executable, '-c', "from pathlib import Path; assert 'check-before-commit' in Path('AGENTS.md').read_text()"],
                'retire_when': 'The project no longer uses Git.'}
        path = self.base / 'rule.json'; path.write_text(json.dumps(rule))
        import subprocess
        for key, value in [('user.name', 'Synthetic'), ('user.email', 'synthetic@example.invalid'), ('commit.gpgsign', 'false')]:
            subprocess.run(['git', 'config', key, value], cwd=self.repo, check=True)
        return path

    def test_migliora_commits_only_its_rule_and_preserves_staged_work(self):
        import subprocess
        rule = self.rule()
        (self.repo / 'value.txt').write_text('Another staged change\n')
        subprocess.run(['git', 'add', 'value.txt'], cwd=self.repo, check=True)
        self.cli('migliora', 'apply', '--actor', 'A', '--task', 'T', '--rule-file', str(rule))
        changed = subprocess.check_output(['git', 'diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD'], cwd=self.repo).decode().splitlines()
        self.assertEqual(changed, ['AGENTS.md'])
        staged = subprocess.check_output(['git', 'diff', '--cached', '--name-only'], cwd=self.repo).decode().splitlines()
        self.assertEqual(staged, ['value.txt'])

    def test_migliora_failed_check_restores_instruction_and_does_not_commit(self):
        rule = self.rule([sys.executable, '-c', 'raise SystemExit(3)'])
        head = self.state()['tasks']['T']['base']
        self.assertIn('check failed', self.refuse('migliora', 'apply', '--actor', 'A', '--task', 'T', '--rule-file', str(rule)))
        self.assertFalse((self.repo / 'AGENTS.md').exists())
        import subprocess
        self.assertEqual(subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.repo).decode().strip(), head)

    def test_migliora_prepares_for_required_review_then_commits_exact_bytes(self):
        rule = self.rule()
        self.cli('migliora', 'apply', '--actor', 'A', '--task', 'T', '--rule-file', str(rule), '--defer-commit')
        self.assertFalse(self.state()['outputs'][-1]['migliora']['committed'])
        self.cli('migliora', 'commit', '--actor', 'A', '--task', 'T', '--rule-file', str(rule))
        self.assertTrue(self.state()['outputs'][-1]['migliora']['committed'])

    def test_migliora_refused_hook_keeps_checked_rule_for_a_later_commit(self):
        rule = self.rule()
        hook = self.repo / '.git/hooks/pre-commit'
        hook.write_text('#!/bin/sh\nexit 1\n'); hook.chmod(0o755)
        self.assertIn('refused', self.refuse('migliora', 'apply', '--actor', 'A', '--task', 'T', '--rule-file', str(rule)))
        self.assertTrue((self.repo / 'AGENTS.md').is_file())
        self.assertFalse(self.state()['outputs'][-1]['migliora']['committed'])
        # The owner/project resolves its hook; the helper does not disable it.
        hook.write_text('#!/bin/sh\nexit 0\n')
        self.cli('migliora', 'commit', '--actor', 'A', '--task', 'T', '--rule-file', str(rule))
        self.assertTrue(self.state()['outputs'][-1]['migliora']['committed'])


if __name__ == '__main__':
    unittest.main()
