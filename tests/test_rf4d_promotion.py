"""A light task is promoted to the full route only when none of its runs is running or unverified (L-26)."""
import unittest
from unittest.mock import patch

import test_light_route as light  # first: its support import puts scripts/ on the path
import runtime
from registry import Error


class PromotionWaitsForRuns(unittest.TestCase):
    setUp = light.support.Workflow.setUp
    git = light.support.Workflow.git
    task = light.support.Workflow.task
    ack = light.support.Workflow.ack
    register = light.LightRoute.register
    change = light.LightRoute.change
    proof = light.LightRoute.proof
    promote = light.LightRoute.promote

    def test_a_cleanup_unknown_run_blocks_promotion(self):
        self.register()
        self.change()
        with patch.object(runtime, "terminate", side_effect=OSError("synthetic cleanup failure")):
            result = self.proof()
        self.assertEqual(result["status"], "cleanup-unknown")
        with self.assertRaisesRegex(Error, "Wait for the task's active run before promotion"):
            self.promote()
        self.assertEqual(self.store.state()["tasks"]["L"]["route"], "light")

    def test_an_ended_run_does_not_block_promotion(self):
        self.register()
        self.change()
        self.assertEqual(self.proof()["status"], "passed")
        self.promote()
        self.assertEqual(self.store.state()["tasks"]["L"]["route"], "full")


if __name__ == "__main__":
    unittest.main()
