"""A reader's probe of a frozen delivery tests the delivered bytes, and a read refused before its client starts spends no budget."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_board as board_support
import test_squadra as support
from registry import Error, Store, snapshot
from runtime import execute
from squadra import main

DELIVERED = 'Synthetic fixture\n'
LIVE = 'LIVE EDIT AFTER DELIVERY\n'


def reads(expected):
    return [sys.executable, '-c', 'import sys; sys.exit(0 if open("value.txt").read() == %r else 3)' % expected]


class FrozenProbe(unittest.TestCase):
    cli = board_support.Board.cli
    state = board_support.Board.state
    task = board_support.Board.task
    start = board_support.Board.start
    deliver = board_support.Board.deliver

    def setUp(self):
        home = tempfile.TemporaryDirectory(prefix='squadra-rf4b-home-'); self.addCleanup(home.cleanup)
        patcher = patch.dict(os.environ, {'HOME': home.name}); patcher.start(); self.addCleanup(patcher.stop)
        board_support.Board.setUp(self)
        self.task(); self.start(); self.deliver()
        (self.repo / 'value.txt').write_text(LIVE)

    def probe(self, actor, argv, expected_rc=0):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            rc = main(['--root', str(self.root), 'probe', '--actor', actor, '--task', 'T',
                       '--expected-rc', str(expected_rc), '--', *argv])
        runs = [r for r in self.state()['runs'].values() if r['mode'] == 'probe']
        return rc, runs[-1] if runs else None, err.getvalue()

    def frozen(self):
        task = self.state()['tasks']['T']
        return task, Path(task['delivery']['frozen']['repo'])

    def test_a_readers_probe_runs_on_the_delivered_bytes_not_the_live_tree(self):
        task, frozen = self.frozen()
        rc, run, err = self.probe('R', reads(DELIVERED))
        self.assertEqual(rc, 0, err)
        self.assertEqual(run['status'], 'matched', run.get('error'))
        self.assertEqual(run['before']['sha256'], task['candidate']['sha256'])
        self.assertEqual(run['after'], run['before'])
        self.assertTrue(run.get('frozen_copy'))
        rc, run, _ = self.probe('R', reads(DELIVERED), expected_rc=3)
        self.assertNotEqual(run['status'], 'matched')
        self.assertEqual((self.repo / 'value.txt').read_text(), LIVE)
        self.assertEqual((frozen / 'value.txt').read_text(), DELIVERED)
        self.assertEqual(frozen.stat().st_mode & 0o222, 0)

    def test_a_probe_that_edits_its_copy_fails_and_leaves_the_frozen_candidate_intact(self):
        task, frozen = self.frozen()
        rc, run, _ = self.probe('R', [sys.executable, '-c', 'open("value.txt", "w").write("probe edit")'])
        self.assertEqual(run['status'], 'failed')
        self.assertIn('Source changed', run['error'])
        self.assertEqual((frozen / 'value.txt').read_text(), DELIVERED)
        self.cli('review', '--actor', 'R', '--task', 'T', '--verdict', 'accepted', '--report', str(self.report))
        self.assertEqual(self.state()['tasks']['T']['status'], 'reviewed')

    def test_the_builders_own_probe_still_runs_on_the_live_tree(self):
        rc, run, err = self.probe('A', reads(LIVE))
        self.assertEqual(rc, 0, err)
        self.assertEqual(run['status'], 'matched', run.get('error'))
        self.assertFalse(run.get('frozen_copy'))
        self.assertEqual(run['before']['sha256'], snapshot(self.repo)['sha256'])


class RefusedReadBudget(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task
    ack = support.Workflow.ack

    def client(self, name, version_rc):
        path = self.base / name
        reply = {'verdict': 'changes-required', 'summary': 'Concrete synthetic refusal', 'findings': ['VALUE remains 1'],
                 'coverage': ['code.py'], 'limitations': []}
        path.write_text('#!' + sys.executable + '\nimport sys, json\n'
                        "if '--version' in sys.argv: print('synthetic-cli 1'); sys.exit(%d)\n" % version_rc +
                        'sys.stdin.read()\n'
                        "print(json.dumps({'type': 'result', 'subtype': 'success', 'session_id': "
                        "'12345678-1234-4567-8123-123456789abc', 'structured_output': %r}))\n" % reply)
        path.chmod(0o700)
        return str(path)

    def reads(self):
        return [r for r in self.store.state()['runs'].values() if r['mode'] == 'read']

    def test_a_read_refused_before_its_client_starts_spends_no_budget(self):
        self.ack()
        broken, missing, good = self.client('broken-client', 1), str(self.base / 'no-such-client'), self.client('good-client', 0)
        for executable in (broken, missing, broken):
            with self.subTest(executable=executable), self.assertRaisesRegex(Error, 'CLI version preflight failed'):
                execute(self.store, 'R', 'T', mode='read', prompt='x', executable=executable, timeout=10)
            self.assertEqual(self.reads(), [])
        for _ in range(2):
            self.assertEqual(execute(self.store, 'R', 'T', mode='read', prompt='x', executable=good, timeout=10)['status'], 'passed')
        with self.assertRaisesRegex(Error, 'budget'):
            execute(self.store, 'R', 'T', mode='read', prompt='third', executable=good, timeout=10)


if __name__ == '__main__':
    unittest.main()
