"""An acceptance its reader withdrew by amendment is not the read that counts (L-20)."""
import contextlib
import io
import json
import unittest
from unittest.mock import patch

from test_review_pool_gate_refusals import Fixture  # first: its support import puts scripts/ on the path
from registry import Error, Store
from squadra import deliver, main


class WithdrawnAcceptance(Fixture, unittest.TestCase):
    """B is Claude; X is a Codex reader; R is a Claude reader; three code rounds."""

    def setUp(self):
        original = Store.append

        def three_rounds(store, kind, actor, data, event_id=None):
            if kind == "init":
                data = {**data, "limits": {**data["limits"], "code": 3}}
            return original(store, kind, actor, data, event_id)

        with patch.object(Store, "append", three_rounds):
            super().setUp()
        self.store.append("agent", "X", {"id": "X", "role": "reviewer", "identity": "codex-reader", "client": "codex",
                                         "repo": str(self.repo), "skills": [], "session": ""})

    def read(self, reader, verdict, amend=None):
        out, err = io.StringIO(), io.StringIO()
        argv = ["--root", str(self.root), "review", "--actor", reader, "--task", "N", "--stage", "code",
                "--verdict", verdict, "--report", str(self.report), *(["--amend", amend] if amend else [])]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(argv)
        self.assertEqual(rc, 0, err.getvalue())
        return json.JSONDecoder().raw_decode(out.getvalue().strip())[0]["receipt"]

    def delivered(self):
        rc, err = self.run_cli("task", "--actor", "C", "--id", "N", "--title", "Claude work", "--owner", "B",
                               "--repo", str(self.repo), "--scope-file", str(self.scope))
        self.assertEqual(rc, 0, err)
        self.ack("N", owner="B")
        self.proved("N", actor="B")
        deliver(self.store, "B", "N")

    def test_a_withdrawn_other_client_acceptance_does_not_count(self):
        self.delivered()
        receipt = self.read("X", "accepted")
        self.read("X", "changes-required", amend=receipt)
        self.read("R", "accepted")  # a Claude read of Claude work: an addition only
        rc, err = self.run_cli("check", "--actor", "C", "--task", "N")
        self.assertEqual(rc, 1)
        self.assertIn("cross_client_read_missing: N was written by claude and its accepted current reads come from "
                      "claude", err)
        with self.assertRaisesRegex(Error, "cross_client_read_missing: N"):
            self.store.append("close", "C", {"task": "N"})

    def test_an_acceptance_that_amends_a_rejection_counts(self):
        self.delivered()
        receipt = self.read("X", "changes-required")
        self.read("X", "accepted", amend=receipt)
        rc, err = self.run_cli("check", "--actor", "C", "--task", "N")
        self.assertEqual(rc, 0, err)
        self.store.append("close", "C", {"task": "N"})
        self.assertEqual(self.store.state()["tasks"]["N"]["status"], "done")


if __name__ == "__main__":
    unittest.main()
