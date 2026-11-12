"""Preference changes preserve presence after a window returns from stand-by."""
import unittest

import test_squadra as support
import model_policy
from registry import Error, plain


class PresenceChoice(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task
    cli = support.Workflow.cli

    def test_toggle_after_standby_and_resume_preserves_presence(self):
        model_policy.configure(self.store, 'A', 'on')
        self.assertEqual(self.cli('standby', '--actor', 'C', '--agent', 'A', '--reason', 'Synthetic pause'), 0)
        self.assertEqual(self.cli('standby', '--actor', 'C', '--agent', 'A', '--resume', '--reason', 'Synthetic return'), 0)
        presence = plain(self.store.resolved()['agents']['A']['availability'])
        for mode in ('off', 'on'):
            with self.subTest(mode=mode):
                try:
                    model_policy.configure(self.store, 'A', mode)
                except Error as exc:
                    self.fail('Changing the choice must preserve presence: ' + str(exc))
                state = self.store.resolved()
                self.assertEqual(model_policy.preference(state, 'A')['mode'], mode)
                self.assertEqual(plain(state['agents']['A']['availability']), presence)


if __name__ == '__main__':
    unittest.main()
