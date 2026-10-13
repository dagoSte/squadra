"""Check recorded conversations and current message ages without exposing bodies."""
import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from comms import communications
from dashboard_test_support import make_server
from registry import Store, apply, attention, initial, requirement_matrix, waits
from squadra import main


class Projection(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.store = Store(self.base / "record", create=True); self.addCleanup(self.store.close)
        self.store.append("init", "system", {"name": "Fixture", "project": str(self.base),
                          "limits": {"plan": 3, "code": 2, "build": 3}})
        for name, role, identity, client in [("C", "coordinator", "window-c", "claude"),
                                             ("A", "builder", "window-a", "codex"),
                                             ("A2", "executor", "window-a", "codex"),
                                             ("R", "reviewer", "window-r", "claude")]:
            self.store.append("agent", name, {"id": name, "role": role, "identity": identity,
                              "client": client, "repo": str(self.base), "skills": []})
        self.store.append("task", "C", {"id": "T", "owner": "A", "title": "Resume 115: local memory authority",
                          "base": "a" * 40, "repo": str(self.base), "depends": [], "scope": {}, "proof": None})

    def send(self, ident, sender="C", to="A", **extra):
        self.store.append("message", sender, {"id": ident, "to": to, "body": "SECRET BODY " + ident, **extra})

    def view(self, seconds=0, wait_list=None, limit=500):
        state = self.store.state()
        at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)
        return communications(state, self.store.events(), waits(state, at) if wait_list is None else wait_list, at, limit)

    def edge(self, view, kind, message):
        return next(e for e in view["edges"] if e["kind"] == kind and e["message"] == message)

    def test_states_sent_queued_acked_and_transports(self):
        self.send("sent")
        self.send("queued")
        self.store.append("transport", "C", {"message": "queued", "via": "codex-queue", "delivered": True, "native_rc": 0})
        self.send("manual")
        self.store.append("transport", "C", {"message": "manual", "via": "manual", "delivered": False})
        self.send("failed")
        self.store.append("transport", "C", {"message": "failed", "via": "codex-queue", "delivered": False, "native_rc": 1})
        self.send("read", related_task="T")
        self.store.append("ack", "A", {"message": "read"})
        view = self.view()
        self.assertEqual(self.edge(view, "message", "sent")["state"], "sent")
        self.assertEqual(self.edge(view, "message", "queued")["state"], "queued")
        self.assertEqual(self.edge(view, "message", "manual")["state"], "sent")
        self.assertIn("manual", self.edge(view, "message", "manual")["flags"])
        self.assertIn("transport-unconfirmed", self.edge(view, "message", "failed")["flags"])
        self.assertEqual([self.edge(view, "transport", m)["state"] for m in ("queued", "manual", "failed")],
                         ["queued", "manual", "unconfirmed"])
        read = self.edge(view, "message", "read")
        self.assertEqual((read["state"], read["task"], read["task_title"]), ("acked", "T", "Resume 115: local memory authority"))
        ack = self.edge(view, "ack", "read")
        self.assertEqual((ack["from"], ack["to"], ack["from_lane"], ack["to_lane"]), ("A", "C", "window-a", "window-c"))
        self.assertIsNotNone(ack["reply_seconds"])

    def test_overdue_only_from_waits_ids_on_the_callers_clock(self):
        self.send("late")
        self.assertEqual(self.edge(self.view(), "message", "late")["state"], "sent")
        late = self.edge(self.view(seconds=1000), "message", "late")
        self.assertEqual((late["state"], late["wait_threshold"]), ("overdue", 900))
        self.assertGreaterEqual(late["age_seconds"], 1000)
        self.assertEqual(self.edge(self.view(seconds=1000, wait_list=[]), "message", "late")["state"], "sent",
                         "age alone never marks a row overdue")

    def test_smallest_threshold_wins_when_one_id_has_two_waits(self):
        self.send("ping-1", ping=True)
        both = [{"kind": "ack", "id": "ping-1", "seconds": 1000, "threshold": 900},
                {"kind": "alive", "id": "ping-1", "seconds": 1000, "threshold": 300}]
        edge = self.edge(self.view(wait_list=both), "ping", "ping-1")
        self.assertEqual((edge["state"], edge["wait_threshold"]), ("overdue", 300))
        self.assertEqual(self.edge(self.view(wait_list=list(reversed(both))), "ping", "ping-1")["wait_threshold"], 300)

    def test_ping_and_its_answer(self):
        self.send("ping-2", ping=True)
        self.store.append("ack", "A", {"message": "ping-2"})
        view = self.view()
        self.assertEqual(self.edge(view, "ping", "ping-2")["state"], "acked")
        self.assertEqual(self.edge(view, "answer", "ping-2")["to"], "C")

    def test_lanes_group_aliases_and_flag_unregistered_recipients(self):
        self.send("alias", sender="A", to="A2")
        with contextlib.redirect_stdout(io.StringIO()):
            body = self.base / "body.md"; body.write_text("For a window that has not joined yet")
            self.assertEqual(main(["--root", str(self.store.root), "send", "--actor", "C", "--to-new", "newcomer",
                                   "--id", "awaited", "--body-file", str(body)]), 0)
        view = self.view()
        self.assertEqual([l["identity"] for l in view["lanes"]], ["window-c", "window-a", "window-r", "newcomer"])
        self.assertEqual({s["id"] for s in view["lanes"][1]["seats"]}, {"A", "A2"})
        self.assertEqual(view["lanes"][1]["work"][0]["task"], "T")
        alias = self.edge(view, "message", "alias")
        self.assertEqual(alias["from_lane"], alias["to_lane"])
        self.assertTrue(next(p for p in view["pairs"] if p["lanes"] == ["window-a", "window-a"])["same_window"])
        self.assertFalse(view["lanes"][3]["registered"])
        self.assertIn("unregistered-recipient", self.edge(view, "message", "awaited")["flags"])

    def test_availability_and_ping_shapes_of_sq_rule0(self):
        self.send("ping-3", ping=True)
        state = self.store.state()
        events = self.store.events()
        state["agents"]["A"]["availability"] = state["agents"]["A2"]["availability"] = {
            "status": "stand-by", "utc": events[-1]["utc"], "event": "evt-standby"}
        events.append({"id": "evt-standby", "seq": len(events) + 1, "utc": events[-1]["utc"], "kind": "availability",
                       "actor": "C", "data": {"agent": "A", "status": "stand-by", "ping": "ping-3",
                                              "reason": "Unanswered ping reached the current alive threshold"}})
        at = dt.datetime.now(dt.timezone.utc)
        view = communications(state, events, [], at)
        self.assertEqual(view["lanes"][1]["availability"], "stand-by")
        edge = view["edges"][-1]
        self.assertEqual((edge["kind"], edge["state"], edge["to_lane"], edge["ping"]), ("availability", "stand-by", "window-a", "ping-3"))
        self.assertEqual(edge["ping_utc"], state["messages"]["ping-3"]["utc"])
        self.assertIn("recipient-stand-by", self.edge(view, "ping", "ping-3")["flags"])

    def test_classification_order_unknown_and_malformed_never_crash(self):
        state = self.store.state()
        events = self.store.events()
        stamp = events[-1]["utc"]
        extra = [("output", {"agent": "A", "status": "stand-by", "text": "known kind, not a change"}),
                 ("future.availability", {"agent": "A", "status": "active"}),
                 ("future.kind", {"to": "R", "anything": 1}),
                 ("future.kind", "not a dict"),
                 ("message", {"id": "missing-from-state", "to": "A"}),
                 ("ack", None)]
        for n, (kind, data) in enumerate(extra):
            events.append({"id": "x%d" % n, "seq": len(events) + 1, "utc": stamp, "kind": kind, "actor": "C", "data": data})
        view = communications(state, events, [], dt.datetime.now(dt.timezone.utc))
        added = view["edges"][-5:]
        self.assertEqual([e["id"] for e in added], ["x1", "x2", "x3", "x4", "x5"], "known non-communication kinds are skipped")
        self.assertEqual((added[0]["kind"], added[0]["state"], added[0]["flags"]), ("availability", "active", ["unrecognised-kind"]))
        self.assertEqual((added[1]["kind"], added[1]["raw_kind"], added[1]["to"]), ("unknown", "future.kind", "R"))
        self.assertEqual((added[2]["kind"], added[2]["to"]), ("unknown", None))
        self.assertEqual([e["flags"] for e in added[3:]], [["malformed"], ["malformed"]])
        self.assertEqual(view["unknown_kinds"], {"future.availability": 1, "future.kind": 2})
        self.assertEqual(view["malformed"], 2)

    def test_bounded_to_the_newest_and_counted(self):
        self.store.batch([("message", "C", {"id": "m%03d" % n, "to": "A", "body": "x"}, None) for n in range(700)])
        view = self.view(limit=500)
        self.assertEqual((view["total"], view["shown"], len(view["edges"])), (700, 500, 500))
        self.assertEqual((view["edges"][0]["message"], view["edges"][-1]["message"]), ("m200", "m699"))
        self.assertEqual(view["pairs"][0]["messages"], 700, "pairs count every message, not only the loaded window")

    def test_pairs_put_overdue_conversations_first(self):
        self.send("calm", sender="C", to="R")
        self.store.append("ack", "R", {"message": "calm"})
        self.send("waiting", sender="C", to="A", related_task="T")
        pairs = self.view(seconds=1000)["pairs"]
        self.assertEqual(pairs[0]["lanes"], ["window-a", "window-c"])
        self.assertEqual((pairs[0]["overdue"], pairs[0]["tasks"][0]["title"]), (1, "Resume 115: local memory authority"))
        self.assertEqual((pairs[1]["acked"], pairs[1]["overdue"]), (1, 0))

    def test_no_bodies_and_nothing_projected_to_disk(self):
        self.send("private")
        view = self.view()
        self.assertNotIn("SECRET BODY", json.dumps(view))
        self.assertFalse(any("body" in e for e in view["edges"]))
        self.store.project()
        for path in self.store.root.rglob("*"):
            if path.is_file() and path.suffix in {".json", ".jsonl", ".md"}:
                text = path.read_text()
                self.assertNotIn("age_seconds", text, path)
                self.assertNotIn('"comms"', text, path)

    def test_dashboard_api_carries_comms_behind_the_token(self):
        self.send("api")
        server, token = make_server(self.store.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        request = urllib.request.Request("http://127.0.0.1:%d/api/state" % server.server_port,
                                         headers={"X-Squadra-Token": token})
        with urllib.request.urlopen(request) as result:
            state = json.loads(result.read())
        edge = next(e for e in state["comms"]["edges"] if e["message"] == "api")
        self.assertEqual((edge["state"], edge["from_lane"]), ("sent", "window-c"))
        clock = dt.datetime.fromisoformat(state["comms"]["as_of"])
        self.assertLess(abs((dt.datetime.now(dt.timezone.utc) - clock).total_seconds()), 60)


class FutureEvents(unittest.TestCase):
    """Unknown future kinds stay neutral rows even when their fields are lists or objects."""
    stamp = "2026-10-10T08:00:00+00:00"

    def setUp(self):
        self.state = initial()
        for name, role in [("a", "coordinator"), ("b", "builder")]:
            self.state["agents"][name] = {"id": name, "role": role, "identity": "window-" + name, "client": "codex"}

    def event(self, kind, data, n=1):
        return {"id": "event-%d" % n, "seq": n, "utc": self.stamp, "kind": kind, "actor": "a", "data": data}

    def view(self, events):
        return communications(self.state, events, [], dt.datetime.fromisoformat(self.stamp))

    def neutral(self, data):
        event = self.event("future.status", data)
        apply(self.state, event)
        view = self.view([event])
        self.assertEqual(view["total"], 1)
        self.assertEqual(view["edges"][0]["kind"], "unknown")
        self.assertEqual(view["unknown_kinds"], {"future.status": 1})

    def test_agent_list(self): self.neutral({"agent": ["b"], "status": "active"})
    def test_agent_object(self): self.neutral({"agent": {"id": "b"}, "status": "active"})
    def test_status_list(self): self.neutral({"agent": "b", "status": ["active"]})
    def test_status_object(self): self.neutral({"agent": "b", "status": {"value": "active"}})
    def test_unknown_scalar_status_stays_neutral(self): self.neutral({"agent": "b", "status": "new-state"})
    def test_unknown_non_mapping_stays_neutral(self): self.neutral(["future", "payload"])

    def test_scalar_availability_shape_is_recognised(self):
        edge = self.view([self.event("future.status", {"agent": "b", "status": "stand-by"})])["edges"][0]
        self.assertEqual((edge["kind"], edge["state"], edge["to"]), ("availability", "stand-by", "b"))
        self.assertIn("unrecognised-kind", edge["flags"])

    def test_known_kinds_take_precedence_over_the_shape(self):
        self.assertEqual(self.view([self.event("output", {"agent": "b", "status": "active"})])["total"], 0)
        view = self.view([self.event("availability", {"agent": ["b"], "status": {"value": "active"}})])
        self.assertEqual((view["edges"][0]["kind"], view["edges"][0]["flags"], view["malformed"]), ("unknown", ["malformed"], 1))
        self.assertEqual(view["unknown_kinds"], {}, "a known kind with compound fields is malformed, not unknown")


def page_snapshot(store, at, limit=500, standby=None, extra_waits_at=None):
    """Build the server state response, optionally including a window on stand-by."""
    state, events = store.state(), store.events()
    if standby:
        seat, ping = standby
        identity = state["agents"][seat]["identity"]
        events.append({"id": "evt-standby", "seq": len(events) + 1, "utc": events[-1]["utc"], "kind": "availability",
                       "actor": "coord-seat", "data": {"agent": seat, "status": "stand-by", "ping": ping,
                                                       "reason": "Unanswered presence call"}})
        for agent in state["agents"].values():
            if agent["identity"] == identity:
                agent["availability"] = {"status": "stand-by", "utc": events[-1]["utc"], "event": "evt-standby"}
        events.append({"id": "evt-future", "seq": len(events) + 1, "utc": events[-1]["utc"], "kind": "future.kind",
                       "actor": "coord-seat", "data": {"note": "a kind this page does not know"}})
    state["attention"] = attention(state)
    # One message is overdue through the same waits() entry the server computes at a later clock.
    state["waits"] = [w for w in waits(state, extra_waits_at or at) if w.get("id") == "m-late"]
    state["requirements"] = {k: requirement_matrix(t) for k, t in state["tasks"].items()}
    state["events"] = events[-120:]
    state["integrity"] = "verified"
    state["comms"] = communications(state, events, state["waits"], at, limit)
    return state


class CommsPage(unittest.TestCase):
    def fixture(self, folder):
        store = Store(folder / "record", create=True); self.addCleanup(store.close)
        store.append("init", "system", {"name": "Synthetic squad", "project": str(folder),
                     "limits": {"plan": 3, "code": 2, "build": 3}})
        for seat, role, identity, client in [("coord-seat", "coordinator", "window-c", "claude"),
                                             ("builder-seat-a", "builder", "window-a", "codex"),
                                             ("alias-seat", "executor", "window-a", "codex"),
                                             ("builder-seat-b", "builder", "window-b", "codex"),
                                             ("review-seat", "reviewer", "window-r", "claude")]:
            store.append("agent", seat, {"id": seat, "role": role, "identity": identity, "client": client,
                                         "repo": str(folder), "skills": []})
        for task, owner, title in [("SQ-T1", "builder-seat-a", "Live view of inter-window communication (parallel)"),
                                   ("MS-T2", "builder-seat-b", "Resume 116: public seams for the core")]:
            store.append("task", "coord-seat", {"id": task, "owner": owner, "title": title, "base": "a" * 40,
                                                "repo": str(folder), "depends": [], "scope": {}, "proof": None,
                                                "reviewer": "review-seat"})
        store.batch([("message", "coord-seat", {"id": "m-fill-%02d" % n, "to": "review-seat", "body": "filler"}, None)
                     for n in range(55)])
        send = lambda ident, sender, to, **extra: store.append("message", sender, {"id": ident, "to": to,
                                                                                 "body": "Body of " + ident, **extra})
        send("m-assign", "coord-seat", "builder-seat-a", task="SQ-T1")
        store.append("ack", "builder-seat-a", {"message": "m-assign"})
        send("m-queued", "coord-seat", "builder-seat-b", related_task="MS-T2")
        store.append("transport", "coord-seat", {"message": "m-queued", "via": "codex-queue", "delivered": True, "native_rc": 0})
        send("m-late", "coord-seat", "review-seat")
        send("m-alias", "builder-seat-a", "alias-seat")
        store.append("message", "review-seat", {"id": "m-xss", "to": "coord-seat",
                                                "body": "<img src=x onerror=alert(1)> zebraword"})
        send("ping-1", "coord-seat", "builder-seat-b", ping=True)
        send("m-awaited", "coord-seat", "newcomer", awaited=True)
        now = dt.datetime.now(dt.timezone.utc)
        later = now + dt.timedelta(seconds=1000)
        snapshots = {"first": page_snapshot(store, now, standby=("builder-seat-b", "ping-1"), extra_waits_at=later)}
        send("m-new-1", "coord-seat", "builder-seat-a", related_task="SQ-T1")
        send("m-new-2", "builder-seat-a", "coord-seat", related_task="SQ-T1")
        snapshots["second"] = page_snapshot(store, now, standby=("builder-seat-b", "ping-1"), extra_waits_at=later)
        snapshots["aged"] = page_snapshot(store, now + dt.timedelta(minutes=20), standby=("builder-seat-b", "ping-1"),
                                          extra_waits_at=later)
        snapshots["bounded"] = page_snapshot(store, now, limit=2, standby=("builder-seat-b", "ping-1"), extra_waits_at=later)
        send("m-new-3", "coord-seat", "builder-seat-a", related_task="SQ-T1")
        send("m-new-4", "builder-seat-a", "coord-seat", related_task="SQ-T1")
        snapshots["third"] = page_snapshot(store, now, standby=("builder-seat-b", "ping-1"), extra_waits_at=later)
        path = folder / "snapshots.json"
        path.write_text(json.dumps(snapshots))
        return path

    def test_comms_page_contract_real_javascript(self):
        node = shutil.which("node")
        if not node: self.fail("Node is required: the dashboard JS contract was not measured")
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            fixture = self.fixture(Path(folder))
            result = subprocess.run([node, str(source / "tests/comms_dashboard.js"), str(source / "assets/dashboard.html"),
                                     str(fixture)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("comms contract passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
