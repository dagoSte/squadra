"""Idle warnings use recorded work and lane relationships, rather than equal bytes or names."""
import datetime as dt
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1] / 'scripts')]
import ripresa
from registry import plain
from replacement import execute as replace
import test_squadra as support


class IdleTasks(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task
    ack = support.Workflow.ack

    def test_successor_registering_the_identical_inherited_plan_counts_as_activity(self):
        self.ack()
        plan = self.base / 'plan.md'
        plan.write_text('The implementation plan\n')
        artifact = self.store.artifact(plan)
        self.store.append('plan', 'A', {'task': 'T', 'artifact': artifact})
        request = {'map': {'A': 'B'}, 'sessions': {'B': 'new-session'}, 'client': 'codex',
                   'carry': 'useful', 'force': True, 'reason': 'Replace the exhausted window'}
        with patch('squadra.codex_queue', return_value={'delivered': True, 'native_rc': 0}):
            successor = replace(self.store, 'C', request, False)['successors']['T']
        self.ack(successor, owner='B')
        state = self.store.view()
        late = dt.datetime.fromisoformat(state['messages']['assign-' + successor]['ack']['utc']) + dt.timedelta(minutes=45)
        self.assertEqual(state['tasks'][successor]['plan'], artifact)
        self.assertIn(successor, {r['task'] for r in ripresa.idle_tasks(state, late)},
                      'Merely inheriting the predecessor\'s plan is not work by the successor')
        self.store.release_views()
        self.store.append('plan', 'B', {'task': successor, 'artifact': artifact})
        before = self.store.events()
        for representation in ('view', 'plain', 'replay'):
            with self.subTest(representation=representation):
                state = self.store.state() if representation == 'replay' else self.store.view()
                if representation == 'plain':
                    state = plain(state)
                self.assertEqual(state['tasks'][successor]['plan'], artifact)
                self.assertNotIn(successor, {r['task'] for r in ripresa.idle_tasks(state, late)},
                                 'Registering the same plan on the successor is recorded work')
                self.store.release_views()
        self.assertEqual(self.store.events(), before, 'Inspecting activity never writes')

    def test_an_unrelated_dot_l_task_does_not_hide_an_idle_assignment(self):
        self.task('FIX')
        self.ack('FIX')
        self.task('FIX.LOGIN')
        state = self.store.view()
        late = dt.datetime.fromisoformat(state['messages']['assign-FIX']['ack']['utc']) + dt.timedelta(minutes=45)
        self.assertIn('FIX', {r['task'] for r in ripresa.idle_tasks(state, late)},
                      'An unrelated task name is not a recorded automatic lane')


if __name__ == '__main__':
    unittest.main()
