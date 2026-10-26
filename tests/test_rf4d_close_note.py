"""A close with a note is written in journal format 3 (M-10).

Only 3.11 folds a close note into the task, so a note in an older envelope would let a 3.10.4
window rebuild a different state from the same journal; format 3 makes it refuse instead.
"""
import sqlite3
import unittest

from test_review_pool_gate_refusals import Fixture  # first: its support import puts scripts/ on the path
from squadra import deliver


class CloseNoteFormat(Fixture, unittest.TestCase):
    def formats(self):
        db = sqlite3.connect(self.root / "registry.sqlite3")
        try:
            return [(row[0], row[1]) for row in db.execute("SELECT kind,hash_format FROM events ORDER BY seq")]
        finally:
            db.close()

    def reviewed(self, name):
        self.full_task(name, reviewer="R")
        self.proved(name)
        handoff = deliver(self.store, "A", name)["handoff"]
        self.store.append("ack", "R", {"message": handoff})
        rc, err = self.run_cli("review", "--actor", "R", "--task", name, "--stage", "code", "--verdict", "accepted",
                               "--report", str(self.report))
        self.assertEqual(rc, 0, err)

    def test_close_note_writes_format_3_on_a_legacy_format_registry(self):
        self.reviewed("N")
        self.assertNotIn(3, [f for _, f in self.formats()], "SETUP: no format-3 event yet")
        rc, err = self.run_cli("close", "--actor", "C", "--task", "N", "--note", "Closed with a recorded note")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.formats()[-1], ("close", 3))
        self.assertEqual(self.store.state()["tasks"]["N"]["close_note"], "Closed with a recorded note")
        self.assertEqual(self.store.audit()["cache"], "matched")

    def test_close_without_a_note_keeps_the_legacy_format(self):
        self.reviewed("N")
        rc, err = self.run_cli("close", "--actor", "C", "--task", "N")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.formats()[-1], ("close", 2))


if __name__ == "__main__":
    unittest.main()
