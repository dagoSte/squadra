"""Identity mail and explicit reader readiness preserve delivery/ACK boundaries."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import test_squadra as support
from registry import Error, Store, TASK_FIELDS, board, now, presence, ready_readers, require_handoff
from squadra import inbox, deliver, dispatch, parser
from inbox_follow import Follower, follow, cursor_path
from comms import mailbox_keys
from replacement import execute


class MailReady(unittest.TestCase):
    git = support.Workflow.git
    task = support.Workflow.task
    ack = support.Workflow.ack
    cli = support.Workflow.cli
    proof = support.Workflow.proof

    def setUp(self):
        support.Workflow.setUp(self)
        task = self.store.state()['tasks']['T']
        task = {k: v for k, v in task.items() if k in TASK_FIELDS}
        self.store.batch([('task', 'C', {**task, 'id': 'U', 'reviewer': 'R'}, None),
                          ('message', 'C', {'id': 'assign-U', 'to': 'A', 'task': 'U', 'body': 'Build U'}, None)])

    def follow_once(self, agents, **kwargs):
        # Run the real poll, emit and cursor save once, regardless of machine load.
        with patch('inbox_follow.time.monotonic', return_value=0), \
                patch('inbox_follow.time.sleep', side_effect=KeyboardInterrupt):
            return follow(self.root, agents, timeout=1, **kwargs)

    def ready(self, value=True):
        self.store.append('reader.ready', 'R', {'task': 'U', 'ready': value})

    def send(self, name, to='A', **extra):
        self.store.append('message', 'C', {'id': name, 'to': to, 'body': name, **extra})

    def alias(self, name='R-more'):
        self.store.append('agent', name, {'id': name, 'role': 'reviewer', 'identity': 'outside',
                          'client': 'claude', 'repo': str(self.repo), 'skills': [], 'session': ''})

    def test_inbox_expands_roles_deduplicates_and_ack_keeps_original_recipient(self):
        self.send('alias', to='A-alias'); self.send('foreign', to='R')
        state = self.store.view()
        rows = inbox(state, ['A', 'A-alias'], everything=True)
        self.assertEqual([r['id'] for r in rows], ['assign-T', 'assign-U', 'alias'])
        self.assertEqual(inbox(state, ['A'], message='alias')[0]['to'], 'A-alias')
        self.store.release_views()
        with self.assertRaises(Error):
            self.store.append('ack', 'A', {'message': 'alias'})
        self.store.append('ack', 'A-alias', {'message': 'alias'})
        self.assertEqual(self.store.state()['messages']['alias']['ack']['agent'], 'A-alias')
        self.store.db.execute('DELETE FROM derived_state_meta')
        self.assertEqual([r['id'] for r in inbox(self.store.view(), ['A'], everything=True)],
                         ['assign-T', 'assign-U', 'alias'])

    def test_follow_initial_and_dynamic_roles_do_not_emit_foreign_mail(self):
        self.send('pre-registration', to='R-more', awaited=True)
        reader = Store(self.root, read_only=True); self.addCleanup(reader.close)
        follower = Follower(reader, ['R'], self.store.events()[-1]['seq'])
        with contextlib.redirect_stdout(io.StringIO()):
            follower.emit(follower.poll())
        self.alias(); self.send('alias-new', to='R-more'); self.send('foreign')
        batch = follower.poll()
        rows = [r for r in batch[2] if r['kind'] == 'message']
        self.assertEqual({r['id'] for r in rows}, {'pre-registration', 'alias-new'})
        self.assertTrue(all('--actor R-more' in r['ack_command'] for r in rows))
        with contextlib.redirect_stdout(io.StringIO()):
            follower.emit(batch)
        self.assertIsNone(follower.poll())
        self.assertEqual([r['id'] for r in inbox(self.store.view(), ['R'])],
                         ['pre-registration', 'alias-new'])

    def test_cursor_is_shared_by_roles_and_new_alias_arrivals_resume(self):
        self.alias()
        keys = mailbox_keys(self.store.view(), ['R'])
        self.assertEqual(keys, mailbox_keys(self.store.view(), ['R-more', 'R']))
        cursor = self.base / 'mail-cursor.json'
        self.send('first', to='R-more')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.follow_once(['R'], cursor_file=str(cursor))
        self.assertIn('first', out.getvalue())
        self.send('next', to='R')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.follow_once(['R-more'], cursor_file=str(cursor))
        messages = [json.loads(line)['id'] for line in out.getvalue().splitlines()
                    if json.loads(line)['kind'] == 'message']
        self.assertEqual(messages, ['next'])
        self.assertEqual(json.loads(cursor.read_text())['agents'], keys)
        self.assertEqual(cursor_path(self.root, keys),
                         cursor_path(self.root, mailbox_keys(self.store.view(), ['R-more'])))

    def test_resume_after_role_registration_exposes_earlier_pending_mail(self):
        self.send('before-role', to='R-more', awaited=True)
        cursor = self.base / 'resume.json'
        with contextlib.redirect_stdout(io.StringIO()):
            self.follow_once(['R'], cursor_file=str(cursor))
        self.alias()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.follow_once(['R'], cursor_file=str(cursor))
        messages = [json.loads(line)['id'] for line in out.getvalue().splitlines()
                    if json.loads(line)['kind'] == 'message']
        self.assertEqual(messages, ['before-role'])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.follow_once(['R-more'], cursor_file=str(cursor))
        self.assertFalse(any(json.loads(line)['kind'] == 'message' for line in out.getvalue().splitlines()))

    def test_legacy_cursor_migrates_and_foreign_identity_is_refused(self):
        self.alias()
        cursor = self.base / 'legacy.json'
        event = self.store.events()[-1]
        cursor.write_text(json.dumps({'root': str(self.root), 'agents': ['R'],
                                     'event': event['seq'], 'hash': event['hash']}))
        self.send('new', to='R-more')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.follow_once(['R-more'], cursor_file=str(cursor))
        self.assertIn('new', out.getvalue())
        self.assertEqual(json.loads(cursor.read_text())['agents'], ['identity:outside'])
        with self.assertRaisesRegex(Error, 'another registry or recipient'):
            self.follow_once(['A'], cursor_file=str(cursor))

    def test_follow_sees_atomic_replacement_digest(self):
        self.store.append('agent', 'B', {'id': 'B', 'identity': 'new', 'role': 'builder',
                          'client': 'codex', 'repo': str(self.repo), 'skills': [], 'session': ''})
        reader = Store(self.root, read_only=True); self.addCleanup(reader.close)
        follower = Follower(reader, ['B'], self.store.events()[-1]['seq'])
        with contextlib.redirect_stdout(io.StringIO()):
            follower.emit(follower.poll())
        result = execute(self.store, 'C', {'map': {'A': 'B'}, 'sessions': {}, 'client': 'codex',
                         'carry': 'useful', 'force': False, 'reason': 'Old window closed'})
        rows = [r['id'] for r in follower.poll()[2] if r['kind'] == 'message']
        self.assertEqual(set(rows), set(result['outbox']) | {'assign-' + name for name in result['successors'].values()})
        self.assertEqual(len(rows), len(set(rows)))

    def test_initial_backlog_never_splits_an_atomic_replacement_at_ten_messages(self):
        for index in range(9):
            self.task('EXTRA-' + str(index))
        result = execute(self.store, 'C', {'map': {'A': 'B'}, 'sessions': {}, 'client': 'manual',
                         'carry': 'useful', 'force': False, 'reason': 'Old window closed'})
        reader = Store(self.root, read_only=True); self.addCleanup(reader.close)
        follower = Follower(reader, ['B'])
        batch = follower.poll()
        rows = [r['id'] for r in batch[2] if r['kind'] == 'message']
        self.assertEqual(set(rows), set(result['outbox']) | {'assign-' + name for name in result['successors'].values()})
        self.assertEqual(len(rows), 12)
        self.assertFalse(any(r['kind'] == 'more' for r in batch[2]))

    def test_readiness_is_explicit_visible_and_does_not_ack_future_delivery(self):
        self.assertEqual(ready_readers(self.store.state(), self.store.state()['tasks']['U']), [])
        self.assertEqual(self.cli('ready', '--actor', 'R', '--task', 'U'), 0)
        state = self.store.state()
        self.assertIn('reader ready, waiting for delivery', board(state, lang='en'))
        self.assertIn('lettore pronto, attende consegna', board(state, lang='it'))
        row = next(r for r in presence(state, now()) if r['agent'] == 'R')
        self.assertEqual(row['ready_tasks'], ['U'])
        self.assertIsNone(state['messages']['assign-U']['ack'])
        with self.assertRaises(Error):
            require_handoff(state, state['tasks']['U'], 'R', 'reading')
        out = io.StringIO()
        from squadra import main
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(['--root', str(self.root), 'status']), 0)
        self.assertIn('lettore pronto, attende consegna', out.getvalue())
        self.ack('U'); self.assertEqual(self.proof(task='U')['status'], 'passed')
        result = deliver(self.store, 'A', 'U')
        state = self.store.state()
        self.assertNotIn('reader_ready', state['tasks']['U'])
        self.assertIsNone(state['messages'][result['handoff']]['ack'])
        with self.assertRaises(Error):
            require_handoff(state, state['tasks']['U'], 'R', 'reading')
        self.store.append('ack', 'R', {'message': result['handoff']})
        state = self.store.state(); require_handoff(state, state['tasks']['U'], 'R', 'reading')
        self.assertEqual(self.cli('review', '--actor', 'R', '--task', 'U', '--verdict', 'accepted',
                                 '--report', str(self.report)), 0)

    def test_revoke_standby_and_registration_clear_readiness(self):
        self.ready(); self.assertEqual(self.cli('ready', '--actor', 'R', '--task', 'U', '--revoke'), 0)
        self.assertFalse(ready_readers(self.store.state(), self.store.state()['tasks']['U']))
        self.ready()
        self.store.append('availability', 'C', {'agent': 'R', 'status': 'stand-by', 'reason': 'Window closed'})
        self.store.append('availability', 'C', {'agent': 'R', 'status': 'active', 'reason': 'Returned'})
        self.assertFalse(ready_readers(self.store.state(), self.store.state()['tasks']['U']))
        self.ready()
        seat = self.store.state()['agents']['R']
        self.store.append('agent', 'R', {k: v for k, v in {**seat, 'session': 'another'}.items()
                                      if k not in {'updated', 'availability'}})
        self.assertFalse(ready_readers(self.store.state(), self.store.state()['tasks']['U']))

    def test_replacement_does_not_transfer_readiness(self):
        self.ready()
        execute(self.store, 'C', {'map': {'R': 'NEW'}, 'sessions': {}, 'client': 'manual',
                'carry': 'none', 'force': False, 'reason': 'Window closed'})
        state = self.store.state()
        self.assertEqual(state['tasks']['U']['reviewer'], 'NEW')
        self.assertFalse(ready_readers(state, state['tasks']['U']))
        self.assertNotIn('reader ready', board(state, lang='en'))

    def test_only_assigned_independent_active_reader_can_declare(self):
        for actor in ('C', 'A', 'A-alias'):
            with self.assertRaises(Error):
                self.store.append('reader.ready', actor, {'task': 'U', 'ready': True})
        self.store.append('availability', 'C', {'agent': 'R', 'status': 'stand-by', 'reason': 'Unavailable'})
        with self.assertRaisesRegex(Error, 'stand-by'):
            self.ready()
        self.store.append('availability', 'C', {'agent': 'R', 'status': 'active', 'reason': 'Returned'})
        for name in ('R', 'A'):
            seat = self.store.state()['agents'][name]
            self.store.append('agent', name, {k: v for k, v in {**seat, 'session': 'same-session'}.items()
                                             if k not in {'updated', 'availability'}})
        with self.assertRaisesRegex(Error, 'Author'):
            self.ready()

    def test_dashboard_renders_recorded_readiness_and_removes_stale_states(self):
        self.ready()
        fixture = self.base / 'ready.json'; fixture.write_text(json.dumps(self.store.state()))
        source = Path(__file__).resolve().parents[1]
        result = subprocess.run(['node', str(source / 'tests/mail_ready_dashboard.js'),
                                 str(source / 'assets/dashboard.html'), str(fixture)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_readiness_format_cache_checkpoint_and_pool(self):
        self.alias('R2')
        task = {k: v for k, v in self.store.state()['tasks']['U'].items() if k in TASK_FIELDS}
        self.store.append('task', 'C', {**task, 'id': 'POOL', 'reviewer': None, 'pool': ['R', 'R2']})
        self.store.append('reader.ready', 'R2', {'task': 'POOL', 'ready': True})
        self.ready()
        self.assertEqual(self.store.events()[-1]['format'], 4)
        self.assertNotEqual(self.store.audit()['cache'], 'mismatch')
        self.assertEqual(ready_readers(self.store.view(), self.store.view()['tasks']['POOL']), ['R2'])
        folder = self.base / 'checkpoint'; self.store.checkpoint(folder)
        check = Store(folder, read_only=True); self.addCleanup(check.close)
        self.assertEqual(check.state()['tasks'], self.store.state()['tasks'])


if __name__ == '__main__':
    unittest.main()
