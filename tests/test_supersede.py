"""Check task replacement when the new task already exists."""
import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))
from registry import (Store, Error, attention, waits, initial, apply, canonical, decision_graph,  # noqa: E402
                      task_progress, table, EDGE_KINDS)
from squadra import main, parser  # noqa: E402
import test_decisions as support  # noqa: E402

SEATS = [("C", "coordinator", "coord"), ("A", "builder", "author"), ("A2", "builder", "author"),
         ("B", "builder", "other"), ("R", "reviewer", "outside")]
# Wall-clock fields: two separate recordings of the same scenario never share them.
STAMPS = {"utc", "status_utc", "superseded_utc", "delivered_utc", "updated", "as_of", "seconds", "created"}
LATER = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)


def unstamped(value):
    if isinstance(value, dict):
        return {k: unstamped(v) for k, v in value.items() if k not in STAMPS}
    if isinstance(value, list):
        return [unstamped(v) for v in value]
    return value


def new_store(root, project):
    store = Store(root, create=True)
    store.append("init", "system", {"name": "Supersede", "project": str(project), "temporary": True,
                                    "limits": {"plan": 3, "code": 2, "build": 3}})
    for seat, role, identity in SEATS:
        store.append("agent", seat, {"id": seat, "role": role, "identity": identity, "client": "manual",
                                     "repo": str(project), "skills": []})
    return store


def add_task(store, project, name, owner="A", **extra):
    data = {"id": name, "title": "Task " + name, "owner": owner, "repo": str(project), "base": "a" * 40,
            "scope": {}, "proof": None, "depends": [], **extra}
    store.batch([("task", "C", data, None),
                 ("message", "C", {"id": "assign-" + name, "to": owner, "task": name, "body": "Assignment"}, None)])


def snap(project, char):
    return {"repo": str(project), "head": char * 40, "files": [], "sha256": char * 64}


def read_report(store, project):
    path = Path(project) / "read.md"
    path.write_text("Independent read\n")
    return store.artifact(path)


def spend_code_budget(store, project, name):
    """A failed proof, a delivered candidate and two refused reads: the code budget is spent."""
    report = read_report(store, project)
    a = store.append
    a("ack", "A", {"message": "assign-" + name})
    a("start", "A", {"task": name})
    a("run.start", "A", {"id": name + "-red", "task": name, "mode": "proof", "covers": []})
    a("run.end", "A", {"id": name + "-red", "status": "failed", "native_rc": 1, "after": snap(project, "1")})
    a("run.start", "A", {"id": name + "-green", "task": name, "mode": "proof", "covers": []})
    a("run.end", "A", {"id": name + "-green", "status": "passed", "native_rc": 0, "after": snap(project, "2")})
    a("deliver", "A", {"task": name, "snapshot": snap(project, "2")})
    for _ in range(2):
        a("review", "R", {"task": name, "stage": "code", "verdict": "changes-required", "binding": "2" * 64,
                          "covers": [], "report": report})
    return report


