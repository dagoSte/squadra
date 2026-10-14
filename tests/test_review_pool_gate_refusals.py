"""One test per refusal code of the reviewer pool and of reading light tasks at the release gate.

Every step a version without these features lacks is checked with an assertion, so such a
version fails these tests by assertion, never by error.
"""
import contextlib
import io
import sys
import unittest

import test_squadra as support
from light_route import POLICY, contract
from registry import Error, snapshot
from runtime import execute
from squadra import deliver, main


class Fixture:
    """Synthetic squad: coordinator C, builders A and B, readers R, R2, R3 and A's alias A-alias."""
    git = support.Workflow.git
    task = support.Workflow.task
    ack = support.Workflow.ack

    def setUp(self):
        support.Workflow.setUp(self)
        for name, role, identity in [("R2", "reviewer", "outside-2"), ("R3", "reviewer", "outside-3"),
                                     ("B", "builder", "other-builder")]:
            self.store.append("agent", name, {"id": name, "role": role, "identity": identity, "client": "claude",
                                              "repo": str(self.repo), "skills": [], "session": ""})

    def run_cli(self, *args):
        """Return the native exit code and stderr, including an argument-parser exit."""
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            try:
                rc = main(["--root", str(self.root), *args])
            except SystemExit as exc:
                rc = exc.code
        return rc, err.getvalue()

    def step(self, what, action, *args, **kwargs):
        """A setup step this feature needs; its refusal is reported as a setup failure, not a guard."""
        try:
            return action(*args, **kwargs)
        except Error as exc:
            self.fail("SETUP refused (" + what + "): " + str(exc))

    def full_task(self, name, owner="A", **fields):
        data = {"id": name, "title": "Synthetic " + name, "owner": owner, "repo": str(self.repo),
                "base": snapshot(self.repo)["head"], "scope": self.store.artifact(self.scope), "proof": None,
                "expected": None, "depends": [], "full_gate": False, "reviewer": None, "requirements": []}
        data.update(fields)
        self.store.batch([("task", "C", data, None), ("message", "C", {
            "id": "assign-" + name, "to": owner, "task": name, "body": "Scoped work"}, None)])
        self.store.append("ack", owner, {"message": "assign-" + name})

    def proved(self, name, actor="A", **kwargs):
        argv = self.store.state()["tasks"][name]["proof"] or [sys.executable, "-c", "print('proof')"]
        result = self.step("proof of " + name, execute, self.store, actor, name, argv=argv, **kwargs)
        self.assertEqual(result["status"], "passed")

    def pool_delivered(self, name="P", pool=("R", "R2", "R3")):
        self.full_task(name, pool=list(pool))
        self.proved(name)
        handoffs = self.step("pool delivery", deliver, self.store, "A", name).get("handoffs") or []
        self.assertEqual(len(handoffs), len(pool), "SETUP: the pool delivery records one handoff per member")
        return handoffs

    def light_at_gate(self, name="L1", **fields):
        data = {"id": name, "title": "Small change " + name, "owner": "A", "reviewer": None,
                "repo": str(self.repo), "base": snapshot(self.repo)["head"], "scope": self.store.artifact(self.scope),
                "proof": [sys.executable, "-c", "print('proof')"], "requirements": [{"id": "R1", "text": "Keep it"}],
                "depends": [], "full_gate": False, "expected": None, "route": "light",
                "light_policy": dict(POLICY), "read_at_gate": True}
        data.update(fields)
        data["contract"] = contract(data)
        self.step("read-at-gate registration", self.store.batch, [("task", "C", data, None), ("message", "C", {
            "id": "assign-" + name, "to": "A", "task": name, "body": "Small change"}, None)])
        self.store.append("ack", "A", {"message": "assign-" + name})

    def closed_at_gate(self, name="L1", path="README.md", text="Documented change\n"):
        """A read-at-gate task proved, delivered and closed without its own read, then committed."""
        self.light_at_gate(name)
        (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / path).write_text(text)
        self.proved(name, covers=["R1"])
        self.step("read-at-gate delivery", deliver, self.store, "A", name)
        self.step("close without a read", self.store.append, "close", "C", {"task": name})
        self.assertEqual(self.store.state()["tasks"][name]["status"], "done")
        self.git("add", "-A"); self.git("-c", "commit.gpgsign=false", "commit", "-qm", name)

    def gate_delivered(self, name="G", owner="B", reviewer="R"):
        self.full_task(name, owner=owner, reviewer=reviewer, release_gate=True)
        self.proved(name, actor=owner)
        handoff = deliver(self.store, owner, name)["handoff"]
        self.store.append("ack", reviewer, {"message": handoff})

    def gate_read(self, name="G", reviewer="R", covers=()):
        task = self.store.state()["tasks"][name]
        data = {"task": name, "stage": "code", "verdict": "accepted", "binding": task["candidate"]["sha256"],
                "covers": [], "report": self.store.artifact(self.report)}
        if covers:
            data["gate_covers"] = list(covers)
        self.store.append("review", reviewer, data)


