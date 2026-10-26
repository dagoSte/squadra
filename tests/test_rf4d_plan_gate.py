"""A registered plan gates the code read and the close at the Store, as the CLI's bindings do (M-9)."""
import unittest

from test_review_pool_gate_refusals import Fixture  # first: its support import puts scripts/ on the path
from registry import Error
from squadra import deliver


class PlanGate(Fixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.plan = self.base / "plan.md"
        self.plan.write_text("Synthetic plan.\n")

    def code_review(self, name, verdict="accepted"):
        task = self.store.state()["tasks"][name]
        self.store.append("review", "R", {"task": name, "stage": "code", "verdict": verdict,
                                          "binding": task["candidate"]["sha256"], "covers": [],
                                          "report": self.store.artifact(self.report)})

    def plan_review(self, name, verdict):
        self.store.append("review", "R", {"task": name, "stage": "plan", "verdict": verdict,
                                          "binding": self.store.artifact(self.plan)["sha256"], "covers": [],
                                          "report": self.store.artifact(self.report)})

    def delivered(self, name):
        self.proved(name)
        handoff = deliver(self.store, "A", name)["handoff"]
        self.store.append("ack", "R", {"message": handoff})

    def test_a_code_read_waits_for_the_current_plan_acceptance(self):
        self.full_task("P", reviewer="R")
        self.store.append("plan", "A", {"task": "P", "artifact": self.store.artifact(self.plan)})
        self.plan_review("P", "changes-required")
        self.delivered("P")
        with self.assertRaisesRegex(Error, "Current plan review pending"):
            self.code_review("P")
        self.assertEqual(self.store.state()["tasks"]["P"]["status"], "delivered")
        self.plan_review("P", "accepted")
        self.code_review("P")
        self.store.append("close", "C", {"task": "P"})
        self.assertEqual(self.store.state()["tasks"]["P"]["status"], "done")

    def test_a_close_waits_for_the_current_plan_acceptance(self):
        self.full_task("Q", reviewer="R")
        self.delivered("Q")
        self.code_review("Q")
        self.store.append("plan", "A", {"task": "Q", "artifact": self.store.artifact(self.plan)})
        with self.assertRaisesRegex(Error, "Current plan review pending"):
            self.store.append("close", "C", {"task": "Q"})
        self.assertEqual(self.store.state()["tasks"]["Q"]["status"], "reviewed")
        rc, err = self.run_cli("check", "--actor", "C", "--task", "Q")
        self.assertEqual(rc, 1)
        self.assertIn("Current plan review pending", err)

    def test_every_code_verdict_waits_for_the_plan_as_the_cli_review_does(self):
        self.full_task("P", reviewer="R")
        self.store.append("plan", "A", {"task": "P", "artifact": self.store.artifact(self.plan)})
        self.delivered("P")
        for verdict in ("changes-required", "blocked"):
            with self.subTest(verdict=verdict), self.assertRaisesRegex(Error, "Current plan review pending"):
                self.code_review("P", verdict)
        rc, err = self.run_cli("review", "--actor", "R", "--task", "P", "--stage", "code",
                               "--verdict", "changes-required", "--report", str(self.report))
        self.assertEqual(rc, 1)
        self.assertIn("Current plan review pending", err)
        self.assertEqual(self.store.state()["tasks"]["P"]["reviews"], [])


if __name__ == "__main__":
    unittest.main()
