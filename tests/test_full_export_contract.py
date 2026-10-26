"""Explicit complete exports and incremental summaries keep separate promises."""
import json
import unittest

import test_projection
from registry import canonical
from registry_projections import freshness


class FullExportContract(unittest.TestCase):
    setUp = test_projection.Projection.setUp

    def test_full_export_omits_reader_location_and_refreshes_after_incremental_publish(self):
        raw_before = canonical(self.store.events())
        state, events = self.store.project(with_events=True)
        self.assertNotIn("root", state)
        self.assertEqual(self.store.state()["root"], str(self.root))
        self.assertEqual(canonical(events), raw_before)
        path = self.root / "state.json"
        saved = path.read_bytes()
        self.assertEqual(json.loads(saved), state)
        self.assertTrue(freshness(self.store)["full_export_current"])

        self.store.append("message", "C", {"id": "later", "to": "A", "related_task": "T", "body": "Later complete text"})
        published = self.store.publish()
        self.assertTrue(published["events_current"])
        self.assertFalse(published["full_export_current"])
        self.assertEqual(path.read_bytes(), saved, "Incremental publication does not pretend to refresh the full state")
        self.assertIn("later", self.store.state()["messages"])
        refreshed = self.store.project()
        self.assertNotIn("root", refreshed)
        self.assertEqual(json.loads(path.read_bytes()), refreshed)
        self.assertEqual(refreshed["messages"]["later"]["body"], "Later complete text")
        self.assertTrue(freshness(self.store)["full_export_current"])
        self.assertEqual([json.loads(line) for line in (self.root / "events.jsonl").read_text().splitlines()], self.store.events())

    def test_explicit_export_keeps_daily_large_body_reference_and_complete_raw_payload(self):
        self.store.publish()
        events = self.store.events()
        large = next(event for event in events if event["data"].get("id") == "big")
        original = canonical(large)
        self.store.project()
        day = self.root / "posta" / ("exchange-" + large["utc"][:10] + ".md")
        summary = day.read_text()
        self.assertNotIn(large["data"]["body"], summary)
        self.assertLess(day.stat().st_size, len(large["data"]["body"].encode()))
        self.assertIn(f"events.jsonl · #{large['seq']} · SHA-256 {large['hash']}", summary)
        raw = (self.root / "events.jsonl").read_text().splitlines()[large["seq"] - 1]
        self.assertEqual(raw, original)
        self.assertEqual(json.loads(raw)["data"]["body"], large["data"]["body"])
        self.assertIn("assign-T", summary, "Acknowledgement still names its original assignment")


if __name__ == "__main__":
    unittest.main()
