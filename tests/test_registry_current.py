import copy
import datetime as dt
import json
import os
from pathlib import Path
import random
import sqlite3
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
if os.environ.get("SQ_CURRENT_SOURCE"):
    sys.path.append(os.environ["SQ_CURRENT_SOURCE"])
import registry
import registry_current as current


class NoScan(dict):
    def __iter__(self):
        raise AssertionError("historical mapping enumerated")
    def values(self):
        raise AssertionError("historical values enumerated")
    def items(self):
        raise AssertionError("historical items enumerated")


class NoListScan(list):
    def __iter__(self):
        raise AssertionError("output history enumerated")
    def __reversed__(self):
        raise AssertionError("output history reversed")


class CurrentIndexes(unittest.TestCase):
    def setUp(self):
        self.state, self.indexes, self.events = registry.initial(), current.empty(), []
        self.add("init", {"name": "Synthetic", "limits": {"build": 2, "plan": 3, "code": 2}})
        self.agent("coord", "coordinator")
        self.agent("builder", "identity-builder")
        self.agent("reviewer", "identity-reviewer", role="reviewer")
        self.task("T")

    def add(self, kind, data, actor="coord", utc=None, guarded=False):
        seq = len(self.events) + 1
        utc = utc or (dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(seconds=seq)).isoformat()
        event = {"seq": seq, "id": "e" + str(seq), "kind": kind, "data": copy.deepcopy(data),
                 "actor": actor, "utc": utc}
        self.events.append(event)
        registry.apply(self.state, event)
        state = self.guarded() if guarded else self.state
        current.advance(self.indexes, state, event)
        return event

    def agent(self, name, identity, role="builder", **kwargs):
        return self.add("agent", {"id": name, "identity": identity, "role": role,
                                 "client": "codex", "skills": []}, **kwargs)

    def task(self, name, **kwargs):
        return self.add("task", {"id": name, "title": name, "owner": "builder", "reviewer": "reviewer",
                                 "repo": "/synthetic", "base": "base", "depends": [], **kwargs})

    def message(self, name, recipient="builder", **kwargs):
        return self.add("message", {"id": name, "to": recipient, "body": "private-canary-do-not-index", **kwargs})

    def start_run(self, name, mode="build", task="T", **kwargs):
        return self.add("run.start", {"id": name, "task": task, "mode": mode, **kwargs}, actor="builder")

    def guarded(self):
        result = dict(self.state)
        for key in ("agents", "tasks", "messages", "runs"):
            result[key] = NoScan(self.state[key])
        result["outputs"] = NoListScan(self.state["outputs"])
        return result

    def assert_equivalent(self):
        state, indexes = self.state, self.indexes
        self.assertEqual(list(current.pending_rows(indexes, state)),
                         [row for row in state["messages"].values() if row["ack"] is None])
        self.assertEqual(list(current.live_rows(indexes, state)),
                         [row for row in state["runs"].values() if row["status"] in current.LIVE_STATUSES])
        self.assertEqual(indexes["task_timings"], registry.board_times(self.events))
        self.assertEqual(list(indexes["current_tasks"]), [name for name, row in state["tasks"].items()
                         if row["status"] not in {"done", "withdrawn"} and not row.get("superseded_by")])
        for agent in state["agents"]:
            self.assertEqual(current.latest_ping(indexes, state, agent), registry.latest_ping(state, agent))
            expected = next((o for o in reversed(state["outputs"]) if o["agent"] == agent), {})
            self.assertEqual(current.latest_output(indexes, state, agent), expected)
            expected = max((m["ack"]["utc"] for m in state["messages"].values() if m["ack"]
                            and state["agents"].get(m["to"], {}).get("identity") == state["agents"][agent]["identity"]), default=None)
            self.assertEqual(current.last_answer(indexes, state, agent), expected)
            self.assertEqual(list(current.message_ids(indexes, recipient=agent)),
                             [m["id"] for m in state["messages"].values() if m["to"] == agent])
            for task in state["tasks"]:
                expected = any(m.get("related_task") == task and m["to"] == agent for m in state["messages"].values())
                self.assertEqual(current.related(indexes, agent, task), expected)
        for task in state["tasks"]:
            for mode in (None, "build", "proof", "probe", "read"):
                expected = [r for r in state["runs"].values() if r["task"] == task and (mode is None or r["mode"] == mode)]
                self.assertEqual(current.run_count(indexes, task, mode), len(expected))
                self.assertEqual(list(current.runs_for(indexes, state, task, mode)), expected)
                if mode is not None:
                    self.assertEqual(current.latest_run(indexes, state, task, mode), expected[-1] if expected else None)

    def test_message_ack_alias_and_output_queries_match_original_views(self):
        self.agent("builder-read", "identity-builder", role="reviewer")
        self.message("m1", ping=True)
        self.add("ack", {"message": "m1"}, actor="builder")
        self.message("m2", "builder-read", ping=True, related_task="T")
        self.message("assignment", task="T")
        self.add("output", {"task": "T", "next": "continue", "text": "private-canary-output"}, actor="builder")
        self.add("output", {"next": "read"}, actor="reviewer")
        self.add("output", {"task": "T", "next": "finished"}, actor="builder")
        self.assert_equivalent()
        before = registry.presence(self.state, "2026-01-01T01:00:00+00:00")
        for row in before:
            self.assertEqual(row["ping"], (current.latest_ping(self.indexes, self.state, row["agent"]) or {}).get("id"))
            self.assertEqual(row["last_answer"], current.last_answer(self.indexes, self.state, row["agent"]))
        expected_acks = [row["id"] for row in registry.attention(self.state)["waiting"] if row["kind"] == "ack"]
        actual_acks = [m["id"] for m in current.pending_rows(self.indexes, self.state) if not m.get("pool")]
        self.assertEqual(actual_acks, expected_acks)
        self.assertIn("finished", registry.table(self.state))
        self.assertEqual(current.latest_output(self.indexes, self.state, "builder")["next"], "finished")
        self.assertNotIn("private-canary", json.dumps(self.indexes))

    def test_registration_after_observation_attaches_identity_without_scanning_messages(self):
        self.message("early", "late", ping=True)
        self.add("ack", {"message": "early"}, actor="late")
        self.agent("alias", "identity-late")
        self.message("later", "alias", ping=True)
        self.agent("late", "identity-late", guarded=True)
        self.assertEqual(current.latest_ping(self.indexes, self.state, "late")["id"], "later")
        self.assertEqual(current.last_answer(self.indexes, self.state, "late"), self.state["messages"]["early"]["ack"]["utc"])
        self.assert_equivalent()

    def test_ping_order_and_answer_order_match_existing_semantics(self):
        self.message("old")
        self.add("ack", {"message": "old"}, actor="builder", utc="2026-01-01T12:00:00Z")
        self.message("next")
        self.add("ack", {"message": "next"}, actor="builder", utc="2026-01-01T12:00:00.500000Z")
        self.message("ping1", ping=True)
        self.add("message", {"id": "ping2", "to": "builder", "ping": True}, utc="2025-01-01T00:00:00Z")
        self.assertEqual(current.last_answer(self.indexes, self.state, "builder"), "2026-01-01T12:00:00Z")
        self.assertEqual(current.latest_ping(self.indexes, self.state, "builder")["id"], "ping2")
        self.assert_equivalent()

    def test_run_lifecycle_counts_and_read_stage_membership(self):
        self.start_run("b1")
        self.start_run("p1", "proof")
        self.start_run("r1", "read", stage="plan")
        self.start_run("r2", "read")
        self.start_run("b2")
        self.add("run.end", {"id": "b2", "status": "cleanup-unknown", "native_rc": None}, actor="builder")
        self.add("run.end", {"id": "b1", "status": "cleanup-unknown", "native_rc": None}, actor="builder")
        self.assertEqual([r["id"] for r in current.live_rows(self.indexes, self.state, "cleanup-unknown")], ["b1", "b2"])
        self.add("run.end", {"id": "p1", "status": "passed", "native_rc": 0, "after": {"sha256": "x"}}, actor="builder")
        self.add("run.cancel", {"id": "r2"}, actor="builder")
        self.add("run.progress", {"id": "r1"}, actor="builder")
        self.assertEqual(current.run_count(self.indexes, "T", "read", "plan"), 1)
        self.assertEqual(current.run_count(self.indexes, "T", "read", "code"), 1)
        self.assertEqual(current.latest_run(self.indexes, self.state, "T", "build")["id"], "b2")
        self.assert_equivalent()

    def test_abandoned_run_is_terminal_not_successful(self):
        self.start_run("orphan")
        self.add("run.abandon", {"id": "orphan"}, actor="builder")
        self.assertEqual(list(current.live_rows(self.indexes, self.state)), [])
        self.assertEqual(current.run_count(self.indexes, "T", "build"), 1)
        self.assertIsNone(current.latest_run(self.indexes, self.state, "T", "build")["native_rc"])

    def test_legacy_terminal_metadata_relocation_repairs_only_affected_buckets(self):
        self.task("T2")
        self.start_run("old")
        self.start_run("new")
        self.start_run("newest", "read", task="T2", stage="code")
        self.add("run.end", {"id": "new", "task": "T2", "mode": "read", "stage": "plan", "status": "passed"}, actor="builder", guarded=True)
        self.assertEqual(current.latest_run(self.indexes, self.state, "T", "build")["id"], "old")
        self.add("run.end", {"id": "old", "task": "T2", "mode": "read", "stage": "code", "status": "passed"}, actor="builder", guarded=True)
        self.assertEqual(current.latest_run(self.indexes, self.state, "T2", "read")["id"], "newest")
        self.assertEqual([r["id"] for r in current.runs_for(self.indexes, self.state, "T2", "read")], ["old", "new", "newest"])
        self.add("run.end", {"id": "newest", "stage": "plan", "status": "passed"}, actor="builder", guarded=True)
        self.assertEqual(current.run_count(self.indexes, "T2", "read", "code"), 1)
        self.assertEqual(current.run_count(self.indexes, "T2", "read", "plan"), 2)
        self.start_run("null-stage", "read", task="T2", stage="code")
        self.add("run.end", {"id": "null-stage", "stage": None, "status": "passed"}, actor="builder", guarded=True)
        self.assertEqual(current.run_count(self.indexes, "T2", "read", "code"), 1)
        self.assertEqual(current.run_count(self.indexes, "T2", "read"), 4)
        self.assert_equivalent()
        self.assertEqual(current.build(iter(self.events), registry.apply, registry.initial), self.indexes)

    def test_task_timing_and_replacement_match_full_board(self):
        self.add("start", {"task": "T"})
        first = self.events[-1]["utc"]
        self.add("start", {"task": "T"})
        self.task("T2", supersedes="T")
        self.add("start", {"task": "T2"})
        self.add("close", {"task": "T2"})
        self.task("T3")
        self.task("T4")
        self.add("supersede", {"task": "T3", "by": "T4"})
        self.assertEqual(self.indexes["task_timings"]["T"]["started"], first)
        self.assertEqual(list(self.indexes["current_tasks"]), ["T4"])
        self.assert_equivalent()

    def test_every_increment_queries_only_demanded_historical_entities(self):
        for n in range(50):
            self.message("old" + str(n))
            self.add("ack", {"message": "old" + str(n)}, actor="builder")
            self.add("output", {"next": str(n)}, actor="builder")
        self.add("message", {"id": "new", "to": "builder", "ping": True, "related_task": "T"}, guarded=True)
        self.add("ack", {"message": "new"}, actor="builder", guarded=True)
        self.add("output", {"next": "last"}, actor="builder", guarded=True)
        self.add("run.start", {"id": "run", "task": "T", "mode": "build"}, actor="builder", guarded=True)
        self.add("run.end", {"id": "run", "status": "passed"}, actor="builder", guarded=True)
        guarded = self.guarded()
        self.assertEqual(current.latest_output(self.indexes, guarded, "builder")["next"], "last")
        self.assertEqual(current.latest_ping(self.indexes, guarded, "builder")["id"], "new")
        self.assertEqual(list(current.pending_rows(self.indexes, guarded)), [])
        self.assertEqual(list(current.live_rows(self.indexes, guarded)), [])
        self.assertEqual(current.latest_run(self.indexes, guarded, "T", "build")["id"], "run")

    def test_build_replays_generator_and_indexes_match_each_randomized_step(self):
        randomizer = random.Random(1234)
        pending, running = [], []
        for position in range(100):
            choice = randomizer.randrange(5)
            if choice == 0 and pending:
                name = pending.pop(randomizer.randrange(len(pending)))
                self.add("ack", {"message": name}, actor="builder")
            elif choice == 1 and running:
                name = running.pop(randomizer.randrange(len(running)))
                self.add("run.end", {"id": name, "status": randomizer.choice(["failed", "passed", "cleanup-unknown"])}, actor="builder")
            elif choice == 2:
                name = "run" + str(position)
                self.start_run(name, randomizer.choice(["build", "probe", "read"]))
                running.append(name)
            elif choice == 3:
                self.add("output", {"next": str(position)}, actor=randomizer.choice(["builder", "reviewer"]))
            else:
                name = "msg" + str(position)
                self.message(name, ping=bool(randomizer.randrange(2)), related_task="T")
                pending.append(name)
            self.assert_equivalent()
        self.assertEqual(current.build((copy.deepcopy(e) for e in self.events), registry.apply, registry.initial), self.indexes)

    def test_query_bounds_and_pending_by_recipient_and_task(self):
        self.message("one", related_task="T")
        self.message("two", "reviewer", task="T")
        self.add("ack", {"message": "one"}, actor="builder")
        self.assertEqual(list(current.message_ids(self.indexes, task="T")), ["one", "two"])
        self.assertEqual(list(current.message_ids(self.indexes, task="T", pending=True)), ["two"])
        self.assertEqual(list(current.message_ids(self.indexes, recipient="builder", pending=True)), [])
        self.assertEqual(current.run_count(self.indexes, "missing"), 0)
        self.assertEqual(current.latest_output(self.indexes, self.state, "missing"), {})
        with self.assertRaises(ValueError):
            current.message_ids(self.indexes)
        with self.assertRaises(ValueError):
            current.message_ids(self.indexes, recipient="builder", task="T")
        with self.assertRaises(ValueError):
            current.run_count(self.indexes, "T", "build", "code")
        with self.assertRaises(ValueError):
            list(current.live_rows(self.indexes, self.state, "passed"))

    def test_private_attachment_does_not_add_public_state_fields(self):
        class View(dict):
            pass
        view = View(self.state)
        view.current = self.indexes
        self.assertIs(current.attached(view), self.indexes)
        self.assertIsNone(current.attached(self.state))
        self.assertNotIn("current", view)
        self.assertNotIn("_index", view)

    def test_message_order_survives_unordered_membership_and_ack_removal(self):
        for name in ('z-last-lexically', 'a-first-lexically', 'm-middle'):
            self.message(name, related_task='T')
        self.add('ack', {'message': 'a-first-lexically'}, actor='builder')
        for key in ('pending_messages', 'message_order'):
            self.indexes[key] = dict(sorted(self.indexes[key].items()))
        for key in ('messages_by_recipient', 'pending_by_recipient', 'messages_by_task'):
            self.indexes[key] = {name: dict(sorted(rows.items())) for name, rows in self.indexes[key].items()}
        self.assertEqual(list(current.message_ids(self.indexes, recipient='builder')),
                         ['z-last-lexically', 'a-first-lexically', 'm-middle'])
        self.assertEqual(list(current.message_ids(self.indexes, task='T', pending=True)),
                         ['z-last-lexically', 'm-middle'])
        self.assertEqual([row['id'] for row in current.pending_rows(self.indexes, self.state)],
                         ['z-last-lexically', 'm-middle'])

    def test_forecast_metadata_matches_complete_history_without_source_or_message_payloads(self):
        import forecast
        self.message('assign-T', task='T')
        self.add('ack', {'message': 'assign-T'}, actor='builder')
        self.message('not-an-assignment', task='T')
        self.add('ack', {'message': 'not-an-assignment'}, actor='builder')
        self.add('plan', {'task': 'T', 'artifact': {'secret': 'private-plan-canary'}})
        self.add('review', {'task': 'T', 'stage': 'plan', 'verdict': 'accepted'})
        self.add('start', {'task': 'T'})
        self.add('deliver', {'task': 'T', 'snapshot': {'files': ['private-source-canary']}})
        self.add('review', {'task': 'T', 'stage': 'code', 'verdict': 'changes-required'})
        self.add('deliver', {'task': 'T', 'snapshot': {'files': ['private-source-canary']}})
        self.add('review', {'task': 'T', 'stage': 'code', 'verdict': 'accepted'})
        self.add('close', {'task': 'T'})
        metadata = self.indexes['forecast_events']['T']
        self.assertEqual(forecast.timings(metadata, 'T'), forecast.timings(self.events, 'T'))
        self.assertNotIn('private-', json.dumps(metadata))
        self.assertEqual([row['kind'] for row in metadata],
                         ['task', 'message', 'ack', 'plan', 'review', 'deliver', 'review', 'deliver', 'review', 'close'])
        self.assertEqual(current.build(self.events, registry.apply, registry.initial)['forecast_events'],
                         self.indexes['forecast_events'])

    def test_indexes_advance_in_authenticated_tree_and_reload_identically(self):
        from registry_cache import Tree, plain
        self.message("message", ping=True, related_task="T")
        self.add("ack", {"message": "message"}, actor="builder")
        self.add("output", {"task": "T", "next": "done"}, actor="builder")
        self.start_run("build")
        self.add("run.end", {"id": "build", "status": "passed"}, actor="builder")
        self.add("start", {"task": "T"})
        self.add("close", {"task": "T"})
        connection = sqlite3.connect(":memory:", isolation_level=None)
        connection.row_factory = sqlite3.Row
        self.addCleanup(connection.close)
        tree = Tree.build(connection, current.empty())
        state = registry.initial()
        for event in self.events:
            registry.apply(state, event)
            current.advance(tree.root, state, event)
            tree.flush()
        descriptor = tree.flush()
        reopened = Tree(connection, descriptor)
        self.assertEqual(plain(reopened.root), self.indexes)
        self.assertEqual(current.latest_output(reopened.root, self.guarded(), "builder")["next"], "done")
        self.assertEqual(list(current.pending_rows(reopened.root, self.guarded())), [])
        self.assertEqual(list(current.live_rows(reopened.root, self.guarded())), [])


if __name__ == "__main__":
    unittest.main()
