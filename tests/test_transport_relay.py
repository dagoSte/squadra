"""Pin the coordinator relay and the recording of every Codex queue attempt, with a fake codex on PATH."""
import contextlib
import io
import os
import signal
import time
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_squadra as support
from registry import Error
from squadra import main

FAKE_CODEX = """#!/bin/sh
mode="${FAKE_CODEX_MODE:-ok}"
if [ "$2" = "--help" ]; then
  [ "$mode" = help-fail ] && { echo "Error: failed to initialize state database" >&2; exit 1; }
  echo "Usage: codex queue --thread <ID> --message <TEXT>"; exit 0
fi
case "$mode" in
  queue-fail) echo "Error: failed to initialize state database: unable to open database file" >&2; exit 1;;
  hang) sleep 40 & echo $! > "$FAKE_CODEX_CHILD"; wait;;
  loud) head -c 100000 /dev/zero | tr '\\0' x; head -c 100000 /dev/zero | tr '\\0' y >&2; exit 0;;
  *) echo "queued"; exit 0;;
esac
"""


class TransportRelay(unittest.TestCase):
    setUp_workflow = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task

    def setUp(self):
        self.setUp_workflow()
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.bin = Path(folder.name)
        (self.bin / 'codex').write_text(FAKE_CODEX); (self.bin / 'codex').chmod(0o755)
        self.store.append('agent', 'X', {'id': 'X', 'role': 'reviewer', 'identity': 'codex-reader', 'client': 'codex',
                                         'repo': str(self.repo), 'skills': [], 'session': 'thread-x'})

    def run_cli(self, *args, mode='ok', path=True):
        out, err = io.StringIO(), io.StringIO()
        env = {'FAKE_CODEX_MODE': mode, 'FAKE_CODEX_CHILD': str(self.bin / 'child.pid'),
               'PATH': (str(self.bin) + os.pathsep if path else '') + '/usr/bin:/bin'}
        with patch.dict(os.environ, env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = main(['--root', str(self.root), *args])
            except BaseException as exc:
                rc = 'raised ' + type(exc).__name__
        return rc, err.getvalue()

    def send(self, ident, sender, to):
        self.store.append('message', sender, {'id': ident, 'to': to, 'body': 'Synthetic note'})

    def transports(self, ident):
        return [e['data'] for e in self.store.events() if e['kind'] == 'transport' and e['data']['message'] == ident]

    def one(self, ident):
        records = self.transports(ident)
        self.assertEqual(len(records), 1, records)
        return records[0]

    def transport(self, actor, ident, via='codex-queue', **kwargs):
        return self.run_cli('transport', '--actor', actor, '--message', ident, '--via', via, **kwargs)

    def test_coordinator_relays_a_workers_message_by_hand_and_only_the_recipient_ack_ends_it(self):
        self.send('M1', 'A', 'R')
        self.assertEqual(self.transport('C', 'M1', via='manual')[0], 0)
        self.assertEqual([t.get('relayed_by') for t in self.transports('M1')], ['C'])
        self.assertIsNone(self.store.state()['messages']['M1']['ack'])
        self.assertEqual(self.run_cli('ack', '--actor', 'R', '--message', 'M1')[0], 0)
        self.assertIsNotNone(self.store.state()['messages']['M1']['ack'])

    def test_coordinator_relays_a_codex_message_through_the_queue(self):
        self.send('M2', 'A', 'X')
        self.assertEqual(self.transport('C', 'M2', mode='ok')[0], 0)
        record = self.one('M2')
        self.assertEqual((record['delivered'], record['relayed_by'], record['stage']), (True, 'C', 'queue'))

    def test_worker_cannot_relay_another_workers_message(self):
        self.send('M3', 'A', 'X')
        self.assertEqual(self.transport('R', 'M3', via='manual')[0], 1)
        self.assertEqual(self.transports('M3'), [])

    def test_validator_binds_relayed_by_to_a_relay(self):
        self.send('M4', 'A', 'R')
        with self.assertRaisesRegex(Error, 'relayed_by'):
            self.store.append('transport', 'C', {'message': 'M4', 'via': 'manual', 'delivered': False})
        with self.assertRaisesRegex(Error, 'relayed_by'):
            self.store.append('transport', 'A', {'message': 'M4', 'via': 'manual', 'delivered': False, 'relayed_by': 'A'})
        self.store.append('transport', 'C', {'message': 'M4', 'via': 'manual', 'delivered': False, 'relayed_by': 'C'})
        self.assertEqual(self.transports('M4')[-1]['relayed_by'], 'C')

    def test_a_queue_that_fails_like_the_sandbox_is_recorded_and_exits_nonzero(self):
        self.send('M5', 'A', 'X')
        self.assertEqual(self.transport('A', 'M5', mode='queue-fail')[0], 1)
        record = self.one('M5')
        self.assertEqual((record['delivered'], record['stage'], record['native_rc']), (False, 'queue', 1))

    def test_a_failed_queue_check_is_recorded_as_an_attempt(self):
        self.send('M6', 'A', 'X')
        self.assertEqual(self.transport('A', 'M6', mode='help-fail')[0], 1)
        record = self.one('M6')
        self.assertEqual((record['delivered'], record['stage'], record['native_rc']), (False, 'help', 1))

    def test_a_missing_codex_is_recorded_as_an_attempt(self):
        self.send('M7', 'A', 'X')
        self.assertEqual(self.transport('A', 'M7', path=False)[0], 1)
        record = self.one('M7')
        self.assertEqual((record['delivered'], record['stage'], record['reason']), (False, 'help', 'not_found'))

    def test_a_hung_queue_is_recorded_as_unknown_without_a_traceback(self):
        self.send('M8', 'A', 'X')
        with patch('squadra.QUEUE_SECONDS', 1, create=True):
            rc, err = self.transport('A', 'M8', mode='hang')
        child = int((self.bin / 'child.pid').read_text())
        self.addCleanup(self.kill, child)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and self.alive(child):
            time.sleep(0.05)
        self.assertFalse(self.alive(child), 'the queue\'s grandchild survived the timeout')
        self.assertEqual(rc, 1)
        self.assertNotIn('Traceback', err)
        record = self.one('M8')
        self.assertEqual((record['delivered'], record['stage'], record['reason']), (None, 'queue', 'timeout'))

    @staticmethod
    def kill(pid):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)

    @staticmethod
    def alive(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    def test_native_output_is_kept_bounded_with_its_length(self):
        self.send('M10', 'A', 'X')
        self.assertEqual(self.transport('A', 'M10', mode='loud')[0], 0)
        record = self.one('M10')
        self.assertEqual((record['receipt'], record.get('receipt_length')), ('x' * 2000, 100000))
        self.assertEqual((record['stderr'], record.get('stderr_length')), ('y' * 2000, 100000))

    def test_a_delivered_queue_exits_zero(self):
        self.send('M9', 'A', 'X')
        self.assertEqual(self.transport('A', 'M9', mode='ok')[0], 0)
        self.assertIs(self.transports('M9')[0]['delivered'], True)


if __name__ == '__main__':
    unittest.main()
