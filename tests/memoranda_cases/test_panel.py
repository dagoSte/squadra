"""K-5: the plugin files follow the SQ-PLUGINS contract; withheld items show only their reason code."""
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

import contract
import fx
from squadra_memories import connector, panel
from squadra_memories.source import Refusal

SENTINEL = "SENTINEL-WITHHELD-5c1d"


class Panel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.info = fx.info()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.reg = fx.copy_registry(self.tmp)
        self.home = self.tmp / "home"
        self.home.mkdir()
        fx.install(self.home)

    def tearDown(self):
        fx.remove(self.tmp)

    def test_k5_collect_writes_the_panel_by_the_contract(self):
        evidence = [{"event": self.info["review_seq"]}]
        seq = fx.declare(self.reg, "b1", [
            {"type": "constraint", "sentence": "Public constraint.", "certainty": "sure", "private_content": "no",
             "evidence": evidence},
            {"type": "trap", "certainty": "sure", "private_content": "yes", "evidence": evidence},
            {"type": "trap", "sentence": SENTINEL, "certainty": "likely", "evidence": evidence},
            {"type": "decision", "sentence": "Pointer to nowhere.", "certainty": "sure", "private_content": "no",
             "evidence": [{"event": 999999}]}])
        run = fx.collect(self.reg, self.tmp / "out", home=self.home)
        self.assertEqual(run.rc, 0, run.stderr)
        _, document = contract.check(self.home, "memoranda", self.reg.event(1)["hash"])
        items = {item["id"]: item for item in document["items"]}
        self.assertEqual({name: (items[name]["status"], items[name]["text"]) for name in items}, {
            f"{seq}:0": ("draft", "Public constraint."), f"{seq}:1": ("withheld", "private"),
            f"{seq}:2": ("withheld", "unanswered"), f"{seq}:3": ("error", "evidence-not-found")})
        self.assertNotIn(SENTINEL, fx.files_text(self.home))
        for folder in (self.home / ".squadra", self.home / ".squadra" / "plugins",
                       self.home / ".squadra" / "plugins" / "memoranda",
                       self.home / ".squadra" / "plugins" / "memoranda" / "panels"):
            self.assertEqual(stat.S_IMODE(os.lstat(folder).st_mode), 0o700, folder)

    def test_k5_a_symlink_below_squadra_is_refused(self):
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        (self.home / ".squadra" / "plugins").rename(self.home / "saved-plugins")
        (self.home / ".squadra" / "plugins").symlink_to(elsewhere)
        run = fx.collect(self.reg, self.tmp / "out", home=self.home, install_bundle=False)
        self.assertEqual(run.rc, 3)
        self.assertTrue(run.stderr.startswith("E-OUTPUT:"), run.stderr)
        self.assertEqual(list(elsewhere.iterdir()), [])
        self.assertEqual(list((self.tmp / "out").iterdir()), [])

    def test_k5_whole_file_limits_hold(self):
        candidates = [{"type": "constraint", "sentence": "y" * 4000, "certainty": "sure", "who": None,
                       "owner_channel": None, "evidence": [{"event": n, "hash": "0" * 64, "kind": "review", "task": "T"}],
                       "task": "T", "item": 0, "declared_by": {"seat": "b1", "identity": "id-b1", "seq": n},
                       "objections": []} for n in range(1, 701)]
        candidates[-1]["sentence"] = "z" * 5000
        result = {"candidates": candidates, "contested": [], "withheld": [], "evidence_not_found": [],
                  "malformed": [], "unauthorized": [], "dropped": []}
        body = panel.build(result, None, updated="2026-10-11T00:00:00Z")
        self.assertLessEqual(len(body), panel.PANEL_LIMIT)
        document = json.loads(body)
        self.assertLessEqual(len(document["items"]), 500)
        self.assertGreater(len(document["items"]), 0)
        self.assertIn("older items are not shown", document["note"]["en"])
        self.assertTrue(all(len(item["text"].encode()) <= 4096 for item in document["items"]))
        self.assertEqual(document["items"][-1]["id"], f"{701 - len(document['items'])}:0")
        self.assertEqual((document["items"][0]["id"], document["items"][0]["text"]), ("700:0", panel.TOO_LONG))

    def test_the_ledger_never_exceeds_its_limit(self):
        with connector.Ledger(self.tmp, "a" * 64) as ledger:
            ledger.bind({"endpoint": "https://example.invalid/t/a/mcp", "tenant": "a", "scope": "squadra/x",
                         "key_id_sha256": "0" * 64})
            before = ledger.path.read_bytes()
            ledger.data["items"]["1:0"] = {"status": "sent", "padding": "x" * (connector.LEDGER_LIMIT + 1)}
            with self.assertRaises(Refusal) as refused:
                ledger.save()
            self.assertEqual(refused.exception.code, "E-LEDGER-SIZE")
            self.assertEqual(ledger.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
