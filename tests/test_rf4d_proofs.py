"""A later failed proof on the delivered bytes stops check and close until a new proof passes (L-22)."""
import sys
import unittest

from test_review_pool_gate_refusals import Fixture  # first: its support import puts scripts/ on the path
from registry import Error
from runtime import execute
from squadra import deliver


class LaterFailedProof(Fixture, unittest.TestCase):
    def reviewed(self, name="F"):
        self.full_task(name, reviewer="R")
        self.proved(name)
        handoff = deliver(self.store, "A", name)["handoff"]
        self.store.append("ack", "R", {"message": handoff})
        task = self.store.state()["tasks"][name]
        self.store.append("review", "R", {"task": name, "stage": "code", "verdict": "accepted",
                                          "binding": task["candidate"]["sha256"], "covers": [],
                                          "report": self.store.artifact(self.report)})

    def failed(self, name="F"):
        result = execute(self.store, "A", name, argv=[sys.executable, "-c", "raise SystemExit(1)"])
        self.assertEqual(result["status"], "failed")
        proofs = self.store.state()["tasks"][name]["proofs"]
        self.assertEqual(proofs[-1]["after"], proofs[-2]["after"], "SETUP: the failure ran on the proved bytes")

    def test_a_later_failed_proof_on_the_same_bytes_stops_check_and_close(self):
        self.reviewed()
        self.failed()
        rc, err = self.run_cli("check", "--actor", "C", "--task", "F")
        self.assertEqual(rc, 1)
        self.assertIn("No complete native proof on current candidate", err)
        with self.assertRaisesRegex(Error, "No complete native proof on current candidate"):
            self.store.append("close", "C", {"task": "F"})
        self.assertEqual(self.store.state()["tasks"]["F"]["status"], "reviewed")

    def test_a_new_passing_proof_after_the_failure_restores_the_close(self):
        self.reviewed()
        self.failed()
        self.proved("F")
        rc, err = self.run_cli("check", "--actor", "C", "--task", "F")
        self.assertEqual(rc, 0, err)
        self.store.append("close", "C", {"task": "F"})
        self.assertEqual(self.store.state()["tasks"]["F"]["status"], "done")

    def test_a_delivery_after_a_later_failure_names_the_missing_proof(self):
        self.full_task("F", reviewer="R")
        self.proved("F")
        self.failed()
        with self.assertRaisesRegex(Error, "No passing proof on this exact source"):
            deliver(self.store, "A", "F")
        self.assertNotIn("delivery", self.store.state()["tasks"]["F"])

    def test_a_failure_on_other_bytes_does_not_stop_the_close(self):
        self.reviewed()
        (self.repo / "code.py").write_text("VALUE = 2\n")
        result = execute(self.store, "A", "F", argv=[sys.executable, "-c", "raise SystemExit(1)"])
        self.assertEqual(result["status"], "failed")
        (self.repo / "code.py").write_text("VALUE = 1\n")
        self.store.append("close", "C", {"task": "F"})
        self.assertEqual(self.store.state()["tasks"]["F"]["status"], "done")


if __name__ == "__main__":
    unittest.main()
