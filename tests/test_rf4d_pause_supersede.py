"""A pause blocks close on every route (L-23); a superseded task cannot start, deliver or close (L-25)."""
import unittest

from test_review_pool_gate_refusals import Fixture  # first: its support import puts scripts/ on the path
from registry import Error, attention
from squadra import deliver


class PauseAndSupersession(Fixture, unittest.TestCase):
    def reviewed(self, name):
        self.full_task(name, reviewer="R")
        self.proved(name)
        handoff = deliver(self.store, "A", name)["handoff"]
        self.store.append("ack", "R", {"message": handoff})
        task = self.store.state()["tasks"][name]
        self.store.append("review", "R", {"task": name, "stage": "code", "verdict": "accepted",
                                          "binding": task["candidate"]["sha256"], "covers": [],
                                          "report": self.store.artifact(self.report)})

    def supersede(self, old, new):
        self.full_task(new)
        self.store.append("supersede", "C", {"task": old, "by": new, "reason": "Replaced by " + new})
        self.assertEqual(self.store.state()["tasks"][old]["superseded_by"], new)

    def test_pause_blocks_a_full_route_close(self):
        self.reviewed("P")
        self.store.append("pause", "C", {"reason": "Owner pause"})
        with self.assertRaisesRegex(Error, "Workspace paused"):
            self.store.append("close", "C", {"task": "P"})
        rc, err = self.run_cli("close", "--actor", "C", "--task", "P")
        self.assertEqual(rc, 1)
        self.assertIn("Workspace paused", err)
        self.assertEqual(self.store.state()["tasks"]["P"]["status"], "reviewed")
        self.store.append("resume", "C", {"reason": "Owner resume"})
        self.store.append("close", "C", {"task": "P"})
        self.assertEqual(self.store.state()["tasks"]["P"]["status"], "done")

    def test_a_superseded_task_cannot_start(self):
        self.full_task("S")
        self.supersede("S", "S2")
        with self.assertRaisesRegex(Error, "superseded"):
            self.store.append("start", "A", {"task": "S"})
        self.assertEqual(self.store.state()["tasks"]["S"]["status"], "ready")

    def test_a_superseded_task_cannot_deliver(self):
        self.full_task("S")
        self.store.append("start", "A", {"task": "S"})
        self.proved("S")
        self.supersede("S", "S2")
        with self.assertRaisesRegex(Error, "superseded"):
            deliver(self.store, "A", "S")
        self.assertEqual(self.store.state()["tasks"]["S"]["status"], "working")

    def test_a_superseded_task_cannot_close_and_leaves_no_hidden_step(self):
        self.reviewed("S")
        self.supersede("S", "S2")
        self.assertNotIn("S", [s["task"] for s in attention(self.store.state())["next_steps"]])
        with self.assertRaisesRegex(Error, "superseded"):
            self.store.append("close", "C", {"task": "S"})
        rc, err = self.run_cli("close", "--actor", "C", "--task", "S")
        self.assertEqual(rc, 1)
        self.assertIn("superseded", err)
        self.assertEqual(self.store.state()["tasks"]["S"]["status"], "reviewed")


if __name__ == "__main__":
    unittest.main()
