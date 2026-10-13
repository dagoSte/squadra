"""Check decision links, provenance, replacement, briefings and their dashboard views."""
import contextlib
import datetime as dt
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))
from registry import (Store, Error, attention, waits, initial, apply, canonical, decision_graph, graph_cycles,  # noqa: E402
                      task_progress, table, digest, snapshot, artifact_refs, EDGE_KINDS)
from squadra import main, parser  # noqa: E402
from dashboard_test_support import make_server  # noqa: E402
from runtime import execute  # noqa: E402

OPTIONS = [{"id": "A", "label": "Continue", "say": "Continue within scope"},
           {"id": "B", "label": "Stop", "say": "Stop and save"}]


def edges_of(graph, node=None, kind=None):
    return {(e["from"], e["to"], e["kind"]) for e in graph["edges"]
            if (node is None or node in (e["from"], e["to"])) and (kind is None or e["kind"] == kind)}


@contextlib.contextmanager
def served(root):
    server, token = make_server(root)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def get(path):
        request = urllib.request.Request("http://127.0.0.1:%d%s" % (server.server_port, path), headers={"X-Squadra-Token": token})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, exc.read()
    try:
        yield get
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def git_repo(path, home):
    path.mkdir()
    home.mkdir(exist_ok=True)
    env = os.environ | {"HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1"}
    for args in (["init", "-q"], ["config", "user.name", "Synthetic"], ["config", "user.email", "test@example.invalid"]):
        subprocess.run(["git", *args], cwd=path, env=env, check=True, capture_output=True)
    (path / "code.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "add", "."], cwd=path, env=env, check=True, capture_output=True)
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-qm", "baseline"], cwd=path, env=env, check=True, capture_output=True)
    return path


class Registry(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="squadra-decisions-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "record"
        self.store = Store(self.root, create=True)
        self.addCleanup(lambda: self.store.close())
        self.store.append("init", "system", {"name": "Decisions", "project": str(self.base), "temporary": True,
                                             "limits": {"plan": 3, "code": 2, "build": 3}})
        for seat, role, identity in [("C", "coordinator", "coord"), ("A", "builder", "author"), ("A2", "builder", "author"),
                                     ("B", "builder", "other"), ("R", "reviewer", "outside")]:
            self.store.append("agent", seat, {"id": seat, "role": role, "identity": identity, "client": "manual",
                                              "repo": str(self.base), "skills": []})
        for name in ("T", "U"):
            self.task(name)

    def task(self, name, owner="A", **extra):
        data = {"id": name, "title": "Task " + name, "owner": owner, "repo": str(self.base), "base": "a" * 40,
                "scope": {}, "proof": None, "depends": [], **extra}
        self.store.batch([("task", "C", data, None),
                          ("message", "C", {"id": "assign-" + name, "to": owner, "task": name, "body": "Assignment"}, None)])

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(["--root", str(self.root), *args])
        self.last_error = err.getvalue()
        return rc

    def ask(self, ident="Q", task="T"):
        self.store.append("ask", "C", {"id": ident, "question": "Choose " + ident + "?", "options": OPTIONS,
                                       "waiting": ["A"], "task": task, "evidence": []})

    def graph(self):
        return decision_graph(self.store.events())

    def reopen(self):
        self.store.close()
        self.store = Store(self.root)

    def snap(self, char):
        return {"repo": str(self.base), "head": char * 40, "files": [], "sha256": char * 64}

    def test_answer_and_follows_on_one_ask_are_two_typed_edges_and_no_cycle(self):
        self.ask()
        self.assertEqual(self.cli("decision", "--actor", "C", "--reason", "Owner chose", "--answers", "Q", "--choice", "A",
                                  "--channel", "typed-here", "--follows", "ask:Q", "--id", "D1"), 0, self.last_error)
        graph = self.graph()
        self.assertEqual(edges_of(graph, "decision:D1"), {("ask:Q", "decision:D1", "answers"), ("ask:Q", "decision:D1", "follows")})
        self.assertEqual((graph["cycles"], graph["unresolved"]), ([], []))

    def test_depends_and_supersedes_on_the_same_task_refused(self):
        with self.assertRaisesRegex(Error, "non può dipendere da quella che sostituisce"):
            self.task("V", depends=["T"], supersedes="T")
        self.assertNotIn("V", self.store.state()["tasks"])
        self.assertNotIn("assign-V", self.store.state()["messages"])

    def test_same_visible_id_in_three_kinds_stays_three_typed_nodes(self):
        self.task("X")
        self.ask("X", task="X")
        self.assertEqual(self.cli("decision", "--actor", "C", "--reason", "same text", "--id", "X", "--task", "X"), 0, self.last_error)
        graph = self.graph()
        self.assertLessEqual({"task:X", "ask:X", "decision:X"}, {n["id"] for n in graph["nodes"]})
        self.assertEqual(edges_of(graph, "decision:X"), {("task:X", "decision:X", "concerns")})
        self.assertEqual(edges_of(graph, "ask:X"), {("task:X", "ask:X", "concerns")})

    def test_unknown_targets_refused(self):
        self.ask()
        for args, message in [(["--task", "missing"], "Unknown decision task"), (["--follows", "ask:missing"], "Unknown ask"),
                              (["--follows", "decision:missing"], "Unknown decision"), (["--follows", "task:T"], "indica ask: o decision:")]:
            with self.subTest(args=args):
                self.assertEqual(self.cli("decision", "--actor", "C", "--reason", "r", *args), 1)
                self.assertIn(message, self.last_error)
        self.assertEqual(self.store.state()["decisions"], [])

    def test_owner_sentence_naming_a_task_without_task_link_has_no_edge(self):
        self.ask()
        self.assertEqual(self.cli("decision", "--actor", "C", "--reason", "The owner named T, U and ask:Q", "--owner-words",
                                  "Procedi con T e poi U; vale anche per Q", "--channel", "typed-here", "--id", "W"), 0, self.last_error)
        graph = self.graph()
        self.assertEqual(edges_of(graph, "decision:W"), set())
        node = next(n for n in graph["nodes"] if n["id"] == "decision:W")
        self.assertEqual((node["kind"], node["tasks"], node["channel"]), ("owner-instruction", [], "typed-here"))

    def test_qualified_follows_draw_the_edge_from_the_named_kind(self):
        self.ask("X")
        for args in (["--reason", "coordinator X", "--id", "X"], ["--reason", "from the ask", "--id", "D-ASK", "--follows", "ask:X"],
                     ["--reason", "from the decision", "--id", "D-DEC", "--follows", "decision:X"]):
            self.assertEqual(self.cli("decision", "--actor", "C", *args), 0, self.last_error)
        graph, refs = self.graph(), self.store.state()["refs"]
        self.assertEqual(edges_of(graph, "decision:D-ASK"), {("ask:X", "decision:D-ASK", "follows")})
        self.assertEqual(edges_of(graph, "decision:D-DEC"), {("decision:X", "decision:D-DEC", "follows")})
        recorded = {d.get("id"): d.get("follows") for d in self.store.state()["decisions"]}
        self.assertEqual(recorded["D-ASK"], [{"kind": "ask", "id": "X", "seq": refs["ask"]["X"]}])
        self.assertEqual(recorded["D-DEC"], [{"kind": "decision", "id": "X", "seq": refs["decision"]["X"]}])
        self.assertNotEqual(refs["ask"]["X"], refs["decision"]["X"])

    def test_bare_or_forged_follows_refused_at_cli_and_store(self):
        self.ask("X")
        self.assertEqual(self.cli("decision", "--actor", "C", "--reason", "bare", "--follows", "X"), 1)
        self.assertIn("indica ask: o decision:", self.last_error)
        wrong = self.store.state()["refs"]["ask"]["X"] + 1
        for bad in ["X", {"kind": "ask", "id": "X"}, {"kind": "task", "id": "T", "seq": 1},
                    {"kind": "ask", "id": "X", "seq": wrong}, {"kind": "ask", "id": "X", "seq": str(wrong - 1)}]:
            with self.subTest(bad=bad), self.assertRaises(Error):
                self.store.append("decision", "C", {"reason": "forged", "follows": [bad]})
        self.assertEqual(self.store.state()["decisions"], [])

    def test_later_records_with_the_same_name_never_retarget_history(self):
        self.ask("X")
        self.assertEqual(self.cli("decision", "--actor", "C", "--reason", "follows the ask", "--id", "D1", "--follows", "ask:X"), 0)
        self.assertEqual(self.cli("decision", "--actor", "C", "--reason", "decision Z", "--id", "Z"), 0)
        self.assertEqual(self.cli("decision", "--actor", "C", "--reason", "follows the decision", "--id", "D2", "--follows", "decision:Z"), 0)
        sources = {"ask:X": self.store.state()["refs"]["ask"]["X"], "decision:Z": self.store.state()["refs"]["decision"]["Z"]}
        # Reuse each name for the other record kind to test typed lookup.
        self.assertEqual(self.cli("decision", "--actor", "C", "--reason", "later decision named X", "--id", "X"), 0)
        self.ask("Z")
        expected = {("ask:X", "decision:D1", "follows"), ("decision:Z", "decision:D2", "follows")}
        self.assertEqual(edges_of(self.graph(), kind="follows"), expected)
        self.reopen()
        graph = self.graph()
        self.assertEqual(edges_of(graph, kind="follows"), expected)
        self.assertEqual({n["id"]: n["seq"] for n in graph["nodes"] if n["id"] in sources}, sources)
        with served(self.root) as get:
            status, body = get("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(edges_of(json.loads(body)["graph"], kind="follows"), expected)

    def test_generated_ids_are_reserved_stable_and_followable(self):
        self.assertEqual(self.cli("decision", "--actor", "C", "--reason", "no explicit id"), 0, self.last_error)
        seq = self.store.events()[-1]["seq"]
        name = "dec-e" + str(seq)
        self.assertEqual(self.store.state()["refs"]["decision"][name], seq)
        for ident, message in [("dec-e999", "reserved"), (" padded", "surrounding spaces")]:
            self.assertEqual(self.cli("decision", "--actor", "C", "--reason", "r", "--id", ident), 1)
            self.assertIn(message, self.last_error)
        self.assertEqual(self.cli("decision", "--actor", "C", "--reason", "first K", "--id", "K"), 0)
        self.assertEqual(self.cli("decision", "--actor", "C", "--reason", "second K", "--id", "K"), 1)
        self.assertIn("already exists", self.last_error)
        self.assertEqual(self.cli("decision", "--actor", "C", "--reason", "follows generated", "--id", "F", "--follows", "decision:" + name), 0)
        self.reopen()
        self.assertIn(("decision:" + name, "decision:F", "follows"), edges_of(self.graph()))
        replayed = initial()
        for event in self.store.events():
            apply(replayed, event)
        self.assertEqual(replayed["refs"]["decision"][name], seq)

    def test_cycles_are_listed_defensively_and_forged_links_stay_unresolved(self):
        nodes = [{"id": name, "seq": n} for n, name in enumerate("abcd", 1)]
        edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}, {"from": "c", "to": "b"}, {"from": "c", "to": "d"}]
        self.assertEqual(graph_cycles(nodes, edges), ["b", "c", "d"])
        forged = [{"seq": 1, "kind": "task", "actor": "C", "utc": "t", "data": {"id": "T", "title": "T", "owner": "A"}},
                  {"seq": 2, "kind": "decision", "actor": "C", "utc": "t", "data": {"reason": "r", "id": "D",
                   "follows": [{"kind": "ask", "id": "Q", "seq": 3}]}},
                  {"seq": 3, "kind": "ask", "actor": "C", "utc": "t", "data": {"id": "Q", "question": "?", "options": [], "task": "T"}}]
        graph = decision_graph(forged)
        self.assertEqual(graph["unresolved"], [{"node": "decision:D", "kind": "follows", "target": {"kind": "ask", "id": "Q", "seq": 3}}])
        self.assertEqual(edges_of(graph), {("task:T", "ask:Q", "concerns")})
        self.assertEqual(graph["cycles"], [])

    def test_history_survives_150_unrelated_events_reopen_and_the_api_tail(self):
        plans = []
        for n in (1, 2):
            path = self.base / ("plan-%d.md" % n)
            path.write_text("Plan version %d\n" % n)
            plans.append(self.store.artifact(path))
        report = self.base / "read.md"
        report.write_text("Independent read\n")
        report = self.store.artifact(report)
        a = self.store.append
        a("plan", "C", {"task": "T", "artifact": plans[0]})
        a("review", "R", {"task": "T", "stage": "plan", "verdict": "changes-required", "binding": plans[0]["sha256"], "covers": [], "report": report})
        a("plan", "C", {"task": "T", "artifact": plans[1]})
        a("review", "R", {"task": "T", "stage": "plan", "verdict": "accepted", "binding": plans[1]["sha256"], "covers": [], "report": report})
        a("ack", "A", {"message": "assign-T"})
        a("start", "A", {"task": "T"})
        a("run.start", "A", {"id": "p1", "task": "T", "mode": "proof", "covers": []})
        a("run.end", "A", {"id": "p1", "status": "passed", "native_rc": 0, "after": self.snap("1")})
        a("deliver", "A", {"task": "T", "snapshot": self.snap("1")})
        a("review", "R", {"task": "T", "stage": "code", "verdict": "changes-required", "binding": "1" * 64, "covers": [], "report": report})
        self.assertEqual(task_progress(self.store.events())["T"]["state"]["id"], "to-fix")
        a("pause", "C", {"reason": "Owner stop"})
        a("resume", "C", {"reason": "Owner resumed"})
        a("run.start", "A", {"id": "p2", "task": "T", "mode": "proof", "covers": []})
        a("run.end", "A", {"id": "p2", "status": "passed", "native_rc": 0, "after": self.snap("2")})
        a("deliver", "A", {"task": "T", "snapshot": self.snap("2")})
        a("review", "R", {"task": "T", "stage": "code", "verdict": "accepted", "binding": "2" * 64, "covers": [], "report": report})
        before = self.graph()
        ids = {}
        for n in before["nodes"]:
            ids.setdefault(n["kind"], []).append(n["id"])
        plan1, plan2 = ids["plan"]
        pv1, pv2 = ids["plan-verdict"]
        proof1, proof2 = ids["proof"]
        del1, del2 = ids["delivery"]
        cv1, cv2 = ids["code-verdict"]
        self.assertEqual(len(ids["pause"]) + len(ids["resume"]), 2)
        expected = {("task:T", plan1, "of-task"), (plan1, pv1, "plan-read"), (pv1, plan2, "cronologia"), (plan2, pv2, "plan-read"),
                    (pv2, proof1, "cronologia"), (proof1, del1, "proof-delivered"), (del1, cv1, "code-read"),
                    (cv1, proof2, "cronologia"), (proof2, del2, "proof-delivered"), (del2, cv2, "code-read")}
        self.assertEqual(edges_of(before), expected)
        self.assertLessEqual({e["kind"] for e in before["edges"]}, set(EDGE_KINDS))
        self.assertEqual({(e["from"], e["to"]) for e in before["edges"] if not e["recorded"]},
                         {(f, t) for f, t, k in expected if k == "cronologia"})
        nodes = {n["id"]: n for n in before["nodes"]}
        self.assertEqual(nodes[plan1]["artifacts"], [plans[0]])
        self.assertEqual(nodes[pv1]["binding"], plans[0]["sha256"])
        for n in range(150):
            a("diagnostic", "system", {"unrelated": n})
        self.reopen()
        events = self.store.events()
        self.assertEqual(self.graph(), before, "node identities, history and bindings are stable")
        tail = {n["id"] for n in decision_graph(events[-120:])["nodes"]}
        self.assertFalse({plan1, pv1, proof1, del1} & tail, "a tail-only projection would lose this history")
        state = self.store.state()
        self.assertEqual(state["tasks"]["T"]["plan"], plans[1])
        self.assertNotIn(plans[0]["sha256"], {a["sha256"] for a in artifact_refs(state)}, "a state-only whitelist would refuse the first plan")
        with served(self.root) as get:
            status, body = get("/api/state")
            self.assertEqual(status, 200)
            served_state = json.loads(body)
            self.assertEqual(served_state["graph"], json.loads(json.dumps(before)))
            self.assertEqual(len(served_state["events"]), 120)
            self.assertEqual(get("/api/artifact/" + plans[0]["sha256"]), (200, b"Plan version 1\n"))
            self.assertEqual(get("/api/artifact/" + "f" * 64)[0], 404)
            (self.root / "artifacts" / plans[0]["sha256"]).write_bytes(b"tampered")
            self.assertEqual(get("/api/artifact/" + plans[0]["sha256"])[0], 409)
        progress = task_progress(events)["T"]
        steps = {s["id"]: s for s in progress["steps"]}
        self.assertEqual([s["id"] for s in progress["steps"]], ["plan", "plan-read", "build", "proof", "code-read", "closed"])
        self.assertEqual(steps["plan"]["notes"][0]["code"], "plan-versions")
        self.assertEqual([n["code"] for n in steps["plan-read"]["notes"]], ["plan-accepted"])
        self.assertEqual([n["code"] for n in steps["code-read"]["notes"]], ["code-accepted"])
        self.assertFalse(steps["closed"]["reached"], "only a recorded close reaches the last step")
        self.assertEqual(progress["state"]["id"], "open")

    def test_progress_shows_negative_outcomes_beside_reached_steps(self):
        repo = git_repo(self.base / "repo", self.base / "home")
        self.task("G", repo=str(repo))
        self.store.append("ack", "A", {"message": "assign-G"})
        self.store.append("start", "A", {"task": "G"})
        self.store.append("run.start", "A", {"id": "g1", "task": "G", "mode": "proof", "covers": []})
        self.store.append("run.end", "A", {"id": "g1", "status": "failed", "native_rc": 1, "counts": {"failed": 2, "errors": 56},
                                           "after": snapshot(repo, self.root)})
        self.assertEqual(self.cli("deliver", "--actor", "A", "--task", "G"), 1)
        refusal = self.store.events()[-1]
        self.assertEqual((refusal["kind"], refusal["data"]["command"], refusal["data"]["task"]), ("diagnostic", "deliver", "G"))
        progress = task_progress(self.store.events())["G"]
        steps = {s["id"]: s for s in progress["steps"]}
        self.assertTrue(steps["proof"]["reached"], "the proof happened")
        self.assertEqual([(n["tone"], n["code"]) for n in steps["proof"]["notes"]], [("bad", "proof"), ("bad", "delivery-refused")])
        self.assertEqual(steps["proof"]["notes"][0]["counts"], {"failed": 2, "errors": 56})
        self.assertEqual(steps["proof"]["notes"][1]["error"], "No passing proof on this exact source")
        self.assertFalse(steps["code-read"]["reached"] or steps["closed"]["reached"])
        # A diagnostic without a task must not be attributed to one.
        self.store.append("diagnostic", "system", {"command": "deliver", "error": "No passing proof on this exact source"})
        self.assertEqual(task_progress(self.store.events())["T"]["steps"][3]["notes"], [])

    def test_idle_obsolete_task_leaves_next_steps_and_only_its_own_waits(self):
        self.store.append("message", "C", {"id": "note", "to": "B", "body": "Unrelated message"})
        self.ask("QU", task="U")
        view = attention(self.store.state())
        self.assertIn("T", [s["task"] for s in view["next_steps"]])
        self.assertIn("assign-T", [w.get("id") for w in view["waiting"]])
        self.task("T2", supersedes="T")
        state = self.store.state()
        view = attention(state)
        self.assertNotIn("T", [s["task"] for s in view["next_steps"]])
        self.assertEqual(sorted(w["id"] for w in view["waiting"] if w["kind"] == "ack"), ["assign-T2", "assign-U", "note"])
        self.assertEqual([w for w in view["waiting"] if w.get("task") == "T"], [])
        self.assertIn("QU", [o.get("id") for o in view["for_owner"]])
        self.assertEqual(view["superseded"], [{"task": "T", "by": "T2", "utc": state["tasks"]["T"]["superseded_utc"], "status": "waiting-ack"}])
        self.assertEqual((state["tasks"]["T"]["status"], state["tasks"]["T"]["superseded_by"]), ("waiting-ack", "T2"))
        later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)
        self.assertEqual(sorted(w.get("id") for w in waits(state, later)), ["assign-T2", "assign-U", "note"])
        self.assertIn(("task:T", "task:T2", "supersedes"), edges_of(self.graph()))
        self.assertEqual(task_progress(self.store.events())["T"]["state"], {"id": "superseded", "by": "T2", "utc": state["tasks"]["T"]["superseded_utc"]})
        rendered = table(state)
        self.assertIn("- T → T2", rendered)
        self.assertNotIn("- T: ", rendered)

    def test_supersession_refused_while_old_task_has_unreconciled_work(self):
        for name in ("T", "U"):
            self.store.append("ack", "A", {"message": "assign-" + name})
        self.task("V")
        self.task("W", owner="B")
        self.store.append("run.start", "A", {"id": "r1", "task": "T", "mode": "proof", "covers": []})
        self.store.append("run.start", "A", {"id": "r2", "task": "U", "mode": "proof", "covers": []})
        self.store.append("run.end", "A", {"id": "r2", "status": "cleanup-unknown", "native_rc": 0})
        self.ask("QV", task="V")
        self.store.append("resource.acquire", "B", {"name": "gate", "reason": "frozen"})
        for old, message in [("T", "run r1"), ("U", "run r2"), ("V", "open question QV"), ("W", "reservation gate")]:
            with self.subTest(old=old), self.assertRaisesRegex(Error, "Reconcile first: " + message):
                self.task("N-" + old, supersedes=old)
        self.assertFalse({"N-T", "N-U", "N-V", "N-W"} & set(self.store.state()["tasks"]))
        # Reconciled obligations allow the replacement; an unverified stop never becomes reconciled.
        self.store.append("run.end", "A", {"id": "r1", "status": "failed", "native_rc": 1})
        self.store.append("decision", "C", {"reason": "Obsolete", "withdraws": "QV"})
        self.store.append("resource.release", "B", {"name": "gate", "reason": "done"})
        for old in ("T", "V", "W"):
            self.task("N-" + old, supersedes=old)
        with self.assertRaisesRegex(Error, "run r2"):
            self.task("N-U", supersedes="U")
        # A reservation held by another seat of the owner's identity also blocks.
        self.task("Y")
        self.store.append("resource.acquire", "A2", {"name": "port", "reason": "alias of the owner"})
        with self.assertRaisesRegex(Error, "reservation port"):
            self.task("N-Y", supersedes="Y")

    def test_dependent_of_a_superseded_task_stays_blocked(self):
        self.task("D", owner="B", depends=["T"])
        self.task("T2", supersedes="T")
        self.store.append("ack", "B", {"message": "assign-D"})
        with self.assertRaisesRegex(Error, "Dependency"):
            self.store.append("start", "B", {"task": "D"})
        waiting = [w for w in attention(self.store.state())["waiting"] if w["kind"] == "dependencies"]
        self.assertEqual([(w["task"], w["depends"]) for w in waiting], [("D", ["T"])])
        self.assertEqual(self.store.state()["tasks"]["T"]["status"], "waiting-ack", "no completion follows from supersession")

    def test_supersession_needs_an_existing_open_task_not_already_replaced(self):
        report = self.base / "read.md"
        report.write_text("read\n")
        self.store.append("ack", "A", {"message": "assign-U"})
        self.store.append("run.start", "A", {"id": "u1", "task": "U", "mode": "proof", "covers": []})
        self.store.append("run.end", "A", {"id": "u1", "status": "passed", "native_rc": 0, "after": self.snap("3")})
        self.store.append("deliver", "A", {"task": "U", "snapshot": self.snap("3")})
        self.store.append("review", "R", {"task": "U", "stage": "code", "verdict": "accepted", "binding": "3" * 64,
                                          "covers": [], "report": self.store.artifact(report)})
        self.store.append("close", "C", {"task": "U"})
        self.task("T2", supersedes="T")
        for old, message in [("missing", "must already exist"), ("U", "closed task"), ("T", "already superseded by T2")]:
            with self.subTest(old=old), self.assertRaisesRegex(Error, message):
                self.task("N-" + old, supersedes=old)

    def test_later_run_alerts_on_a_superseded_task_stay_visible(self):
        self.store.append("ack", "A", {"message": "assign-T"})
        self.task("T2", supersedes="T")
        self.store.append("run.start", "A", {"id": "late", "task": "T", "mode": "proof", "covers": []})
        state = self.store.state()
        self.assertIn("late", [w.get("id") for w in attention(state)["waiting"] if w["kind"] == "running"])
        later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)
        self.assertIn("late", [w["id"] for w in waits(state, later) if w["kind"] == "heartbeat"])

    def test_task_cli_records_supersedes(self):
        repo = git_repo(self.base / "repo", self.base / "home")
        scope = self.base / "scope.md"
        scope.write_text("Replacement scope\n")
        args = ["task", "--actor", "C", "--owner", "B", "--repo", str(repo), "--scope-file", str(scope), "--title", "Replacement"]
        self.assertEqual(self.cli(*args, "--id", "T2", "--supersedes", "T"), 0, self.last_error)
        self.assertEqual(self.store.state()["tasks"]["T"]["superseded_by"], "T2")
        self.assertEqual(self.cli(*args, "--id", "T3", "--supersedes", "U", "--depends", "U"), 1)
        self.assertIn("non può dipendere", self.last_error)
        self.assertEqual(self.cli(*args, "--id", "T4"), 0, self.last_error)
        self.assertNotIn("supersedes", self.store.state()["tasks"]["T4"], "unused flag leaves the task data as before")

    def test_owner_words_provenance_is_recorded_or_explicitly_unknown(self):
        for ident, extra in [("P1", ["--channel", "typed-here"]), ("P2", ["--channel", "relayed"]), ("P3", [])]:
            self.assertEqual(self.cli("decision", "--actor", "C", "--reason", "Owner instruction", "--owner-words", "Fai " + ident,
                                      "--id", ident, *extra), 0, self.last_error)
        self.store.append("decision", "C", {"reason": "Recorded before channels existed", "owner_words": "Parole vecchie"})
        old_name = "dec-e" + str(self.store.events()[-1]["seq"])
        nodes = {n["id"]: n for n in self.graph()["nodes"]}
        self.assertEqual([nodes["decision:" + i]["channel"] for i in ("P1", "P2", "P3", old_name)], ["typed-here", "relayed", None, None])
        self.assertEqual({nodes["decision:" + i]["kind"] for i in ("P1", "P2", "P3", old_name)}, {"owner-instruction"})
        for args, message in [(["--channel", "typed-here", "--owner-words", "   "], "nonblank owner words"),
                              (["--channel", "relayed"], "nonblank owner words"), (["--choice", "A"], "Choice requires an answer")]:
            with self.subTest(args=args):
                self.assertEqual(self.cli("decision", "--actor", "C", "--reason", "r", *args), 1)
                self.assertIn(message, self.last_error)
        with self.assertRaisesRegex(Error, "typed-here or relayed"):
            self.store.append("decision", "C", {"reason": "r", "owner_words": "words", "channel": "chat"})
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            main(["--root", str(self.root), "decision", "--actor", "C", "--reason", "r", "--owner-words", "w", "--channel", "chat"])
        decided = {d.get("id"): d for d in attention(self.store.state())["decided"]}
        self.assertEqual([decided[i]["legacy"] for i in ("P1", "P2", "P3")], [False, False, True])

    def test_unrelated_owner_instruction_never_closes_an_open_ask(self):
        self.ask("Q")
        self.assertEqual(self.cli("decision", "--actor", "C", "--reason", "Owner wrote about Q", "--owner-words", "Per Q scelgo A",
                                  "--channel", "typed-here", "--follows", "ask:Q", "--task", "T", "--id", "I"), 0, self.last_error)
        state = self.store.state()
        self.assertEqual(state["asks"]["Q"]["status"], "open")
        self.assertIn("Q", [o.get("id") for o in attention(state)["for_owner"]])
        self.assertEqual(edges_of(self.graph(), "ask:Q"), {("task:T", "ask:Q", "concerns"), ("ask:Q", "decision:I", "follows")})

    def test_brief_latest_wins_history_and_artifacts_stay(self):
        first, second, project = self.base / "brief-1.md", self.base / "brief-2.md", self.base / "project.md"
        first.write_text("# Servizio delle chiavi\nPerché: prima versione.\n")
        second.write_text("# Servizio delle chiavi\nPerché: i clienti ruotano le chiavi.\nFinito quando: prova e lettura sulla stessa versione.\n")
        project.write_text("# Atlas\nTiene in ordine le chiavi.\n\n## Glossario\n- JEV: verifica\n")
        for args in (["--task", "T", "--file", str(first)], ["--task", "T", "--file", str(second)], ["--project", "--file", str(project)]):
            self.assertEqual(self.cli("brief", "--actor", "C", *args), 0, self.last_error)
        state = self.store.state()
        self.assertEqual(state["tasks"]["T"]["brief"]["text"], second.read_text())
        self.assertEqual(state["tasks"]["T"]["brief"]["artifact"]["sha256"], digest(second.read_bytes()))
        self.assertEqual(state["workspace"]["brief"]["text"], project.read_text())
        self.assertEqual([e["data"]["text"] for e in self.store.events() if e["kind"] == "brief" and e["data"].get("task") == "T"],
                         [first.read_text(), second.read_text()])
        self.assertTrue(self.store.verify_artifacts())
        self.assertEqual(state["tasks"]["T"]["status"], "waiting-ack", "a brief never changes recorded status")
        with served(self.root) as get:
            self.assertEqual(get("/api/artifact/" + digest(first.read_bytes())), (200, first.read_bytes()))

    def test_brief_size_role_target_and_integrity(self):
        exact, big, binary, blank = (self.base / name for name in ("exact.md", "big.md", "binary.md", "blank.md"))
        exact.write_bytes(b"a" * 8192)
        big.write_bytes(b"a" * 8193)
        binary.write_bytes(b"\xff\xfe broken")
        blank.write_text("  \n")
        self.assertEqual(self.cli("brief", "--actor", "C", "--task", "T", "--file", str(exact)), 0, self.last_error)
        for args, message in [(["--actor", "C", "--task", "T", "--file", str(big)], "exceeds 8192"),
                              (["--actor", "A", "--task", "T", "--file", str(exact)], "Coordinator role required"),
                              (["--actor", "C", "--task", "missing", "--file", str(exact)], "Unknown task"),
                              (["--actor", "C", "--task", "T", "--file", str(binary)], "utf-8"),
                              (["--actor", "C", "--project", "--file", str(blank)], "Brief text required")]:
            with self.subTest(args=args):
                self.assertEqual(self.cli("brief", *args), 1)
                self.assertIn(message, self.last_error)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            main(["--root", str(self.root), "brief", "--actor", "C", "--task", "T", "--project", "--file", str(exact)])
        artifact = self.store.state()["tasks"]["T"]["brief"]["artifact"]
        for data in ({"project": True, "task": "T", "text": "a" * 8192, "artifact": artifact},
                     {"text": "a" * 8192, "artifact": artifact},
                     {"task": "T", "text": "different text", "artifact": artifact}):
            with self.subTest(data=sorted(data)), self.assertRaises(Error):
                self.store.append("brief", "C", data)


