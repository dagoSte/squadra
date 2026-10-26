"""An ACK moves a task only as its owner's ACK of the task's own assignment (G-1).

A new message carrying ``task`` is the coordinator's ``assign-<task>`` to the owner of a waiting
task, and a new ACK of a message carrying ``task`` is that owner's ACK of it, before the task
closes. So no message or ACK can reopen a closed task, start work without the assignment ACK or
free a read-at-gate task from its gate, and recorded history replays exactly as written.
"""
import unittest
from unittest.mock import patch

from test_review_pool_gate_refusals import Fixture  # first: its support import puts scripts/ on the path
from registry import Error, apply, gate_pending, initial
from squadra import deliver


class AssignmentAck(Fixture, unittest.TestCase):
    def assigned(self, name, owner="A"):
        """A task and its assignment, without the owner's ACK."""
        data = {"id": name, "title": "Synthetic " + name, "owner": owner, "repo": str(self.repo),
                "base": self.store.state()["tasks"]["T"]["base"], "scope": self.store.artifact(self.scope),
                "proof": None, "expected": None, "depends": [], "full_gate": False, "reviewer": None,
                "requirements": []}
        self.store.batch([("task", "C", data, None), ("message", "C", {
            "id": "assign-" + name, "to": owner, "task": name, "body": "Scoped work"}, None)])

    def closed(self, name="D"):
        self.full_task(name, reviewer="R")
        self.proved(name)
        handoff = deliver(self.store, "A", name)["handoff"]
        self.store.append("ack", "R", {"message": handoff})
        task = self.store.state()["tasks"][name]
        self.store.append("review", "R", {"task": name, "stage": "code", "verdict": "accepted",
                                          "binding": task["candidate"]["sha256"], "covers": [],
                                          "report": self.store.artifact(self.report)})
        self.store.append("close", "C", {"task": name})
        self.assertEqual(self.store.state()["tasks"][name]["status"], "done")

    def forged(self, actor, message, task, to=None):
        """Send and ACK a task message; report whether the Store accepted it."""
        try:
            self.store.append("message", actor, {"id": message, "to": to or actor, "task": task, "body": "x"})
            self.store.append("ack", to or actor, {"message": message})
        except Error:
            return False
        return True

    def test_only_the_coordinators_assignment_to_a_waiting_owner_carries_task(self):
        self.closed("D")
        self.assigned("U")
        cases = [("A", {"id": "reopen-D", "to": "A", "task": "D"}),       # builder, closed task
                 ("R", {"id": "take-U", "to": "R", "task": "U"}),         # outsider, unACKed task
                 ("C", {"id": "again-U", "to": "A", "task": "U"}),        # second assignment, another name
                 ("C", {"id": "assign-D", "to": "A", "task": "D"}),       # the name already exists
                 ("C", {"id": "assign-X", "to": "A", "task": "X"})]       # unknown task
        for actor, data in cases:
            with self.subTest(message=data["id"]), self.assertRaisesRegex(Error, "assignment_invalid|already exists"):
                self.store.append("message", actor, {**data, "body": "x"})
        name = "V"
        data = {"id": name, "title": "Assigned later", "owner": "A", "repo": str(self.repo),
                "base": self.store.state()["tasks"]["T"]["base"], "scope": self.store.artifact(self.scope),
                "proof": None, "expected": None, "depends": [], "full_gate": False, "reviewer": None,
                "requirements": []}
        self.store.append("task", "C", data)
        with self.assertRaisesRegex(Error, "assignment_invalid"):
            self.store.append("message", "C", {"id": "assign-V", "to": "R", "task": "V", "body": "x"})
        self.store.append("message", "C", {"id": "assign-V", "to": "A", "task": "V", "body": "Scoped work"})
        self.store.append("ack", "A", {"message": "assign-V"})
        self.assertEqual(self.store.state()["tasks"]["V"]["status"], "ready")

    def test_a_forged_ack_cannot_reopen_a_closed_task(self):
        self.closed("D")
        self.forged("A", "reopen-D", "D")
        self.assertEqual(self.store.state()["tasks"]["D"]["status"], "done")
        with self.assertRaisesRegex(Error, "Task already closed"):
            self.store.append("start", "A", {"task": "D"})

    def test_a_forged_ack_cannot_start_work_without_the_assignment_ack(self):
        self.assigned("U")
        self.forged("A", "self-U", "U")
        self.forged("R", "take-U", "U")
        self.assertEqual(self.store.state()["tasks"]["U"]["status"], "waiting-ack")
        with self.assertRaisesRegex(Error, "Assignment has no recipient ACK"):
            self.store.append("start", "A", {"task": "U"})
        self.ack("U")
        self.store.append("start", "A", {"task": "U"})
        self.assertEqual(self.store.state()["tasks"]["U"]["status"], "working")

    def test_a_forged_ack_cannot_free_a_read_at_gate_task_from_its_release_gate(self):
        self.closed_at_gate("L1")
        self.gate_delivered("G")
        self.gate_read("G")
        self.forged("A", "reopen-L1", "L1")
        task = self.store.state()["tasks"]["L1"]
        self.assertEqual(task["status"], "done")
        self.assertTrue(gate_pending(task))
        with self.assertRaisesRegex(Error, "gate_read_missing: L1"):
            self.store.append("close", "C", {"task": "G"})

    def test_a_task_message_recorded_before_this_rule_cannot_be_acknowledged(self):
        """Recorded history replays as written; the rule binds the new ACK instead."""
        self.closed("D")
        self.closed_at_gate("L1")
        self.assigned("U")
        with patch("registry.validate"):  # as an earlier version accepted them
            for message, task in (("reopen-D", "D"), ("reopen-L1", "L1"), ("self-U", "U")):
                self.store.append("message", "A", {"id": message, "to": "A", "task": task, "body": "x"})
            self.store.append("message", "C", {"id": "assign-W", "to": "R", "task": "U", "body": "x"})
        for actor, message in (("A", "reopen-D"), ("A", "reopen-L1"), ("A", "self-U"), ("R", "assign-W")):
            with self.subTest(message=message), self.assertRaisesRegex(Error, "assignment_ack_invalid"):
                self.store.append("ack", actor, {"message": message})
        state = self.store.state()
        self.assertEqual((state["tasks"]["D"]["status"], state["tasks"]["L1"]["status"]), ("done", "done"))
        self.assertTrue(gate_pending(state["tasks"]["L1"]))
        self.assertEqual(state["tasks"]["U"]["status"], "waiting-ack")
        self.ack("U")
        self.assertEqual(self.store.state()["tasks"]["U"]["status"], "ready")

    def test_replay_of_recorded_acks_is_unchanged(self):
        self.closed("D")
        state = initial()
        for event in self.store.events():
            apply(state, event)
        self.assertEqual(state["tasks"]["D"]["status"], "done")
        self.assertEqual(self.store.audit()["cache"], "matched")

if __name__ == "__main__":
    unittest.main()
