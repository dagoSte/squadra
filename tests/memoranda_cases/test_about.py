"""M-1…M-3: the plugin page's «about» block (contract-plugin-about.md), its claims, and W's checker of it."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest

import contract
import fx
from squadra_memories import VERSION, panel

LABELS = {"it": ["Bozza", "Inviato", "Approvato", "Rifiutato", "Trattenuto", "Errore"],
          "en": ["Draft", "Sent", "Approved", "Rejected", "Withheld", "Error"]}
PROVIDERS = {"claude", "codex", "anthropic", "openai", "github", "gitlab", "google", "cursor", "apple", "mac", "macos",
             "microsoft", "vscode", "hetzner"}
JARGON = {"scope", "ledger", "mcp", "endpoint", "token", "registry", "snapshot", "propose-only"}


def texts(about, language):
    found = [about["tagline"][language]]
    for section in about["sections"]:
        found.append(section["title"][language])
        found.append(section.get("text", {}).get(language, ""))
        found += [item[language] for item in section.get("items", [])]
    return found


def words(text):
    token, result = [], []
    for char in text.lower() + " ":
        if char.isalnum() or char == "-":
            token.append(char)
        elif token:
            result.append("".join(token))
            token = []
    return result


class About(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()

    def tearDown(self):
        fx.remove(self.tmp)

    def test_m1_the_about_block_follows_the_contract_in_both_languages(self):
        reg = fx.copy_registry(self.tmp)
        run = fx.collect(reg, self.tmp / "out", home=self.home)
        self.assertEqual(run.rc, 0, run.stderr)
        manifest, _ = contract.check(self.home, "memoranda", reg.event(1)["hash"])
        about = manifest["about"]
        self.assertEqual((about["publisher"], about["version"]), ("memoranda", VERSION))
        self.assertEqual([s["id"] for s in about["sections"]], ["what", "does", "how", "privacy", "statuses", "price"])
        for section in about["sections"]:
            for field in ("title", "text"):
                if field in section:
                    self.assertTrue(section[field]["it"].strip() and section[field]["en"].strip(), section["id"])
            for item in section.get("items", []):
                self.assertTrue(item["it"].strip() and item["en"].strip(), section["id"])
        how = next(s for s in about["sections"] if s["id"] == "how")
        for language in ("it", "en"):
            self.assertEqual([item[language].split(" ", 1)[0] for item in how["items"]], ["1.", "2.", "3.", "4.", "5."])
        statuses = next(s for s in about["sections"] if s["id"] == "statuses")
        for language in ("it", "en"):
            self.assertEqual([item[language].split(":", 1)[0] for item in statuses["items"]], LABELS[language])

    def test_m2_the_claims_are_propose_only_and_follow_the_area_rule(self):
        about = panel.MANIFEST["about"]
        sections = {s["id"]: s for s in about["sections"]}
        self.assertIn("non approva mai", sections["does"]["text"]["it"])
        self.assertIn("never approves", sections["does"]["text"]["en"])
        self.assertIn("una persona", sections["what"]["text"]["it"])
        self.assertIn("regola", sections["what"]["text"]["it"])
        self.assertIn("a person", sections["what"]["text"]["en"])
        self.assertIn("rule", sections["what"]["text"]["en"])
        for language, promises in (("it", ("attendono", "attende", "in attesa", "già inviat", "sono state inviate")),
                                   ("en", ("wait for", "waits for", "awaiting", "already sent", "were sent"))):
            joined = " ".join(texts(about, language)).lower()
            for promise in promises:
                self.assertNotIn(promise, joined, language)
            tokens = set(words(joined))
            self.assertEqual(tokens & PROVIDERS, set(), language)
            self.assertEqual(tokens & JARGON, set(), language)

    def test_m2_a_path_in_a_public_sentence_leaves_unchanged_and_the_copy_says_so(self):
        """ABOUT-C1 (core-A-review's source-backed example): the sentence is sent as written, so the copy must not
        promise that no path leaves; it says agents keep paths out of the sentence and that no file is attached."""
        import fake_mcp
        reg = fx.copy_registry(self.tmp)
        sentence = "Use the fixtures in /example/project/data/fixtures.json for the slow tests."
        fx.declare(reg, "b1", [{"type": "constraint", "sentence": sentence, "certainty": "sure",
                                "private_content": "no", "evidence": [{"event": fx.info()["review_seq"]}]}])
        fake = fake_mcp.Fake(fx.TOKEN)
        try:
            fx.choose_drafts(reg, self.home)
            done = fx.run_connector("send", reg, self.tmp / "out", self.home, endpoint=fake.url)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual([p["arguments"]["content"] for p in fake.proposes], [sentence])
            self.assertEqual({e["kind"] for e in fake.proposes[0]["arguments"]["evidence"]}, {"conversation"})
        finally:
            fake.close()
        privacy = next(s for s in panel.MANIFEST["about"]["sections"] if s["id"] == "privacy")
        bullet = privacy["items"][2]
        self.assertIn("devono tenerne fuori dati privati e percorsi", bullet["it"])
        self.assertIn("nessun file viene mai allegato", bullet["it"])
        self.assertIn("must keep private data and paths out of it", bullet["en"])
        self.assertIn("no file is ever attached", bullet["en"])
        for language, promise in (("it", "nessun percorso"), ("en", "no path")):
            self.assertNotIn(promise, " ".join(texts(panel.MANIFEST["about"], language)).lower())

    def test_m3_the_checker_refuses_every_malformed_about_block(self):
        contract.validate_manifest(panel.MANIFEST, "memoranda")

        def change(edit):
            manifest = copy.deepcopy(panel.MANIFEST)
            edit(manifest["about"])
            return manifest

        cases = {
            "unknown key": lambda a: a.update(link="https://example.invalid"),
            "missing tagline": lambda a: a.pop("tagline"),
            "control character in publisher": lambda a: a.update(publisher="memoranda\x07dev"),
            "long publisher": lambda a: a.update(publisher="p" * 65),
            "bad version": lambda a: a.update(version="-1.0"),
            "long tagline": lambda a: a["tagline"].update(it="t" * 201),
            "one-language tagline": lambda a: a["tagline"].pop("en"),
            "nine sections": lambda a: a["sections"].extend(copy.deepcopy(a["sections"][:4])),
            "no sections": lambda a: a.update(sections=[]),
            "duplicate id": lambda a: a["sections"][1].update(id="what"),
            "bad id": lambda a: a["sections"][0].update(id="What"),
            "long title": lambda a: a["sections"][0]["title"].update(en="x" * 81),
            "long text": lambda a: a["sections"][0]["text"].update(it="x" * 2001),
            "neither text nor items": lambda a: a["sections"][0].pop("text"),
            "thirteen items": lambda a: a["sections"][2].update(items=a["sections"][2]["items"] * 3),
            "long item": lambda a: a["sections"][2]["items"][0].update(en="x" * 301),
            "unknown section key": lambda a: a["sections"][0].update(icon="star"),
        }
        for name, edit in cases.items():
            with self.subTest(name):
                with self.assertRaises(contract.Broken):
                    contract.validate_manifest(change(edit), "memoranda")
        big = self.tmp / "plugin.json"
        for size, refused in ((20 * 1024, False), (64 * 1024 + 1, True)):
            with self.subTest(bytes=size):
                body = json.dumps({"pad": "x" * (size - 11)})
                big.write_text(body)
                os.chmod(big, 0o600)
                self.assertEqual(len(body.encode()), size)
                if refused:
                    with self.assertRaises(contract.Broken):
                        contract._load(big, contract.MANIFEST_LIMIT)
                else:
                    contract._load(big, contract.MANIFEST_LIMIT)


if __name__ == "__main__":
    unittest.main()