class Replay(unittest.TestCase):
    def test_c53fa0a_log_projects_the_same_state_field_for_field(self):
        events = [json.loads(line) for line in (FIXTURES / "c53fa0a-events.jsonl").read_text().splitlines()]
        old = json.loads((FIXTURES / "c53fa0a-state.json").read_text())
        with tempfile.TemporaryDirectory(prefix="squadra-replay-") as temp:
            store = Store(Path(temp) / "record", create=True)
            try:
                for e in events:
                    store.db.execute("INSERT INTO events(seq,id,utc,kind,actor,data,previous,hash) VALUES(?,?,?,?,?,?,?,?)",
                                     (e["seq"], e["id"], e["utc"], e["kind"], e["actor"], canonical(e["data"]), e["previous"], e["hash"]))
                self.assertEqual(store.events(), events, "the recorded c53fa0a chain verifies unchanged")
                new = store.state()
            finally:
                store.close()
        new.pop("root")
        self.assertEqual(set(new) - set(old), {"refs"}, "the only new field of an old log")
        self.assertEqual({key: new[key] for key in old}, old)
        decisions = [e for e in events if e["kind"] == "decision"]
        self.assertEqual(new["refs"]["decision"], {"dec-e%d" % e["seq"]: e["seq"] for e in decisions})
        graph = decision_graph(events)
        nodes = {n["id"]: n for n in graph["nodes"]}
        unlinked = [e for e in decisions if not (e["data"].get("answers") or e["data"].get("withdraws"))]
        self.assertEqual(len(unlinked), 2)
        for e in unlinked:
            name = "decision:dec-e%d" % e["seq"]
            self.assertEqual(edges_of(graph, name), set(), "no edge is invented for an old unlinked decision")
            self.assertIsNone(nodes[name]["channel"])
        self.assertEqual(nodes["decision:dec-e%d" % unlinked[0]["seq"]]["kind"], "owner-instruction")
        self.assertEqual((graph["cycles"], graph["unresolved"]), ([], []))
        self.assertLessEqual({e["kind"] for e in graph["edges"]}, set(EDGE_KINDS))
        self.assertEqual(set(task_progress(events)), set(old["tasks"]))
        attention(new)
        table(new)


