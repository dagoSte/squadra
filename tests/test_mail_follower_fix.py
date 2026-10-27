"""Frozen M-F1 regressions: resume and continuous polling share registration semantics."""
import contextlib
import io
import unittest
from unittest.mock import patch
import test_mail_ready as support
from inbox_follow import Follower, follow, save_cursor
from registry import Store
from comms import mailbox_keys


class MailFollowerFix(unittest.TestCase):
    setUp = support.MailReady.setUp
    git = support.MailReady.git
    task = support.MailReady.task
    send = support.MailReady.send
    alias = support.MailReady.alias

    def prepared(self):
        self.send('already-shown', to='R')
        reader = Store(self.root, read_only=True); self.addCleanup(reader.close)
        follower = Follower(reader, ['R'])
        with contextlib.redirect_stdout(io.StringIO()):
            follower.emit(follower.poll())
        return reader, follower

    def register(self, **changes):
        seat = {k: v for k, v in self.store.state()['agents']['R'].items()
                if k not in {'updated', 'availability'}}
        self.store.append('agent', 'R', {**seat, **changes})

    def test_unchanged_registration_matches_continuous_follower_after_resume(self):
        reader, live = self.prepared(); saved = live.cursor
        self.register()
        resumed = Follower(reader, ['R'], saved, pending_on_start=True)
        live_rows, resumed_batch = live.poll()[2], resumed.poll()
        self.assertEqual([r['id'] for r in live_rows if r['kind'] == 'message'], [])
        self.assertEqual([r['id'] for r in resumed_batch[2] if r['kind'] == 'message'], [],
                         'M-F1: unchanged registration must not replay already shown mail')
        self.assertEqual(next(r for r in resumed_batch[2] if r['kind'] == 'pending')['ids'], ['already-shown'])
        with contextlib.redirect_stdout(io.StringIO()):
            resumed.emit(resumed_batch)
        self.assertFalse(resumed.printed)
        self.assertIsNone(self.store.state()['messages']['already-shown']['ack'])

    def test_unchanged_registration_does_not_satisfy_until_first(self):
        reader, live = self.prepared()
        cursor = self.base / 'cursor.json'
        save_cursor(cursor, live, mailbox_keys(self.store.view(), ['R']))
        self.register()
        with contextlib.redirect_stdout(io.StringIO()), \
                patch('inbox_follow.time.monotonic', return_value=0), \
                patch('inbox_follow.time.sleep', side_effect=KeyboardInterrupt) as sleep:
            follow(self.root, ['R'], timeout=1, until_first=True, cursor_file=str(cursor))
        self.assertEqual(sleep.call_count, 1, 'M-F1: until-first must keep waiting for genuinely new mail')

    def test_rebound_session_is_discovered_once_by_live_and_resumed_polling(self):
        reader, live = self.prepared(); saved = live.cursor
        self.register(session='new-window')
        resumed = Follower(reader, ['R'], saved, pending_on_start=True)
        for follower in (live, resumed):
            batch = follower.poll()
            rows = [r for r in batch[2] if r['kind'] == 'message']
            self.assertEqual([r['id'] for r in rows], ['already-shown'])
            self.assertEqual(rows[0]['to'], 'R')
            self.assertIn('--actor R ', rows[0]['ack_command'])
            with contextlib.redirect_stdout(io.StringIO()):
                follower.emit(batch)
            self.assertIsNone(follower.poll())
        self.assertIsNone(self.store.state()['messages']['already-shown']['ack'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
