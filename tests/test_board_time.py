"""Elapsed board time comes from replayed events, never an estimate or status age."""
import copy
import datetime as dt
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from registry import Error, apply, board, initial, table


class BoardTime(unittest.TestCase):
    def setUp(self):
        self.state = initial()
        self.events = []
        self.record("init", "2026-10-01T00:00:00Z", name="Clock fixture", lang="en",
                    limits={"plan": 3, "code": 2, "build": 3})
        self.record("agent", "2026-10-01T00:00:00Z", id="A", identity="builder", role="builder", client="manual")
        self.record("agent", "2026-10-01T00:00:00Z", id="R", identity="reader", role="reviewer", client="manual")

    def record(self, kind, utc, **data):
        event = {"kind": kind, "utc": utc, "data": data, "actor": "A", "id": str(self.state["event_count"])}
        self.events.append(event)
        apply(self.state, event)

    def assign(self, name, utc):
        self.record("task", utc, id=name, owner="A", reviewer="R", title=name)

    def test_three_groups_both_languages_first_start_and_assignment_fallback(self):
        self.assign("Closed", "2026-10-01T10:00:00Z")
        self.record("start", "2026-10-02T10:00:00Z", task="Closed")
        self.record("close", "2026-10-03T12:00:00Z", task="Closed")
        self.assign("Active", "2026-10-03T06:00:00Z")
        self.record("start", "2026-10-03T10:00:00Z", task="Active")
        self.record("start", "2026-10-03T11:30:00Z", task="Active")
        self.record("deliver", "2026-10-03T11:59:00Z", task="Active", snapshot={})
        self.assign("Planning", "2026-10-03T11:50:30Z")
        self.record("plan", "2026-10-03T11:59:00Z", task="Planning", artifact={"sha256": "synthetic"})
        self.assign("Waiting", "2026-10-01T00:00:00Z")
        before = copy.deepcopy(self.state)
        for lang, prefix, days, closed, delivered, planning, waiting in [
            ("en", "about", "days", "closed", "delivered: code review", "plan review", "awaiting assignment acknowledgement"),
            ("it", "circa", "giorni", "chiuso", "consegnato: lettura del codice", "piano in lettura", "presa in carico da confermare"),
        ]:
            with self.subTest(lang=lang):
                text = board(self.state, lang, events=self.events, read_at="2026-10-03T12:00:00Z")
                for row in [f"| Closed | {closed} | {prefix} 2 {days} |",
                            f"| Active | {delivered} | {prefix} 2 h |",
                            f"| Planning | {planning} | {prefix} 10 min |",
                            f"| Waiting | {waiting} | — |"]:
                    self.assertIn(row, text)
        self.assertEqual(self.state, before)
        later = board(self.state, "en", events=self.events, read_at="2026-10-04T12:00:00Z")
        self.assertIn("| Closed | closed | about 2 days |", later)
        self.assertIn("| Active | delivered: code review | about 1 day |", later)
        self.assertIn("| Waiting | awaiting assignment acknowledgement | — |", later)

    def test_units_rounding_and_future_clock_are_bounded(self):
        self.assign("Timing", "2026-10-01T00:00:00Z")
        self.record("start", "2026-10-01T00:00:00Z", task="Timing")
        for seconds, en, it in [(-60, "0 min", "0 min"), (29, "0 min", "0 min"),
                                (30, "1 min", "1 min"), (3599, "60 min", "60 min"),
                                (3600, "1 h", "1 h"), (5400, "2 h", "2 h"),
                                (86400, "1 day", "1 giorno"), (216000, "3 days", "3 giorni")]:
            at = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc) + dt.timedelta(seconds=seconds)
            for lang, prefix, expected in [("en", "about", en), ("it", "circa", it)]:
                with self.subTest(seconds=seconds, lang=lang):
                    self.assertIn(f"| {prefix} {expected} |", board(self.state, lang, events=self.events, read_at=at))

    def test_table_forwards_read_clock_and_default_board_reads_it_once(self):
        self.assign("Timing", "2026-10-01T00:00:00Z")
        self.record("start", "2026-10-01T01:00:00Z", task="Timing")
        at = "2026-10-01T03:00:00Z"
        with patch("registry.now", return_value=at) as clock:
            expected = board(self.state, events=self.events)
            clock.assert_called_once_with()
        with patch("registry.now", side_effect=AssertionError("explicit clock must be used")):
            self.assertTrue(table(self.state, read_at=at, events=self.events).startswith(expected + "\n\n"))
        self.assertIn("| Timing | being built | about 2 h |", expected)
        with self.assertRaisesRegex(Error, "Timezone-aware clock required"):
            board(self.state, events=self.events, read_at=dt.datetime(2026, 10, 1))