class Page(unittest.TestCase):
    POLICY = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; "
              "img-src 'self' data:; font-src 'self'; frame-ancestors 'none'; base-uri 'none'")

    def test_page_stays_self_contained_under_the_unchanged_policy(self):
        page = (ROOT / "assets/dashboard.html").read_text()
        for unsafe in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            self.assertNotIn(unsafe, page)
        self.assertIsNone(re.search(r"<script[^>]+src=|<link\b|@import|url\(\s*['\"]?https?:|<img\b|<iframe\b", page))
        with tempfile.TemporaryDirectory(prefix="squadra-page-") as temp:
            store = Store(Path(temp) / "record", create=True)
            store.append("init", "system", {"name": "Page", "project": temp, "temporary": True, "limits": {"plan": 3, "code": 2, "build": 3}})
            store.close()
            server, _ = make_server(Path(temp) / "record")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/" % server.server_port) as response:
                    self.assertEqual(response.headers["Content-Security-Policy"], self.POLICY)
                    self.assertEqual(response.read().decode(), page)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


class Documentation(unittest.TestCase):
    def test_command_tables_list_exactly_the_cli_subcommands(self):
        subcommands = set(next(a for a in parser()._actions if a.dest == "command").choices)
        self.assertIn("brief", subcommands)
        for readme in ("README.md", "README.it.md"):
            text = (ROOT / readme).read_text()
            section = text.split('<a id="commands"></a>', 1)[1].split("\n## ", 2)[1]
            cells = [line.split("|")[1] for line in section.splitlines() if line.startswith("| `")]
            listed = [name for cell in cells for name in re.findall(r"`([^`]+)`", cell)]
            with self.subTest(readme=readme):
                self.assertEqual(len(listed), len(set(listed)))
                self.assertEqual(set(listed) - {"--version"}, subcommands)
                for flag in ("--follows", "--supersedes", "--owner-words", "brief --project", "brief --task"):
                    self.assertIn(flag, text)


