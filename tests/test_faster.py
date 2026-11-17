"""Closing advice uses recorded intervals and never turns a remedy into a gate."""
from datetime import datetime, timedelta, timezone
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

import faster
from registry import Error, Store, apply, plain
from squadra import bindings, main
from test_errata import Fixture, run

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = datetime(2026, 1, 1, tzinfo=timezone.utc)


def put(events, kind, data, minute=0):
    event = {'seq': len(events) + 1, 'kind': kind, 'data': data, 'actor': 'C',
             'utc': (ORIGIN + timedelta(minutes=minute)).isoformat()}
    events.append(event)
    return event


def history(task='T', longest='build'):
    events = []
    put(events, 'task', {'id': task}, 0)
    put(events, 'message', {'id': 'assign-' + task, 'task': task}, 0)
    minute = 60 if longest == 'ack' else 1
    put(events, 'ack', {'message': 'assign-' + task}, minute)
    minute += 60 if longest == 'build' else 1
    put(events, 'deliver', {'task': task}, minute)
    minute += 60 if longest == 'read' else 1
    put(events, 'review', {'task': task, 'stage': 'code',
                         'verdict': 'changes-required' if longest == 'fix' else 'accepted'}, minute)
    if longest == 'fix':
        minute += 60
        put(events, 'deliver', {'task': task}, minute)
        minute += 1
        put(events, 'review', {'task': task, 'stage': 'code', 'verdict': 'accepted'}, minute)
    put(events, 'close', {'task': task}, minute + 1)
    return events


