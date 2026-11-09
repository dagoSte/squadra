"""Council fixes before 3.14.0: idle parents of automatic lanes and per-task limits in context packets."""
import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import ripresa
from squadra import main
import test_squadra as support


class CouncilFixes(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task
    ack = support.Workflow.ack
    cli = support.Workflow.cli

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(['--root', str(self.root), *args])
        return code, out.getvalue(), err.getvalue()

    def test_K4_a_parent_with_open_lanes_is_not_reported_idle(self):
        self.task('F', owner='C')
        self.ack('F', owner='C')
        self.task('F.L1', owner='A')
        # lanes_apply records this parent/children relation in the assignment transaction.
        assignment = {'form': 'squadra-lanes-assignment/1', 'parent': 'F', 'lanes': [{'id': 'F.L1'}]}
        self.store.append('decision', 'C', {'id': 'lanes-F', 'reason': 'Automatic lanes',
                          'tasks': ['F', 'F.L1'], 'plan': self.store.artifact_bytes(
                              json.dumps(assignment).encode(), 'lanes-F.json', 'generated-lanes:F')})
        state = self.store.view()
        accepted = dt.datetime.fromisoformat(state['messages']['assign-F']['ack']['utc'])
        late = accepted + dt.timedelta(minutes=45)
        idle = {row['task'] for row in ripresa.idle_tasks(state, late)}
        self.store.release_views()
        self.assertNotIn('F', idle, 'The integration parent waits for its lanes by design')
        self.task('Z', owner='A')
        self.store.append('supersede', 'C', {'task': 'F.L1', 'by': 'Z', 'reason': 'Synthetic: lane withdrawn'})
        idle = {row['task'] for row in ripresa.idle_tasks(self.store.view(), late)}
        self.store.release_views()
        self.assertIn('F', idle, 'Without open lanes an accepted, workless parent is idle again')

    def test_K5_the_context_packet_shows_the_owner_granted_review_limit(self):
        self.assertEqual(self.cli('decision', '--actor', 'C', '--reason', 'Two more reads', '--extend-budget', 'T',
                                  '--stage', 'code', '--by', '2', '--owner-words', 'Due letture in piu',
                                  '--channel', 'typed-here'), 0)
        code, out, err = self.run_cli('context', '--task', 'T', '--json', '--max-chars', '32000')
        self.assertEqual(code, 0, err)
        packet = json.JSONDecoder().raw_decode(out)[0]
        global_code = self.store.state()['workspace']['limits']['code']
        self.assertEqual(packet['limits']['code'], global_code + 2)
        self.assertEqual(packet['limits']['plan'], self.store.state()['workspace']['limits']['plan'])


if __name__ == '__main__':
    unittest.main()