def build_dashboard_state(base):
    """Record a synthetic team through the CLI and read it through the server."""
    repo = git_repo(base / "repo", base / "home")
    root = base / "record"
    files = base / "evidence"
    files.mkdir()

    def write(name, text):
        path = files / name
        path.write_text(text)
        return str(path)

    def cli(*args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(["--root", str(root), *args])
        return rc, err.getvalue()

    def ok(*args):
        rc, err = cli(*args)
        assert rc == 0, (args, err)

    ok("init", "--name", "Atlas", "--project", str(repo), "--temporary")
    for seat, identity, role, client in [("coord", "window-1", "coordinator", "codex"), ("keys", "window-2", "builder", "codex"),
                                         ("web", "window-3", "builder", "claude"), ("web-alias", "window-3", "builder", "claude"),
                                         ("reader", "window-4", "reviewer", "claude")]:
        ok("agent", "--id", seat, "--identity", identity, "--role", role, "--client", client, "--repo", str(repo))
    scope = write("scope.md", "Synthetic scope\n")
    task = ["task", "--actor", "coord", "--repo", str(repo), "--scope-file", scope]
    ok(*task, "--id", "K", "--owner", "keys", "--reviewer", "reader", "--title", "Key service")
    ok(*task, "--id", "W", "--owner", "web", "--title", "Web view")
    ok(*task, "--id", "OLD", "--owner", "keys", "--title", "Old import")
    ok(*task, "--id", "DOC", "--owner", "web-alias", "--depends", "OLD", "--title", "Docs")
    ok(*task, "--id", "NEW", "--owner", "keys", "--supersedes", "OLD", "--title", "New import")
    ok(*task, "--id", "DONE", "--owner", "keys", "--title", "Closed piece")
    ok(*task, "--id", "RED", "--owner", "keys", "--title", "Red proof")
    ok("brief", "--actor", "coord", "--project", "--file", write("project.md", "\n".join([
        "# Atlas", "Atlas tiene in ordine le chiavi dei clienti e porta il servizio JEV in produzione; SQ-DECISIONS-OLD resta fuori, ZZQ-9 non ha spiegazione.",
        "", "## Passi", "- [x] Piano del servizio delle chiavi", "- [ ] Servizio delle chiavi in produzione",
        "", "## Steps", "- [x] Key service plan", "- [ ] Key service in production",
        "", "## Glossario", "- JEV: verifica del motore dei lavori", "- SQ-DECISIONS: il grafo delle decisioni",
        "", "## Glossary", "- JEV: job engine verification", "- SQ-DECISIONS: the decision graph", ""])))
    ok("brief", "--actor", "coord", "--task", "K", "--file", write("k.md",
       "# Servizio delle chiavi\nPerché: i clienti ruotano le chiavi senza fermare il servizio JEV.\nFinito quando: prova verde e lettura accettata sulla stessa versione.\n"))
    ok("brief", "--actor", "coord", "--task", "W", "--file", write("w.md", "Vista web per i clienti\nPer chi: il proprietario.\n"))
    ok("brief", "--actor", "coord", "--task", "NEW", "--file", write("new.md", "Nuovo import dei clienti\n"))
    ok("brief", "--actor", "coord", "--task", "DONE", "--file", write("done.md",
       "<script>alert(1)</script> Pezzo chiuso\n<img src=x onerror=alert(2)>\n"))
    report = write("read.md", "Independent read\n")
    ok("plan", "--actor", "coord", "--task", "K", "--file", write("plan-1.md", "Plan 1\n"))
    ok("review", "--actor", "reader", "--task", "K", "--stage", "plan", "--verdict", "changes-required", "--report", report)
    ok("plan", "--actor", "coord", "--task", "K", "--file", write("plan-2.md", "Plan 2\n"))
    ok("review", "--actor", "reader", "--task", "K", "--stage", "plan", "--verdict", "accepted", "--report", report)
    for seat, name in [("keys", "K"), ("web", "W"), ("keys", "DONE"), ("keys", "RED")]:
        ok("ack", "--actor", seat, "--message", "assign-" + name)
    ok("start", "--actor", "keys", "--task", "K")
    ok("start", "--actor", "keys", "--task", "RED")
    store = Store(root)
    try:
        store.append("run.start", "keys", {"id": "red-1", "task": "RED", "mode": "proof", "covers": []})
        store.append("run.end", "keys", {"id": "red-1", "status": "failed", "native_rc": 1, "counts": {"failed": 2, "errors": 56},
                                         "after": snapshot(repo, root)})
    finally:
        store.close()
    rc, _ = cli("deliver", "--actor", "keys", "--task", "RED")
    assert rc == 1
    store = Store(root)
    try:
        for name in ("K", "DONE"):
            assert execute(store, "keys", name, argv=[sys.executable, "-c", "print('synthetic proof')"])["status"] == "passed"
    finally:
        store.close()
    ok("deliver", "--actor", "keys", "--task", "K")
    store = Store(root)
    try:
        handoff = [m for m in store.state()["messages"] if m.startswith("review-")][0]
    finally:
        store.close()
    ok("ack", "--actor", "reader", "--message", handoff)
    ok("review", "--actor", "reader", "--task", "K", "--verdict", "changes-required", "--report", report)
    ok("deliver", "--actor", "keys", "--task", "DONE")
    ok("review", "--actor", "reader", "--task", "DONE", "--verdict", "accepted", "--report", report)
    ok("close", "--actor", "coord", "--task", "DONE")
    ok("output", "--actor", "web", "--task", "W", "--file", write("web-out.md", "Ho letto il perimetro della vista web\nDettagli dopo.\n"),
       "--next", "Avvio la costruzione dopo l'approvazione del piano")
    ok("output", "--actor", "keys", "--task", "K", "--file", write("keys-out.md", "Prova rossa analizzata\n"), "--next", "Correggo e riconsegno")
    options = write("options.json", json.dumps(OPTIONS))
    ok("ask", "--actor", "coord", "--id", "Q-KEYS", "--task", "K", "--waiting", "keys", "--question", "Procediamo con le chiavi?", "--options-file", options)
    ok("decision", "--actor", "coord", "--reason", "Owner answered", "--answers", "Q-KEYS", "--choice", "A", "--channel", "typed-here",
       "--owner-words", "Procedi con il servizio delle chiavi JEV", "--id", "D-KEYS-GO")
    # Valid answers without owner words: the channel is still the owner's recorded provenance.
    ok("ask", "--actor", "coord", "--id", "Q-RELAY", "--task", "K", "--question", "Ruotiamo anche le chiavi di prova?", "--options-file", options)
    ok("decision", "--actor", "coord", "--reason", "Answer relayed by coord", "--answers", "Q-RELAY", "--choice", "B",
       "--channel", "relayed", "--id", "D-RELAY")
    ok("ask", "--actor", "coord", "--id", "Q-TYPED", "--task", "W", "--question", "La vista web va anche sul telefono?", "--options-file", options)
    ok("decision", "--actor", "coord", "--reason", "Answer typed here", "--answers", "Q-TYPED", "--choice", "A",
       "--channel", "typed-here", "--id", "D-TYPED")
    ok("decision", "--actor", "coord", "--reason", "Owner instruction relayed by coord", "--id", "D-SCOPE", "--task", "K",
       "--follows", "ask:Q-KEYS", "--follows", "decision:D-KEYS-GO", "--owner-words", "Tieni fuori SQ-DECISIONS-OLD", "--channel", "relayed")
    ok("decision", "--actor", "coord", "--reason", "Owner instruction, channel not stated", "--task", "K",
       "--owner-words", "Le chiavi vecchie restano leggibili per un mese")
    ok("decision", "--actor", "coord", "--reason", "Coordinator keeps the scope")
    ok("ask", "--actor", "coord", "--id", "Q-OLD", "--question", "Serve ancora il vecchio import?", "--options-file", options)
    ok("decision", "--actor", "coord", "--reason", "No longer needed", "--withdraws", "Q-OLD")
    ok("ask", "--actor", "coord", "--id", "Q-WEB", "--task", "W", "--waiting", "web", "--question", "Quale vista prima?", "--options-file", options)
    ok("pause", "--actor", "coord", "--reason", "Owner stop")
    ok("resume", "--actor", "coord", "--reason", "Owner resumed")
    store = Store(root)
    try:
        store.append("decision", "coord", {"reason": "hostile words", "id": "D-HOSTILE", "channel": "typed-here",
                                           "owner_words": "<img src=x onerror=alert(1)>\u0000\u0007‮" + "lungo " * 4000})
    finally:
        store.close()
    with served(root) as get:
        status, body = get("/api/state")
    assert status == 200, body
    return json.loads(body)


SCENARIOS = ["graph", "refresh", "language", "overview", "cards", "glossary", "hostile", "cycle", "phone",
             "channels", "titles", "focus", "assignment"]


class DashboardJavaScript(unittest.TestCase):
    """Run all page scripts in Node; Node is required."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="squadra-dash-")
        base = Path(cls.temp.name).resolve()
        cls.fixture = base / "state.json"
        cls.fixture.write_text(json.dumps(build_dashboard_state(base), ensure_ascii=False))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def scenario(self, name):
        node = shutil.which("node")
        if not node:
            self.fail("Node is required: the dashboard JavaScript contract was not measured")
        result = subprocess.run([node, str(ROOT / "tests/decisions_contract.js"), str(ROOT / "assets/dashboard.html"), str(self.fixture), name],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("decisions contract passed: " + name, result.stdout)

    def test_js_contract_graph_typed_edges_owner_channels_verdicts_and_details(self):
        self.scenario("graph")

    def test_js_contract_refresh_adding_and_reordering_nodes_keeps_selection_scroll_zoom_filter(self):
        self.scenario("refresh")

    def test_js_contract_both_languages_without_missing_keys_and_switch_keeps_selection(self):
        self.scenario("language")

    def test_js_contract_overview_answers_the_five_questions_in_order(self):
        self.scenario("overview")

    def test_js_contract_window_cards_keep_declared_next_step_apart_from_state(self):
        self.scenario("cards")

    def test_js_contract_glossary_on_hover_and_keyboard_focus(self):
        self.scenario("glossary")

    def test_js_contract_hostile_text_stays_text(self):
        self.scenario("hostile")

    def test_js_contract_cycle_is_listed_not_hanging(self):
        self.scenario("cycle")

    def test_js_contract_phone_list_reduced_motion_and_light_theme(self):
        self.scenario("phone")

    def test_js_contract_answer_channel_shown_with_or_without_words(self):
        self.scenario("channels")

    def test_js_contract_brief_titles_across_overview_graph_tasks_and_work_summary(self):
        self.scenario("titles")

    def test_js_contract_glossary_focus_follows_the_same_term_or_falls_back(self):
        self.scenario("focus")

    def test_js_contract_reviewer_and_builder_assignment_times(self):
        self.scenario("assignment")


if __name__ == "__main__":
    unittest.main()