class Measurements(unittest.TestCase):
    def test_each_phase_and_minutes_are_measured_from_events(self):
        for phase in faster.PHASES:
            with self.subTest(phase=phase):
                value = faster.measure(history(longest=phase), 'T')
                self.assertEqual((value['phase'], value['minutes']), (phase, 60))

    def test_missing_ack_delivery_or_review_never_invents_times(self):
        for kind in ('ack', 'deliver', 'review', 'close'):
            with self.subTest(kind=kind):
                events = [event for event in history() if event['kind'] != kind]
                self.assertIsNone(faster.measure(events, 'T'))

    def test_partition_matches_dashboard_including_plan_wait_and_corrections(self):
        events = history(longest='fix')
        put(events, 'plan', {'task': 'T'}, 1)
        put(events, 'review', {'task': 'T', 'stage': 'plan', 'verdict': 'accepted'}, 1.5)
        events.sort(key=lambda event: event['utc'])
        for seq, event in enumerate(events, 1):
            event['seq'] = seq
        steps = [{'id': 'closed', 'reached': True, 'utc': events[-1]['utc']}]
        graph = []
        for event in events:
            kind, data = event['kind'], event['data']
            node_kind = ('delivery' if kind == 'deliver' else
                         data['stage'] + '-verdict' if kind == 'review' else kind)
            graph.append({**event, **data, 'kind': node_kind, 'task': 'T'})
        state = {'progress': {'T': {'created': events[0]['utc'], 'steps': steps}},
                 'graph': {'nodes': graph}, 'messages': {'assign-T': {'task': 'T',
                 'ack': {'utc': next(event['utc'] for event in events if event['kind'] == 'ack')}}}}
        source = (ROOT / 'assets/dashboard.html').read_text()
        source = 'function timeStamp' + source.split('function timeStamp', 1)[1].split('function taskTimeDetails', 1)[0]
        program = 'const state=JSON.parse(process.argv[1]);\n' + source
        program += '\nconsole.log(JSON.stringify(taskTimePhases({id:"T",status:"done"})))'
        node = shutil.which('node')
        self.assertIsNotNone(node)
        result = subprocess.run([node, '-e', program, json.dumps(state)], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        dashboard = json.loads(result.stdout)
        measured = faster.measure(events, 'T')
        self.assertEqual(measured['seconds'], dashboard['values'])
        self.assertEqual(measured['plan_minutes'], 0.5)

    def test_plan_is_visible_but_not_a_declarable_phase(self):
        events = []
        for kind, data, minute in [('task', {'id': 'T'}, 0),
                ('message', {'id': 'assign-T', 'task': 'T'}, 0), ('ack', {'message': 'assign-T'}, 1),
                ('plan', {'task': 'T'}, 2),
                ('review', {'task': 'T', 'stage': 'plan', 'verdict': 'accepted'}, 102),
                ('deliver', {'task': 'T'}, 103),
                ('review', {'task': 'T', 'stage': 'code', 'verdict': 'accepted'}, 104),
                ('close', {'task': 'T'}, 105)]:
            put(events, kind, data, minute)
        measured = faster.measure(events, 'T')
        self.assertEqual((measured['phase'], measured['minutes'], measured['plan_minutes']), ('build', 2, 100))


class RemedyEvaluation(unittest.TestCase):
    def corpus(self):
        remedy = {'seq': 10, 'task': 'original', 'phase': 'build', 'minutes': 60,
                  'remedy': 'Split independent changes before assignment',
                  'assigned_before': ['original', 'already-assigned'], 'following': ['one', 'two', 'three']}
        histories = {task: history(task, phase) for task, phase in
                     [('already-assigned', 'build'), ('one', 'build'), ('two', 'read'), ('three', 'build')]}
        return {'faster': [remedy]}, histories

    def test_two_of_first_three_new_assignments_warns_across_builders(self):
        state, histories = self.corpus()
        warnings = faster.ineffective(state, histories, 'three')
        self.assertEqual(len(warnings), 1)
        self.assertEqual((warnings[0]['event'], warnings[0]['repeated']), (10, 2))
        self.assertEqual(warnings[0]['tasks'], ['one', 'two', 'three'])
        self.assertEqual(faster.ineffective(state, histories, 'two'), [])

    def test_under_three_and_only_one_recurrence_give_no_warning(self):
        state, histories = self.corpus()
        state['faster'][0]['following'] = ['one', 'two']
        self.assertEqual(faster.ineffective(state, histories, 'two'), [])
        state['faster'][0]['following'].append('three')
        histories['three'] = history('three', 'read')
        self.assertEqual(faster.ineffective(state, histories, 'three'), [])

    def test_warning_does_not_repeat_on_fourth_close(self):
        state, histories = self.corpus()
        histories['four'] = history('four')
        self.assertEqual(faster.ineffective(state, histories, 'four'), [])


class RegistryAndCLI(Fixture, unittest.TestCase):
    def close_task(self):
        self.delivered()
        self.store.append('review', 'R', {'task': 'T', 'stage': 'code', 'verdict': 'accepted',
            'binding': bindings(self.store, 'T', require_review=False)['sha256'],
            'report': self.store.artifact(self.report), 'covers': []})
        return run(self.root, 'close', '--actor', 'C', '--task', 'T')

    def test_close_prints_measured_phase_and_faster_survives_reopen(self):
        rc, result, err = self.close_task()
        self.assertEqual(rc, 0, err)
        self.assertIn('min', err)
        phase = result['faster']['measured']['phase']
        rc, data, err = run(self.root, 'faster', '--actor', 'C', '--task', 'T',
                            '--phase', phase, '--remedy', 'Prepare the next assignment earlier')
        self.assertEqual(rc, 0, err)
        self.assertGreaterEqual(data['minutes'], 0)
        reopened = Store(self.root)
        self.addCleanup(reopened.close)
        event = reopened.events()[-1]
        self.assertEqual((event['kind'], event['data']['phase']), ('faster', phase))
        self.assertEqual(reopened.state()['tasks']['T']['status'], 'done')

    def test_invalid_events_and_actor_do_not_commit(self):
        self.close_task()
        data = {'task': 'T', 'phase': 'build', 'minutes': 1, 'remedy': 'Prepare earlier'}
        before = self.last_seq()
        cases = [('A', data)]
        for field, values in {'task': ['absent', []], 'phase': ['plan', []],
                'minutes': [True, -1, float('nan'), float('inf'), '1', []],
                'remedy': ['', ' ', 'bad\nline', 'x' * 1001]}.items():
            cases.extend(('C', {**data, field: value}) for value in values)
        cases.append(('C', {**data, 'extra': True}))
        for actor, value in cases:
            with self.subTest(actor=actor, data=value), self.assertRaises(Error):
                self.store.append('faster', actor, value)
        self.assertEqual(self.last_seq(), before)

    def test_missing_timing_and_unknown_phase_refuse_without_recording(self):
        rc, _, err = run(self.root, 'faster', '--actor', 'C', '--task', 'T',
                         '--phase', 'build', '--remedy', 'Prepare earlier')
        self.assertEqual(rc, 1)
        self.assertIn('faster_timing_unavailable', err)
        rc, _, _ = run(self.root, 'faster', '--actor', 'C', '--task', 'T',
                        '--phase', 'plan', '--remedy', 'Prepare earlier')
        self.assertEqual(rc, 2)

    def test_compact_faster_reply_stays_a_bounded_receipt(self):
        self.close_task()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), patch.object(Store, 'events', side_effect=AssertionError('Full replay')):
            rc = main(['--root', str(self.root), 'faster', '--actor', 'C', '--task', 'T',
                       '--phase', 'build', '--remedy', 'Prepare earlier'])
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out.getvalue())['committed'])
        self.assertLessEqual(len(out.getvalue().encode()), 200)

    def test_ineffectiveness_is_only_a_warning_at_close(self):
        warning = {'phase': 'build', 'repeated': 2, 'remedy': 'Old remedy'}
        with patch.object(faster, 'ineffective', return_value=[warning]):
            rc, result, err = self.close_task()
        self.assertEqual(rc, 0, err)
        self.assertEqual(result['faster']['ineffective'], [warning])
        self.assertIn('inefficace', err)
        self.assertEqual(self.store.state()['tasks']['T']['status'], 'done')

    def test_only_new_assignments_count_in_close_event_order(self):
        self.task('already-assigned')
        self.close_task()
        rc, _, err = run(self.root, 'faster', '--actor', 'C', '--task', 'T',
                         '--phase', 'build', '--remedy', 'Prepare earlier')
        self.assertEqual(rc, 0, err)
        for task in ('one', 'two', 'three', 'four'):
            self.task(task)
        state = plain(self.store.state())
        for task in ('already-assigned', 'two', 'one', 'three', 'four'):
            # Identical UTC stamps must not reorder the recorded close sequence.
            apply(state, {'kind': 'close', 'actor': 'C', 'utc': ORIGIN.isoformat(),
                          'data': {'task': task}, 'seq': state['event_count'] + 1})
        self.assertEqual(state['faster'][0]['following'], ['two', 'one', 'three'])

    def test_all_bridges_replay_faster_without_importing_its_writer(self):
        self.close_task()
        self.assertEqual(run(self.root, 'faster', '--actor', 'C', '--task', 'T',
                             '--phase', 'build', '--remedy', 'Prepare earlier')[0], 0)
        program = ('import sys;sys.path.insert(0,sys.argv[1]);import registry;'
                   's=registry.Store(sys.argv[2],read_only=True);'
                   'st=s.state(events=s.events(),resolve=False,with_waits=False);s.close();'
                   'assert st["faster"][0]["task"]=="T";'
                   'assert "faster" not in sys.modules;print("read")')
        for name in ('memoranda', 'playwright', 'sentry'):
            with self.subTest(bridge=name):
                result = subprocess.run([sys.executable, '-I', '-B', '-c', program,
                    str(ROOT / 'plugins' / name / 'bridge'), str(self.root)],
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('read', result.stdout)


if __name__ == '__main__':
    unittest.main()
