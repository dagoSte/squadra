"""Keep command output bounded while retaining the full recorded views."""
import contextlib
from copy import deepcopy
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import registry
from squadra import main
from test_supersede import new_store, add_task


class ShortOutput(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='sq-short-output-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base / 'registry'
        for target in ('registry.now', 'squadra.now'):
            clock = patch(target, return_value='2026-10-15T12:00:00+00:00')
            clock.start()
            self.addCleanup(clock.stop)
        self.store = new_store(self.root, self.base)
        self.addCleanup(self.store.close)
        self.store.batch([('message', 'C', {'id': f'INFO-{i}', 'to': 'A', 'body': 'Information'}, None)
                          for i in range(30)])
        for i in range(8):
            with patch('registry.now', return_value=f'2026-10-15T12:0{i}:00+00:00'):
                add_task(self.store, self.base, 'T' + str(i))
        self.at = '2026-10-15T14:00:00+00:00'

    def cli(self, *args):
        out = io.StringIO()
        with patch('registry.now', return_value=self.at), patch('squadra.now', return_value=self.at), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = main(['--root', str(self.root), *args])
        self.assertEqual(rc, 0, out.getvalue())
        return out.getvalue()

    def test_default_command_counts_information_without_reprinting_it(self):
        text = self.cli('status')
        self.assertNotIn('INFO-0', text)
        self.assertIn('ack: 38', text)
        self.assertIn('squadra status --all', text)
        self.assertEqual(text.count('- ack ·'), 5)

    def test_full_status_keeps_every_previous_line(self):
        # status shows the composition advice (D-STATUS-PIN); every other line stays as it was.
        expected = registry.table(self.store.state(), read_at=self.at, events=self.store.events(), advice=True)
        self.assertEqual(self.cli('status', '--all'), expected + '\n')
        self.assertIn('- ack · INFO-29 ·', expected)
        self.assertEqual(expected.count('- ack ·'), 38)

    def test_oldest_blocking_assignments_win_over_older_information(self):
        text = self.cli('status')
        shown = [line.split(' · ')[1] for line in text.splitlines() if line.startswith('- ack ·')]
        self.assertEqual(shown, ['assign-T' + str(i) for i in range(5)])
        self.store.append('ack', 'A', {'message': 'assign-T0'})
        text = self.cli('status')
        shown = [line.split(' · ')[1] for line in text.splitlines() if line.startswith('- ack ·')]
        self.assertEqual(shown, ['assign-T' + str(i) for i in range(1, 6)])

    def test_owner_requests_and_options_are_never_limited(self):
        for i in range(7):
            self.store.append('ask', 'C', {'id': 'Q' + str(i), 'question': 'Owner question ' + str(i),
                              'options': [{'id': 'A', 'label': 'Continue', 'say': 'Exact choice ' + str(i)},
                                          {'id': 'B', 'label': 'Stop', 'say': 'Stop'}],
                              'waiting': ['A'], 'task': 'T0', 'evidence': []})
        text = self.cli('status')
        for i in range(7):
            self.assertIn('Owner question ' + str(i), text)
            self.assertIn('Frase: Exact choice ' + str(i), text)

    def test_saved_projections_and_full_waits_are_unchanged(self):
        self.store.project()
        before = {name: (self.root / name).read_bytes() for name in ['state.json', 'stato.md']}
        state = self.store.state()
        waiting = deepcopy(registry.waits(state, self.at))
        self.cli('status')
        self.assertEqual({name: (self.root / name).read_bytes() for name in before}, before)
        self.assertEqual(registry.waits(self.store.state(), self.at), waiting)
        self.assertEqual(len(waiting), 38)

    def test_information_only_waits_are_counted_without_false_blockers(self):
        state = self.store.state()
        for task in state['tasks'].values():
            task['status'] = 'done'
        text = registry.table(state, read_at=self.at, compact=True)
        self.assertIn('ack: 30', text)
        self.assertNotIn('- ack ·', text)

    def test_task_waits_processes_and_active_window_pings_remain_visible(self):
        state = self.store.state()
        for task in state['tasks'].values():
            task['status'] = 'done'
        state['tasks']['T0'].update(status='delivered', reviewer='R', status_utc='2026-10-15T12:00:00+00:00')
        state['tasks']['T1'].update(status='reviewed', coordinator='C', status_utc='2026-10-15T12:00:00+00:00')
        state['runs']['RUN'] = {'id': 'RUN', 'task': 'T0', 'mode': 'proof', 'agent': 'A',
                                'status': 'running', 'utc': '2026-10-15T12:00:00+00:00'}
        state['runs']['CLEANUP'] = {**state['runs']['RUN'], 'id': 'CLEANUP', 'status': 'cleanup-unknown'}
        state['messages']['PING'] = {'id': 'PING', 'from': 'C', 'to': 'R', 'body': 'Ping', 'ping': True,
                                     'availability_event': None, 'utc': '2026-10-15T12:00:00+00:00', 'ack': None,
                                     'transport_all_failed': False}
        text = registry.table(state, read_at=self.at, compact=True)
        for kind in ('delivered', 'reviewed', 'heartbeat', 'cleanup-unknown', 'alive'):
            self.assertIn('- ' + kind + ' ·', text)
        self.assertNotIn('- ack ·', text)

    def test_current_handoff_ack_survives_while_stale_handoff_is_only_counted(self):
        state = self.store.state()
        for task in state['tasks'].values():
            task['status'] = 'done'
        task = state['tasks']['T0']
        task.update(status='delivered', delivery={'handoff': 'CURRENT'})
        for name in ('OLD', 'CURRENT'):
            state['messages'][name] = {'id': name, 'from': 'C', 'to': 'R', 'related_task': 'T0',
                                       'body': 'Read', 'utc': '2026-10-15T12:00:00+00:00', 'ack': None}
        text = registry.table(state, read_at=self.at, compact=True)
        self.assertIn('- ack · CURRENT ·', text)
        self.assertNotIn('- ack · OLD ·', text)

    def test_five_orphan_heartbeats_cannot_hide_a_delivered_read(self):
        state = self.store.state()
        for task in state['tasks'].values():
            task['status'] = 'done'
        state['tasks']['T0'].update(status='delivered', reviewer='R', status_utc='2026-10-15T13:00:00+00:00')
        for i in range(5):
            state['runs']['ORPHAN' + str(i)] = {'id': 'ORPHAN' + str(i), 'task': 'T0', 'mode': 'proof',
                'agent': 'A', 'status': 'running', 'utc': f'2026-10-15T12:0{i}:00+00:00'}
        before = deepcopy(registry.waits(state, self.at))
        text = registry.table(state, read_at=self.at, compact=True)
        shown = [line for line in text.splitlines() if line.startswith(('- delivered ·', '- heartbeat ·'))]
        self.assertEqual(len(shown), 2, text)
        self.assertTrue(shown[0].startswith('- delivered · T0 ·'), shown)
        self.assertIn('5 attese', shown[1])
        self.assertIn('7200s', shown[1])
        self.assertIn('la più vecchia', shown[1])
        self.assertIn('heartbeat: 5', text)
        self.assertEqual(registry.waits(state, self.at), before)
        full = registry.table(state, read_at=self.at)
        self.assertEqual(full.count('- heartbeat · T0 ·'), 5)

    def test_compact_next_steps_name_only_windows_holding_open_work(self):
        state = self.store.state()
        for task in state['tasks'].values():
            task['status'] = 'done'
        state['tasks']['T0'].update(status='delivered', reviewer='R', status_utc='2026-10-15T13:00:00+00:00')
        state['tasks']['T1'].update(status='working')
        text = registry.table(state, read_at=self.at, compact=True)
        self.assertIn('Prossimo passo per incarico:', text)
        block = text.split('Prossimo passo per incarico:', 1)[1]
        self.assertIn('| T0 | R / reviewer | Lettura non autore |', block)
        self.assertIn('| T1 | A / builder |', block)
        self.assertNotIn('| T2 |', block)
        self.assertNotIn('Sostituiti', block)

    def test_compact_next_steps_have_a_bound_and_an_explicit_remainder(self):
        text = self.cli('status')
        self.assertIn('Prossimo passo per incarico:', text)
        block = text.split('Prossimo passo per incarico:', 1)[1]
        self.assertEqual(sum(line.startswith('| T') for line in block.splitlines()), 5)
        self.assertIn('Altri 3 passi:', block)
        self.assertIn('squadra status --all', block)


if __name__ == '__main__':
    unittest.main()
