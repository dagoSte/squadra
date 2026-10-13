"""K-1…K-4 and the plan-read oracles: propose-only sends against a local fake of memoranda's MCP door."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest

import contract
import fake_mcp
import fx

SENTENCE = "A slow registry write is not a failed write; wait for the receipt."


class Connector(unittest.TestCase):
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

    def fresh(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.folders.append(self.tmp)
        self.reg = fx.copy_registry(self.tmp)
        self.home, self.out = self.tmp / "home", self.tmp / "out"
        self.home.mkdir()
        self.out.mkdir()

    def serve(self, **options):
        fake = fake_mcp.Fake(fx.TOKEN, **options)
        self.fakes.append(fake)
        return fake

    def item(self, sentence=SENTENCE, kind="constraint", **extra):
        return {"type": kind, "sentence": sentence, "certainty": "sure", "private_content": "no",
                "evidence": [{"event": self.info["review_seq"]}], **extra}

    def run_cli(self, command, fake, *extra, token=fx.TOKEN):
        if command == "send":
            fx.choose_drafts(self.reg, self.home)
        return fx.run_connector(command, self.reg, self.out, self.home, *extra, endpoint=fake.url, token=token)

    def ledger(self):
        files = list(self.out.glob("ledger-*.json"))
        return json.loads(files[0].read_text()) if files else None

    def registry_id(self):
        return self.reg.event(1)["hash"]

    def panel_items(self):
        _, panel = contract.check(self.home, "memoranda", self.registry_id())
        return {item["id"]: item for item in panel["items"]}

    def assert_no_leak(self, *runs):
        text = "\n".join([r.stdout + r.stderr for r in runs] + [fx.files_text(self.out, self.home)])
        self.assertNotIn(fx.SECRET, text)
        self.assertNotIn(fx.TOKEN, text)

    def test_k1_only_candidates_are_proposed_with_the_mapped_arguments(self):
        long = "x" * 1501
        seq = fx.declare(self.reg, "b1", [
            self.item(evidence=[{"event": self.info["review_seq"]}, {"artifact": self.info["report_sha"]}]),
            self.item("Contested sentence."),
            {"type": "trap", "certainty": "sure", "private_content": "yes", "evidence": [{"event": self.info["review_seq"]}]},
            self.item(long)])
        fx.reply(self.reg, "r1", seq, 1, "object", "Not durable.")
        fake = self.serve()
        done = self.run_cli("send", fake)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(fake.proposes), 1)
        review = self.reg.event(self.info["review_seq"])
        self.assertEqual(fake.proposes[0]["arguments"], {
            "scope": "squadra/fixture", "content": SENTENCE, "confidence": 0.8, "tags": ["squadra", "constraint"],
            "evidence": [{"kind": "conversation", "conv_id": "squadra-" + self.registry_id()[:16],
                          "turn": self.info["review_seq"], "excerpt_hash": "sha256:" + review["hash"]},
                         {"kind": "document", "path": "squadra/report.md", "page": 1,
                          "content_hash": "sha256:" + self.info["report_sha"]}]})
        ledger = self.ledger()
        self.assertEqual(ledger["binding"], {"endpoint": fake.url, "tenant": "acme", "scope": "squadra/fixture",
                                             "key_id_sha256": hashlib.sha256(b"0123456789abcdef" * 2).hexdigest()})
        self.assertEqual(sorted(ledger["items"]), [f"{seq}:0", f"{seq}:3"])
        self.assertEqual(ledger["items"][f"{seq}:0"]["status"], "sent")
        self.assertEqual((ledger["items"][f"{seq}:3"]["status"], ledger["items"][f"{seq}:3"]["error_code"]),
                         ("error", "E-TOO-LONG"))
        panel = self.panel_items()
        self.assertEqual({name: panel[name]["status"] for name in panel},
                         {f"{seq}:0": "sent", f"{seq}:1": "draft", f"{seq}:2": "withheld", f"{seq}:3": "error"})
        self.assertEqual({request["authorization"] for request in fake.requests}, {"Bearer " + fx.TOKEN})
        self.assert_no_leak(done)
        for endpoint in ("http://example.invalid/t/acme/mcp", fake.url + "?x=1", "https://user:pw@example.invalid/t/a/mcp"):
            with self.subTest(endpoint=endpoint):
                before = len(fake.requests)
                refused = fx.run_connector("send", self.reg, self.out, self.home, endpoint=endpoint)
                self.assertEqual(refused.returncode, 3)
                self.assertTrue(refused.stderr.startswith("E-ENDPOINT:"), refused.stderr)
                self.assertEqual(len(fake.requests), before)

    def test_k2_a_missing_or_unsafe_key_fails_before_any_read(self):
        fake = self.serve()
        nowhere = fx.Reg(self.tmp / "no-registry")
        done = fx.run_connector("send", nowhere, self.out, self.home, endpoint=fake.url, token=None)
        self.assertEqual(done.returncode, 3)
        self.assertTrue(done.stderr.startswith("E-KEY:"), done.stderr)
        token_file = self.tmp / "token"
        token_file.write_text(fx.TOKEN + "\n")
        for name, mode in (("group-readable", 0o640), ("world-readable", 0o644)):
            with self.subTest(name):
                token_file.chmod(mode)
                done = fx.run_connector("send", self.reg, self.out, self.home, "--token-file", str(token_file),
                                        endpoint=fake.url, token=None)
                self.assertTrue(done.stderr.startswith("E-KEY:"), done.stderr)
        link = self.tmp / "token-link"
        token_file.chmod(0o600)
        link.symlink_to(token_file)
        done = fx.run_connector("send", self.reg, self.out, self.home, "--token-file", str(link), endpoint=fake.url,
                                token=None)
        self.assertTrue(done.stderr.startswith("E-KEY:"), done.stderr)
        self.assertEqual(fake.requests, [])
        self.assertEqual(list(self.out.glob("ledger-*")), [])
        fx.declare(self.reg, "b1", [self.item()])
        fx.choose_drafts(self.reg, self.home)
        done = fx.run_connector("send", self.reg, self.out, self.home, "--token-file", str(token_file),
                                endpoint=fake.url, token=None)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assert_no_leak(done)

    def test_mc_p2_a_reflected_secret_never_leaves(self):
        for step, status in (("reflect-code", "error"), ("reflect-message", "uncertain"), ("reflect-id", "uncertain"),
                             ("reflect-malformed", "uncertain")):
            with self.subTest(step):
                self.fresh()
                seq = fx.declare(self.reg, "b1", [self.item()])
                fake = self.serve()
                fake.script = [step]
                done = self.run_cli("send", fake)
                entry = self.ledger()["items"][f"{seq}:0"]
                self.assertEqual(entry["status"], status)
                if step == "reflect-code":
                    self.assertEqual(entry["error_code"], "E-REMOTE")
                self.assertNotIn("proposal_id", entry)
                self.panel_items()
                self.assert_no_leak(done)

    def test_mc_p1_a_lost_response_is_never_resent(self):
        for follow in ("sensitive", "forget", "expiry"):
            with self.subTest(follow):
                self.fresh()
                seq = fx.declare(self.reg, "b1", [self.item()])
                fake = self.serve()
                fake.script = ["lost"]
                done = self.run_cli("send", fake)
                self.assertEqual(done.returncode, 3)
                self.assertTrue(done.stderr.startswith("E-UNCERTAIN:"), done.stderr)
                (proposal,) = fake.proposals
                if follow == "sensitive":
                    fake.decide(proposal, "rejected", reason_code="sensitive")
                elif follow == "forget":
                    fake.decide(proposal, "rejected", forgotten=True)
                else:
                    fake.decide(proposal, "rejected",
                                when=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=40))
                again = self.run_cli("send", fake)
                status = self.run_cli("status", fake)
                self.assertEqual((again.returncode, status.returncode), (0, 0), again.stderr + status.stderr)
                self.assertEqual(len(fake.proposes), 1)
                self.assertEqual(self.ledger()["items"][f"{seq}:0"]["status"], "uncertain")
                item = self.panel_items()[f"{seq}:0"]
                self.assertEqual(item["status"], "error")
                self.assertTrue(any("resolve explicitly" in m["value"] for m in item["meta"]))

    def test_mc_p1_a_crash_after_acceptance_is_not_resent(self):
        seq = fx.declare(self.reg, "b1", [self.item()])
        fake = self.serve()
        fake.script = ["hold"]
        fx.choose_drafts(self.reg, self.home)
        argv = fx.connector_argv("send", self.reg, self.out, endpoint=fake.url)
        child = subprocess.Popen(argv, cwd=fx.ROOT, env=fx.connector_env(self.home), stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        try:
            if not fake.held.wait(60):
                raise fx.FixtureError("the client never reached the fake")
            child.send_signal(signal.SIGKILL)
            child.wait(30)
        finally:
            if child.poll() is None:
                child.kill()
            fake.release.set()
        again = self.run_cli("send", fake)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(len(fake.proposes), 1)
        entry = self.ledger()["items"][f"{seq}:0"]
        self.assertEqual((entry["status"], entry["error_code"]), ("uncertain", "E-INTERRUPTED"))
        self.assertEqual(self.panel_items()[f"{seq}:0"]["status"], "error")

    def test_mc_p1_a_concurrent_send_is_refused(self):
        fx.declare(self.reg, "b1", [self.item(), self.item("A second durable constraint.")])
        fake = self.serve()
        fake.script = ["hold"]
        fx.choose_drafts(self.reg, self.home)
        argv = fx.connector_argv("send", self.reg, self.out, "--timeout", "60", endpoint=fake.url)
        first = subprocess.Popen(argv, cwd=fx.ROOT, env=fx.connector_env(self.home), stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        try:
            if not fake.held.wait(60):
                raise fx.FixtureError("the first send never reached the fake")
            second = self.run_cli("send", fake)
            proposes_while_held = len(fake.proposes)
        finally:
            fake.release.set()
            out, err = first.communicate(timeout=120)
        self.assertEqual(second.returncode, 3)
        self.assertTrue(second.stderr.startswith("E-BUSY:"), second.stderr)
        self.assertEqual(proposes_while_held, 1)
        self.assertEqual(first.returncode, 0, err)
        self.assertEqual(len(fake.proposes), 2)

    def test_mc_p1_a_binding_change_is_refused(self):
        fx.declare(self.reg, "b1", [self.item()])
        fake = self.serve()
        self.assertEqual(self.run_cli("send", fake).returncode, 0)
        other_scope = self.run_cli("send", fake, "--scope", "squadra/other")
        other_key = self.run_cli("send", fake, token="mrdk_" + "f" * 32 + "_another-secret")
        for done in (other_scope, other_key):
            self.assertEqual(done.returncode, 3)
            self.assertTrue(done.stderr.startswith("E-BINDING:"), done.stderr)
        self.assertEqual(len(fake.proposes), 1)

    def test_k3_retry_after_is_honoured(self):
        seq = fx.declare(self.reg, "b1", [self.item()])
        fake = self.serve()
        fake.script = ["429:3"]
        done = self.run_cli("send", fake)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(fake.proposes), 2)
        self.assertGreaterEqual(fake.proposes[1]["at"] - fake.proposes[0]["at"], 2.9)
        self.assertEqual(self.ledger()["items"][f"{seq}:0"]["status"], "sent")

    def test_k3_refusals_stop_and_uncertain_busy_is_kept(self):
        for step, code, kept in (("401", "E-AUTH", None), ("queue_full", "E-QUEUE-FULL", None),
                                 ("503", "E-UNCERTAIN", "uncertain")):
            with self.subTest(step):
                self.fresh()
                seq = fx.declare(self.reg, "b1", [self.item()])
                fake = self.serve()
                fake.script = [step]
                done = self.run_cli("send", fake)
                self.assertEqual(done.returncode, 3)
                self.assertTrue(done.stderr.startswith(code + ":"), done.stderr)
                self.assertEqual((self.ledger()["items"].get(f"{seq}:0") or {}).get("status"), kept)
                self.assertEqual(self.panel_items()[f"{seq}:0"]["status"], "draft" if kept is None else "error")

    def test_mc_p3_an_automatic_scope_stops_until_accepted(self):
        seq = fx.declare(self.reg, "b1", [self.item(), self.item("A second durable constraint.")])
        fake = self.serve(auto_scopes={"squadra/fixture"})
        first = self.run_cli("send", fake)
        self.assertEqual(first.returncode, 3)
        self.assertTrue(first.stderr.startswith("E-SCOPE-POLICY:"), first.stderr)
        self.assertEqual(len(fake.proposes), 1)
        ledger = self.ledger()
        self.assertEqual(ledger["scope_policy"], "automatic")
        entry = ledger["items"][f"{seq}:0"]
        self.assertEqual((entry["status"], entry["by_policy"]), ("approved", True))
        item = self.panel_items()[f"{seq}:0"]
        self.assertEqual(item["status"], "approved")
        self.assertTrue(any("subscriber's policy" in m["value"] for m in item["meta"]))
        again = self.run_cli("send", fake)
        self.assertTrue(again.stderr.startswith("E-SCOPE-POLICY:"), again.stderr)
        self.assertEqual(len(fake.proposes), 1)
        accepted = self.run_cli("send", fake, "--accept-scope-policy")
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(len(fake.proposes), 2)
        self.assertEqual(self.ledger()["accepted_scope_policy"]["by"], "explicit")

    def test_k4_status_follows_the_cursor_and_maps_outcomes(self):
        seq = fx.declare(self.reg, "b1", [self.item("First."), self.item("Second."), self.item("Third.")])
        fake = self.serve(page_size=2)
        self.assertEqual(self.run_cli("send", fake).returncode, 0)
        ids = [self.ledger()["items"][f"{seq}:{i}"]["proposal_id"] for i in range(3)]
        memory, merged = "mem_" + "7" * 26, "mem_" + "8" * 26
        fake.decide(ids[0], "approved", memory_id=memory)
        fake.decide(ids[1], "merged", merged_into=merged)
        fake.decide(ids[2], "rejected", reason_code="sensitive")
        done = self.run_cli("status", fake)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(fake.reads), 2)
        self.assertIn("after", fake.reads[1])
        items = self.ledger()["items"]
        self.assertEqual([items[f"{seq}:{i}"]["status"] for i in range(3)], ["approved", "merged", "rejected"])
        self.assertEqual((items[f"{seq}:0"]["memory_id"], items[f"{seq}:1"]["merged_into"],
                          items[f"{seq}:2"]["reason_code"]), (memory, merged, "sensitive"))
        panel = self.panel_items()
        self.assertEqual([panel[f"{seq}:{i}"]["status"] for i in range(3)], ["approved", "approved", "rejected"])
        self.assertTrue(any("merged into" in m["value"] for m in panel[f"{seq}:1"]["meta"]))
        path = next(self.out.glob("ledger-*.json"))
        ledger = json.loads(path.read_text())
        ledger["decided_cursor"] = {"decided_since": "2020-01-01T00:00:00Z"}
        path.write_text(json.dumps(ledger))
        for name in ids:
            fake.proposals[name]["status"] = "pending"
        for name in ledger["items"]:
            ledger["items"][name]["status"] = "sent"
        path.write_text(json.dumps(ledger))
        clamped = self.run_cli("status", fake)
        self.assertEqual(clamped.returncode, 0, clamped.stderr)
        since = datetime.datetime.fromisoformat(fake.reads[-1]["decided_since"].replace("Z", "+00:00"))
        floor = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=29, minutes=5)
        self.assertGreaterEqual(since, floor)

    def test_resolve_is_explicit_and_resend_adopts_the_waiting_id(self):
        seq = fx.declare(self.reg, "b1", [self.item()])
        fake = self.serve()
        fake.script = ["lost"]
        self.run_cli("send", fake)
        (original,) = fake.proposals
        item = f"{seq}:0"
        bad = fx.run_connector("resolve", self.reg, self.out, self.home, "--item", item, "--proposal", "prp_123")
        self.assertTrue(bad.stderr.startswith("E-ID:"), bad.stderr)
        resend = fx.run_connector("resolve", self.reg, self.out, self.home, "--item", item, "--resend")
        self.assertEqual(resend.returncode, 0, resend.stderr)
        self.assertEqual(self.ledger()["items"][item]["resolution"]["by"], "explicit")
        again = self.run_cli("send", fake)
        self.assertEqual(again.returncode, 0, again.stderr)
        entry = self.ledger()["items"][item]
        self.assertEqual((entry["status"], entry["proposal_id"], entry["adopted"]),
                         ("sent", original, "duplicate_waiting"))
        self.assertEqual((len(fake.proposes), len(fake.proposals)), (2, 1))
        late = fx.run_connector("resolve", self.reg, self.out, self.home, "--item", item, "--abandon")
        self.assertTrue(late.stderr.startswith("E-NOT-UNCERTAIN:"), late.stderr)


if __name__ == "__main__":
    unittest.main()
