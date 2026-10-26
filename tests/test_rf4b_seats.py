"""A seat's client is as fixed as its identity and role; recorded names and titles print as written, never as terminal controls."""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_board as support
import registry
from registry import Store
from squadra import main

ESCAPE = '\x1b]0;forged-title\x07\x1b[2J'


class Fixture(unittest.TestCase):
    cli = support.Board.cli
    state = support.Board.state

    def setUp(self):
        home = tempfile.TemporaryDirectory(prefix='squadra-rf4b-home-'); self.addCleanup(home.cleanup)
        patcher = patch.dict(os.environ, {'HOME': home.name}); patcher.start(); self.addCleanup(patcher.stop)
        support.Board.setUp(self)

    def run_cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            rc = main(['--root', str(self.root), *args])
        return rc, out.getvalue(), err.getvalue()

    def seat(self, name, role, client):
        return self.run_cli('agent', '--id', name, '--identity', 'identity-' + name, '--role', role,
                            '--client', client, '--repo', str(self.repo))

    def task_for(self, name, owner, reader):
        return self.run_cli('task', '--actor', 'C', '--id', name, '--title', 'Work ' + name, '--owner', owner,
                            '--repo', str(self.repo), '--scope-file', str(self.scope), '--proof-file', str(self.proof),
                            '--reviewer', reader)


class SeatClient(Fixture):
    def setUp(self):
        super().setUp()
        for name, role, client in (('CB', 'builder', 'claude'), ('CR', 'reviewer', 'claude'), ('XR', 'reviewer', 'codex')):
            self.assertEqual(self.seat(name, role, client)[0], 0)

    def test_reregistration_cannot_change_the_client(self):
        before = self.state()['event_count']
        rc, _, err = self.seat('CR', 'reviewer', 'codex')
        self.assertEqual(rc, 1)
        self.assertIn('client', err)
        self.assertEqual(self.state()['agents']['CR']['client'], 'claude')
        self.assertEqual(self.seat('CR', 'reviewer', 'manual')[0], 1)
        self.assertEqual(self.state()['agents']['CR']['client'], 'claude')
        self.assertEqual([e['kind'] for e in Store(self.root, read_only=True).events()[before:]], ['diagnostic', 'diagnostic'])

    def test_same_client_reader_stays_refused_after_a_refused_rewrite(self):
        rc, _, err = self.task_for('S', 'CB', 'CR')
        self.assertEqual(rc, 1); self.assertIn('same_client_reader', err)
        self.assertEqual(self.seat('CR', 'reviewer', 'codex')[0], 1)
        rc, _, err = self.task_for('S', 'CB', 'CR')
        self.assertEqual(rc, 1); self.assertIn('same_client_reader', err)
        self.assertNotIn('S', self.state()['tasks'])

    def test_an_accepted_cross_client_read_cannot_be_reversed_by_reregistration(self):
        self.assertEqual(self.task_for('K', 'CB', 'XR')[0], 0)
        self.cli('ack', '--actor', 'CB', '--message', 'assign-K')
        self.cli('start', '--actor', 'CB', '--task', 'K')
        self.cli('prove', '--actor', 'CB', '--task', 'K', '--', sys.executable, '-c', 'pass')
        self.cli('deliver', '--actor', 'CB', '--task', 'K')
        self.cli('ack', '--actor', 'XR', '--message', self.state()['tasks']['K']['delivery']['handoff'])
        self.cli('review', '--actor', 'XR', '--task', 'K', '--verdict', 'accepted', '--report', str(self.report))
        self.assertEqual(self.seat('XR', 'reviewer', 'claude')[0], 1)
        self.assertEqual(self.seat('CB', 'builder', 'codex')[0], 1)
        self.cli('close', '--actor', 'C', '--task', 'K')
        self.assertEqual(self.state()['tasks']['K']['status'], 'done')

    def test_reregistration_with_the_same_client_still_updates_the_seat(self):
        rc, _, err = self.run_cli('agent', '--id', 'CR', '--identity', 'identity-CR', '--role', 'reviewer',
                                  '--client', 'claude', '--model', 'requested-model', '--repo', str(self.repo))
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.state()['agents']['CR']['model'], 'requested-model')


