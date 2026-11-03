"""Replacement tasks keep dashboard progress and lifecycle evidence visible."""
from copy import deepcopy
import unittest

import test_replacement as support
from dashboard import VerifiedView
from registry import task_progress


class ProgressReplace(unittest.TestCase):
    setUp = support.Replacement.setUp
    git = support.Replacement.git
    task = support.Replacement.task
    req = support.Replacement.req
    replace = support.Replacement.replace

    def test_takeover_records_successor_and_keeps_original_history(self):
        self.store.append('ack', 'A', {'message': 'assign-T'})
        self.store.append('start', 'A', {'task': 'T'})
        successor = self.replace(self.req(force=True))['successors']['T']
        events = self.store.events()
        before = deepcopy(events)
        replacement = next(event for event in events if event['kind'] == 'replace')
        try:
            progress = task_progress(events)
        except KeyError as error:
            self.fail(f'Replacement progress must include the successor: {error}')
        self.assertEqual(progress['T']['state']['id'], 'superseded')
        self.assertEqual(progress['T']['state']['by'], successor)
        self.assertTrue(progress['T']['steps'][2]['reached'])
        self.assertEqual(progress[successor]['created'], replacement['utc'])
        self.assertEqual(progress[successor]['status'], 'waiting-ack')
        self.assertFalse(any(step['reached'] for step in progress[successor]['steps']))
        self.store.append('ack', 'B', {'message': 'assign-' + successor})
        self.store.append('start', 'B', {'task': successor})
        started = task_progress(self.store.events())[successor]
        self.assertEqual(started['status'], 'working')
        self.assertTrue(started['steps'][2]['reached'])
        self.assertEqual(events, before, 'A projection must not rewrite recorded events')

    def test_dashboard_cold_and_cached_reads_include_repeated_takeovers(self):
        warm = VerifiedView(self.root)
        self.addCleanup(warm.close)
        warm.read()
        first = self.replace()['successors']['T']
        second = self.replace(self.req({'B': 'D'}, sessions={'D': 'next-session'}))['successors'][first]
        cold = VerifiedView(self.root)
        self.addCleanup(cold.close)
        for view in (warm, cold):
            with self.subTest(view='warm' if view is warm else 'cold'):
                try:
                    events, state, compact = view.read()
                except KeyError as error:
                    self.fail(f'Dashboard must serve replacement tasks: {error}')
                self.assertEqual(set(state['progress']), set(state['tasks']))
                self.assertEqual(state['progress'][first]['state']['by'], second)
                self.assertEqual(state['progress'][second]['status'], 'waiting-ack')
                self.assertIn(second, compact['tasks'])
                self.assertEqual(state['event_count'], len(events))
                self.assertEqual(view.read()[1]['progress'], state['progress'])

    def test_bundled_lifecycle_keeps_each_stage_and_parent_timestamp(self):
        events = self.store.events()
        stamp, sequence = '2026-11-03T09:00:00+00:00', events[-1]['seq'] + 1
        operations = [
            ('plan', 'A', {'task': 'T', 'artifact': {'sha256': 'plan'}}),
            ('review', 'R', {'task': 'T', 'stage': 'plan', 'verdict': 'accepted', 'binding': 'plan'}),
            ('start', 'A', {'task': 'T'}),
            ('run.start', 'A', {'id': 'proof', 'task': 'T', 'mode': 'proof'}),
            ('run.end', 'A', {'id': 'proof', 'status': 'passed', 'native_rc': 0,
                             'after': {'sha256': 'candidate'}}),
            ('deliver', 'A', {'task': 'T', 'snapshot': {'sha256': 'candidate'}}),
            ('review', 'R', {'task': 'T', 'stage': 'code', 'verdict': 'accepted', 'binding': 'candidate'}),
            ('close', 'C', {'task': 'T'}),
        ]
        events.append({'id': 'bundled', 'kind': 'replace', 'actor': 'C', 'utc': stamp, 'seq': sequence,
                       'data': {'request': {}, 'successors': {}, 'outbox': [], 'unresolved_runs': [],
                                'operations': [{'kind': kind, 'actor': actor, 'data': data}
                                               for kind, actor, data in operations]}})
        before = deepcopy(events)
        progress = task_progress(events)['T']
        self.assertEqual(progress['status'], 'done')
        self.assertTrue(all(step['reached'] for step in progress['steps']))
        self.assertTrue(all(step['utc'] == stamp for step in progress['steps']))
        notes = {note['code']: note for step in progress['steps'] for note in step['notes']}
        self.assertEqual(notes['plan-accepted']['tone'], 'good')
        self.assertEqual(notes['proof']['native_rc'], 0)
        self.assertEqual(notes['delivered']['utc'], stamp)
        self.assertEqual(notes['code-accepted']['tone'], 'good')
        self.assertEqual(events, before)


if __name__ == '__main__':
    unittest.main()
