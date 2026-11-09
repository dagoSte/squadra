"""R315-FIX.POSTA: mail that waits is visible — listener heartbeat, listening state, delivery age, «Aspetta te»."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_composite as composite_support  # also puts scripts/ on sys.path
import inbox_follow
from comms import mailbox_keys
from registry import Store, plain

ROOT = Path(__file__).resolve().parents[1]
LATER = '2026-11-09T11:00:00+00:00'  # one hour after the composite fixture's clock


class PostaAscolto(composite_support.Composite):
    """Reuses the composite squad: coordinator C (claude), executor A (codex), reader R (claude)."""


for _name in [n for n in dir(composite_support.Composite) if n.startswith('test_')]:
    setattr(PostaAscolto, _name, None)


class PostaAscolto(PostaAscolto):
    def cursor(self, agents):
        store = Store(self.root, read_only=True)
        try:
            keys = mailbox_keys(store.view(), agents)
        finally:
            store.close()
        return inbox_follow.cursor_path(self.root, keys)

    def listening(self, at):
        store = Store(self.root, read_only=True)
        try:
            return inbox_follow.listening(store.resolved(), store.root, at)
        finally:
            store.close()

    def now(self):
        return dt.datetime.now(dt.timezone.utc)

    # 1. heartbeat, requested timeout and stop in the follower's own cursor file
    def test_follow_writes_start_heartbeat_timeout_and_stop_without_events(self):
        before = self.events()
        self.assertEqual(inbox_follow.follow(self.root, ['C'], timeout=0.3), 0)
        saved = json.loads(self.cursor(['C']).read_text())
        for key in ('started_utc', 'heartbeat_utc', 'stopped_utc'):
            self.assertIsInstance(saved.get(key), str, key)
        self.assertEqual(saved['timeout'], 0.3)
        self.assertGreaterEqual(saved['stopped_utc'], saved['heartbeat_utc'])
        self.assertEqual(self.events(), before)

    def test_old_cursor_without_life_fields_still_resumes(self):
        inbox_follow.follow(self.root, ['C'], timeout=0.2)
        path = self.cursor(['C'])
        old = {k: v for k, v in json.loads(path.read_text()).items() if k in {'root', 'agents', 'event', 'hash'}}
        path.write_text(json.dumps(old))
        self.assertEqual(inbox_follow.follow(self.root, ['C'], timeout=0.2), 0)

    def spawn_follower(self):
        env = dict(os.environ)
        process = subprocess.Popen([sys.executable, '-B', str(ROOT / 'scripts/squadra.py'), '--root', str(self.root),
                                    'inbox', '--agent', 'C', '--follow'], env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        path = self.cursor(['C'])
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if path.exists() and json.loads(path.read_text() or '{}').get('heartbeat_utc'):
                return process
            time.sleep(0.1)
        self.fail('follower wrote no heartbeat')

    def test_a_listener_killed_by_sigterm_is_stopped_not_listening(self):
        process = self.spawn_follower()
        self.assertEqual(self.listening(self.now())['C']['state'], 'listening')
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=20)
        row = self.listening(self.now())['C']
        self.assertEqual(row['state'], 'stopped')

    def test_a_listener_killed_by_sigkill_is_stopped_once_its_heartbeat_is_old(self):
        process = self.spawn_follower()
        process.kill()
        process.wait(timeout=20)
        later = self.now() + dt.timedelta(seconds=inbox_follow.LISTENING_SECONDS + 30)
        row = self.listening(later)['C']
        self.assertEqual(row['state'], 'stopped')
        self.assertGreaterEqual(row['minutes'], 2)

    # 2. listening state of every active seat, read-only, in status
    def test_listening_states_never_listening_stopped_and_not_visible(self):
        self.assertEqual(self.listening(self.now())['A'], {'state': 'never'})
        inbox_follow.follow(self.root, ['R'], timeout=0.2,
                            cursor_file=str(self.base / 'elsewhere' / 'r-cursor.json'))
        (self.base / 'elsewhere' / 'r-cursor.json').unlink()
        self.assertEqual(self.listening(self.now())['R'], {'state': 'not-visible'})
        path = self.cursor(['C'])
        path.parent.mkdir(parents=True, exist_ok=True)
        beat = self.now() - dt.timedelta(seconds=20)
        store = Store(self.root, read_only=True)
        root = str(store.root)
        store.close()
        path.write_text(json.dumps({'root': root, 'agents': ['C'], 'event': 0, 'hash': '0' * 64,
                                    'heartbeat_utc': beat.isoformat(), 'stopped_utc': None}))
        self.assertEqual(self.listening(self.now())['C']['state'], 'listening')
        later = self.listening(beat + dt.timedelta(minutes=12))['C']
        self.assertEqual((later['state'], later['minutes']), ('stopped', 12))

    def test_status_shows_one_listening_line_under_the_board(self):
        with patch('squadra.now', return_value=LATER):
            out = self.assert_ok('status')
        line = next(l for l in out.splitlines() if l.startswith('Ascolto della posta:'))
        self.assertIn('A: mai armato', line)
        self.assertLess(out.index('## Fatti'), out.index('Ascolto della posta:'))

    # 3. a pending message says how long ago it was delivered and whether its recipient listens
    def test_pending_message_shows_delivery_age_and_recipient_listening(self):
        self.assert_ok('assign', *self.task_args(), '--brief-file', str(self.brief), '--via', 'manual')
        with patch('squadra.now', return_value=LATER):
            out = self.assert_ok('status', '--all')
        line = next(l for l in out.splitlines() if 'assign-Q' in l and l.startswith(('- to-forward', '- ack')))
        self.assertIn('recapitato da 60 min, destinatario non in ascolto', line)

    # 4. «Aspetta te» opens status and the coordinator's pickup, each item with its command
    def test_aspetta_te_opens_status_and_coordinator_pickup_with_commands(self):
        self.assert_ok('assign', *self.task_args(), '--brief-file', str(self.brief), '--via', 'manual')
        self.assert_ok('send', '--actor', 'A', '--to', 'C', '--id', 'QUESTION', '--body-file', str(self.body))
        with patch('squadra.now', return_value=LATER):
            out = self.assert_ok('status')
        # Status opens with the board; «Aspetta te» comes right under it, before «Serve a te».
        self.assertLess(out.index('## Fatti'), out.index('Aspetta te (C):'))
        self.assertLess(out.index('Aspetta te (C):'), out.index('Serve a te:'))
        block = out[out.index('Aspetta te (C):'):out.index('Serve a te:')]
        self.assertIn('QUESTION · messaggio da A senza ACK', block)
        self.assertIn('squadra ack --actor C --message QUESTION', block)
        self.assertIn('da inoltrare a A', block)
        self.assertIn('squadra transport --actor C --message assign-Q --via codex-queue', block)
        with patch('squadra.now', return_value=LATER), patch('composite.now', return_value=LATER):
            picked = self.assert_ok('pickup', '--actor', 'C')
        self.assertTrue(picked.startswith('Aspetta te (C):'), picked[:200])
        with patch('squadra.now', return_value=LATER), patch('composite.now', return_value=LATER):
            other = self.assert_ok('pickup', '--actor', 'A')
        self.assertNotIn('Aspetta te', other)

    def test_aspetta_te_lists_accepted_verdicts_to_close(self):
        import registry
        self.assert_ok('assign', *self.task_args(), '--brief-file', str(self.brief), '--via', 'manual')
        store = Store(self.root, read_only=True)
        try:
            state = plain(store.resolved())
        finally:
            store.close()
        # The recorded task, as a coordinator finds it after an accepted code read.
        state['tasks']['Q'].update(status='reviewed', reviews=[{'utc': '2026-11-09T10:30:00+00:00', 'stage': 'code',
                                                                   'verdict': 'accepted', 'reviewer': 'R'}])
        # Forwarding rows come from attention(), covered through the CLI above; here only the close rows.
        with patch.object(registry, 'attention', return_value={'waiting': []}):
            rows = registry.coordinator_waits(state, LATER, 'C')
        close = next(r for r in rows if r['kind'] == 'close')
        self.assertEqual((close['task'], close['minutes']), ('Q', 30))
        self.assertIn('squadra check --actor C --task Q', close['command'])
        self.assertIn('squadra close --actor C --task Q', close['command'])

    def test_status_stays_read_only(self):
        self.assert_ok('assign', *self.task_args(), '--brief-file', str(self.brief), '--via', 'manual')

        def files(folder):
            return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in folder.rglob('*') if p.is_file() and not p.name.endswith(('-wal', '-shm'))}
        before, cursors, events = files(self.root), files(inbox_follow.cursor_dir().parent), self.events()
        with patch('squadra.now', return_value=LATER):
            self.assert_ok('status', '--all')
        self.assertEqual(self.events(), events)
        self.assertEqual(files(self.root), before)
        self.assertEqual(files(inbox_follow.cursor_dir().parent), cursors)


if __name__ == '__main__':
    unittest.main()
