"""L-1…L-4: settings declared in the manifest, the key and endpoint saved from the squadra panel, the connected note,
and MC-C1. Saved files are written as squadra's server would; W only reads them."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

import contract
import fake_mcp
import fx
from squadra_memories import connector

SAVED = "mrdk_" + "a" * 32 + "_SAVED-SECRET-b2c3"
FILED = "mrdk_" + "b" * 32 + "_FILE-SECRET-d4e5"
SPACE = "https://mcp.example.invalid/t/sentinel-space-91/mcp"
POLICY_IT = "l'approvazione segue la policy dello scope dell'abbonato, manuale per default"
POLICY_EN = "approval follows the subscriber's scope policy, manual by default"


class Stub:
    def __init__(self, answer):
        self.answer = answer

    def open(self):
        pass

    def tool(self, name, arguments, write=False):
        return self.answer(name, arguments)


class Link(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.info = fx.info()

    def setUp(self):
        self.fakes, self.folders = [], []
        self.fresh()

    def tearDown(self):
        for fake in self.fakes:
            fake.close()
        for folder in self.folders:
            fx.remove(folder)

    def fresh(self, declare=True):
        self.tmp = Path(tempfile.mkdtemp())
        self.folders.append(self.tmp)
        self.reg = fx.copy_registry(self.tmp)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.outs = 0
        if declare:
            fx.declare(self.reg, "b1", [self.item("A durable constraint."), self.item("A second durable constraint.")])
            fx.choose_drafts(self.reg, self.home)
            self.selection = (self.home / ".squadra/plugins/memoranda/actions/send.json").read_bytes()
            self.draft_panel = next((self.home / ".squadra/plugins/memoranda/panels").glob('*.json')).read_bytes()
        else:
            self.selection = None

    def item(self, sentence):
        return {"type": "constraint", "sentence": sentence, "certainty": "sure", "private_content": "no",
                "evidence": [{"event": self.info["review_seq"]}]}

    def out(self):
        self.outs += 1
        return self.tmp / f"out{self.outs}"

    def serve(self, token, **options):
        fake = fake_mcp.Fake(token, **options)
        self.fakes.append(fake)
        return fake

    def send(self, *extra, token=None, endpoint=None, out=None):
        # Replaying the original explicit user choice exercises connector eligibility even against stale UI state.
        if self.selection is not None:
            fx.request(self.reg, self.home, json.loads(self.selection)["ids"])
            (self.home / ".squadra/plugins/memoranda/panels" /
             (self.reg.event(1)["hash"] + '.json')).write_bytes(self.draft_panel)
        argv = list(extra) + (["--endpoint", endpoint] if endpoint else [])
        return fx.run_connector("send", self.reg, out or self.out(), self.home, *argv, "--timeout", "5", token=token)

    def panel(self):
        _, document = contract.check(self.home, "memoranda", self.reg.event(1)["hash"])
        return document

    def panel_text(self):
        folder = self.home / ".squadra" / "plugins" / "memoranda"
        return "\n".join(p.read_text() for p in [folder / "plugin.json", *sorted((folder / "panels").glob("*.json"))])

    def test_l1_the_manifest_declares_the_two_settings(self):
        run = fx.collect(self.reg, self.out(), home=self.home)
        self.assertEqual(run.rc, 0, run.stderr)
        manifest, _ = contract.check(self.home, "memoranda", self.reg.event(1)["hash"])
        fields = {field["id"]: field for field in manifest["settings"]}
        self.assertEqual({name: field["kind"] for name, field in fields.items()}, {"endpoint": "url", "key": "secret"})
        self.assertEqual(fields["endpoint"]["label"], {"it": "Indirizzo del tuo spazio memoranda",
                                                       "en": "Your memoranda space address"})
        self.assertEqual(fields["key"]["label"], {"it": "Chiave dell'agente", "en": "Agent key"})
        self.assertIn("https://mcp.memoranda.dev/t/<spazio>/mcp", fields["endpoint"]["help"]["it"])
        for words in ("secondo fattore", "Agenti e chiavi", "squadra", "Crea la chiave", "l'unica volta", "Salva"):
            self.assertIn(words, fields["key"]["help"]["it"])
        for words in ("second factor", "Agents and keys", "squadra", "Create the key", "only once", "Save"):
            self.assertIn(words, fields["key"]["help"]["en"])

    def test_l2_the_key_comes_from_env_then_file_then_saved(self):
        fx.write_settings(self.home, key=SAVED)
        token_file = self.tmp / "token"
        token_file.write_text(FILED + "\n")
        token_file.chmod(0o600)
        for name, token, extra, expected in (
                ("environment", fx.TOKEN, ["--token-file", str(token_file)], fx.TOKEN),
                ("token file", None, ["--token-file", str(token_file)], FILED),
                ("saved secret", None, [], SAVED)):
            with self.subTest(name):
                fake = self.serve(expected)
                done = self.send(*extra, token=token, endpoint=fake.url)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual({r["authorization"] for r in fake.requests}, {"Bearer " + expected})
                self.assertEqual(len(fake.proposes), 2)

    def test_l2_an_unsafe_saved_secret_is_refused(self):
        def symlinked_file(folder):
            target = self.tmp / "elsewhere-key"
            target.write_text(SAVED)
            target.chmod(0o600)
            (folder / "secrets" / "key").unlink()
            (folder / "secrets" / "key").symlink_to(target)

        def loose(folder):
            (folder / "secrets" / "key").chmod(0o644)

        def oversized(folder):
            (folder / "secrets" / "key").write_text(SAVED + "x" * 4097)

        def newline(folder):
            (folder / "secrets" / "key").write_text(SAVED + "\n")

        def symlinked_folder(folder):
            elsewhere = self.tmp / "elsewhere-secrets"
            shutil.move(str(folder / "secrets"), str(elsewhere))
            (folder / "secrets").symlink_to(elsewhere)

        for damage in (symlinked_file, loose, oversized, newline, symlinked_folder):
            with self.subTest(damage.__name__):
                self.fresh(declare=False)
                fake = self.serve(SAVED)
                damage(fx.write_settings(self.home, endpoint=fake.url, key=SAVED))
                done = self.send()
                self.assertEqual(done.returncode, 3)
                self.assertTrue(done.stderr.startswith("E-KEY:"), done.stderr)
                self.assertEqual(fake.requests, [])

    def test_l2_the_endpoint_comes_from_settings_and_the_flag_wins(self):
        saved, flagged = self.serve(SAVED), self.serve(SAVED)
        fx.write_settings(self.home, endpoint=saved.url, key=SAVED)
        done = self.send()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((len(saved.proposes), len(flagged.requests)), (2, 0))
        self.fresh()
        fx.write_settings(self.home, endpoint=saved.url, key=SAVED)
        done = self.send(endpoint=flagged.url)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(flagged.proposes), 2)
        self.fresh()
        nowhere = fx.Reg(self.tmp / "no-registry")
        done = fx.run_connector("send", nowhere, self.out(), self.home, token=fx.TOKEN)
        self.assertEqual(done.returncode, 3)
        self.assertTrue(done.stderr.startswith("E-ENDPOINT:"), done.stderr)

    def test_l2_send_and_status_never_write_the_saved_settings(self):
        fake = self.serve(SAVED)
        fx.write_settings(self.home, endpoint=fake.url, key=SAVED)
        before = fx.saved_files(self.home)
        out = self.out()
        self.assertEqual(self.send(out=out).returncode, 0)
        status = fx.run_connector("status", self.reg, out, self.home, token=None)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(fx.saved_files(self.home), before)

    def test_l3_the_note_says_connected_or_not_and_shows_no_value(self):
        for name, endpoint, key, connected in (("nothing saved", None, None, False),
                                               ("address only", SPACE, None, False),
                                               ("key only", None, SAVED, False),
                                               ("both saved", SPACE, SAVED, True)):
            with self.subTest(name):
                if endpoint or key:
                    fx.write_settings(self.home, endpoint=endpoint, key=key)
                run = fx.collect(self.reg, self.out(), home=self.home)
                self.assertEqual(run.rc, 0, run.stderr)
                note = self.panel()["note"]
                if connected:
                    self.assertTrue(note["it"].startswith("memoranda collegato."), note["it"])
                    self.assertTrue(note["en"].startswith("memoranda connected."), note["en"])
                else:
                    self.assertTrue(note["it"].startswith("memoranda non collegato: apri Impostazioni qui sotto."))
                    self.assertTrue(note["en"].startswith("memoranda not connected: open Settings below."))
                self.assertIn(POLICY_IT, note["it"])
                self.assertIn(POLICY_EN, note["en"])
                text = self.panel_text()
                for value in (SPACE, "sentinel-space-91", SAVED, "SAVED-SECRET"):
                    self.assertNotIn(value, text)

    def test_l3_an_automatic_scope_is_shown_as_policy_approval_before_and_after_acceptance(self):
        fake = self.serve(SAVED, auto_scopes={"squadra/fixture"})
        fx.write_settings(self.home, endpoint=fake.url, key=SAVED)
        out = self.out()
        first = self.send(out=out)
        self.assertTrue(first.stderr.startswith("E-SCOPE-POLICY:"), first.stderr)
        for stage in ("before acceptance", "after acceptance"):
            if stage == "after acceptance":
                accepted = self.send("--accept-scope-policy", out=out)
                self.assertEqual(accepted.returncode, 0, accepted.stderr)
            with self.subTest(stage):
                document = self.panel()
                note = document["note"]
                self.assertTrue(note["en"].startswith("memoranda connected."), note["en"])
                self.assertIn(POLICY_EN, note["en"])
                self.assertIn(POLICY_IT, note["it"])
                self.assertIn("This scope approves proposals automatically", note["en"])
                self.assertIn("Questo scope approva automaticamente le proposte", note["it"])
                if stage == "before acceptance":
                    self.assertIn("further sends need --accept-scope-policy", note["en"])
                else:
                    self.assertIn("continued sending was accepted explicitly", note["en"])
                for promise in ("wait for", "waits for", "awaiting"):
                    self.assertNotIn(promise, note["en"].lower())
                for promise in ("attendono", "attende", "in attesa"):
                    self.assertNotIn(promise, note["it"].lower())
                approved = [item for item in document["items"] if item["status"] == "approved"]
                self.assertEqual(len(approved), 1 if stage == "before acceptance" else 2)
                for item in approved:
                    self.assertTrue(any("subscriber's policy" in m["value"] for m in item["meta"]))

    def test_l4_one_refresh_updates_every_alias(self):
        """core-A-review's MC-C1 oracle (alias_oracles.py), ported: two declarations adopt one waiting proposal."""
        proposal, memory = "prp_" + "1" * 26, "mem_" + "2" * 26
        candidates = [{"type": "constraint", "sentence": f"Alias {n}.", "certainty": "sure", "who": None,
                       "owner_channel": None, "evidence": [{"event": 3, "hash": "0" * 64, "kind": "review", "task": "T1"}],
                       "task": "T1", "item": n, "declared_by": {"seat": "b1", "identity": "id-b1", "seq": 40},
                       "objections": []} for n in (0, 1)]
        answers = iter([{"proposal_id": proposal, "status": "pending"},
                        {"error": {"code": "duplicate_waiting",
                                   "message": "an identical proposal of yours is already waiting: " + proposal}}])
        with connector.Ledger(self.tmp, "c" * 64) as ledger:
            ledger.bind({"endpoint": "http://127.0.0.1:1/t/acme/mcp", "tenant": "acme", "scope": "squadra/fixture",
                         "key_id_sha256": "0" * 64})
            connector.send(ledger, Stub(lambda *a: next(answers)), candidates, "squadra/fixture", "c" * 64, {},
                           sleep=lambda seconds: None)
            self.assertEqual([e["proposal_id"] for e in ledger.data["items"].values()], [proposal, proposal])
            decided = {"decisions": [{"proposal_id": proposal, "outcome": "approved", "decided_at": connector.now_iso(),
                                      "memory_id": memory}], "truncated": False}
            connector.status(ledger, Stub(lambda *a: decided), "squadra/fixture")
            self.assertEqual([e["status"] for e in ledger.data["items"].values()], ["approved", "approved"])


if __name__ == "__main__":
    unittest.main()
