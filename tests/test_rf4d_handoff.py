"""A delivery's single-reader handoff is bound to its packet, its reader and its existence (H-5).

The handoff a delivery names is ``review-<digest of its packet>``, the message under that name is
addressed to the configured reader, and the reader's own ACK of it comes before the read. A pool
slot is addressed to the member its name carries.
"""
import unittest
from unittest.mock import patch

from test_review_pool_gate_refusals import Fixture  # first: its support import puts scripts/ on the path
from registry import Error
from squadra import deliver


class HandoffBinding(Fixture, unittest.TestCase):
    def new_task(self, name, *readers):
        rc, err = self.run_cli("task", "--actor", "C", "--id", name, "--title", "Synthetic " + name, "--owner", "A",
                               "--repo", str(self.repo), "--scope-file", str(self.scope),
                               *[x for r in readers for x in ("--reviewer", r)])
        self.assertEqual(rc, 0, err)
        self.ack(name)

    def operations(self, name):
        """The events a CLI delivery of the current bytes would write, without writing them."""
        captured = []
        with patch.object(self.store, "batch", side_effect=lambda ops: captured.extend(ops) or []):
            deliver(self.store, "A", name)
        self.assertEqual(captured[0][0], "deliver", "SETUP: the delivery event comes first")
        return captured

    def review(self, name, reader):
        task = self.store.state()["tasks"][name]
        self.store.append("review", reader, {"task": name, "stage": "code", "verdict": "accepted",
                                             "binding": task["candidate"]["sha256"], "covers": [],
                                             "report": self.store.artifact(self.report)})

    def test_a_reader_task_delivery_without_its_handoff_is_refused(self):
        self.new_task("H", "R")
        self.proved("H")
        _, actor, data, _ = self.operations("H")[0]
        data = {k: v for k, v in data.items() if k != "handoff"}
        with self.assertRaisesRegex(Error, "handoff_unbound"):
            self.store.append("deliver", actor, data)
        self.assertNotIn("delivery", self.store.state()["tasks"]["H"])

    def test_new_bytes_cannot_reuse_the_previous_acknowledged_handoff(self):
        self.new_task("H", "R")
        self.proved("H")
        old = deliver(self.store, "A", "H")["handoff"]
        self.store.append("ack", "R", {"message": old})
        (self.repo / "code.py").write_text("VALUE = 999  # unread change\n")
        self.proved("H")
        _, actor, data, _ = self.operations("H")[0]
        with self.assertRaisesRegex(Error, "handoff_unbound"):
            self.store.append("deliver", actor, {**data, "handoff": old})
        self.assertEqual(self.store.state()["tasks"]["H"]["delivery"]["handoff"], old)

    def test_a_message_the_builder_sent_itself_is_not_a_handoff(self):
        self.new_task("H", "R")
        self.proved("H")
        self.store.append("message", "A", {"id": "self-note", "to": "A", "body": "note to self"})
        self.store.append("ack", "A", {"message": "self-note"})
        _, actor, data, _ = self.operations("H")[0]
        with self.assertRaisesRegex(Error, "handoff_unbound"):
            self.store.append("deliver", actor, {**data, "handoff": "self-note"})

    def test_the_read_needs_the_bound_handoff_message_addressed_to_and_acknowledged_by_its_reader(self):
        self.new_task("H", "R")
        self.proved("H")
        (_, actor, data, _), (_, sender, message, _) = self.operations("H")
        self.store.append("deliver", actor, data)
        with self.assertRaisesRegex(Error, "handoff_missing"):
            self.review("H", "R")
        # The message under the bound name exists, but for another seat: its ACK is not the reader's.
        self.store.append("message", sender, {**message, "to": "R2"}, data["handoff"])
        self.store.append("ack", "R2", {"message": data["handoff"]})
        for reader in ("R", "R2"):
            with self.subTest(reader=reader), self.assertRaises(Error):
                self.review("H", reader)
        self.assertEqual(self.store.state()["tasks"]["H"]["reviews"], [])

    def test_a_pool_slot_is_addressed_to_the_member_its_name_carries(self):
        self.new_task("P", "R", "R2")
        self.proved("P")
        operations = self.operations("P")
        kind, sender, slot, event_id = operations[1]
        self.assertEqual(slot["to"], "R", "SETUP: the first slot is R's")
        operations[1] = (kind, sender, {**slot, "to": "R3"}, event_id)
        with self.assertRaisesRegex(Error, "pool_slot_invalid"):
            self.store.batch(operations)
        self.assertNotIn("delivery", self.store.state()["tasks"]["P"])

    def test_the_cli_delivery_and_read_still_close(self):
        self.new_task("H", "R")
        self.proved("H")
        handoff = deliver(self.store, "A", "H")["handoff"]
        self.store.append("ack", "R", {"message": handoff})
        rc, err = self.run_cli("review", "--actor", "R", "--task", "H", "--stage", "code", "--verdict", "accepted",
                               "--report", str(self.report))
        self.assertEqual(rc, 0, err)
        rc, err = self.run_cli("close", "--actor", "C", "--task", "H")
        self.assertEqual(rc, 0, err)


if __name__ == "__main__":
    unittest.main()
