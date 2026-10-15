"""Recorded history stays unchanged while workspace updates are projected."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from registry import Store, apply, canonical, initial  # noqa: E402
from squadra import main  # noqa: E402


class FoldIntegrity(unittest.TestCase):
    def init_event(self):
        return {"id": "init", "kind": "init", "actor": "system", "utc": "2026-01-01T00:00:00+00:00",
                "data": {"name": "Original", "project": "/synthetic", "temporary": True,
                         "limits": {"plan": 3, "code": 2, "build": 3}}}

    def test_every_event_kind_preserves_recorded_data_during_fold(self):
        fixtures = ROOT / "tests" / "fixtures"
        events = [json.loads(line) for line in (fixtures / "c53fa0a-events.jsonl").read_text().splitlines()]
        old_state = json.loads((fixtures / "c53fa0a-state.json").read_text())
        historical_count = len(events)
        artifact = {"sha256": "a" * 64, "name": "brief.txt", "original": "/synthetic/brief.txt", "bytes": 5}
        for kind, data in [
            ("brief", {"project": True, "text": "Brief", "artifact": artifact}),
            ("brief", {"task": "T", "text": "Brief", "artifact": artifact}),
            ("availability", {"agent": "A", "status": "stand-by", "reason": "Synthetic absence"}),
            ("availability", {"agent": "A", "status": "active", "reason": "Synthetic return"}),
            ("settings", {"wait_seconds": {"alive": 30}}),
            ("supersede", {"task": "T2", "by": "T4", "reason": "Synthetic replacement"}),
        ]:
            events.append({"id": "extra-" + str(len(events)), "seq": len(events) + 1, "kind": kind,
                           "actor": "C", "utc": events[-1]["utc"], "data": copy.deepcopy(data)})
        self.assertEqual({e["kind"] for e in events}, {
            "init", "agent", "task", "message", "ack", "plan", "review", "start", "run.start",
            "run.progress", "run.end", "deliver", "transport", "resource.acquire", "resource.release",
            "run.cancel", "ask", "decision", "pause", "resume", "output", "legacy", "settings",
            "save", "restore.check", "challenge", "benchmark", "diagnostic", "close", "restore.freeze",
            "brief", "availability", "supersede",
        })
        recorded = canonical(events)
        state = initial()
        for index, event in enumerate(events, 1):
            apply(state, event)
            self.assertEqual(canonical(events), recorded, f"recorded history changed at {index}: {event['kind']}")
            if index == historical_count:
                self.assertEqual({key: state[key] for key in old_state}, old_state)
        self.assertEqual(state["workspace"]["brief"]["text"], "Brief")
        self.assertEqual(state["tasks"]["T"]["brief"]["text"], "Brief")
        self.assertEqual(state["workspace"]["wait_seconds"], {"alive": 30})

    def test_workspace_add_replace_delete_does_not_change_init(self):
        event = self.init_event()
        recorded = canonical(event)
        state = initial()
        apply(state, event)
        state["workspace"]["brief"] = {"text": "Later"}
        state["workspace"]["name"] = "Changed"
        del state["workspace"]["project"]
        self.assertEqual(canonical(event), recorded)

    def test_init_add_replace_delete_does_not_change_workspace(self):
        event = self.init_event()
        state = initial()
        apply(state, event)
        projected = canonical(state)
        event["data"]["wait_seconds"] = {"alive": 1}
        event["data"]["name"] = "Changed"
        del event["data"]["project"]
        self.assertEqual(canonical(state), projected)


class ExportIntegrity(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="squadra-export-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / "record"
        self.cli("init", "--name", "Export integrity", "--project", str(self.base), "--temporary")
        self.store = Store(self.root)
        self.addCleanup(self.store.close)

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(["--root", str(self.root), *args])
        self.assertEqual(rc, 0, err.getvalue())

    def rows(self):
        return [dict(row) for row in self.store.db.execute("SELECT * FROM events ORDER BY seq")]

    def prepare_task(self):
        for seat, role in (("C", "coordinator"), ("A", "builder")):
            self.cli("agent", "--id", seat, "--identity", seat, "--role", role,
                     "--client", "manual", "--repo", str(self.base))
        self.store.append("task", "C", {"id": "T", "title": "Synthetic task", "owner": "A",
                                         "repo": str(self.base), "base": "a" * 40,
                                         "scope": {}, "proof": None, "depends": []})

    def updates(self):
        for revision in (1, 2):
            brief = self.base / f"brief-{revision}.txt"
            brief.write_text(f"Project description {revision}\n")
            settings = self.base / f"settings-{revision}.json"
            settings.write_text(json.dumps({"alive": 30 * revision, "ack": 60 * revision}))
            yield ("brief", "--actor", "C", "--project", "--file", str(brief))
            yield ("settings", "--actor", "C", "--file", str(settings))
            yield ("brief", "--actor", "C", "--task", "T", "--file", str(brief))

    def assert_export_matches(self, rows):
        exported = [json.loads(line) for line in (self.root / "events.jsonl").read_text().splitlines()]
        self.assertEqual(len(exported), len(rows))
        previous = "0" * 64
        for row, event in zip(rows, exported):
            self.assertEqual(canonical(event["data"]), row["data"], f"event {row['seq']} data")
            self.assertEqual(event["previous"], row["previous"])
            self.assertEqual(event["hash"], row["hash"])
            self.assertEqual(event["previous"], previous)
            payload = {key: event[key] for key in ("id", "utc", "kind", "actor", "previous", "data")}
            if "format" in event:
                self.assertEqual(event["format"], row["hash_format"])
                payload["format"] = event["format"]
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            self.assertEqual(hashlib.sha256(encoded).hexdigest(), event["hash"], f"event {row['seq']} hash")
            previous = event["hash"]

    def test_repeated_cli_briefs_settings_and_task_briefs_preserve_export_chain(self):
        self.prepare_task()
        for args in self.updates():
            before = self.rows()
            self.cli(*args)
            after = self.rows()
            self.assertEqual(after[:len(before)], before)
            self.assert_export_matches(after)
        before = self.rows()
        self.cli("export")
        self.assertEqual(self.rows(), before)
        self.assert_export_matches(before)

    def test_ordinary_export_repairs_old_projection_without_rewriting_sqlite(self):
        self.prepare_task()
        for args in self.updates():
            self.cli(*args)
        rows = self.rows()
        corrupted = self.store.events()
        corrupted[0]["data"]["brief"] = {"text": "A later brief in the old init event"}
        old_projection = "".join(canonical(event) + "\n" for event in corrupted).encode()
        self.assertNotEqual(canonical(corrupted[0]["data"]), rows[0]["data"])
        (self.root / "events.jsonl").write_bytes(old_projection)
        self.cli("export")
        self.assertEqual(self.rows(), rows)
        self.assert_export_matches(rows)
        self.assertNotEqual((self.root / "events.jsonl").read_bytes(), old_projection)

    def test_plain_init_export_remains_byte_identical(self):
        rows = self.rows()
        expected = "".join(canonical(event) + "\n" for event in self.store.events()).encode()
        self.assertEqual((self.root / "events.jsonl").read_bytes(), expected)
        self.cli("export")
        self.assertEqual(self.rows(), rows)
        self.assertEqual((self.root / "events.jsonl").read_bytes(), expected)
        self.assert_export_matches(rows)


if __name__ == "__main__":
    unittest.main()
