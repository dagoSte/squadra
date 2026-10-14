"""Reviewer pool and light tasks read at the release gate: behaviour and compatibility."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import unittest
import urllib.request

from test_review_pool_gate_refusals import Fixture  # first: its support import puts scripts/ on the path
from fix_challenge import execute as challenge, prepare, register as register_oracle
from light_route import POLICY, contract
from registry import Error, Store, attention, board, canonical, digest, snapshot, table, task_progress, waits
from runtime import execute
from squadra import deliver

ROOT = Path(__file__).resolve().parents[1]
FAR = "2099-01-01T00:00:00+00:00"


class Pool(Fixture, unittest.TestCase):
    def registered(self, name):
        return next(e["data"] for e in self.store.events() if e["kind"] == "task" and e["data"]["id"] == name)

    def test_one_reviewer_keeps_the_single_record_and_pools_keep_their_order(self):
        common = ["--actor", "C", "--owner", "A", "--repo", str(self.repo), "--scope-file", str(self.scope)]
        self.assertEqual(self.run_cli("task", "--id", "S", "--title", "Single", *common, "--reviewer", "R")[0], 0)
        self.assertEqual(self.registered("S")["reviewer"], "R")
        self.assertEqual(set(self.registered("S")), {"id", "title", "owner", "repo", "base", "scope", "proof",
                                                     "expected", "full_gate", "depends", "reviewer", "requirements"})
        for size, members in ((2, ["R3", "R"]), (3, ["R2", "R", "R3"]), (4, ["R", "R2", "R3", "C"])):
            name = "P" + str(size)
            argv = [x for m in members for x in ("--reviewer", m)]
            self.assertEqual(self.run_cli("task", "--id", name, "--title", "Pool", *common, *argv)[0], 0)
            self.assertEqual(self.registered(name)["pool"], members)
            self.assertIsNone(self.registered(name)["reviewer"])

    def test_any_invalid_member_refuses_the_whole_registration(self):
        self.store.append("availability", "C", {"agent": "R3", "status": "stand-by", "reason": "Window away"})
        for members, named in ((["R", "R2", "B"], "B: registered reviewer required"),
                               (["A-alias", "R"], "A-alias: author alias is not independent"),
                               (["R", "R"], "R: named twice"), (["R", "ghost"], "ghost: registered reviewer required"),
                               (["R", "R3"], "R3: in stand-by")):
            before = len(self.store.events())
            rc, err = self.run_cli("task", "--actor", "C", "--id", "P", "--title", "Pool", "--owner", "A",
                                   "--repo", str(self.repo), "--scope-file", str(self.scope),
                                   *[x for m in members for x in ("--reviewer", m)])
            self.assertEqual((rc, "review_pool_member: " + named in err), (1, True), err)
            self.assertEqual([e["kind"] for e in self.store.events()[before:]], ["diagnostic"])

    def test_one_packet_per_delivery_first_ack_claims_and_only_the_claimant_reads(self):
        before = len(self.store.events())
        handoffs = self.pool_delivered()
        events = self.store.events()[before:]
        delivery = next(e["data"] for e in events if e["kind"] == "deliver")
        messages = [e["data"] for e in events if e["kind"] == "message" and e["data"]["id"] in handoffs]
        key = "review-" + digest(canonical(delivery["packet_data"]).encode())
        self.assertEqual(handoffs, [key + "-" + m for m in ("R", "R2", "R3")])
        self.assertEqual(delivery["packet_data"]["pool"], ["R", "R2", "R3"])
        self.assertNotIn("reviewer", delivery["packet_data"])
        self.assertEqual([m["to"] for m in messages], ["R", "R2", "R3"])
        self.assertTrue(all(m["pool"] == handoffs and delivery["packet"]["sha256"] in m["body"] for m in messages))
        self.store.append("ack", "R2", {"message": handoffs[1]})
        self.assertEqual(self.store.state()["tasks"]["P"]["claim"]["agent"], "R2")
        candidate = self.store.state()["tasks"]["P"]["candidate"]["sha256"]
        review = {"task": "P", "stage": "code", "verdict": "accepted", "binding": candidate, "covers": [],
                  "report": self.store.artifact(self.report)}
        read = {"id": "read-1", "task": "P", "mode": "read", "stage": "code", "argv": ["read"], "before": snapshot(self.repo)}
        for kind, actor, data in (("ack", "R", {"message": handoffs[0]}), ("review", "R3", review),
                                  ("run.start", "R", read), ("review", "C", review)):
            state = self.store.state()
            with self.assertRaisesRegex(Error, "^review_claimed: R2 holds the read of P"):
                self.store.append(kind, actor, data)
            self.assertEqual(self.store.state(), state)
        self.assertEqual(self.run_cli("review", "--actor", "R2", "--task", "P", "--verdict", "accepted",
                                      "--report", str(self.report))[0], 0)
        self.assertEqual(self.run_cli("check", "--actor", "C", "--task", "P")[0], 0)
        self.assertEqual(self.run_cli("close", "--actor", "C", "--task", "P")[0], 0)
        self.assertEqual(self.store.state()["tasks"]["P"]["status"], "done")

    def test_unread_member_cannot_review_before_any_claim(self):
        self.pool_delivered()
        candidate = self.store.state()["tasks"]["P"]["candidate"]["sha256"]
        with self.assertRaisesRegex(Error, "Configured reviewer must ACK exact handoff before review"):
            self.store.append("review", "R", {"task": "P", "stage": "code", "verdict": "accepted",
                                              "binding": candidate, "covers": [], "report": self.store.artifact(self.report)})

    def test_competing_acks_on_two_connections_have_exactly_one_winner(self):
        handoffs = self.pool_delivered()
        barrier, results = threading.Barrier(2), {}

        def ack(member, handoff):
            store = Store(self.root)
            try:
                barrier.wait(timeout=10)
                store.append("ack", member, {"message": handoff})
                results[member] = "claimed"
            except Error as exc:
                results[member] = str(exc)
            finally:
                store.close()
        threads = [threading.Thread(target=ack, args=pair) for pair in zip(("R", "R2"), handoffs)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        winners = [m for m, r in results.items() if r == "claimed"]
        self.assertEqual(len(winners), 1, results)
        loser = ({"R", "R2"} - set(winners)).pop()
        self.assertTrue(results[loser].startswith("review_claimed: " + winners[0] + " holds the read of P"), results)
        state = self.store.state()
        self.assertEqual(state["tasks"]["P"]["claim"]["agent"], winners[0])
        self.assertEqual([h for h in handoffs if state["messages"][h]["ack"]], [handoffs[("R", "R2").index(winners[0])]])
        self.assertEqual(sum(e["kind"] == "ack" and e["data"]["message"] in handoffs for e in self.store.events()), 1)

    def test_a_claim_belongs_to_one_immutable_packet(self):
        first = self.pool_delivered()
        self.proved("P")
        second = deliver(self.store, "A", "P")["handoffs"]
        self.assertTrue(set(first).isdisjoint(second))
        # An ACK on the earlier packet is a stale message: it claims nothing now.
        self.store.append("ack", "R", {"message": first[0]})
        self.assertNotIn("claim", self.store.state()["tasks"]["P"])
        pool_waits = [w for w in attention(self.store.state())["waiting"] if w.get("handoffs")]
        self.assertEqual([w["handoffs"] for w in pool_waits], [second])
        self.store.append("ack", "R3", {"message": second[2]})
        claim = self.store.state()["tasks"]["P"]["claim"]
        rounds = self.store.state()["tasks"]["P"]["rounds"]
        # Identical deliver, advance and direct redelivery keep the packet's ACK and claim.
        retry = deliver(self.store, "A", "P")
        self.assertEqual((retry["handoffs"], retry.get("already_delivered")), (second, True))
        self.assertEqual(deliver(self.store, "C", "P", reconcile=True)["handoffs"], second)
        self.store.append("deliver", "A", self.store.state()["tasks"]["P"]["delivery"])
        state = self.store.state()
        self.assertEqual((state["tasks"]["P"]["claim"], state["tasks"]["P"]["rounds"]), (claim, rounds))
        self.assertEqual(state["messages"][second[2]]["ack"]["agent"], "R3")

    def test_changes_required_then_new_packet_opens_a_fresh_claim_without_resetting_budget(self):
        handoffs = self.pool_delivered()
        self.store.append("ack", "R", {"message": handoffs[0]})
        self.assertEqual(self.run_cli("review", "--actor", "R", "--task", "P", "--verdict", "changes-required",
                                      "--report", str(self.report))[0], 0)
        (self.repo / "code.py").write_text("VALUE = 2\n")
        self.proved("P")
        fresh = deliver(self.store, "A", "P")["handoffs"]
        self.assertNotIn("claim", self.store.state()["tasks"]["P"])
        self.store.append("ack", "R2", {"message": fresh[1]})
        self.assertEqual(self.run_cli("review", "--actor", "R2", "--task", "P", "--verdict", "accepted",
                                      "--report", str(self.report))[0], 0)
        task = self.store.state()["tasks"]["P"]
        self.assertEqual((task["status"], task["rounds"]["code"], task["claim"]["agent"]), ("reviewed", 2, "R2"))

    def test_unclaimed_pool_wait_lists_every_member_and_acts_on_nothing(self):
        handoffs = self.pool_delivered()
        state = self.store.state()
        pool = [w for w in attention(state)["waiting"] if w["kind"] == "ack" and w.get("task") == "P"]
        self.assertEqual([(w["who"], w["handoffs"]) for w in pool], [(["R", "R2", "R3"], handoffs)])
        self.assertFalse([w for w in attention(state)["waiting"] if w["kind"] == "ack" and w.get("id") in handoffs[1:]])
        late = [w for w in waits(state, FAR) if w["kind"] == "ack" and w.get("task") == "P"]
        self.assertEqual([w["who"] for w in late], [["R", "R2", "R3"]])
        self.assertIn("R / R2 / R3 (lettore)", board(state, "it"))
        events, recorded = len(self.store.events()), canonical(self.store.state())
        for command in ("status", "doctor", "board"):
            self.assertEqual(self.run_cli(command)[0], 0)
        self.assertEqual((len(self.store.events()), canonical(self.store.state())), (events, recorded))
        self.store.append("ack", "R3", {"message": handoffs[2]})
        view = attention(self.store.state())
        self.assertFalse([w for w in view["waiting"] if w.get("handoffs")])
        self.assertEqual([w["who"] for w in view["waiting"] if w["kind"] == "delivered"], [["R3"]])
        self.assertEqual(next(s for s in view["next_steps"] if s["task"] == "P")["who"], ["R3"])

    def test_a_member_in_stand_by_refuses_the_whole_delivery(self):
        self.full_task("P", pool=["R", "R2"])
        self.proved("P")
        self.store.append("availability", "C", {"agent": "R2", "status": "stand-by", "reason": "Window away"})
        before = len(self.store.events())
        with self.assertRaisesRegex(Error, "Reviewer is in stand-by; no new review handoff: R2"):
            deliver(self.store, "A", "P")
        self.assertEqual(len(self.store.events()), before)

    def test_light_pool_read_run_needs_the_claimant(self):
        data = {"id": "LP", "title": "Small", "owner": "A", "reviewer": None, "pool": ["R", "R2"],
                "repo": str(self.repo), "base": snapshot(self.repo)["head"], "scope": self.store.artifact(self.scope),
                "proof": [sys.executable, "-c", "print('proof')"], "requirements": [{"id": "R1", "text": "Keep"}],
                "depends": [], "full_gate": False, "expected": None, "route": "light", "light_policy": dict(POLICY)}
        data["contract"] = contract(data)
        self.store.batch([("task", "C", data, None), ("message", "C", {"id": "assign-LP", "to": "A", "task": "LP",
                                                                       "body": "Small"}, None)])
        self.ack("LP")
        (self.repo / "README.md").write_text("Documented\n")
        self.proved("LP", covers=["R1"])
        handoffs = deliver(self.store, "A", "LP")["handoffs"]
        task = self.store.state()["tasks"]["LP"]
        self.assertEqual(task["delivery"]["packet_data"]["light"], task["delivery"]["light"])
        read = {"id": "read-lp", "task": "LP", "mode": "read", "stage": "code", "argv": ["read"],
                "before": snapshot(self.repo), "contract": task["contract"]}
        with self.assertRaisesRegex(Error, "Configured reviewer must ACK exact handoff before read"):
            self.store.append("run.start", "R", read)
        self.store.append("ack", "R", {"message": handoffs[0]})
        with self.assertRaisesRegex(Error, "^review_claimed: R holds the read of LP"):
            self.store.append("run.start", "R2", read)
        self.store.append("run.start", "R", read)
        self.assertEqual(self.store.state()["runs"]["read-lp"]["agent"], "R")

    def test_light_pool_promoted_to_full_still_reads_only_through_the_claim(self):
        data = {"id": "LP", "title": "Small", "owner": "A", "reviewer": None, "pool": ["R", "R2"],
                "repo": str(self.repo), "base": snapshot(self.repo)["head"], "scope": self.store.artifact(self.scope),
                "proof": [sys.executable, "-c", "print('proof')"], "requirements": [{"id": "R1", "text": "Keep"}],
                "depends": [], "full_gate": False, "expected": None, "route": "light", "light_policy": dict(POLICY)}
        data["contract"] = contract(data)
        self.store.batch([("task", "C", data, None), ("message", "C", {"id": "assign-LP", "to": "A", "task": "LP",
                                                                       "body": "Small"}, None)])
        self.ack("LP")
        old = self.store.state()["tasks"]["LP"]
        route = {"task": "LP", "route": "full", "scope": self.store.artifact(self.scope),
                 "proof": [sys.executable, "-c", "print('full proof')"], "reason": "Broader impact",
                 "previous_contract": old["contract"], "expected": None, "full_gate": False}
        route["contract"] = contract({**old, **route})
        self.store.append("route", "C", route)
        (self.repo / "code.py").write_text("VALUE = 3\n")
        self.proved("LP", covers=["R1"])
        handoffs = deliver(self.store, "A", "LP")["handoffs"]
        task = self.store.state()["tasks"]["LP"]
        read = {"id": "read-full", "task": "LP", "mode": "read", "stage": "code", "argv": ["read"],
                "before": snapshot(self.repo), "contract": task["contract"]}
        with self.assertRaisesRegex(Error, "Configured reviewer must ACK exact handoff before read"):
            self.store.append("run.start", "R2", read)
        self.store.append("ack", "R2", {"message": handoffs[1]})
        with self.assertRaisesRegex(Error, "^review_claimed: R2 holds the read of LP"):
            self.store.append("run.start", "R", read)
        self.store.append("run.start", "R2", read)
        self.assertEqual(self.store.state()["runs"]["read-full"]["agent"], "R2")


class ReadAtGate(Fixture, unittest.TestCase):
    def test_closes_without_its_own_read_only_after_ack_proof_and_covers(self):
        self.light_at_gate("L1")
        (self.repo / "README.md").write_text("Documented change\n")
        with self.assertRaisesRegex(Error, "Exact candidate independent review still pending"):
            self.store.append("close", "C", {"task": "L1"})
        self.proved("L1", covers=[])
        with self.assertRaisesRegex(Error, "Requirement lacks native current proof"):
            deliver(self.store, "A", "L1")
        self.proved("L1", covers=["R1"])
        messages = len(self.store.state()["messages"])
        self.assertIsNone(deliver(self.store, "A", "L1")["handoff"])
        task = self.store.state()["tasks"]["L1"]
        self.assertEqual((len(self.store.state()["messages"]), "packet_data" in task["delivery"]), (messages, False))
        review = {"task": "L1", "stage": "code", "verdict": "accepted", "binding": task["candidate"]["sha256"],
                  "covers": ["R1"], "report": self.store.artifact(self.report), "contract": task["contract"]}
        read = {"id": "read-l1", "task": "L1", "mode": "read", "stage": "code", "argv": ["read"],
                "before": snapshot(self.repo), "contract": task["contract"]}
        for kind, data in (("review", review), ("run.start", read)):
            with self.assertRaisesRegex(Error, "^read_at_gate_no_own_read"):
                self.store.append(kind, "R", data)
        self.assertEqual(self.run_cli("check", "--actor", "C", "--task", "L1")[0], 0)
        self.assertEqual(self.run_cli("close", "--actor", "C", "--task", "L1")[0], 0)
        task = self.store.state()["tasks"]["L1"]
        self.assertEqual((task["status"], task["reviews"], task.get("gate_read")), ("done", [], None))

    def test_light_limits_and_protected_paths_are_unchanged(self):
        self.assertEqual(POLICY, {"version": "light-v1", "lines": 80, "files": 4, "profile": "P2"})
        self.light_at_gate("L1")
        argv = self.store.state()["tasks"]["L1"]["proof"]
        (self.repo / "README.md").write_text("".join(f"Line {i}\n" for i in range(81)))
        with self.assertRaisesRegex(Error, "light_lines_exceeded"):
            execute(self.store, "A", "L1", argv=argv, covers=["R1"])
        (self.repo / "README.md").unlink()
        (self.repo / "scripts").mkdir()
        (self.repo / "scripts" / "tool.py").write_text("print('x')\n")
        with self.assertRaisesRegex(Error, "light_protected_path"):
            execute(self.store, "A", "L1", argv=argv, covers=["R1"])

    def test_defect_fix_at_gate_closes_only_after_its_challenge(self):
        (self.repo / "README.md").write_text("wrong\n")
        self.git("add", "README.md"); self.git("-c", "commit.gpgsign=false", "commit", "-qm", "documented baseline")
        self.light_at_gate("L", fix=prepare(self.store, {}))
        tests = self.repo / "tests"; tests.mkdir()
        (tests / "test_text.py").write_text(
            "import pathlib, unittest\nclass Text(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(pathlib.Path('README.md').read_text(), 'right\\n')\n")
        config = self.base / "oracle.json"
        config.write_text(json.dumps({"ids": ["test_text.Text.test_value"], "files": ["tests/test_text.py"]}))
        register_oracle(self.store, "C", "L", config, "Correct the documented text")
        (self.repo / "README.md").write_text("right\n")
        self.proved("L", covers=["R1"])
        deliver(self.store, "A", "L")
        with self.assertRaisesRegex(Error, "fix_challenge_required_or_stale"):
            self.store.append("close", "C", {"task": "L"})
        self.assertEqual(challenge(self.store, "A", "L", self.base / "challenge")["status"], "matched")
        self.assertEqual(self.run_cli("close", "--actor", "C", "--task", "L")[0], 0)
        self.assertEqual(self.store.state()["tasks"]["L"]["reviews"], [])

    def test_defect_fix_exception_at_gate_needs_its_own_read_after_promotion(self):
        self.light_at_gate("L", fix=prepare(self.store, {}))
        (self.repo / "README.md").write_text("Documented change\n")
        self.proved("L", covers=["R1"])
        deliver(self.store, "A", "L")
        challenge(self.store, "C", "L", None, exception="Hardware-only observation")
        with self.assertRaisesRegex(Error, "^read_at_gate_fix_exception_needs_read"):
            self.store.append("close", "C", {"task": "L"})
        old = self.store.state()["tasks"]["L"]
        data = {"task": "L", "route": "full", "scope": self.store.artifact(self.scope),
                "proof": [sys.executable, "-c", "print('full proof')"], "reason": "The exception needs a reader",
                "previous_contract": old["contract"], "expected": None, "full_gate": False}
        data["contract"] = contract({**old, **data})
        self.store.append("route", "C", data)
        challenge(self.store, "C", "L", None, exception="Hardware-only observation, read on the full route")
        self.proved("L", covers=["R1"])
        deliver(self.store, "A", "L")
        with self.assertRaisesRegex(Error, "Independent current code review pending"):
            self.store.append("close", "C", {"task": "L"})
        self.assertEqual(self.run_cli("review", "--actor", "R", "--task", "L", "--verdict", "accepted",
                                      "--report", str(self.report), "--covers", "R1")[0], 0)
        self.assertEqual(self.run_cli("close", "--actor", "C", "--task", "L")[0], 0)
        self.assertEqual(self.store.state()["tasks"]["L"]["status"], "done")

    def test_promotion_to_full_restores_its_own_read(self):
        self.light_at_gate("L1")
        old = self.store.state()["tasks"]["L1"]
        data = {"task": "L1", "route": "full", "scope": self.store.artifact(self.scope),
                "proof": [sys.executable, "-c", "print('full proof')"], "reason": "Broader impact",
                "previous_contract": old["contract"], "expected": None, "full_gate": False}
        data["contract"] = contract({**old, **data})
        self.store.append("route", "C", data)
        (self.repo / "README.md").write_text("Documented change\n")
        self.proved("L1", covers=["R1"])
        deliver(self.store, "A", "L1")
        with self.assertRaisesRegex(Error, "Exact candidate independent review still pending"):
            self.store.append("close", "C", {"task": "L1"})
        self.assertEqual(self.run_cli("review", "--actor", "R", "--task", "L1", "--verdict", "accepted",
                                      "--report", str(self.report), "--covers", "R1")[0], 0)
        self.assertEqual(self.run_cli("close", "--actor", "C", "--task", "L1")[0], 0)
        self.assertNotIn("closed-unread", json.dumps(task_progress(self.store.events())["L1"]["state"]))

    def test_a_full_route_task_cannot_be_read_at_gate_through_the_cli(self):
        rc, err = self.run_cli("task", "--actor", "C", "--id", "F", "--title", "Full", "--owner", "A",
                               "--repo", str(self.repo), "--scope-file", str(self.scope), "--read-at-gate")
        self.assertEqual(rc, 1)
        self.assertIn("read_at_gate_requires_light", err)


class Gate(Fixture, unittest.TestCase):
    def test_check_and_close_need_every_outstanding_task_covered_by_a_non_author(self):
        self.closed_at_gate("L1")
        self.closed_at_gate("L2", path="docs/notes.md")
        self.gate_delivered()
        self.gate_read(covers=["L1"])
        rc, err = self.run_cli("check", "--actor", "C", "--task", "G")
        self.assertEqual(rc, 1)
        self.assertIn("gate_read_missing: L2", err)
        with self.assertRaisesRegex(Error, "^gate_read_missing: L2$"):
            self.store.append("close", "C", {"task": "G"})
        self.gate_read(covers=["L1", "L2"])
        self.assertEqual(self.run_cli("check", "--actor", "C", "--task", "G")[0], 0)
        self.assertEqual(self.run_cli("close", "--actor", "C", "--task", "G")[0], 0)
        state = self.store.state()
        self.assertEqual([state["tasks"][t]["gate_read"]["gate"] for t in ("L1", "L2")], ["G", "G"])
        self.assertEqual(state["tasks"]["L1"]["gate_read"]["reviewer"], "R")
        self.gate_delivered("G2")
        self.gate_read("G2")
        self.assertEqual(self.run_cli("close", "--actor", "C", "--task", "G2")[0], 0)

    def test_an_author_alias_cannot_cover_the_task(self):
        self.closed_at_gate("L1")
        self.gate_delivered(reviewer="A-alias")
        with self.assertRaisesRegex(Error, "^gate_covers_invalid: L1: the reader is its author"):
            self.gate_read(reviewer="A-alias", covers=["L1"])
        self.gate_read(reviewer="A-alias")
        with self.assertRaisesRegex(Error, "^gate_read_missing: L1"):
            self.store.append("close", "C", {"task": "G"})

    def test_missing_bytes_name_the_task_and_path_at_check_and_close(self):
        self.closed_at_gate("L1")
        (self.repo / "README.md").write_text("Rewritten after L1\n")
        self.gate_delivered()
        self.gate_read(covers=["L1"])
        rc, err = self.run_cli("check", "--actor", "C", "--task", "G")
        self.assertEqual(rc, 1)
        self.assertIn("gate_bytes_missing: L1 (README.md)", err)
        (self.repo / "README.md").unlink()
        rc, err = self.run_cli("check", "--actor", "C", "--task", "G")
        self.assertIn("Candidate drift", err)
        self.git("checkout", "--", "README.md")
        self.proved("G", actor="B")
        handoff = deliver(self.store, "B", "G")["handoff"]
        self.store.append("ack", "R", {"message": handoff})
        self.gate_read(covers=["L1"])
        self.assertEqual(self.run_cli("close", "--actor", "C", "--task", "G")[0], 0)

    def test_covers_only_outstanding_tasks_on_a_release_gate_code_read(self):
        self.closed_at_gate("L1")
        self.gate_delivered()
        for covers, reason in ((["L1", "L1"], "L1: not an outstanding"), (["G"], "G: not an outstanding"),
                               (["ghost"], "ghost: not an outstanding")):
            with self.assertRaisesRegex(Error, "^gate_covers_invalid: " + reason):
                self.gate_read(covers=covers)


class Deferral(Fixture, unittest.TestCase):
    def test_only_the_coordinator_defers_and_only_from_the_named_gate(self):
        self.closed_at_gate("L1")
        self.gate_delivered()
        self.gate_read()
        with self.assertRaisesRegex(Error, "^gate_read_missing: L1"):
            self.store.append("close", "C", {"task": "G"})
        with self.assertRaisesRegex(Error, "Coordinator role required"):
            self.store.append("decision", "R", {"reason": "Later", "defer_read": {"task": "L1", "gate": "G"}})
        rc, err = self.run_cli("decision", "--actor", "C", "--reason", "Later", "--defer-read", "L1")
        self.assertEqual(rc, 1)
        self.assertIn("gate_defer_invalid: --defer-read and --gate go together", err)
        self.assertEqual(self.run_cli("decision", "--actor", "C", "--defer-read", "L1", "--gate", "G",
                                      "--reason", "Docs only; next gate")[0], 0)
        decision = self.store.state()["decisions"][-1]
        self.assertEqual((decision["defer_read"], decision["tasks"]), ({"task": "L1", "gate": "G"}, ["L1", "G"]))
        self.assertIn("L1: Chiuso senza lettura: da leggere al gate · rinviato da G (C): Docs only; next gate",
                      table(self.store.state()))
        with self.assertRaisesRegex(Error, "^gate_defer_invalid: L1 is already deferred from G"):
            self.store.append("decision", "C", {"reason": "Again", "defer_read": {"task": "L1", "gate": "G"}})
        self.assertEqual(self.run_cli("close", "--actor", "C", "--task", "G")[0], 0)
        state = self.store.state()
        self.assertNotIn("gate_read", state["tasks"]["L1"])
        with self.assertRaisesRegex(Error, "^gate_defer_invalid: G is not an open release gate"):
            self.store.append("decision", "C", {"reason": "Closed gate", "defer_read": {"task": "L1", "gate": "G"}})
        self.gate_delivered("G2")
        self.gate_read("G2")
        with self.assertRaisesRegex(Error, "^gate_read_missing: L1"):
            self.store.append("close", "C", {"task": "G2"})
        self.gate_read("G2", covers=["L1"])
        self.assertEqual(self.run_cli("close", "--actor", "C", "--task", "G2")[0], 0)
        self.assertEqual(self.store.state()["tasks"]["L1"]["gate_read"]["gate"], "G2")


class Views(Fixture, unittest.TestCase):
    def test_board_status_projection_and_progress_show_closed_unread_until_a_gate_reads_it(self):
        self.closed_at_gate("L1")
        state = self.store.state()
        it, en = board(state, "it"), board(state, "en")
        self.assertIn("chiuso — da leggere al gate", it)
        self.assertIn("Chiusi senza lettura, da leggere al gate: 1", it)
        self.assertIn("closed — read at the gate", en)
        self.assertIn("Closed without a read, to be read at the gate: 1", en)
        self.assertIn("- L1: Chiuso senza lettura: da leggere al gate", table(state))
        self.store.project()
        self.assertIn("Chiusi senza lettura, da leggere al gate: 1", (self.root / "stato.md").read_text())
        progress = task_progress(self.store.events())["L1"]
        self.assertEqual(progress["state"]["id"], "closed-unread")
        self.assertFalse(next(s for s in progress["steps"] if s["id"] == "code-read")["reached"])
        self.gate_delivered()
        self.gate_read(covers=["L1"])
        self.assertEqual(self.run_cli("close", "--actor", "C", "--task", "G")[0], 0)
        state = self.store.state()
        self.assertNotIn("da leggere al gate", board(state, "it"))
        progress = task_progress(self.store.events())["L1"]
        step = next(s for s in progress["steps"] if s["id"] == "code-read")
        self.assertEqual((progress["state"]["id"], step["reached"], step["notes"][-1]["code"]),
                         ("closed", True, "gate-read"))

    def test_dashboard_page_shows_closed_unread_with_its_own_count(self):
        from dashboard_test_support import make_server
        self.closed_at_gate("L1")
        server, token = make_server(self.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/api/state",
                                             headers={"X-Squadra-Token": token})
            with urllib.request.urlopen(request) as response:
                state = json.loads(response.read())
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        self.assertEqual(state["progress"]["L1"]["state"]["id"], "closed-unread")
        fixture = self.base / "page-state.json"; fixture.write_text(json.dumps(state))
        node = shutil.which("node"); self.assertIsNotNone(node, "Node required; no skipped page proof")
        result = subprocess.run([node, str(ROOT / "tests/review_gate_contract.js"), str(ROOT / "assets/dashboard.html"),
                                 str(fixture)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("read at the gate page passed: it en", result.stdout)


class Compatibility(Fixture, unittest.TestCase):
    def test_single_reviewer_lifecycle_keeps_its_record_shape(self):
        self.assertEqual(self.run_cli("task", "--actor", "C", "--id", "S", "--title", "Single", "--owner", "A",
                                      "--repo", str(self.repo), "--scope-file", str(self.scope), "--reviewer", "R")[0], 0)
        self.ack("S"); self.proved("S")
        before = len(self.store.events())
        self.assertEqual(self.run_cli("deliver", "--actor", "A", "--task", "S")[0], 0)
        handoff = self.store.state()["tasks"]["S"]["delivery"]["handoff"]
        self.store.append("ack", "R", {"message": handoff})
        for command in (("review", "--actor", "R", "--task", "S", "--verdict", "accepted", "--report", str(self.report)),
                        ("check", "--actor", "C", "--task", "S"), ("close", "--actor", "C", "--task", "S")):
            self.assertEqual(self.run_cli(*command)[0], 0)
        shapes = {}
        for event in self.store.events()[before:]:
            shapes.setdefault(event["kind"], set(event["data"]))
        self.assertEqual(shapes, {"deliver": {"task", "snapshot", "packet", "packet_data", "handoff"},
                                  "message": {"id", "to", "related_task", "body"}, "ack": {"message"},
                                  "review": {"task", "stage", "verdict", "binding", "covers", "report"},
                                  "close": {"task"}})
        task = self.store.state()["tasks"]["S"]
        self.assertEqual(set(task["delivery"]["packet_data"]),
                         {"task", "candidate", "reviewer", "base", "scope", "plan", "requirements", "proofs", "instructions"})
        self.assertEqual(handoff, "review-" + digest(canonical(task["delivery"]["packet_data"]).encode()))
        self.assertFalse({"pool", "claim", "read_at_gate", "release_gate", "gate_read", "gate_deferrals"} & set(task))


class Documentation(unittest.TestCase):
    def test_work_reference_documents_both_with_a_worked_example_and_every_code(self):
        text = (ROOT / "references/work.md").read_text()
        pool = text.split('<a id="reviewer-pool"></a>', 1)[1].split("\n## ", 2)[1]
        gate = text.split('<a id="read-at-gate"></a>', 1)[1].split("\n## ", 1)[0]
        self.assertIn("--reviewer reader-a --reviewer reader-b", pool)
        self.assertIn("--read-at-gate", gate)
        self.assertIn("--release-gate", gate)
        self.assertIn("--gate-covers D1", gate)
        self.assertIn("--defer-read D1 --gate REL", gate)
        for code in ("review_pool_size", "review_pool_member", "review_claimed", "read_at_gate_requires_light",
                     "read_at_gate_no_own_read", "gate_covers_invalid", "gate_read_missing", "gate_bytes_missing",
                     "gate_defer_invalid", "read_at_gate_fix_exception_needs_read"):
            self.assertIn("`" + code + "`", pool + gate, code)


if __name__ == "__main__":
    unittest.main()
