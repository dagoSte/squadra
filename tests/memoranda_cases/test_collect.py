"""T1–T5b: only declared answers become candidates, with verified evidence and declared privacy."""
import tempfile
import unittest
from pathlib import Path

import fx

SENTENCE = "Non usare l'indice parziale: è lento — prefer «full scan» with `LIMIT`."
DECISION = "Owner chose X over Y because Y loses the receipt."
SENTINEL = "SENTINEL-PRIVATE-7f3a91"


class Collect(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.info = fx.info()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.reg = fx.copy_registry(self.tmp)
        self.count = 0

    def tearDown(self):
        fx.remove(self.tmp)

    def run_tool(self, reg=None):
        self.count += 1
        return fx.collect(reg or self.reg, self.tmp / f"out{self.count}")

    def event(self, seq):
        cited = self.reg.event(seq)
        return {"event": seq, "hash": cited["hash"], "kind": cited["kind"]}

    def item(self, sentence=SENTENCE, kind="constraint", evidence=None, **extra):
        return {"type": kind, "sentence": sentence, "certainty": "sure", "private_content": "no",
                "evidence": evidence or [{"event": self.info["review_seq"]}], **extra}

    def test_t1_declared_items_are_collected_with_snapshot_evidence(self):
        seq = fx.declare(self.reg, "b1", [
            self.item(evidence=[{"event": self.info["review_seq"]}, {"artifact": self.info["report_sha"]}]),
            self.item(DECISION, "decision", [{"event": self.info["d1_seq"]}], who="owner", certainty="likely")])
        run = self.run_tool()
        self.assertEqual(run.rc, 0, run.stderr)
        doc = run.doc
        self.assertEqual((doc["format"], doc["format_version"], doc["is_memoranda_request"]),
                         ("squadra-memory-candidates", 1, False))
        self.assertEqual(len(doc["candidates"]), 2)
        first, second = doc["candidates"]
        self.assertEqual(first["evidence"], [{**self.event(self.info["review_seq"]), "task": "T1"},
                                             {"artifact": self.info["report_sha"], "bytes": self.info["report_bytes"]}])
        self.assertEqual(first["declared_by"], {"seat": "b1", "identity": "id-b1", "seq": seq})
        self.assertEqual((first["type"], first["item"], first["task"], first["objections"]), ("constraint", 0, "T1", []))
        self.assertEqual(second["evidence"], [{**self.event(self.info["d1_seq"]), "task": ["T1"]}])
        self.assertEqual((second["owner_channel"], second["who"], second["certainty"]), ("typed-here", "owner", "likely"))
        for name, text in run.texts.items():
            self.assertIn(SENTENCE, text, name)
            self.assertIn(DECISION, text, name)
        self.assertIn("Candidati di memoria (privato)", run.texts["candidates.it.md"])
        self.assertIn("Vincolo — sicuro", run.texts["candidates.it.md"])
        self.assertIn("Memory candidates (private)", run.texts["candidates.en.md"])
        self.assertIn("Constraint — sure", run.texts["candidates.en.md"])
        self.assertEqual(sorted(p.name for p in run.folder.iterdir()),
                         ["candidates.en.md", "candidates.it.md", "candidates.json"])
        self.assertEqual(oct(run.folder.stat().st_mode & 0o777), "0o700")
        self.assertEqual({oct(p.stat().st_mode & 0o777) for p in run.folder.iterdir()}, {"0o600"})

    def test_t2_nothing_is_collected_without_a_declaration(self):
        run = self.run_tool()
        self.assertEqual(run.rc, 0, run.stderr)
        self.assertEqual({name: run.doc[name] for name in fx_lists()}, {name: [] for name in fx_lists()})
        fx.declare(self.reg, "b1", [], answer="no")
        run = self.run_tool()
        self.assertEqual(run.rc, 0, run.stderr)
        self.assertEqual({name: run.doc[name] for name in fx_lists()}, {name: [] for name in fx_lists()})
        self.assertEqual((run.doc["counts"]["declarations"], run.doc["counts"]["answered_no"]), (1, 1))

    def test_t3_unverifiable_evidence_is_listed_without_the_sentence(self):
        never = "0" * 64
        seq = fx.declare(self.reg, "b1", [self.item(SENTINEL, evidence=[{"event": 999999}]),
                                          self.item(SENTINEL, "trap", [{"artifact": never}])])
        run = self.run_tool()
        self.assertEqual(run.rc, 0, run.stderr)
        self.assertEqual(run.doc["evidence_not_found"], [{"seq": seq, "item": 0}, {"seq": seq, "item": 1}])
        self.assertEqual(run.doc["candidates"], [])
        self.assertNotIn(SENTINEL, run.everything())

    def test_t3_an_altered_stored_artifact_refuses_the_run(self):
        fx.declare(self.reg, "b1", [self.item(evidence=[{"artifact": self.info["report_sha"]}])])
        stored = self.reg.root / "artifacts" / self.info["report_sha"]
        stored.chmod(0o600)
        stored.write_bytes(fx.REPORT.replace(b"Accepted", b"Rejected"))
        run = self.run_tool()
        self.assertEqual(run.rc, 3)
        self.assertTrue(run.stderr.startswith("E-ARTIFACT:"), run.stderr)
        self.assertEqual(list(run.out_root.iterdir()), [])

    def test_t4_objection_then_coordinator_drop_or_keep(self):
        seq = fx.declare(self.reg, "b1", [self.item()])
        objection = fx.reply(self.reg, "r1", seq, 0, "object", "Too narrow to keep.")
        run = self.run_tool()
        self.assertEqual(run.rc, 0, run.stderr)
        self.assertEqual((run.doc["candidates"], len(run.doc["contested"])), ([], 1))
        self.assertEqual(run.doc["contested"][0]["objections"], [{"seq": objection, "by": "r1", "why": "Too narrow to keep."}])
        kept = fx.clone(self.reg, self.tmp)
        fx.reply(self.reg, "coord", seq, 0, "drop", "Not durable.")
        run = self.run_tool()
        self.assertEqual(run.rc, 0, run.stderr)
        self.assertEqual((run.doc["dropped"], run.doc["contested"], run.doc["candidates"]),
                         ([{"seq": seq, "item": 0}], [], []))
        self.assertNotIn(SENTENCE, run.texts["candidates.json"])
        fx.reply(kept, "coord", seq, 0, "keep", "Durable.")
        run = self.run_tool(kept)
        self.assertEqual(run.rc, 0, run.stderr)
        self.assertEqual((run.doc["contested"], len(run.doc["candidates"])), ([], 1))
        self.assertEqual([o["seq"] for o in run.doc["candidates"][0]["objections"]], [objection])

    def test_t4b_unauthorized_replies_are_listed_and_ignored(self):
        seq = fx.declare(self.reg, "b1", [self.item()])
        alias = fx.reply(self.reg, "b1-alias", seq, 0, "object", "Alias objection.")
        run = self.run_tool()
        self.assertEqual(run.rc, 0, run.stderr)
        self.assertEqual(run.doc["unauthorized"], [{"seq": alias, "verdict": "object", "reason": "same-identity"}])
        self.assertEqual([c["objections"] for c in run.doc["candidates"]], [[]])
        fx.reply(self.reg, "r1", seq, 0, "object", "Real objection.")
        builder = fx.reply(self.reg, "b2", seq, 0, "keep", "A builder cannot settle.")
        run = self.run_tool()
        self.assertEqual(run.rc, 0, run.stderr)
        self.assertEqual(run.doc["unauthorized"], [{"seq": alias, "verdict": "object", "reason": "same-identity"},
                                                   {"seq": builder, "verdict": "keep", "reason": "not-coordinator"}])
        self.assertEqual((len(run.doc["contested"]), run.doc["candidates"]), (1, []))

    def test_a_declarer_cannot_settle_its_own_contested_item(self):
        seq = fx.declare(self.reg, "coord", [self.item(DECISION, "decision", [{"event": self.info["d1_seq"]}], who="owner")])
        fx.reply(self.reg, "r1", seq, 0, "object", "Owner did not say this.")
        own = fx.reply(self.reg, "coord", seq, 0, "keep", "I keep my own item.")
        run = self.run_tool()
        self.assertEqual(run.rc, 0, run.stderr)
        self.assertEqual(run.doc["unauthorized"], [{"seq": own, "verdict": "keep", "reason": "self-settlement"}])
        self.assertEqual((len(run.doc["contested"]), run.doc["candidates"]), (1, []))
        fx.reply(self.reg, "coord2", seq, 0, "keep", "Another coordinator keeps it.")
        run = self.run_tool()
        self.assertEqual(run.rc, 0, run.stderr)
        self.assertEqual((len(run.doc["candidates"]), run.doc["contested"]), (1, []))

    def test_t5_private_and_unanswered_items_are_withheld(self):
        evidence = [{"event": self.info["review_seq"]}]
        private = {"type": "trap", "certainty": "sure", "private_content": "yes", "evidence": evidence}
        unanswered = {"type": "constraint", "sentence": SENTINEL, "certainty": "sure", "evidence": evidence}
        seq = fx.declare(self.reg, "b1", [private, unanswered])
        run = self.run_tool()
        self.assertEqual(run.rc, 0, run.stderr)
        cited = [{**self.event(self.info["review_seq"]), "task": "T1"}]
        self.assertEqual(run.doc["withheld"], [{"seq": seq, "item": 0, "reason": "private", "evidence": cited},
                                               {"seq": seq, "item": 1, "reason": "unanswered", "evidence": cited}])
        self.assertEqual(run.doc["candidates"], [])
        self.assertNotIn(SENTINEL, run.everything())

    def test_t5b_a_private_item_with_a_sentence_is_malformed(self):
        seq = fx.declare(self.reg, "b1", [{"type": "trap", "sentence": SENTINEL, "certainty": "sure",
                                           "private_content": "yes", "evidence": [{"event": self.info["review_seq"]}]}])
        run = self.run_tool()
        self.assertEqual(run.rc, 0, run.stderr)
        self.assertEqual(run.doc["malformed"], [{"seq": seq, "item": 0}])
        self.assertEqual((run.doc["candidates"], run.doc["withheld"]), ([], []))
        self.assertNotIn(SENTINEL, run.everything())


    def test_an_objection_reason_follows_the_privacy_answer(self):
        seq = fx.declare(self.reg, "b1", [self.item()])
        unanswered = fx.reply(self.reg, "r1", seq, 0, "object", SENTINEL, private=None)
        both = fx.reply(self.reg, "r1", seq, 0, "object", SENTINEL + "-B", private="yes")
        private = fx.reply(self.reg, "r1", seq, 0, "object", private="yes")
        public = fx.reply(self.reg, "r1", seq, 0, "object", "Public reason.")
        run = self.run_tool()
        self.assertEqual(run.rc, 0, run.stderr)
        self.assertEqual(run.doc["malformed"], [{"seq": both, "item": None}])
        self.assertEqual(len(run.doc["contested"]), 1)
        self.assertEqual(run.doc["contested"][0]["objections"],
                         [{"seq": unanswered, "by": "r1", "why": None}, {"seq": private, "by": "r1", "why": None},
                          {"seq": public, "by": "r1", "why": "Public reason."}])
        self.assertNotIn(SENTINEL, run.everything())
        for name in ("candidates.it.md", "candidates.en.md"):
            self.assertIn("Public reason.", run.texts[name])

    def test_who_renders_literally_in_both_previews(self):
        who = "**owner** <b>x</b> [link](https://example.invalid) `code` # heading"
        fx.declare(self.reg, "b1", [self.item(DECISION, "decision", [{"event": self.info["d1_seq"]}], who=who)])
        run = self.run_tool()
        self.assertEqual(run.rc, 0, run.stderr)
        self.assertEqual(run.doc["candidates"][0]["who"] if run.doc["candidates"] else None, who)
        for name in ("candidates.it.md", "candidates.en.md"):
            text = run.texts[name]
            self.assertEqual(text.count(who), 1, name)
            self.assertIn("\n```\n" + who + "\n```\n", text, name)


def fx_lists():
    return ("candidates", "contested", "withheld", "evidence_not_found", "malformed", "unauthorized", "dropped")


if __name__ == "__main__":
    unittest.main()
