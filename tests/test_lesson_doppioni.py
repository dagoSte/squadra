"""M1: a new lesson whose trigger meets an active one is named at once, with ineffective, retire or --despite."""
import unittest

from test_errata import Fixture, run  # first: it puts scripts/ on the path
from registry import Error


class Twins(Fixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.delivered()
        self.assertEqual(self.cli("review", "--actor", "R", "--task", "T", "--verdict", "changes-required",
                                  "--report", str(self.report)), 0)
        self.red = next(e["seq"] for e in self.store.events() if e["kind"] == "review")

    def record(self, *trigger, extra=()):
        return run(self.root, "lesson", "--actor", "R", "--task", "T", "--source-event", str(self.red),
                   "--text", "Search every mirror after a bump", *trigger, *extra)

    def test_an_overlapping_trigger_is_named_with_the_ways_forward(self):
        self.lesson("L0001", paths=["scripts/*.py"], text="Keep registry mirrors equal")
        rc, data, err = self.record("--path", "scripts/registry.py")
        self.assertEqual(rc, 0, err)
        self.assertEqual([o["id"] for o in data["overlaps"]], ["L0001"])
        for part in ("lesson_overlaps", "L0001", "Keep registry mirrors equal", "--ineffective", "--retire",
                     "--despite L0001", "--retire " + data["lesson"]):
            self.assertIn(part, err)
        self.assertNotIn("despite", self.store.state()["lessons"][data["lesson"]])

    def test_the_brief_start_score_runs_both_ways(self):
        self.lesson("L0001", paths=["scripts/registry.py"]); self.lesson("L0002", words=["mirror"])
        self.lesson("L0003", paths=["docs/*.md"]); self.lesson("L0004", words=["registry"])
        rc, data, err = self.record("--path", "scripts/*.py", "--path", "plugins/x/registry.py", "--word", "mirror")
        self.assertEqual(rc, 0, err)
        self.assertEqual([o["id"] for o in data["overlaps"]], ["L0001", "L0002", "L0004"])

    def test_a_refused_lesson_announces_nothing(self):
        self.lesson("L0001", words=["mirror"])
        rc, _, err = run(self.root, "lesson", "--actor", "R", "--task", "T", "--source-event", str(self.red),
                         "--text", " ", "--word", "mirror")
        self.assertEqual(rc, 1); self.assertIn("lesson_text_required", err); self.assertNotIn("lesson_overlaps", err)

    def test_retired_and_unrelated_lessons_say_nothing(self):
        self.lesson("L0001", words=["mirror"]); self.lesson("L0002", paths=["docs/*.md"])
        self.store.append("lesson.retire", "C", {"lesson": "L0001", "reason": "Replaced"})
        rc, data, err = self.record("--word", "mirror")
        self.assertEqual((rc, data["overlaps"], err.count("lesson_overlaps")), (0, [], 0), err)

    def test_ineffective_lessons_still_count(self):
        self.lesson("L0001", words=["mirror"])
        recall = self.seq(self.store.append("lesson.recall", "C", {"task": "T", "at": "brief", "lessons": ["L0001"]}))
        self.store.append("lesson.ineffective", "R", {"lesson": "L0001", "recall": recall, "reason": "Came back"})
        rc, data, err = self.record("--word", "mirror")
        self.assertEqual([(o["id"], o["status"]) for o in data["overlaps"]], [("L0001", "ineffective")])

    def test_despite_records_the_conscious_twin(self):
        self.lesson("L0001", words=["mirror"]); self.lesson("L0002", paths=["*.py"])
        rc, data, err = self.record("--word", "mirror", "--path", "scripts/registry.py",
                                    extra=("--despite", "L0002", "--despite", "L0001", "--reason", "Different failure"))
        self.assertEqual(rc, 0, err); self.assertNotIn("lesson_overlaps", err)
        self.assertEqual([o["id"] for o in data["overlaps"]], ["L0001", "L0002"])
        self.assertEqual(self.store.state()["lessons"][data["lesson"]]["despite"],
                         {"lessons": ["L0001", "L0002"], "reason": "Different failure"})

    def test_a_wrong_despite_writes_nothing(self):
        self.lesson("L0001", words=["mirror"]); self.lesson("L0002", paths=["*.py"])
        cases = (("lesson_despite_mismatch", ("--despite", "L0001", "--reason", "Only one named")),
                 ("lesson_despite_mismatch", ("--despite", "L0001", "--despite", "L0002", "--despite", "L0009",
                                              "--reason", "One too many")),
                 ("lesson_despite_invalid", ("--despite", "L0001", "--despite", "L0002")))
        for code, extra in cases:
            with self.subTest(extra=extra):
                before = self.last_seq()
                rc, _, err = self.record("--word", "mirror", "--path", "a.py", extra=extra)
                self.assertEqual(rc, 1, err); self.assertIn(code, err)
                self.assertNotIn("lesson", self.kinds_after(before))  # only the refusal diagnostic
        rc, _, err = self.record("--word", "unrelated", extra=("--despite", "L0001", "--reason", "Nothing overlaps"))
        self.assertEqual(rc, 1); self.assertIn("lesson_despite_mismatch: --despite must name exactly nothing", err)

    def test_the_despite_field_is_validated_and_history_reads_the_same(self):
        self.lesson("L0001", words=["mirror"])
        base = {"id": "L0002", "text": "Twin", "trigger": {"words": ["mirror"]},
                "source": {"task": "T", "event": 1, "eligible": True}}
        for despite in ({"lessons": [], "reason": "x"}, {"lessons": ["L0404"], "reason": "x"},
                        {"lessons": ["L0001", "L0001"], "reason": "x"}, {"lessons": ["L0001"]},
                        {"lessons": ["L0001"], "reason": "a\x07"}, ["L0001"]):
            with self.subTest(despite=despite), self.assertRaisesRegex(Error, "lesson_despite_invalid"):
                self.store.append("lesson", "R", {**base, "despite": despite})
        self.store.append("lesson", "R", base)  # an old client's twin without despite stays valid
        lesson = self.store.state()["lessons"]["L0002"]
        self.assertEqual((lesson["trigger"], "despite" in lesson), ({"words": ["mirror"]}, False))


if __name__ == "__main__":
    unittest.main()
