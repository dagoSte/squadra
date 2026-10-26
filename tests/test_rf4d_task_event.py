"""A task event carries only the fields a task declaration has (M-8), and from journal format 3 on
it declares frozen delivery (L-19): README «New tasks use frozen delivery»."""
import sqlite3
import unittest

from test_review_pool_gate_refusals import Fixture  # first: its support import puts scripts/ on the path
from registry import Error, now
from light_route import POLICY, contract


class TaskEventFields(Fixture, unittest.TestCase):
    def declaration(self, name, **fields):
        data = {"id": name, "title": "Synthetic " + name, "owner": "A", "repo": str(self.repo),
                "base": self.store.state()["tasks"]["T"]["base"], "scope": self.store.artifact(self.scope),
                "proof": None, "expected": None, "depends": [], "full_gate": False, "reviewer": None,
                "requirements": []}
        data.update(fields)
        return data

    def formats(self):
        db = sqlite3.connect(self.root / "registry.sqlite3")
        try:
            return [row[0] for row in db.execute("SELECT hash_format FROM events ORDER BY seq")]
        finally:
            db.close()

    def test_reducer_owned_and_unknown_fields_are_refused(self):
        derived = {"status": "done", "authors": ["R"], "coordinator": "R", "reviews": [], "proofs": [],
                   "rounds": {"plan": 0, "code": 0}, "status_utc": now(), "candidate": {}, "delivery": {},
                   "plan": {}, "claim": {}, "gate_read": {"gate": "G", "reviewer": "R", "utc": now()},
                   "gate_deferrals": [], "superseded_by": "T", "close_note": "x", "reassignments": [],
                   "route_history": [], "fix_history": [], "fix_result": {}, "brief": {}, "notes": "free text"}
        for key, value in derived.items():
            with self.subTest(field=key), self.assertRaisesRegex(Error, "task_field_unknown: " + key):
                self.store.append("task", "C", self.declaration("X-" + key.replace("_", "-"), **{key: value}))

    def test_a_preset_gate_read_cannot_hide_a_missing_read(self):
        data = self.declaration("L9", proof=["python3", "-c", "pass"], route="light", light_policy=dict(POLICY),
                                read_at_gate=True, requirements=[{"id": "R1", "text": "Keep it"}])
        data["contract"] = contract(data)
        forged = {**data, "gate_read": {"gate": "NO-SUCH-GATE", "reviewer": "nobody", "utc": now()}}
        with self.assertRaisesRegex(Error, "task_field_unknown: gate_read"):
            self.store.append("task", "C", forged)
        self.assertNotIn("L9", self.store.state()["tasks"])

    def test_after_format_3_a_task_declares_frozen_delivery(self):
        self.store.append("task", "C", self.declaration("OLD"))
        self.assertNotIn(3, self.formats(), "SETUP: a legacy-format registry")
        rc, err = self.run_cli("task", "--actor", "C", "--id", "NEW", "--title", "Frozen", "--owner", "A",
                               "--repo", str(self.repo), "--scope-file", str(self.scope))
        self.assertEqual(rc, 0, err)
        self.assertIn(3, self.formats(), "SETUP: a CLI task writes format 3")
        with self.assertRaisesRegex(Error, "delivery_policy_required"):
            self.store.append("task", "C", self.declaration("LATE"))
        with self.assertRaisesRegex(Error, "delivery_policy_required"):
            self.store.append("task", "C", self.declaration("ODD", delivery_policy="frozen-v2"))
        self.store.append("task", "C", self.declaration("FROZEN", delivery_policy="frozen-v1"))
        self.assertNotIn("LATE", self.store.state()["tasks"])
        self.assertEqual(self.store.state()["tasks"]["FROZEN"]["delivery_policy"], "frozen-v1")

    def test_the_cli_declarations_are_accepted(self):
        req = self.base / "req.json"
        req.write_text('[{"id": "R1", "text": "Keep it"}]')
        for name, extra in (("F1", ["--reviewer", "R"]), ("F2", ["--reviewer", "R", "--reviewer", "R2"]),
                            ("F3", ["--route", "light", "--read-at-gate", "--requirements-file", str(req),
                                    "--proof", "python3", "-c", "pass"]),
                            ("F4", ["--release-gate", "--reviewer", "R", "--full-gate"])):
            rc, err = self.run_cli("task", "--actor", "C", "--id", name, "--title", "Declared " + name, "--owner", "A",
                                   "--repo", str(self.repo), "--scope-file", str(self.scope), *extra)
            with self.subTest(task=name):
                self.assertEqual(rc, 0, err)
        rc, err = self.run_cli("task", "--actor", "C", "--id", "F5", "--title", "Replacement", "--owner", "B",
                               "--repo", str(self.repo), "--scope-file", str(self.scope), "--supersedes", "F1",
                               "--reviewer", "R", "--same-client-reason", "Synthetic reason")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.store.state()["tasks"]["F1"]["superseded_by"], "F5")


if __name__ == "__main__":
    unittest.main()
