"""A diagnostic is the tool's own receipt: written by `system`, and a refusal receipt keeps its shape (L-24).

A refusal receipt names its command, and possibly its task, and is what puts a «delivery
refused» note into a task's story; a seat cannot write one, and none carries other fields.
"""
import unittest

from test_review_pool_gate_refusals import Fixture  # first: its support import puts scripts/ on the path
from registry import Error, now, task_progress


class DiagnosticReceipts(Fixture, unittest.TestCase):
    def proof_notes(self, name="T"):
        steps = {s["id"]: s for s in task_progress(self.store.events())[name]["steps"]}
        return [n["code"] for n in steps["proof"]["notes"]]

    def test_a_seat_cannot_write_a_diagnostic(self):
        refusal = {"command": "deliver", "error": "No passing proof on this exact source", "utc": now(), "task": "T"}
        for actor in ("A", "R", "C", "nobody"):
            with self.subTest(actor=actor), self.assertRaisesRegex(Error, "diagnostic_invalid"):
                self.store.append("diagnostic", actor, refusal)
        self.assertNotIn("delivery-refused", self.proof_notes())

    def test_a_refusal_receipt_keeps_the_cli_shape(self):
        bad = [{"command": "deliver", "error": 7, "task": "T"},
               {"command": ["deliver"], "error": "x", "task": "T"},
               {"command": "deliver", "error": "x", "task": {"id": "T"}},
               {"command": "deliver", "error": "x", "task": "T", "utc": 5},
               {"command": "deliver", "error": "x", "task": "T", "tone": "good"},
               {"task": "T", "error": "x"}]
        for data in bad:
            with self.subTest(data=data), self.assertRaisesRegex(Error, "diagnostic_invalid"):
                self.store.append("diagnostic", "system", data)
        self.assertNotIn("delivery-refused", self.proof_notes())

    def test_the_cli_receipt_and_system_notes_are_recorded(self):
        rc, err = self.run_cli("deliver", "--actor", "A", "--task", "T")
        self.assertEqual(rc, 1)
        receipt = self.store.events()[-1]
        self.assertEqual((receipt["kind"], receipt["actor"], set(receipt["data"])),
                         ("diagnostic", "system", {"command", "error", "utc", "task"}))
        self.store.append("diagnostic", "system", {"command": "deliver", "error": "x"})
        self.store.append("diagnostic", "system", {"reason": "A system note without a task"})
        self.assertEqual(self.store.events()[-1]["data"], {"reason": "A system note without a task"})


if __name__ == "__main__":
    unittest.main()