class Names(Fixture):
    def test_control_characters_are_refused_in_seat_ids_task_ids_and_titles(self):
        for value in (ESCAPE, 'tab\there', 'carriage\rreturn', 'line separator', 'zero​width', 'nel\x85'):
            with self.subTest(value=value):
                before = self.state()['event_count']
                self.assertEqual(self.seat('W' + value, 'builder', 'manual')[0], 1)
                rc, _, err = self.run_cli('task', '--actor', 'C', '--id', 'X' + value, '--title', 'Plain', '--owner', 'A',
                                          '--repo', str(self.repo), '--scope-file', str(self.scope), '--reviewer', 'R')
                self.assertEqual(rc, 1, err)
                rc, _, err = self.run_cli('task', '--actor', 'C', '--id', 'X', '--title', 'Fix' + value, '--owner', 'A',
                                          '--repo', str(self.repo), '--scope-file', str(self.scope), '--reviewer', 'R')
                self.assertEqual(rc, 1, err)
                state = self.state()
                self.assertEqual(set(state['agents']), {'C', 'A', 'R', 'Other'})
                self.assertEqual(state['tasks'], {})
                self.assertEqual(state['event_count'], before + 3)
        self.assertEqual(self.seat('Seat-è', 'builder', 'manual')[0], 0)
        rc, _, err = self.run_cli('task', '--actor', 'C', '--id', 'Y', '--title', 'Résumé — «ok» 👩‍💻'.replace('‍', ''),
                                  '--owner', 'A', '--repo', str(self.repo), '--scope-file', str(self.scope), '--reviewer', 'R')
        self.assertEqual(rc, 0, err)

    def test_status_and_board_escape_names_an_older_writer_recorded(self):
        store = Store(self.root)
        try:
            with patch.object(registry, 'validate', lambda *args, **kwargs: None):
                store.append('agent', 'W' + ESCAPE, {'id': 'W' + ESCAPE, 'identity': 'identity-W', 'role': 'builder',
                             'client': 'manual', 'model': '', 'session': '', 'repo': str(self.repo), 'skills': []})
                store.append('task', 'C', {'id': 'X\r' + ESCAPE, 'title': 'Fix' + ESCAPE + ' tail', 'owner': 'W' + ESCAPE,
                             'repo': str(self.repo), 'base': registry.snapshot(self.repo)['head'],
                             'scope': store.artifact(self.scope), 'proof': None, 'expected': None, 'depends': [],
                             'full_gate': False, 'reviewer': 'R', 'requirements': []})
        finally:
            store.close()
        for args in (('status',), ('status', '--all'), ('board',), ('board', '--lang', 'en')):
            with self.subTest(args=args):
                rc, out, err = self.run_cli(*args)
                self.assertEqual(rc, 0, err)
                for raw in ('\x1b', '\x07', '\r', ' '):
                    self.assertNotIn(raw, out)
                self.assertIn('Fix\\x1b]0;forged-title\\x07\\x1b[2J\\u2028tail', out)
        rc, out, err = self.run_cli('status')
        self.assertIn('W\\x1b]0;forged-title', out)

    def test_an_older_multiline_title_renders_on_one_board_line_and_the_cli_refuses_it(self):
        title = 'Title | second\nline'
        rc, _, err = self.run_cli('task', '--actor', 'C', '--id', 'T', '--title', title, '--owner', 'C', '--repo', str(self.repo),
                                  '--scope-file', str(self.scope), '--proof-file', str(self.proof), '--reviewer', 'R')
        self.assertEqual(rc, 1); self.assertIn('name_invalid', err)
        store = Store(self.root)
        try:
            with patch.object(registry, 'validate', lambda *args, **kwargs: None):
                store.append('task', 'C', {'id': 'T', 'title': title, 'owner': 'C', 'repo': str(self.repo),
                             'base': registry.snapshot(self.repo)['head'], 'scope': store.artifact(self.scope),
                             'proof': None, 'expected': None, 'depends': [], 'full_gate': False, 'reviewer': 'R',
                             'requirements': []})
        finally:
            store.close()
        rc, out, err = self.run_cli('board')
        self.assertEqual(rc, 0, err)
        self.assertIn('| C (costruttore) | Title \\| second line |', out)


if __name__ == '__main__':
    unittest.main()
