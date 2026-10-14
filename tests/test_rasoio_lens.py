"""rasoio: the optional lean-code lens on a review, and the two built-in skill guides."""
import hashlib
from pathlib import Path
import re
import unittest

from test_errata import Fixture, run
from registry import Error
from squadra import bindings

ROOT = Path(__file__).resolve().parents[1]
GUIDES = [ROOT / "references/skills/errata.md", ROOT / "references/skills/rasoio.md"]
PRIVATE_PATH = re.compile(r"/Users/|/home/|~/\.")
# sha256 of lowercase private tokens: the guard must not publish what it guards.
PRIVATE_TOKENS = {
    "05b9115df05a2a467841772eccc969822d884c9e71841050fe9e0893cce7d11b",
    "473d2e4f906dd0e791e1160665112764e7b787228e50954c0d27d39e3cee1110",
    "5df06ce255fdd09d8d01500db400b47241fa34e83bf87169a17fd37f135e1296",
    "63a92b4c488119b1edf26cc5a5f473a8df570b9d39b8c749bf54218053e16d1a",
    "6455bee2d1ebbe3037e1abc2e0a1bf236136b7be47fc9285fb599c822292ce65",
    "c1493ad775659da6b01d6f0985dd3086529c7b31d5f8762ff7258a8ab511124e",
    "c1b4ed05397df5eebb4ea2e233ce45ce62e56b34d4b37d81f53059291ff741c2",
}


def leaks(text, hashes=PRIVATE_TOKENS):
    tokens = {t for t in re.findall(r"[a-z]+", text.lower()) if hashlib.sha256(t.encode()).hexdigest() in hashes}
    return PRIVATE_PATH.findall(text) + sorted(tokens)


class Lens(Fixture, unittest.TestCase):
    def review(self, **extra):
        data = {"task": "T", "stage": "code", "verdict": "accepted", "covers": [],
                "binding": bindings(self.store, "T", require_review=False)["sha256"],
                "report": self.store.artifact(self.report), **extra}
        return self.store.append("review", "R", data)

    def test_lens_refusals(self):
        self.delivered()
        for extra, code in (({"lens": "occam"}, "lens_unknown"), ({"net_lines": 3}, "net_lines_requires_lens"),
                            ({"lens": "rasoio", "net_lines": -1}, "net_lines_invalid"),
                            ({"lens": "rasoio", "net_lines": True}, "net_lines_invalid"),
                            ({"lens": "rasoio", "net_lines": "3"}, "net_lines_invalid"),
                            ({"lens": "rasoio", "net_lines": 10 ** 7}, "net_lines_invalid")):
            with self.subTest(extra=extra), self.assertRaisesRegex(Error, code):
                self.review(**extra)
        rc, _, err = run(self.root, "review", "--actor", "R", "--task", "T", "--verdict", "accepted",
                         "--report", str(self.report), "--net-lines", "3")
        self.assertEqual(rc, 1, err); self.assertIn("net_lines_requires_lens", err)

    def test_without_the_lens_the_review_is_unchanged_and_the_lens_never_changes_close(self):
        self.delivered()
        self.assertEqual(self.cli("review", "--actor", "R", "--task", "T", "--verdict", "accepted", "--report", str(self.report)), 0)
        review = [e for e in self.store.events() if e["kind"] == "review"][-1]["data"]
        self.assertEqual(set(review), {"task", "stage", "verdict", "binding", "covers", "report"})
        rc, data, err = run(self.root, "review", "--actor", "R", "--task", "T", "--verdict", "accepted",
                            "--report", str(self.report), "--lens", "rasoio", "--net-lines", "12")
        self.assertEqual((rc, data), (0, None), err)
        review = [e for e in self.store.events() if e["kind"] == "review"][-1]["data"]
        self.assertEqual((review.get("lens"), review.get("net_lines")), ("rasoio", 12))
        self.assertEqual(self.cli("check", "--actor", "C", "--task", "T"), 0)
        self.assertEqual(self.cli("close", "--actor", "C", "--task", "T"), 0)
        self.assertEqual(self.store.state()["tasks"]["T"]["status"], "done")
        rc, listing, err = run(self.root, "lesson", "--list")
        self.assertEqual((listing["rasoio_reads"], listing["net_lines_declared"]), (1, 12))

    def test_the_lens_keeps_the_changes_required_status(self):
        self.delivered()
        rc, data, err = run(self.root, "review", "--actor", "R", "--task", "T", "--verdict", "changes-required",
                            "--report", str(self.report), "--lens", "rasoio", "--net-lines", "0")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.store.state()["tasks"]["T"]["status"], "changes-required")
        self.assertIn("errata", data or {})


class Guides(unittest.TestCase):
    def test_both_guides_exist_and_skill_links_them_for_every_install(self):
        skill = (ROOT / "SKILL.md").read_text()
        self.assertEqual([g.name for g in GUIDES if not g.is_file()], [])
        for guide in GUIDES:
            with self.subTest(guide=guide.name):
                self.assertTrue(guide.is_file())
                self.assertIn("(references/skills/%s)" % guide.name, skill)
        rasoio = GUIDES[1].read_text()
        for needed in ("## The ladder", "Must it exist?", "`delete:`", "`stdlib:`", "`native:`", "`yagni:`", "`reduce:`",
                       "`comment:`", "net: -<N> lines possible.", "Already lean. Ship.", "## When not to be lean",
                       "--lens rasoio --net-lines N"):
            self.assertIn(needed, rasoio)
        errata = GUIDES[0].read_text()
        for needed in ("squadra lesson", "--ineffective", "--retire", "--any-source", "lesson.recall", "`ineffective` accuses"):
            self.assertIn(needed, errata)

    def test_the_guides_hold_no_private_paths_or_names(self):
        self.assertTrue(leaks("see /Users/someone/notes"), "positive control: a private path is found")
        planted = "plantedcontroltoken"
        self.assertEqual(leaks("a %s b" % planted, PRIVATE_TOKENS | {hashlib.sha256(planted.encode()).hexdigest()}), [planted])
        self.assertEqual([g.name for g in GUIDES if not g.is_file()], [])
        for guide in GUIDES:
            with self.subTest(guide=guide.name):
                self.assertEqual(leaks(guide.read_text()), [])

    def test_skill_says_when_the_built_in_skills_activate(self):
        skill = (ROOT / "SKILL.md").read_text()
        self.assertIn("## Built-in skills: errata and rasoio", skill)
        section = skill.split("## Built-in skills: errata and rasoio", 1)[1].split("\n## ", 1)[0]
        for needed in ("by themselves", "without asking", "`brief --task` and `start`", "lesson.recall",
                       "`squadra lesson`", "lesson --ineffective", "review --lens rasoio --net-lines N",
                       "never\n  changes a verdict"):
            self.assertIn(needed, section)

    def test_work_md_documents_every_new_kind_and_refusal(self):
        work = (ROOT / "references/work.md").read_text()
        self.assertTrue((ROOT / "scripts/errata.py").is_file())
        codes = set()
        for name in ("scripts/errata.py", "scripts/registry.py", "scripts/squadra.py"):
            codes |= set(re.findall(r'"((?:lesson|lens|net_lines)_[a-z_]+)"', (ROOT / name).read_text()))
        self.assertGreaterEqual(len(codes), 15)
        for item in sorted(codes) + ["`lesson`", "`lesson.retire`", "`lesson.recall`", "`lesson.ineffective`",
                                     "`lens`", "`net_lines`", "recalled_pairs"]:
            self.assertIn(item, work)


if __name__ == "__main__":
    unittest.main()