class Refusals(Fixture, unittest.TestCase):
    def test_review_pool_size(self):
        before = len(self.store.events())
        rc, err = self.run_cli("task", "--actor", "C", "--id", "P", "--title", "Pool", "--owner", "A",
                               "--repo", str(self.repo), "--scope-file", str(self.scope),
                               *[x for r in ("R", "R2", "R3", "A-alias", "C") for x in ("--reviewer", r)])
        self.assertEqual(rc, 1, err)
        self.assertIn("review_pool_size", err)
        self.assertNotIn("P", self.store.state()["tasks"])
        self.assertEqual([e["kind"] for e in self.store.events()[before:]], ["diagnostic"])

    def test_review_pool_member(self):
        rc, err = self.run_cli("task", "--actor", "C", "--id", "P", "--title", "Pool", "--owner", "A",
                               "--repo", str(self.repo), "--scope-file", str(self.scope),
                               "--reviewer", "B", "--reviewer", "R")
        self.assertEqual(rc, 1, err)
        self.assertIn("review_pool_member: B", err)
        self.assertNotIn("P", self.store.state()["tasks"])

    def test_review_claimed(self):
        handoffs = self.pool_delivered()
        self.step("first ACK", self.store.append, "ack", "R", {"message": handoffs[0]})
        before = self.store.state()
        with self.assertRaisesRegex(Error, "^review_claimed: R holds the read of P"):
            self.store.append("ack", "R2", {"message": handoffs[1]})
        after = self.store.state()
        self.assertIsNone(after["messages"][handoffs[1]]["ack"])
        self.assertEqual(after["tasks"]["P"], before["tasks"]["P"])

    def test_read_at_gate_requires_light(self):
        with self.assertRaisesRegex(Error, "^read_at_gate_requires_light"):
            self.full_task("F", reviewer="R", read_at_gate=True)
        self.assertNotIn("F", self.store.state()["tasks"])

    def test_read_at_gate_no_own_read(self):
        with self.assertRaisesRegex(Error, "^read_at_gate_no_own_read"):
            data = {"id": "L", "title": "Small", "owner": "A", "reviewer": "R", "repo": str(self.repo),
                    "base": snapshot(self.repo)["head"], "scope": self.store.artifact(self.scope),
                    "proof": [sys.executable, "-c", "print('proof')"], "requirements": [{"id": "R1", "text": "Keep"}],
                    "depends": [], "full_gate": False, "expected": None, "route": "light",
                    "light_policy": dict(POLICY), "read_at_gate": True}
            data["contract"] = contract(data)
            self.store.append("task", "C", data)
        self.assertNotIn("L", self.store.state()["tasks"])

    def test_gate_covers_invalid(self):
        self.ack(); self.proved("T")
        deliver(self.store, "A", "T")
        with self.assertRaisesRegex(Error, "^gate_covers_invalid"):
            self.gate_read("T", covers=["T"])
        self.assertEqual(self.store.state()["tasks"]["T"]["reviews"], [])

    def test_gate_read_missing(self):
        self.closed_at_gate("L1")
        self.gate_delivered()
        self.gate_read()
        with self.assertRaisesRegex(Error, "^gate_read_missing: L1"):
            self.store.append("close", "C", {"task": "G"})
        self.assertEqual(self.store.state()["tasks"]["G"]["status"], "reviewed")

    def test_gate_bytes_missing(self):
        self.closed_at_gate("L1")
        (self.repo / "README.md").write_text("Rewritten after L1\n")
        self.gate_delivered()
        self.step("gate read covering L1", self.gate_read, covers=["L1"])
        with self.assertRaisesRegex(Error, r"^gate_bytes_missing: L1 \(README\.md\)"):
            self.store.append("close", "C", {"task": "G"})
        self.assertEqual(self.store.state()["tasks"]["G"]["status"], "reviewed")

    def test_read_at_gate_fix_exception_needs_read(self):
        from fix_challenge import execute as challenge, prepare
        self.light_at_gate("L", fix=prepare(self.store, {}))
        (self.repo / "README.md").write_text("Documented change\n")
        self.proved("L", covers=["R1"])
        self.step("read-at-gate fix delivery", deliver, self.store, "A", "L")
        self.step("coordinator exception", challenge, self.store, "C", "L", None, exception="Hardware-only observation")
        rc, err = self.run_cli("check", "--actor", "C", "--task", "L")
        self.assertEqual(rc, 1, err)
        self.assertIn("read_at_gate_fix_exception_needs_read", err)
        with self.assertRaisesRegex(Error, "^read_at_gate_fix_exception_needs_read"):
            self.store.append("close", "C", {"task": "L"})
        self.assertEqual(self.store.state()["tasks"]["L"]["status"], "delivered")

    def test_gate_defer_invalid(self):
        with self.assertRaisesRegex(Error, "^gate_defer_invalid: T is not an outstanding read-at-gate task"):
            self.store.append("decision", "C", {"reason": "Not this gate", "defer_read": {"task": "T", "gate": "T"}})
        self.assertEqual(self.store.state()["decisions"], [])


if __name__ == "__main__":
    unittest.main()
