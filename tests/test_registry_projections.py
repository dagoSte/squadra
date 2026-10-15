import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import registry_projections as projections


class StoreFixture:
    def __init__(self, root, read_only=False):
        self.root, self.read_only = Path(root), read_only
        self.db = sqlite3.connect(self.root / "registry.sqlite3", isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY,id TEXT,utc TEXT,kind TEXT,actor TEXT,data TEXT,previous TEXT,hash TEXT)")
        self.full_reads = 0
        self.suffix_reads = []

    def append(self, kind, data, day="2026-10-15"):
        row = self.db.execute("SELECT seq,hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        seq, previous = (row[0] + 1, row[1]) if row else (1, projections.ZERO)
        event = {"id": f"e{seq}", "utc": day + "T12:00:00Z", "kind": kind,
                 "actor": "builder", "data": data, "previous": previous}
        digest = hashlib.sha256(projections._canonical(event).encode()).hexdigest()
        self.db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
                        (seq, event["id"], event["utc"], kind, event["actor"],
                         projections._canonical(data), previous, digest))
        return {**event, "seq": seq, "hash": digest}

    def events_since(self, seq=0, head=None):
        self.suffix_reads.append(seq)
        previous, rows = head or projections.ZERO, []
        if seq:
            anchor = self.db.execute("SELECT * FROM events WHERE seq=?", (seq,)).fetchone()
            if anchor is None or anchor["hash"] != previous:
                raise ValueError("Bad anchor")
            self.decode(anchor)
        for row in self.db.execute("SELECT * FROM events WHERE seq>? ORDER BY seq", (seq,)):
            event = self.decode(row)
            if event["seq"] != seq + 1 or event["previous"] != previous:
                raise ValueError("Bad chain")
            rows.append(event)
            previous, seq = event["hash"], event["seq"]
        return rows

    @staticmethod
    def decode(row):
        event = {key: row[key] for key in ("id", "utc", "kind", "actor", "previous")}
        event["data"] = json.loads(row["data"])
        if hashlib.sha256(projections._canonical(event).encode()).hexdigest() != row["hash"]:
            raise ValueError("Bad hash")
        return {**event, "seq": row["seq"], "hash": row["hash"]}

    def events(self):
        self.full_reads += 1
        return self.events_since()


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.store = StoreFixture(self.root)
        self.addCleanup(self.store.db.close)

    def file(self, name):
        return self.root / name

    def exported(self):
        return [json.loads(line) for line in self.file("events.jsonl").read_text().splitlines()]

    def test_initial_raw_export_and_idle_do_not_rewrite(self):
        event = self.store.append("init", {"name": "Example"})
        first = projections.publish(self.store)
        self.assertTrue(first["rebuilt"])
        self.assertEqual(self.exported(), [event])
        self.assertFalse(first["full_export_current"])
        before = {str(path): (path.stat().st_mtime_ns, path.read_bytes())
                  for path in self.root.rglob("*") if path.is_file()}
        with patch.object(self.store, "events", side_effect=AssertionError("Unexpected full replay")), \
                patch.object(projections, "_file_record", side_effect=AssertionError("Unexpected historical read")):
            second = projections.publish(self.store)
        after = {str(path): (path.stat().st_mtime_ns, path.read_bytes())
                 for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(second["changed"])
        self.assertTrue(second["events_current"])

    def test_append_updates_only_new_day_and_preserves_full_export_marker(self):
        first = self.store.append("init", {"name": "Example"}, "2026-10-14")
        projections.publish(self.store)
        self.file("state.json").write_text('{"immutable_export":true}\n')
        projections.mark_full_export(self.store, first["seq"], first["hash"])
        old_day = self.file("posta/exchange-2026-10-14.md")
        before = old_day.read_bytes(), old_day.stat().st_mtime_ns
        event = self.store.append("message", {"id": "hello", "to": "reader", "body": "Readable message"})
        with patch.object(self.store, "events", side_effect=AssertionError("Unexpected full replay")), \
                patch.object(projections, "_file_record", side_effect=AssertionError("Unexpected historical read")):
            result = projections.publish(self.store)
        self.assertFalse(result["rebuilt"])
        self.assertEqual((old_day.read_bytes(), old_day.stat().st_mtime_ns), before)
        self.assertEqual(self.exported(), [first, event])
        self.assertIn("Readable message", self.file("posta/exchange-2026-10-15.md").read_text())
        self.assertEqual(self.file("state.json").read_text(), '{"immutable_export":true}\n')
        self.assertEqual(result["full_export"], {"seq": first["seq"], "head": first["hash"]})
        self.assertFalse(result["full_export_current"])

    def test_partial_append_crash_recovers_exactly_once(self):
        first = self.store.append("message", {"id": "first", "to": "reader", "body": "before"})
        projections.publish(self.store)
        second = self.store.append("message", {"id": "second", "to": "reader", "body": "after-crash"})
        original, calls = projections._append, []

        def interrupted(path, parts):
            result = original(path, parts)
            calls.append(str(path))
            if len(calls) == 2:
                raise RuntimeError("Crash after data fsync, before cursor commit")
            return result

        with patch.object(projections, "_append", side_effect=interrupted), self.assertRaises(RuntimeError):
            projections.publish(self.store)
        self.assertFalse(projections.freshness(self.store)["events_current"])
        with patch.object(self.store, "events", side_effect=AssertionError("Should recover committed offsets")):
            result = projections.publish(self.store)
        self.assertFalse(result["rebuilt"])
        self.assertEqual(self.exported(), [first, second])
        self.assertEqual(self.file("squadra-posta.md").read_text().count("after-crash"), 1)
        self.assertEqual(self.file("posta/exchange-2026-10-15.md").read_text().count("after-crash"), 1)

    def test_uncommitted_tail_without_new_events_is_repaired_and_new_stamp_saved(self):
        event = self.store.append("init", {"name": "Example"})
        projections.publish(self.store)
        with self.file("events.jsonl").open("ab") as out:
            out.write(b'{"partial":')
        result = projections.publish(self.store)
        self.assertTrue(result["changed"])
        self.assertFalse(result["rebuilt"])
        self.assertEqual(self.exported(), [event])
        self.assertFalse(projections.publish(self.store)["changed"])

    def test_missing_touched_sidefile_or_corrupt_cursor_regenerates(self):
        first = self.store.append("message", {"id": "one", "body": "message"})
        projections.publish(self.store)
        self.file("posta/exchange-2026-10-15.md").unlink()
        second = self.store.append("ack", {"message": "one"})
        result = projections.publish(self.store)
        self.assertTrue(result["rebuilt"])
        self.assertEqual(self.exported(), [first, second])
        self.assertIn("message: one", self.file("squadra-posta.md").read_text())
        self.store.db.execute("UPDATE derived_projection_meta SET data='{}'")
        self.assertTrue(projections.publish(self.store)["rebuilt"])
        self.assertEqual(self.exported(), [first, second])

    def test_rewritten_committed_projection_is_regenerated_not_trusted(self):
        event = self.store.append("init", {"name": "Example"})
        projections.publish(self.store)
        self.file("events.jsonl").write_text("x" * self.file("events.jsonl").stat().st_size)
        self.assertTrue(projections.publish(self.store)["rebuilt"])
        self.assertEqual(self.exported(), [event])

    def test_corrupt_committed_anchor_with_extra_tail_forces_regeneration(self):
        event = self.store.append("init", {"name": "Example"})
        projections.publish(self.store)
        path = self.file("events.jsonl")
        path.write_bytes(path.read_bytes().replace(b"Example", b"Fxample") + b"uncommitted tail")
        self.assertTrue(projections.publish(self.store)["rebuilt"])
        self.assertEqual(self.exported(), [event])

    def test_bad_authoritative_hash_never_falls_back_to_existing_export(self):
        self.store.append("init", {"name": "Example"})
        projections.publish(self.store)
        before = self.file("events.jsonl").read_bytes()
        self.store.db.execute("UPDATE events SET data='{}'")
        with self.assertRaisesRegex(ValueError, "Bad hash"):
            projections.publish(self.store)
        self.assertEqual(self.file("events.jsonl").read_bytes(), before)

    def test_large_inventory_omitted_but_text_and_artifact_remain_readable(self):
        artifact = {"sha256": "a" * 64, "name": "proof.txt", "bytes": 12, "original": "proof.txt"}
        event = self.store.append("deliver", {"task": "task-one", "reason": "Ready for review",
            "snapshot": {"files": [{"path": "private-marker-" + str(i), "sha256": "b" * 64} for i in range(8000)]},
            "proof": artifact})
        projections.publish(self.store)
        daily = self.file("posta/exchange-2026-10-15.md").read_text()
        self.assertLess(len(daily.encode()), 2000)
        self.assertNotIn("private-marker", daily)
        self.assertIn("Ready for review", daily)
        self.assertIn(artifact["sha256"], daily)
        self.assertIn(event["hash"], daily)
        self.assertEqual(self.exported(), [event])

    def test_full_export_adopts_replaced_files_and_does_not_deadlock_with_owned_lock(self):
        event = self.store.append("init", {"name": "Example"})
        projections.publish(self.store)
        for relative in ["events.jsonl", "squadra-posta.md", "posta/exchange-2026-10-15.md"]:
            path = self.file(relative)
            projections._atomic_parts(path, [path.read_bytes()])
        with projections._locked(self.root):
            result = projections.mark_full_export(self.store, event["seq"], event["hash"], locked=True)
        self.assertTrue(result["full_export_current"])
        with patch.object(self.store, "events", side_effect=AssertionError("Unexpected regeneration")):
            self.assertFalse(projections.publish(self.store)["changed"])

    def test_read_only_refuses_without_creating_files_or_tables(self):
        self.store.append("init", {"name": "Example"})
        self.store.read_only = True
        before = self.file("registry.sqlite3").read_bytes(), sorted(os.listdir(self.root))
        self.assertIsNone(projections.freshness(self.store)["events"])
        with self.assertRaises(projections.ProjectionError):
            projections.publish(self.store)
        with self.assertRaises(projections.ProjectionError):
            projections.mark_full_export(self.store, 0, projections.ZERO)
        self.assertEqual((self.file("registry.sqlite3").read_bytes(), sorted(os.listdir(self.root))), before)

    def test_captured_marker_does_not_promise_no_later_commit(self):
        self.store.append("init", {"name": "Example"})
        projections.publish(self.store)
        path = self.file("projection-freshness.json")
        captured = path.read_bytes()
        marker = json.loads(captured)
        self.assertTrue(marker["events_current"])
        self.assertTrue(marker["captured_only"])
        self.store.append("message", {"id": "new", "body": "not projected yet"})
        current = projections.freshness(self.store)
        self.assertFalse(current["events_current"])
        self.assertEqual(current["authority"]["seq"], 2)
        self.assertEqual(current["events"]["seq"], 1)
        self.assertEqual(path.read_bytes(), captured)

    def test_stale_marker_after_committed_projection_is_repaired_on_idle(self):
        self.store.append("init", {"name": "Example"})
        projections.publish(self.store)
        path = self.file("projection-freshness.json")
        path.write_text('{}\n')
        with patch.object(self.store, "events", side_effect=AssertionError("Unexpected full replay")):
            result = projections.publish(self.store)
        self.assertFalse(result["changed"])
        self.assertEqual(json.loads(path.read_text()), projections.freshness(self.store))

    def test_symlinked_day_directory_never_changes_its_target(self):
        self.store.append("init", {"name": "Example"})
        projections.publish(self.store)
        directory = self.file("posta")
        moved = self.file("moved-posta")
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
        old_day = moved / "exchange-2026-10-15.md"
        with old_day.open("ab") as out:
            out.write(b"partial-external-tail")
        before = old_day.read_bytes(), old_day.stat().st_mtime_ns
        self.store.append("message", {"id": "new", "body": "do not follow link"})
        with self.assertRaises(projections.ProjectionError):
            projections.publish(self.store)
        self.assertEqual((old_day.read_bytes(), old_day.stat().st_mtime_ns), before)

    def test_created_projection_modes_are_private(self):
        self.store.append("init", {"name": "Example"})
        projections.publish(self.store)
        for relative in ["projection.lock", "events.jsonl", "squadra-posta.md",
                         "projection-freshness.json", "posta/exchange-2026-10-15.md"]:
            self.assertEqual(self.file(relative).stat().st_mode & 0o777, 0o600, relative)

    @unittest.skipIf(os.name == "nt", "POSIX flock test")
    def test_projection_lock_serializes_concurrent_publishers(self):
        self.store.append("init", {"name": "Example"})
        entered, finished, errors = threading.Event(), threading.Event(), []

        def worker():
            other = StoreFixture(self.root)
            try:
                entered.set()
                projections.publish(other)
            except BaseException as error:
                errors.append(error)
            finally:
                other.db.close()
                finished.set()

        with projections._locked(self.root):
            thread = threading.Thread(target=worker)
            thread.start()
            self.assertTrue(entered.wait(2))
            self.assertFalse(finished.wait(0.08))
        self.assertTrue(finished.wait(2))
        thread.join(2)
        self.assertEqual(errors, [])
        self.assertEqual(len(self.exported()), 1)


if __name__ == "__main__":
    unittest.main()
