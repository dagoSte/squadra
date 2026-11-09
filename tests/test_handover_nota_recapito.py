"""A delivered pending message must survive a real coordinator handover."""
import contextlib
import io
import json
import unittest

import test_squadra as support
from registry import plain, standby
from squadra import main
import handover


class HandoverDeliveredNote(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    def task(self):
        rc, out, err = self.cli_result('task', '--actor', 'C', '--id', 'T', '--title', 'Synthetic assignment',
                                       '--owner', 'A', '--reviewer', 'R', '--repo', str(self.repo),
                                       '--scope-file', str(self.scope))
        self.assertEqual(rc, 0, out + err)

    def cli_result(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(['--root', str(self.root), *args])
        return rc, out.getvalue(), err.getvalue()

    def test_accept_coordinator_with_delivered_pending_mail_keeps_one_coordinator_and_the_mail(self):
        # Real transport and gate, no stub of the planner, validation or condition evaluator.
        rc, out, err = self.cli_result('transport', '--actor', 'C', '--message', 'assign-T', '--via', 'manual')
        self.assertEqual(rc, 0, out + err)
        self.store.append('message', 'A', {'id': 'NOTE-1', 'to': 'C', 'body': 'Preserve this pending question'})
        original = plain(self.store.view()['messages']['assign-T'])
        self.assertIsNone(original['ack'])
        self.assertEqual(original['transport']['via'], 'manual')
        outside = self.base / 'outside.md'
        outside.write_text('\n'.join(heading + '\nnessuno\n' for heading in handover.HEADINGS))
        rc, out, err = self.cli_result('handover', 'prepare', '--actor', 'C', '--from', 'C', '--to', 'New',
                                        '--outside-file', str(outside), '--owner-words', 'Open a new coordinator window',
                                        '--channel', 'typed-here')
        self.assertEqual(rc, 0, out + err)
        ident = next(iter(self.store.view()['handovers']))
        rc, out, err = self.cli_result('handover', 'accept', '--actor', 'New', '--id', ident,
                                        '--session', 'new-coordinator-session', '--client', 'claude')
        self.assertEqual(rc, 0, out + err)
        state = self.store.state()
        self.assertEqual(state['handovers'][ident]['status'], 'accepted')
        self.assertEqual([name for name, agent in state['agents'].items()
                          if agent['role'] == 'coordinator' and not standby(state, name)], ['New'])
        self.assertTrue(standby(state, 'C'))
        self.assertEqual(state['tasks']['T']['owner'], 'A')
        self.assertEqual(state['tasks']['T']['reviewer'], 'R')
        for key in ('body', 'to', 'from', 'transport', 'ack'):
            self.assertEqual(state['messages']['assign-T'][key], original[key])
        self.assertEqual(state['messages']['NOTE-1']['body'], 'Preserve this pending question')
        self.assertIsNone(state['messages']['NOTE-1']['ack'])
        note = json.loads(state['handovers'][ident]['accepted']['seat_note'])
        pending = {message['id']: message for message in note['pending_messages']}
        self.assertEqual(set(pending), {'assign-T', 'NOTE-1'})
        self.assertEqual(pending['assign-T']['transport'], original['transport'])
        for message in pending.values():
            self.assertNotIn('transport_first_utc', message)
            self.assertNotIn('transport_all_failed', message)
        onboarding = next(state['messages'][key] for key in state['handovers'][ident]['accepted']['outbox']
                          if '-onboard-New' in key)
        self.assertIn('Preserve this pending question', onboarding['body'])
        self.assertIn('assign-T', onboarding['body'])
        self.assertIsNone(onboarding['ack'])


if __name__ == '__main__':
    unittest.main()
