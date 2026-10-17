"""Reading mail stays small: a bounded default inbox and a follower that reads only new events."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_squadra as support
from registry import Error, Store, pending_ack_ids, plain
from squadra import dispatch, parser

CLI = Path(__file__).resolve().parents[1] / 'scripts/squadra.py'
RECENT = 10  # The documented default: ACK waits plus the latest ten messages.


class InboxLight(unittest.TestCase):
    def setUp(self):
        self.f = support.Workflow()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store = self.f.store

    def send(self, name, to='A', **extra):
        ident = self.store.append('message', 'C', dict(id=name, to=to, body='Body of ' + name, **extra))
        return self.store.db.execute('SELECT seq FROM events WHERE id=?', (ident,)).fetchone()[0]

    def inbox(self, *args, agent='A'):
        rows = dispatch(parser().parse_args(['inbox', '--agent', agent, *args]), self.store)
        try:
            return [plain(r) for r in rows], rows.total, rows.pending
        finally:
            self.store.release_views()

    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-B', str(CLI), '--root', str(self.f.root), 'inbox', *args],
                              env=self.f.env, capture_output=True, text=True, timeout=30)

    def mailbox(self):
        self.f.ack()
        self.f.task('T2'); self.f.task('T3')
        self.store.append('supersede', 'C', {'task': 'T2', 'by': 'T3', 'reason': 'Work moved to T3'})
        self.send('wait-old', related_task='T3')
        for n in range(RECENT + 2):
            self.send('note-' + str(n), info=True)
        self.send('acked'); self.store.append('ack', 'A', {'message': 'acked'})
        self.send('other', to='R')
        every = ['assign-T', 'assign-T2', 'assign-T3', 'wait-old'] + ['note-' + str(n) for n in range(RECENT + 2)] + ['acked']
        waits = ['assign-T3', 'wait-old']
        return every, waits, every[-RECENT:]

    def test_default_shows_the_ack_waits_and_the_latest_ten_in_the_full_row_shape(self):
        every, waits, latest = self.mailbox()
        state = self.store.view()
        shared = [k for k in pending_ack_ids(state) if state['messages'][k]['to'] == 'A']
        self.store.release_views()
        self.assertEqual(sorted(shared), sorted(waits), 'superseded assign-T2 is not a wait')
        full, total, pending = self.inbox('--all')
        self.assertEqual([r['id'] for r in full], every)
        self.assertEqual((total, pending), (len(every), 0))
        default, total, pending = self.inbox()
        self.assertEqual([r['id'] for r in default], [k for k in every if k in waits or k in latest])
        rows = {r['id']: r for r in full}
        for row in default:
            self.assertEqual(row, rows[row['id']])
        self.assertEqual((total, pending), (len(every), len(waits)))
        self.assertEqual(self.inbox('--message', 'assign-T2')[0], [rows['assign-T2']])
        for name in ('other', 'missing'):
            with self.assertRaisesRegex(Error, 'Unknown message for the named agents'):
                self.inbox('--message', name)

    def test_a_small_mailbox_and_the_verified_fallback_show_the_same_rows(self):
        every, waits, latest = self.mailbox()
        expected = [k for k in every if k in waits or k in latest]
        cached = self.inbox()
        self.store.db.execute('DELETE FROM derived_state_meta')
        replayed = self.inbox()
        self.assertEqual([r['id'] for r in replayed[0]], expected)
        self.assertEqual(replayed, cached)
        self.assertEqual([r['id'] for r in self.inbox('--all')[0]], every)
        self.store.append('agent', 'Q', {'id': 'Q', 'role': 'builder', 'identity': 'quiet', 'client': 'claude',
                                         'repo': str(self.f.repo), 'skills': [], 'session': ''})
        self.send('only', to='Q', info=True)
        rows, total, pending = self.inbox(agent='Q')
        self.assertEqual(([r['id'] for r in rows], total, pending), (['only'], 1, 0))

    def test_cli_keeps_stdout_a_json_list_and_says_on_stderr_what_it_left_out(self):
        every, waits, latest = self.mailbox()
        p = self.run_cli('--agent', 'A')
        self.assertEqual(p.returncode, 0, p.stderr)
        shown = [r['id'] for r in json.loads(p.stdout)]
        self.assertEqual(shown, [k for k in every if k in waits or k in latest])
        self.assertIn(f'showing {len(shown)} of {len(every)} messages', p.stderr)
        self.assertIn('--all', p.stderr); self.assertIn('--message ID', p.stderr)
        p = self.run_cli('--agent', 'A', '--all')
        self.assertEqual((p.returncode, p.stderr), (0, ''))
        self.assertEqual([r['id'] for r in json.loads(p.stdout)], every)
        p = self.run_cli('--agent', 'A', '--message', 'wait-old')
        self.assertEqual((p.returncode, p.stderr), (0, ''))
        self.assertEqual([r['body'] for r in json.loads(p.stdout)], ['Body of wait-old'])
        p = self.run_cli('--agent', 'A', '--message', 'other')
        self.assertEqual((p.returncode, p.stdout), (1, ''))
        self.assertIn('Unknown message for the named agents', p.stderr)
        for flags in (('--all', '--follow', '--timeout', '1'), ('--message', 'acked', '--follow'),
                      ('--all', '--message', 'acked')):
            p = self.run_cli('--agent', 'A', *flags)
            self.assertNotEqual(p.returncode, 0, flags); self.assertEqual(p.stdout, '', flags)

    def test_changed_poll_decodes_only_new_events_and_reads_a_view_only_for_named_mail(self):
        from inbox_follow import Follower
        self.f.ack()
        for n in range(40):
            self.send('history-' + str(n), info=True)
        s = Store(self.f.root, read_only=True); self.addCleanup(s.close)
        follower = Follower(s, ['A'], self.store.events()[-1]['seq'])
        with contextlib.redirect_stdout(io.StringIO()):
            follower.emit(follower.poll())
        head = follower.cursor
        with patch.object(s, 'decode_event', wraps=s.decode_event) as decoded, \
                patch.object(s, 'view', wraps=s.view) as view, \
                patch('inbox_follow.message_ids', side_effect=AssertionError('whole mailbox sorted')), \
                contextlib.redirect_stdout(io.StringIO()):
            self.send('elsewhere', to='R'); self.send('elsewhere-too', to='R')
            batch = follower.poll()
            self.assertEqual((view.call_count, batch[2], batch[1]), (0, [], head + 2))
            self.assertEqual([c.args[0]['seq'] for c in decoded.call_args_list], [head, head + 1, head + 2])
            follower.emit(batch)
            decoded.reset_mock()
            self.send('mine', related_task='T')
            batch = follower.poll()
            self.assertEqual(view.call_count, 1)
            self.assertEqual(s._views, [])
            self.assertEqual([c.args[0]['seq'] for c in decoded.call_args_list], [head + 2, head + 3])
            message, cursor = batch[2]
            self.assertEqual((message['kind'], message['id'], message['event'], message['ack_needed']),
                             ('message', 'mine', head + 3, True))
            self.assertEqual(cursor, {'kind': 'cursor', 'event': head + 3})
            follower.emit(batch)
            self.assertIsNone(follower.poll())
            self.assertEqual(view.call_count, 1)

    def test_a_changed_event_after_the_anchor_is_refused(self):
        from inbox_follow import Follower
        s = Store(self.f.root, read_only=True); self.addCleanup(s.close)
        follower = Follower(s, ['A'], self.store.events()[-1]['seq'])
        with contextlib.redirect_stdout(io.StringIO()):
            follower.emit(follower.poll())
        tampered = self.send('tampered')
        self.send('later', to='R')
        self.store.release_views()
        self.store.db.execute('DROP TRIGGER no_update')
        self.store.db.execute('UPDATE events SET data=? WHERE seq=?',
                              (json.dumps({'id': 'tampered', 'to': 'A', 'body': 'Rewritten'}), tampered))
        with self.assertRaisesRegex(Error, 'integrity failure at sequence ' + str(tampered)):
            follower.poll()


if __name__ == '__main__':
    unittest.main()
