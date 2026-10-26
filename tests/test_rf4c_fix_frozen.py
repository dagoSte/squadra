"""A frozen defect-fix delivery is read and closed on its frozen copy, as check measures it."""
import unittest

import test_fix_challenge as fix
import test_squadra as support
from squadra import deliver


class FrozenDefectFix(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task
    ack = support.Workflow.ack
    cli = support.Workflow.cli
    proof = support.Workflow.proof
    configure = fix.FixChallenge.configure
    challenge = fix.FixChallenge.challenge
    review = fix.FixChallenge.review

    def frozen_task(self, name='F'):
        self.assertEqual(self.cli('task', '--actor', 'C', '--id', name, '--title', 'Fix', '--owner', 'A',
                                  '--reviewer', 'R', '--repo', str(self.repo), '--scope-file', str(self.scope),
                                  '--defect-fix'), 0)
        self.assertEqual(self.store.state()['tasks'][name]['delivery_policy'], 'frozen-v1')
        self.configure(name=name); self.challenge(name=name)

    def test_builder_edit_after_accepted_read_does_not_split_check_and_close(self):
        self.frozen_task(); self.review('F')
        self.assertTrue(self.store.state()['tasks']['F']['delivery']['frozen'])
        (self.repo / 'notes.txt').write_text('builder keeps working after the accepted read\n')
        self.assertEqual(self.cli('check', '--actor', 'C', '--task', 'F'), 0)
        self.assertEqual(self.cli('close', '--actor', 'C', '--task', 'F'), 0)
        self.assertEqual(self.store.state()['tasks']['F']['status'], 'done')

    def test_builder_edit_before_the_read_still_lets_the_frozen_candidate_be_accepted(self):
        self.frozen_task()
        self.assertEqual(self.proof(task='F')['status'], 'passed')
        handoff = deliver(self.store, 'A', 'F')['handoff']
        self.store.append('ack', 'R', {'message': handoff})
        (self.repo / 'notes.txt').write_text('builder keeps working before the read\n')
        self.assertEqual(self.cli('review', '--actor', 'R', '--task', 'F', '--verdict', 'accepted',
                                  '--report', str(self.report)), 0)
        self.assertEqual(self.store.state()['tasks']['F']['status'], 'reviewed')
        self.assertEqual(self.cli('close', '--actor', 'C', '--task', 'F'), 0)

    def test_a_changed_frozen_copy_still_refuses_close(self):
        self.frozen_task(); self.review('F')
        frozen = self.store.state()['tasks']['F']['delivery']['frozen']['sha256']
        code = self.root / 'candidates' / frozen / 'repo' / 'code.py'
        code.chmod(0o644); code.write_text('VALUE = 3\n')
        self.assertNotEqual(self.cli('close', '--actor', 'C', '--task', 'F'), 0)
        self.assertNotEqual(self.store.state()['tasks']['F']['status'], 'done')


if __name__ == '__main__':
    unittest.main()
