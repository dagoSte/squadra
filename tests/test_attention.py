"""Check that recorded owner requests produce one consistent attention view."""
import contextlib
import copy
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from registry import Store, Error, attention, initial, apply, TASK_STATUSES, NEXT_STEPS, table
from squadra import main


class Attention(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.store = Store(self.base / "record", create=True); self.addCleanup(self.store.close)
        self.store.append("init", "system", {"name": "Fixture", "project": str(self.base),
                          "limits": {"plan": 3, "code": 2, "build": 3}})
        for name, role, identity in [("C", "coordinator", "author"), ("A", "builder", "author"),
                                     ("R", "reviewer", "outside"), ("Alias", "reviewer", "author")]:
            self.store.append("agent", name, {"id": name, "role": role, "identity": identity,
                              "client": "manual", "repo": str(self.base), "skills": []})
        self.store.append("task", "C", {"id": "T", "owner": "A", "title": "Fixture", "base": "a" * 40,
                          "repo": str(self.base), "depends": [], "scope": {}, "proof": None})
        self.store.append("message", "C", {"id": "M", "to": "A", "task": "T", "body": "Assignment"})
        self.options = [{"id": "A", "label": "Continue", "say": "Continue within scope"},
                        {"id": "B", "label": "Stop", "say": "Stop and save"}]

    def ask(self, **values):
        data = {"id": "Q", "question": "Choose?", "options": self.options,
                "waiting": ["A"], "task": "T", "evidence": []} | values
        self.store.append("ask", "C", data)

    def decision(self, **values):
        self.store.append("decision", "C", {"reason": "Owner instruction", "answers": "Q",
                          "choice": "A", "channel": "typed-here"} | values)

    def view(self):
        return attention(self.store.state())

    def test_open_ask_until_answered_with_evidence(self):
        evidence = self.base / "evidence.md"; evidence.write_text("Observed evidence")
        artifact = self.store.artifact(evidence)
        self.ask(evidence=[artifact], recommended="A")
        self.assertEqual(self.view()["for_owner"][0]["evidence"], [artifact])
        self.decision(owner_words="Continue within scope")
        self.assertFalse(self.view()["for_owner"])
        d = self.view()["decided"][0]
        self.assertEqual(d["ask"]["id"], "Q")
        self.assertEqual(d["owner_words"], "Continue within scope")
        self.assertFalse(d["legacy"])

    def test_answer_unknown_or_invalid_choice_or_channel_refused(self):
        for fields in ({"answers": "missing"}, {"choice": "unknown"}, {"channel": None}):
            with self.subTest(fields=fields):
                if "Q" not in self.store.state()["asks"]: self.ask()
                with self.assertRaises(Error): self.decision(**fields)
        self.assertEqual(self.store.state()["asks"]["Q"]["status"], "open")

    def test_answer_can_close_only_once(self):
        self.ask(); self.decision()
        with self.assertRaisesRegex(Error, "open"): self.decision()
        with self.assertRaises(Error): self.store.append("decision", "C", {"reason": "withdraw", "withdraws": "Q"})

    def test_withdrawal_and_answer_are_exclusive(self):
        self.ask()
        with self.assertRaisesRegex(Error, "exclusive"): self.decision(withdraws="Q")
        self.store.append("decision", "C", {"reason": "obsolete", "withdraws": "Q"})
        self.assertFalse(self.view()["for_owner"])
        self.assertEqual(self.store.state()["asks"]["Q"]["status"], "withdrawn")
        with self.assertRaises(Error): self.decision()

    def test_relayed_answer_retains_channel(self):
        self.ask(); self.decision(channel="relayed", owner_words="Exact relayed words")
        self.assertEqual(self.view()["decided"][0]["channel"], "relayed")

    def test_legacy_decision_never_closes_ask(self):
        self.ask(); self.store.append("decision", "C", {"reason": "Choose? Continue"})
        self.assertEqual(self.view()["for_owner"][0]["id"], "Q")
        self.assertTrue(self.view()["decided"][0]["legacy"])

    def test_only_coordinator_can_ask_or_answer(self):
        self.ask()
        for actor in ("A", "R"):
            with self.assertRaisesRegex(Error, "Coordinator"):
                self.store.append("decision", actor, {"reason": "no", "answers": "Q", "choice": "A", "channel": "typed-here"})
            with self.assertRaises(Error): self.store.append("ask", actor, {"id": "other"})

    def test_ask_ids_options_and_references_validated(self):
        for bad in ({"question": ""}, {"options": []}, {"options": [self.options[0]]},
                    {"options": [self.options[0], self.options[0]]}, {"recommended": "unknown"},
                    {"waiting": ["missing"]}, {"task": "missing"}, {"evidence": ["fake"]}):
            with self.subTest(bad=bad), self.assertRaises(Error): self.ask(**bad)
        self.ask()
        with self.assertRaises(Error): self.ask()

    def test_peer_ack_wait_not_owner_decision(self):
        view = self.view()
        self.assertFalse(view["for_owner"])
        self.assertEqual(view["waiting"][0]["who"], ["A"])

    def test_free_text_next_is_not_a_decision_or_derived_step(self):
        self.store.append("output", "C", {"task": "T", "text": "Owner must choose", "next": "Decide now", "artifacts": []})
        self.assertFalse(self.view()["for_owner"])
        self.assertEqual(self.view()["next_steps"][0]["text"], NEXT_STEPS["waiting-ack"])

    def test_next_step_table_covers_all_statuses(self):
        self.assertEqual(set(NEXT_STEPS), set(TASK_STATUSES))
        state = self.store.state()
        for status in TASK_STATUSES:
            state["tasks"]["T"]["status"] = status
            self.assertEqual(attention(state)["next_steps"][0]["text"], NEXT_STEPS[status])

    def test_exhausted_budget_deduplicates_when_ask_exists(self):
        state = self.store.state(); task = state["tasks"]["T"]
        task["rounds"]["code"] = 2
        task["reviews"] = [{"stage": "code", "verdict": "changes-required"}]
        self.assertEqual([x["kind"] for x in attention(state)["for_owner"]], ["budget"])
        self.ask(); state["asks"] = self.store.state()["asks"]
        self.assertEqual([x["kind"] for x in attention(state)["for_owner"]], ["ask"])

    def test_reader_eligibility_and_dependency_waits(self):
        state = self.store.state(); task = state["tasks"]["T"]
        task["status"] = "delivered"
        state["tasks"]["D"] = dict(task, id="D", status="working")
        task["depends"] = ["D"]
        waits = attention(state)["waiting"]
        self.assertEqual(next(w for w in waits if w["kind"] == "delivered")["who"], ["R"])
        self.assertEqual(next(w for w in waits if w["kind"] == "dependencies")["depends"], ["D"])

    def test_accepted_candidate_drops_obsolete_failed_build_budget(self):
        state = self.store.state(); task = state["tasks"]["T"]
        for n in range(3):
            state["runs"][str(n)] = {"task": "T", "mode": "build", "status": "failed"}
        self.assertEqual(attention(state)["for_owner"][0]["stages"], ["build"])
        task.update(status="reviewed", candidate={"sha256": "exact"})
        task["reviews"] = [{"stage": "code", "verdict": "accepted", "binding": "exact"}]
        self.assertFalse(attention(state)["for_owner"])
        self.assertIn("reviewed", [w["kind"] for w in attention(state)["waiting"]])
        task["reviews"][-1]["binding"] = "old"
        self.assertEqual(attention(state)["for_owner"][0]["stages"], ["build"])

    def test_plan_requires_latest_exact_accepted_review(self):
        state = self.store.state(); task = state["tasks"]["T"]
        task["plan"] = {"sha256": "a"}
        task["reviews"] = [{"stage": "plan", "binding": "b", "verdict": "accepted"}]
        self.assertIn("plan", [w["kind"] for w in attention(state)["waiting"]])
        task["reviews"][-1]["binding"] = "a"
        self.assertNotIn("plan", [w["kind"] for w in attention(state)["waiting"]])

    def test_pause_and_uncertain_receipt_do_not_imply_stop(self):
        state = self.store.state(); state["paused"] = True
        state["runs"]["r"] = {"id": "r", "task": "T", "agent": "A", "mode": "proof", "utc": "time", "status": "cleanup-unknown"}
        view = attention(state)
        self.assertEqual(view["for_owner"][0]["kind"], "paused")
        self.assertIn("ricevuta presente", next(w for w in view["waiting"] if w["kind"] == "cleanup-unknown")["text"])

    def test_transition_timestamps_replayed_from_actual_events(self):
        self.store.append("ack", "A", {"message": "M"})
        self.store.append("start", "A", {"task": "T"})
        event = self.store.events()[-1]
        self.assertEqual(self.store.state()["tasks"]["T"]["status_utc"], event["utc"])
        state = self.store.state()
        apply(state, {"kind": "deliver", "actor": "A", "utc": "original-delivery-UTC", "id": "x", "data": {"task": "T", "snapshot": {}}})
        self.assertEqual(state["tasks"]["T"]["status_utc"], "original-delivery-UTC")
        self.assertEqual(state["tasks"]["T"]["delivered_utc"], "original-delivery-UTC")

    def test_old_ledger_replay_adds_fields_without_rewriting_events(self):
        events_before = copy.deepcopy(self.store.events())
        state = initial()
        for e in events_before: apply(state, e)
        self.assertEqual(state["tasks"]["T"]["status_utc"], events_before[-2]["utc"])
        self.assertEqual(self.store.events(), events_before)
        self.assertEqual(state["tasks"]["T"]["base"], "a" * 40)
        self.assertEqual(state["messages"]["M"]["body"], "Assignment")

    def test_attention_is_pure_and_projection_uses_same_data(self):
        self.ask(); state = self.store.state(); before = copy.deepcopy(state)
        first = attention(state)
        self.assertEqual(attention(state), first); self.assertEqual(state, before)
        self.store.project()
        rendered = (self.store.root / "stato.md").read_text()
        self.assertIn(first["for_owner"][0]["question"], rendered)
        self.assertIn("Frase: Continue within scope", table(state))

    def test_actual_cli_ask_and_relayed_decision(self):
        options = self.base / "options.json"; options.write_text(json.dumps(self.options))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--root", str(self.store.root), "ask", "--actor", "C", "--id", "Q", "--question", "Choose?", "--options-file", str(options)]), 0)
            self.assertEqual(main(["--root", str(self.store.root), "decision", "--actor", "C", "--reason", "owner", "--answers", "Q", "--choice", "A", "--channel", "relayed"]), 0)
        self.assertEqual(self.view()["decided"][0]["channel"], "relayed")

    def test_page_contract_real_javascript(self):
        node = shutil.which("node")
        if not node: self.fail("Node is required: the dashboard JS contract was not measured")
        source = Path(__file__).resolve().parents[1]
        result = subprocess.run([node, str(source / "tests/dashboard_contract.js"), str(source / "assets/dashboard.html")], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("dashboard contract passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