class Supersede(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="squadra-supersede-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "record"
        self.store = new_store(self.root, self.base)
        self.addCleanup(lambda: self.store.close())
        for name in ("T", "U"):
            self.task(name)

    def task(self, name, owner="A", **extra):
        add_task(self.store, self.base, name, owner, **extra)

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(["--root", str(self.root), *args])
        self.last_output, self.last_error = out.getvalue(), err.getvalue()
        return rc

    def supersede(self, old, new, actor="C", reason="Work moved to the replacement"):
        return self.cli("supersede", "--actor", actor, "--task", old, "--by", new, "--reason", reason)

    def recorded(self):
        return [e for e in self.store.events() if e["kind"] == "supersede"]

    def refused(self, old, new, message, **extra):
        before = self.store.state()["tasks"].get(old, {}).get("superseded_by")
        self.assertEqual(self.supersede(old, new, **extra), 1)
        self.assertIn(message, self.last_error)
        self.assertEqual(self.recorded(), [])
        self.assertEqual(self.store.state()["tasks"].get(old, {}).get("superseded_by"), before)

    def reopen(self):
        self.store.close()
        self.store = Store(self.root)

    def graph(self):
        return decision_graph(self.store.events())

    def close_task(self, name):
        report = read_report(self.store, self.base)
        a = self.store.append
        a("ack", "A", {"message": "assign-" + name})
        a("run.start", "A", {"id": name + "-p", "task": name, "mode": "proof", "covers": []})
        a("run.end", "A", {"id": name + "-p", "status": "passed", "native_rc": 0, "after": snap(self.base, "3")})
        a("deliver", "A", {"task": name, "snapshot": snap(self.base, "3")})
        a("review", "R", {"task": name, "stage": "code", "verdict": "accepted", "binding": "3" * 64,
                          "covers": [], "report": report})
        a("close", "C", {"task": name})

    def test_cli_records_exactly_task_by_and_reason_as_the_coordinator(self):
        self.assertEqual(self.supersede("T", "U", reason="Work moved to U by recorded decision"), 0, self.last_error)
        event = self.store.events()[-1]
        self.assertEqual((event["kind"], event["actor"], event["data"]),
                         ("supersede", "C", {"task": "T", "by": "U", "reason": "Work moved to U by recorded decision"}))
        self.assertEqual(self.recorded(), [event])
        self.assertIn("- T → U", self.last_output)
        self.assertIn("- T → U", (self.root / "stato.md").read_text())
        self.assertEqual(json.loads((self.root / "events.jsonl").read_text().splitlines()[-1])["kind"], "supersede")
        for missing in ("--task", "--by", "--reason", "--actor"):
            args = {"--actor": "C", "--task": "T", "--by": "U", "--reason": "r"}
            args.pop(missing)
            with self.subTest(missing=missing), self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                main(["--root", str(self.root), "supersede", *[x for pair in args.items() for x in pair]])

    def test_supersede_is_one_cli_subcommand(self):
        self.assertIn("supersede", set(next(a for a in parser()._actions if a.dest == "command").choices))

    def test_unknown_or_same_tasks_refused(self):
        self.refused("missing", "U", "Superseded task must already exist")
        self.refused("T", "missing", "Replacement task must already exist")
        self.refused("T", "T", "A task cannot supersede itself")

    def test_closed_old_task_refused(self):
        self.close_task("T")
        self.task("V")
        self.refused("T", "V", "A closed task cannot be superseded")

    def test_already_superseded_old_task_refused_whichever_way_it_was_recorded(self):
        self.task("T2", supersedes="T")
        self.task("V")
        self.refused("T", "V", "already superseded by T2")
        self.assertEqual(self.supersede("U", "V"), 0, self.last_error)
        self.task("W")
        self.assertEqual(self.supersede("U", "W"), 1)
        self.assertIn("Task U is already superseded by V", self.last_error)
        with self.assertRaisesRegex(Error, "already superseded by V"):
            self.task("X", supersedes="U")
        self.assertEqual(self.store.state()["tasks"]["U"]["superseded_by"], "V")

    def test_replacement_recorded_before_the_old_task_refused(self):
        self.refused("U", "T", "The replacement must be a task recorded after U")

    def test_replacement_that_depends_on_the_old_task_refused_directly_or_through_others(self):
        self.task("D", depends=["T"])
        self.task("E", depends=["D"])
        self.task("F", depends=["U"])
        self.refused("T", "D", "non può dipendere da quella che sostituisce")
        self.refused("T", "E", "non può dipendere da quella che sostituisce")
        self.assertEqual(self.supersede("T", "F"), 0, "a dependency on another task is not one on the old task")

    def test_blank_or_missing_reason_refused(self):
        for reason in ("", "   \n\t"):
            with self.subTest(reason=reason):
                self.refused("T", "U", "Supersede reason required", reason=reason)
        for data in ({"task": "T", "by": "U"}, {"task": "T", "by": "U", "reason": ["moved"]}):
            with self.subTest(data=data), self.assertRaisesRegex(Error, "Supersede reason required"):
                self.store.append("supersede", "C", data)
        for data in ({"task": ["T"], "by": "U", "reason": "r"}, {"task": "T", "by": ["U"], "reason": "r"}, {"reason": "r"}):
            with self.subTest(data=data), self.assertRaisesRegex(Error, "must already exist"):
                self.store.append("supersede", "C", data)
        self.assertEqual(self.recorded(), [])

    def test_only_the_coordinator_may_record_it(self):
        for actor in ("A", "B", "R"):
            with self.subTest(actor=actor):
                self.refused("T", "U", "Coordinator role required", actor=actor)
        self.refused("T", "U", "Unknown agent: ghost", actor="ghost")
        with self.assertRaisesRegex(Error, "Coordinator role required"):
            self.store.append("supersede", "R", {"task": "T", "by": "U", "reason": "r"})

    def test_running_or_unverified_run_refused_until_reconciled(self):
        self.store.append("ack", "A", {"message": "assign-T"})
        self.store.append("ack", "A", {"message": "assign-U"})
        self.task("N")
        self.store.append("run.start", "A", {"id": "r1", "task": "T", "mode": "proof", "covers": []})
        self.refused("T", "N", "Reconcile first: run r1 of T is running")
        self.store.append("run.end", "A", {"id": "r1", "status": "failed", "native_rc": 1})
        self.store.append("run.start", "A", {"id": "r2", "task": "U", "mode": "proof", "covers": []})
        self.store.append("run.end", "A", {"id": "r2", "status": "cleanup-unknown", "native_rc": 0})
        self.refused("U", "N", "Reconcile first: run r2 of U")
        self.assertEqual(self.supersede("T", "N"), 0, self.last_error)

    def test_reservation_held_by_the_owner_identity_refused_until_released(self):
        self.task("N", owner="B")
        self.store.append("resource.acquire", "A", {"name": "gate", "reason": "frozen"})
        self.refused("T", "N", "Reconcile first: reservation gate is held by the owner of T")
        self.store.append("resource.release", "A", {"name": "gate", "reason": "done"})
        self.store.append("resource.acquire", "A2", {"name": "port", "reason": "alias of the owner"})
        self.refused("T", "N", "Reconcile first: reservation port")
        self.store.append("resource.release", "A2", {"name": "port", "reason": "done"})
        self.store.append("resource.acquire", "B", {"name": "other", "reason": "another identity"})
        self.assertEqual(self.supersede("T", "N"), 0, self.last_error)

    def test_open_question_about_the_old_task_refused_until_answered_or_withdrawn(self):
        self.store.append("ask", "C", {"id": "Q", "question": "Keep T?", "options": support.OPTIONS,
                                       "waiting": [], "task": "T", "evidence": []})
        self.refused("T", "U", "Reconcile first: open question Q about T")
        self.store.append("decision", "C", {"reason": "Obsolete", "withdraws": "Q"})
        self.assertEqual(self.supersede("T", "U"), 0, self.last_error)

    def test_spent_budget_task_leaves_owner_items_and_next_steps_and_keeps_everything_recorded(self):
        self.task("OLD")
        report = spend_code_budget(self.store, self.base, "OLD")
        self.store.append("ask", "C", {"id": "QU", "question": "Which view first?", "options": support.OPTIONS,
                                       "waiting": [], "task": "U", "evidence": []})
        self.store.append("message", "C", {"id": "note", "to": "B", "body": "Unrelated message"})
        self.task("NEW", owner="B")
        before, history = self.store.state(), self.store.events()
        view = attention(before)
        self.assertIn(("budget", "OLD", ["code"]), [(o["kind"], o.get("task"), o.get("stages")) for o in view["for_owner"]])
        self.assertIn("OLD", [s["task"] for s in view["next_steps"]])
        self.assertIn(("changes-required", "OLD"), [(w["kind"], w.get("task")) for w in view["waiting"]])
        progress = task_progress(history)["OLD"]

        self.assertEqual(self.supersede("OLD", "NEW", reason="MS-116R moved to NEW by recorded decision"), 0, self.last_error)
        state, events = self.store.state(), self.store.events()
        view = attention(state)
        self.assertEqual([(o["kind"], o.get("id")) for o in view["for_owner"]], [("ask", "QU")])
        self.assertNotIn("OLD", [s["task"] for s in view["next_steps"]])
        self.assertEqual([w for w in view["waiting"] if w.get("task") == "OLD"], [])
        self.assertEqual(sorted(w["id"] for w in view["waiting"] if w["kind"] == "ack"), ["assign-NEW", "assign-T", "assign-U", "note"])
        self.assertEqual(view["superseded"], [{"task": "OLD", "by": "NEW", "utc": events[-1]["utc"], "status": "changes-required"}])
        self.assertEqual(sorted(w.get("id") for w in waits(state, LATER)), ["assign-NEW", "assign-T", "assign-U", "note"])
        # Nothing is erased: the log only grew, and the old task keeps status, reviews, candidates and budgets.
        self.assertEqual(events[:-1], history)
        old = state["tasks"]["OLD"]
        self.assertEqual({k: v for k, v in old.items() if k not in {"superseded_by", "superseded_utc"}}, before["tasks"]["OLD"])
        self.assertEqual((old["superseded_by"], old["superseded_utc"]), ("NEW", events[-1]["utc"]))
        self.assertEqual([r["verdict"] for r in old["reviews"]], ["changes-required", "changes-required"])
        self.assertEqual([(p["id"], p["status"]) for p in old["proofs"]], [("OLD-red", "failed"), ("OLD-green", "passed")])
        self.assertEqual((old["status"], old["rounds"], old["candidate"]), ("changes-required", {"plan": 0, "code": 2}, snap(self.base, "2")))
        self.assertEqual(state["runs"], before["runs"])
        self.assertEqual(state["tasks"]["NEW"], before["tasks"]["NEW"], "the replacement itself is not changed")
        with self.assertRaisesRegex(Error, "Review round budget exhausted"):
            self.store.append("review", "R", {"task": "OLD", "stage": "code", "verdict": "accepted", "binding": "2" * 64,
                                              "covers": [], "report": report})
        after = task_progress(events)["OLD"]
        self.assertEqual(after["steps"], progress["steps"], "every reached step and its outcome stay")
        self.assertEqual(after["state"], {"id": "superseded", "by": "NEW", "utc": events[-1]["utc"]})

    def test_idle_old_task_loses_only_its_own_waits_and_its_dependents_stay_blocked(self):
        self.store.append("message", "C", {"id": "note", "to": "B", "body": "Unrelated message"})
        self.task("D", owner="B", depends=["T"])
        self.task("NEW", owner="B")
        self.assertEqual(self.supersede("T", "NEW"), 0, self.last_error)
        state = self.store.state()
        view = attention(state)
        self.assertNotIn("T", [s["task"] for s in view["next_steps"]])
        self.assertEqual(sorted(w["id"] for w in view["waiting"] if w["kind"] == "ack"), ["assign-D", "assign-NEW", "assign-U", "note"])
        self.assertEqual(sorted(w.get("id") for w in waits(state, LATER)), ["assign-D", "assign-NEW", "assign-U", "note"])
        self.assertEqual([(w["task"], w["depends"]) for w in view["waiting"] if w["kind"] == "dependencies"], [("D", ["T"])])
        self.store.append("ack", "B", {"message": "assign-D"})
        with self.assertRaisesRegex(Error, "Dependency"):
            self.store.append("start", "B", {"task": "D"})
        self.assertEqual(state["tasks"]["T"]["status"], "waiting-ack", "no completion follows from supersession")
        self.assertIn("- T → NEW", table(state))
        self.assertNotIn("- T: ", table(state))

    def test_graph_edge_comes_from_the_recorded_event_and_never_from_words(self):
        self.task("NEW")
        self.assertEqual(self.cli("decision", "--actor", "C", "--reason", "T is superseded by NEW", "--id", "D",
                                  "--owner-words", "Sostituisci T con NEW", "--channel", "typed-here"), 0, self.last_error)
        before = self.graph()
        self.assertEqual(support.edges_of(before, kind="supersedes"), set(), "words naming both tasks draw no edge")
        self.assertEqual(self.supersede("T", "NEW", reason="Work moved to NEW"), 0, self.last_error)
        graph = self.graph()
        self.assertIn("supersedes", EDGE_KINDS)
        self.assertEqual([e for e in graph["edges"] if e["kind"] == "supersedes"],
                         [{"from": "task:T", "to": "task:NEW", "kind": "supersedes", "recorded": True}])
        self.assertEqual([e for e in graph["edges"] if e["kind"] != "supersedes"], before["edges"])
        self.assertEqual(graph["nodes"], before["nodes"], "the event adds one edge and no node")
        seq = {n["id"]: n["seq"] for n in graph["nodes"]}
        self.assertTrue(all(seq[e["from"]] < seq[e["to"]] for e in graph["edges"]), "every edge goes from earlier to later")
        self.assertEqual((graph["cycles"], graph["unresolved"]), ([], []))

    def test_graph_ignores_forged_supersede_events_without_both_recorded_tasks(self):
        def event(seq, kind, data):
            return {"seq": seq, "kind": kind, "actor": "C", "utc": "t", "data": data}
        forged = [event(1, "task", {"id": "T", "title": "T", "owner": "A"}),
                  event(2, "supersede", {"task": "T", "by": "missing", "reason": "r"}),
                  event(3, "supersede", {"task": ["T"], "by": {"id": "T"}, "reason": "r"}),
                  event(4, "supersede", {}),
                  event(5, "supersede", {"task": "ghost", "by": "T", "reason": "r"})]
        graph = decision_graph(forged)
        self.assertEqual((graph["edges"], graph["cycles"], graph["unresolved"]), ([], [], []))
        self.assertEqual([n["id"] for n in graph["nodes"]], ["task:T"])

    def test_supersession_survives_reopen_read_only_open_and_the_served_state(self):
        self.task("NEW", owner="B")
        self.assertEqual(self.supersede("T", "NEW"), 0, self.last_error)
        state, graph = self.store.state(), self.graph()
        self.reopen()
        self.assertEqual(self.store.state(), state)
        self.assertEqual(self.graph(), graph)
        reader = Store(self.root, read_only=True)
        try:
            self.assertEqual(reader.state()["tasks"]["T"]["superseded_by"], "NEW")
        finally:
            reader.close()
        with support.served(self.root) as get:
            status, body = get("/api/state")
        self.assertEqual(status, 200)
        served = json.loads(body)
        self.assertEqual(served["tasks"]["T"]["superseded_by"], "NEW")
        self.assertEqual([(s["task"], s["by"]) for s in served["attention"]["superseded"]], [("T", "NEW")])
        self.assertIn({"from": "task:T", "to": "task:NEW", "kind": "supersedes", "recorded": True}, served["graph"]["edges"])
        self.assertEqual(served["progress"]["T"]["state"]["id"], "superseded")
        self.assertEqual((served["comms"].get("unknown_kinds") or {}, served["comms"].get("malformed") or 0), ({}, 0),
                         "the new kind is a known non-communication, never an unrecognised row")

    def test_recorded_log_with_the_new_kind_replays_into_a_fresh_store(self):
        self.task("NEW", owner="B")
        self.assertEqual(self.supersede("T", "NEW"), 0, self.last_error)
        events, state = self.store.events(), self.store.state()
        with tempfile.TemporaryDirectory(prefix="squadra-supersede-replay-") as temp:
            copy = Store(Path(temp) / "record", create=True)
            try:
                for e in events:
                    copy.db.execute("INSERT INTO events(seq,id,utc,kind,actor,data,previous,hash) VALUES(?,?,?,?,?,?,?,?)",
                                    (e["seq"], e["id"], e["utc"], e["kind"], e["actor"], canonical(e["data"]), e["previous"], e["hash"]))
                self.assertEqual(copy.events(), events, "the chain with a supersede event verifies unchanged")
                replayed = copy.state()
            finally:
                copy.close()
        replayed.pop("root")
        state.pop("root")
        self.assertEqual(replayed, state)


class Equivalence(unittest.TestCase):
    """Compare replacement during task creation with replacement recorded afterward."""

    def record(self, base, name, after_the_fact):
        store = new_store(base / name, base)
        try:
            add_task(store, base, "OLD")
            spend_code_budget(store, base, "OLD")
            add_task(store, base, "U", owner="B")
            store.append("ask", "C", {"id": "QU", "question": "Which view first?", "options": support.OPTIONS,
                                      "waiting": [], "task": "U", "evidence": []})
            store.append("message", "C", {"id": "note", "to": "B", "body": "Unrelated message"})
            add_task(store, base, "D", owner="B", depends=["OLD"])
            if after_the_fact:
                add_task(store, base, "NEW", owner="B")
                store.append("supersede", "C", {"task": "OLD", "by": "NEW", "reason": "Work moved to NEW"})
            else:
                add_task(store, base, "NEW", owner="B", supersedes="OLD")
            return store.events(), store.state()
        finally:
            store.close()

    def test_supersede_has_exactly_the_effect_of_task_supersedes(self):
        with tempfile.TemporaryDirectory(prefix="squadra-supersede-same-") as temp:
            base = Path(temp).resolve()
            created, created_state = self.record(base, "at-creation", False)
            later, later_state = self.record(base, "after-the-fact", True)
        for state in (created_state, later_state):
            state.pop("root")
        self.assertEqual(unstamped(later_state["tasks"]["OLD"]), unstamped(created_state["tasks"]["OLD"]))
        self.assertEqual(later_state["tasks"]["OLD"]["superseded_by"], "NEW")
        self.assertEqual(created_state["tasks"]["NEW"].pop("supersedes"), "OLD")
        self.assertNotIn("supersedes", later_state["tasks"]["NEW"], "the replacement's own record is not rewritten")
        self.assertEqual(unstamped(later_state["tasks"]), unstamped(created_state["tasks"]))
        self.assertEqual(unstamped(attention(later_state)), unstamped(attention(created_state)))
        self.assertEqual(unstamped(waits(later_state, LATER)), unstamped(waits(created_state, LATER)))
        self.assertEqual(unstamped(task_progress(later)), unstamped(task_progress(created)))
        self.assertEqual(table(later_state).splitlines()[1:], table(created_state).splitlines()[1:])
        self.assertEqual(decision_graph(later)["edges"], decision_graph(created)["edges"])
        self.assertIn({"from": "task:OLD", "to": "task:NEW", "kind": "supersedes", "recorded": True},
                      decision_graph(later)["edges"])


class Replay(unittest.TestCase):
    def test_old_log_without_the_kind_projects_as_before(self):
        events = [json.loads(line) for line in (FIXTURES / "c53fa0a-events.jsonl").read_text().splitlines()]
        old = json.loads((FIXTURES / "c53fa0a-state.json").read_text())
        self.assertNotIn("supersede", {e["kind"] for e in events})
        state = initial()
        for event in events:
            apply(state, event)
        self.assertEqual(set(state) - set(old), {"refs"})
        self.assertEqual({key: state[key] for key in old}, old)
        self.assertEqual(attention(state)["superseded"], [])
        self.assertEqual(support.edges_of(decision_graph(events), kind="supersedes"), set())


if __name__ == "__main__":
    unittest.main()
