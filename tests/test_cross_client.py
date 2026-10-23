"""Codex writes and Claude reads, Claude writes and Codex reads: the read that counts comes from the other model."""
import sys
import unittest
from unittest.mock import patch

from test_review_pool_gate_refusals import Fixture  # first: its support import puts scripts/ on the path
from registry import Error
from squadra import deliver


class CrossClient(Fixture, unittest.TestCase):
    """A is Codex; R, R2, R3 and B are Claude; X is a second Codex reader; C coordinates."""

    def setUp(self):
        super().setUp()
        self.store.append("agent", "X", {"id": "X", "role": "reviewer", "identity": "codex-reader", "client": "codex",
                                         "repo": str(self.repo), "skills": [], "session": ""})

    def registered(self, name):
        return next(e["data"] for e in self.store.events() if e["kind"] == "task" and e["data"]["id"] == name)

    def new_task(self, name, owner, *readers, reason=None):
        argv = ["task", "--actor", "C", "--id", name, "--title", "Synthetic " + name, "--owner", owner,
                "--repo", str(self.repo), "--scope-file", str(self.scope), *[x for r in readers for x in ("--reviewer", r)]]
        return self.run_cli(*argv, *(["--same-client-reason", reason] if reason is not None else []))

    def delivered(self, name, owner):
        self.store.append("ack", owner, {"message": "assign-" + name})
        self.proved(name, actor=owner)
        return deliver(self.store, owner, name)

    def review(self, name, reader, handoff=None):
        if handoff:
            self.store.append("ack", reader, {"message": handoff})
        return self.run_cli("review", "--actor", reader, "--task", name, "--stage", "code", "--verdict", "accepted",
                            "--report", str(self.report))

    # PT6
    def test_a_same_client_reader_is_refused_naming_both_clients(self):
        before = len(self.store.events())
        rc, err = self.new_task("S", "B", "R")
        self.assertEqual(rc, 1)
        self.assertIn("same_client_reader: R (claude) would read the work of B (claude); the read that counts comes "
                      "from another client: Codex writes and Claude reads, Claude writes and Codex reads", err)
        self.assertIn("--same-client-reason", err)
        rc, err = self.new_task("P", "A", "R", "X")
        self.assertIn("same_client_reader: X (codex) would read the work of A (codex)", err)
        self.assertEqual([e["kind"] for e in self.store.events()[before:]], ["diagnostic", "diagnostic"])
        self.assertEqual(self.new_task("K", "A", "R")[0], 0)
        self.assertIs(self.registered("K")["cross_client"], True)
        self.assertNotIn("same_client_reason", self.registered("K"))

    # PT6
    def test_the_override_is_recorded_and_lets_a_same_client_read_count(self):
        reason = "The Codex window is closed until tomorrow"
        rc, err = self.new_task("S", "B", "R", reason=reason)
        self.assertEqual(rc, 0, err)
        self.assertEqual((self.registered("S")["cross_client"], self.registered("S")["same_client_reason"]), (True, reason))
        handoff = self.delivered("S", "B")["handoff"]
        self.assertEqual(self.review("S", "R", handoff)[0], 0)
        self.assertEqual(self.run_cli("close", "--actor", "C", "--task", "S")[0], 0)
        self.assertEqual(self.store.state()["tasks"]["S"]["status"], "done")

    # PT6
    def test_a_reason_is_refused_when_every_named_reader_is_of_another_client(self):
        rc, err = self.new_task("K", "A", "R", reason="Not needed")
        self.assertEqual(rc, 1)
        self.assertIn("same_client_reason_unneeded: every reader named for A (codex) is of another client", err)
        rc, err = self.new_task("E", "A", reason=" ")
        self.assertIn("same_client_reason_invalid", err)

    # PT6
    def test_close_needs_an_accepted_current_read_from_the_other_client(self):
        self.assertEqual(self.new_task("N", "A")[0], 0)  # no reader named: the manual protocol reads it
        self.delivered("N", "A")
        self.assertEqual(self.review("N", "X")[0], 0)  # a Codex read of Codex work: an addition only
        rc, err = self.run_cli("check", "--actor", "C", "--task", "N")
        self.assertEqual(rc, 1)
        self.assertIn("cross_client_read_missing: N was written by codex and its accepted current reads come from "
                      "codex; the read that counts comes from another client", err)
        with self.assertRaisesRegex(Error, "cross_client_read_missing: N"):
            self.store.append("close", "C", {"task": "N"})
        self.assertEqual(self.run_cli("close", "--actor", "C", "--task", "N")[0], 1)
        self.assertEqual(self.review("N", "R")[0], 0)  # the Claude read is the one that counts
        self.assertEqual(self.run_cli("check", "--actor", "C", "--task", "N")[0], 0)
        self.assertEqual(self.run_cli("close", "--actor", "C", "--task", "N")[0], 0)

    # PT6
    def test_a_cross_client_read_of_an_older_candidate_does_not_count(self):
        self.assertEqual(self.new_task("N", "A")[0], 0)
        self.delivered("N", "A")
        self.assertEqual(self.review("N", "R")[0], 0)
        (self.repo / "code.py").write_text("VALUE = 2\n")
        self.proved("N")
        deliver(self.store, "A", "N")
        self.assertEqual(self.review("N", "X")[0], 0)
        rc, err = self.run_cli("close", "--actor", "C", "--task", "N")
        self.assertIn("cross_client_read_missing", err)

    # PT6
    def test_tasks_recorded_before_the_upgrade_close_as_before(self):
        # A task event without cross_client, as an earlier CLI wrote it: a Claude builder read by Claude.
        self.full_task("OLD", owner="B", reviewer="R")
        self.proved("OLD", actor="B")
        handoff = deliver(self.store, "B", "OLD")["handoff"]
        self.assertEqual(self.review("OLD", "R", handoff)[0], 0)
        self.assertEqual(self.run_cli("check", "--actor", "C", "--task", "OLD")[0], 0)
        self.assertEqual(self.run_cli("close", "--actor", "C", "--task", "OLD")[0], 0)
        self.assertNotIn("cross_client", self.store.state()["tasks"]["OLD"])

    # PT6
    def test_a_manual_or_cursor_author_is_not_judged_by_the_model_rule(self):
        for name, client in (("M", "manual"), ("U", "cursor")):
            self.store.append("agent", name, {"id": name, "role": "builder", "identity": "seat-" + name, "client": client,
                                              "repo": str(self.repo), "skills": [], "session": ""})
            self.store.append("agent", name + "R", {"id": name + "R", "role": "reviewer", "identity": "reader-" + name,
                                                    "client": client, "repo": str(self.repo), "skills": [], "session": ""})
            self.assertEqual(self.new_task("T" + name, name, name + "R")[0], 0)
            handoff = self.delivered("T" + name, name)["handoff"]
            self.assertEqual(self.review("T" + name, name + "R", handoff)[0], 0)
            self.assertEqual(self.run_cli("close", "--actor", "C", "--task", "T" + name)[0], 0)

    # PT6
    def test_raw_task_events_cannot_forge_the_marker(self):
        for fields, code in (({"cross_client": False}, "cross_client_invalid"),
                             ({"same_client_reason": "no marker"}, "cross_client_invalid"),
                             ({"cross_client": True, "reviewer": "R2", "owner": "B"}, "same_client_reader")):
            with self.assertRaisesRegex(Error, code):
                self.full_task("F", **fields)

    # PT6: light work read at a release gate takes the gate's reader as the read that counts.
    def test_a_gate_reader_of_the_authors_client_cannot_cover_new_light_work(self):
        self.closed_at_gate("L", cross_client=True)  # written by A (Codex) under the rule
        self.gate_delivered("G", reviewer="X")  # the gate's own Claude -> Codex read is fine
        before = len(self.store.events())
        with self.assertRaisesRegex(Error, "^gate_covers_invalid: L was written by codex and the gate reader X is "
                                           "codex too; the read that counts comes from another client"):
            self.gate_read("G", reviewer="X", covers=["L"])
        self.assertEqual(len(self.store.events()), before)
        self.gate_read("G", reviewer="X")
        rc, err = self.run_cli("close", "--actor", "C", "--task", "G")
        self.assertIn("gate_read_missing: L", err)
        # A deferral still lets the gate close; a Claude-read gate then covers L.
        self.assertEqual(self.run_cli("decision", "--actor", "C", "--defer-read", "L", "--gate", "G",
                                      "--reason", "Codex-only gate; the next gate is read by Claude")[0], 0)
        self.assertEqual(self.run_cli("close", "--actor", "C", "--task", "G")[0], 0)
        self.assertNotIn("gate_read", self.store.state()["tasks"]["L"])
        self.gate_delivered("G2")
        self.gate_read("G2", covers=["L"])
        self.assertEqual(self.run_cli("close", "--actor", "C", "--task", "G2")[0], 0)
        read = self.store.state()["tasks"]["L"]["gate_read"]
        self.assertEqual((read["gate"], read["reviewer"]), ("G2", "R"))

    # PT6
    def test_check_and_close_refuse_a_same_client_gate_read_that_reached_the_record(self):
        self.closed_at_gate("L", cross_client=True)
        self.gate_delivered("G", reviewer="X")
        with patch("registry.gate_same_client", return_value=None):  # as a writer without the rule records it
            self.gate_read("G", reviewer="X", covers=["L"])
        for command in ("check", "close"):
            rc, err = self.run_cli(command, "--actor", "C", "--task", "G")
            self.assertEqual(rc, 1, command)
            self.assertIn("cross_client_read_missing: L was written by codex and the gate reader X is codex too; the "
                          "read that counts comes from another client", err)
        with self.assertRaisesRegex(Error, "^cross_client_read_missing: L "):
            self.store.append("close", "C", {"task": "G"})
        self.assertEqual(self.store.state()["tasks"]["G"]["status"], "reviewed")

    # PT6
    def test_older_and_reasoned_light_work_keeps_its_gate_read(self):
        self.closed_at_gate("OLD")  # recorded before the upgrade: no marker
        self.closed_at_gate("WHY", path="docs/why.md", cross_client=True, same_client_reason="Only Codex is open")
        self.gate_delivered("G", reviewer="X")
        self.gate_read("G", reviewer="X", covers=["OLD", "WHY"])
        self.assertEqual(self.run_cli("close", "--actor", "C", "--task", "G")[0], 0)
        self.assertEqual({t: self.store.state()["tasks"][t]["gate_read"]["reviewer"] for t in ("OLD", "WHY")},
                         {"OLD": "X", "WHY": "X"})


if __name__ == "__main__":
    unittest.main()
