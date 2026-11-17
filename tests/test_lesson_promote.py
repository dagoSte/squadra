"""M5: a lesson recalled in two tasks and never ineffective moves into the repository, and comes back from there."""
import json
import unittest

from test_errata import Fixture, run  # first: it puts scripts/ on the path
from registry import Error


class Promote(Fixture, unittest.TestCase):
    TEXT = "Pin alpha before the bump"

    def setUp(self):
        super().setUp()
        self.file = self.repo / "lessons" / "promoted.json"
        self.lesson("L0001", words=["alpha"], text=self.TEXT)
        self.lesson("L0002", words=["beta"], text="Check beta twice")
        for name in ("S1", "S2"):
            self.scoped(name, "The alpha and beta paths.\n")
            rc, data, err = run(self.root, "brief", "--actor", "C", "--task", name, "--file", str(self.brief_file()))
            self.assertEqual(sorted(l["id"] for l in data["lessons"]), ["L0001", "L0002"], err)

    def promote(self, name="L0001", *extra, reason=("--reason", "Recalled in two tasks")):
        return run(self.root, "lesson", "--actor", "C", "--promote", name, "--repo", str(self.repo), *reason, *extra)

    def entries(self):
        return {e["id"]: e for e in json.loads(self.file.read_text())["lessons"]}

    def test_promotion_writes_stable_entry_with_verifiable_provenance(self):
        rc, data, err = self.promote()
        self.assertEqual(rc, 0, err)
        event = self.store.events()[-1]
        self.assertEqual((event["kind"], event["data"]),
                         ("lesson.promote", {"lesson": "L0001", "path": str(self.file), "reason": "Recalled in two tasks"}))
        lesson = self.store.state()["lessons"]["L0001"]
        self.assertEqual(self.entries()["L0001"], {
            "id": "L0001", "text": "Pin alpha before the bump", "trigger": {"words": ["alpha"]},
            "provenance": {"registry": "Synthetic team", "registry_id": self.store.events()[0]["hash"],
                           "task": "T", "lesson_event": lesson["seq"],
                           "promote_event": event["seq"], "promote_hash": event["hash"], "utc": event["utc"]}})
        self.assertEqual(data["event"], event["seq"])
        self.assertNotIn(str(self.base), self.file.read_text())  # no private path travels with the repository
        self.assertEqual(self.promote("L0002")[0], 0)
        body = self.file.read_bytes()
        self.assertEqual(body, (json.dumps(json.loads(body), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
        self.assertEqual(list(self.entries()), ["L0001", "L0002"])

    def test_promoting_again_changes_nothing(self):
        self.assertEqual(self.promote()[0], 0)
        body, before = self.file.read_bytes(), self.last_seq()
        rc, data, err = self.promote()
        self.assertEqual((rc, data.get("unchanged")), (0, True), err)
        self.assertEqual((self.file.read_bytes(), self.last_seq()), (body, before))

    def test_refusals_write_neither_file_nor_event(self):
        self.scoped("S3", "Only gamma here.\n")
        self.lesson("L0003", words=["gamma"])
        run(self.root, "brief", "--actor", "C", "--task", "S3", "--file", str(self.brief_file()))
        self.lesson("L0004", words=["alpha"])
        rc, data, err = run(self.root, "brief", "--actor", "C", "--task", "S1", "--file", str(self.brief_file()))
        self.store.append("lesson.ineffective", "R", {"lesson": "L0004", "recall": data["recall"], "reason": "Came back"})
        self.store.append("lesson.retire", "C", {"lesson": "L0002", "reason": "Folded into L0001"})
        cases = (("lesson_promote_needs_recall_in_two_tasks", self.promote, ("L0003",)),
                 ("lesson_promote_ineffective", self.promote, ("L0004",)),
                 ("lesson_promote_retired", self.promote, ("L0002",)),
                 ("lesson_unknown", self.promote, ("L0404",)),
                 ("lesson_reason_required", lambda: self.promote(reason=()), ()),
                 ("lesson_absorbed_not_retired", self.promote, ("L0001", "--absorbs", "L0003")),
                 ("lesson_absorbs_invalid", self.promote, ("L0001", "--absorbs", "L0001")),
                 ("lesson_promote_repo_required",
                  lambda: run(self.root, "lesson", "--actor", "C", "--promote", "L0001", "--reason", "x"), ()),
                 ("lesson_actor_required",
                  lambda: run(self.root, "lesson", "--promote", "L0001", "--repo", str(self.repo), "--reason", "x"), ()))
        for code, call, args in cases:
            with self.subTest(code=code):
                before = self.last_seq()
                rc, _, err = call(*args)
                self.assertEqual(rc, 1, err); self.assertIn(code, err)
                self.assertNotIn("lesson.promote", self.kinds_after(before))
                self.assertFalse(self.file.exists())

    def test_a_malformed_file_is_refused_and_left_alone(self):
        self.file.parent.mkdir()
        for body in ("{", "[]", '{"version": 2, "lessons": []}', '{"version": 1, "lessons": [{"id": "L0009"}]}',
                     '{"version": 1, "lessons": [{"id": "L1", "text": "a", "trigger": {"words": ["ab"]}},'
                     ' {"id": "L1", "text": "b", "trigger": {"words": ["ab"]}}]}'):
            with self.subTest(body=body):
                self.file.write_text(body)
                rc, _, err = self.promote()
                self.assertEqual(rc, 1); self.assertIn("lesson_promoted_file_invalid", err)
                self.assertEqual(self.file.read_text(), body)

    def test_an_id_promoted_by_another_registry_is_never_overwritten(self):
        self.file.parent.mkdir()
        foreign = {"id": "L0001", "text": "Another squad's rule", "trigger": {"words": ["alpha"]},
                   "provenance": {"registry": "Other team"}}
        self.file.write_text(json.dumps({"version": 1, "lessons": [foreign]}))
        body = self.file.read_bytes()
        rc, _, err = self.promote()
        self.assertEqual(rc, 1); self.assertIn("lesson_id_taken", err)
        self.assertEqual(self.file.read_bytes(), body)
        self.assertEqual(self.promote("L0002")[0], 0)
        self.assertEqual(self.entries()["L0001"], foreign)

    def other_registry(self, text):
        """A registry created apart, with the same name, whose L0001 was recalled in two tasks."""
        other = Promote("test_promoting_again_changes_nothing")
        other.TEXT = text
        other.setUp(); self.addCleanup(other.doCleanups)
        return other

    def test_a_namesake_registry_never_takes_over_an_entry(self):
        self.assertEqual(self.promote()[0], 0)
        body = self.file.read_bytes()
        for text in (self.TEXT, "A different registry owns this rule"):
            with self.subTest(text=text):
                other = self.other_registry(text)
                self.assertEqual(other.store.state()["workspace"]["name"], "Synthetic team")
                before = other.last_seq()
                rc, data, err = run(other.root, "lesson", "--actor", "C", "--promote", "L0001",
                                    "--repo", str(self.repo), "--reason", "Two recalls elsewhere")
                self.assertEqual(rc, 1, data); self.assertIn("lesson_id_taken", err)
                self.assertEqual(self.file.read_bytes(), body)
                self.assertNotIn("lesson.promote", other.kinds_after(before))

    def test_unchanged_needs_the_promote_event_in_this_registry(self):
        self.assertEqual(self.promote()[0], 0)
        entries = self.entries()
        entries["L0001"]["provenance"]["promote_hash"] = "0" * 64
        self.file.write_text(json.dumps({"version": 1, "lessons": list(entries.values())}))
        before = self.last_seq()
        rc, data, err = self.promote()
        self.assertEqual((rc, data.get("unchanged")), (0, None), err)
        self.assertEqual(self.kinds_after(before), ["lesson.promote"])
        self.assertEqual(self.entries()["L0001"]["provenance"]["promote_hash"], self.store.events()[-1]["hash"])

    def test_a_linked_lessons_folder_or_file_is_refused_before_any_event(self):
        outside = self.base / "outside"; outside.mkdir()
        sentinel = outside / "promoted.json"
        sentinel.write_text('{"version": 1, "lessons": []}\n')
        body = sentinel.read_bytes()
        lessons = self.repo / "lessons"
        for name, make in (("folder", lambda: lessons.symlink_to(outside, target_is_directory=True)),
                           ("file", lambda: (lessons.mkdir(), self.file.symlink_to(sentinel)))):
            with self.subTest(link=name):
                make()
                before = self.last_seq()
                rc, _, err = self.promote()
                self.assertEqual(rc, 1, err); self.assertIn("lesson_promoted_path_unsafe", err)
                self.assertEqual(sentinel.read_bytes(), body)
                self.assertNotIn("lesson.promote", self.kinds_after(before))
                if lessons.is_symlink():
                    lessons.unlink()
                else:
                    self.file.unlink(); lessons.rmdir()

    def test_absorbing_needs_the_old_lesson_retired_and_removes_it(self):
        self.assertEqual(self.promote("L0002")[0], 0)
        rc, _, err = self.promote("L0001", "--absorbs", "L0002")
        self.assertEqual(rc, 1); self.assertIn("lesson_absorbed_not_retired", err)
        self.store.append("lesson.retire", "C", {"lesson": "L0002", "reason": "Folded into L0001"})
        rc, data, err = self.promote("L0001", "--absorbs", "L0002")
        self.assertEqual((rc, data["absorbed"]), (0, ["L0002"]), err)
        self.assertEqual(list(self.entries()), ["L0001"])
        self.assertEqual(self.store.events()[-1]["data"]["absorbs"], ["L0002"])
        rc, data, err = self.promote("L0001", "--absorbs", "L0002")  # already gone: nothing to do
        self.assertEqual((rc, data.get("unchanged")), (0, True), err)

    def test_the_event_is_validated_by_errata(self):
        good = {"lesson": "L0001", "path": str(self.file), "reason": "Two tasks"}
        for code, change in (("lesson_promote_path_invalid", {"path": "lessons/promoted.json"}),
                             ("lesson_promote_path_invalid", {"path": str(self.repo / "promoted.json")}),
                             ("lesson_fields_invalid", {"extra": 1}),
                             ("lesson_absorbs_invalid", {"absorbs": []}),
                             ("lesson_absorbs_invalid", {"absorbs": "L0002"}),
                             ("lesson_reason_required", {"reason": ""})):
            with self.subTest(change=change), self.assertRaisesRegex(Error, code):
                self.store.append("lesson.promote", "C", {**good, **change})
        self.store.append("lesson.promote", "C", good)
        self.assertEqual(self.store.state()["lessons"]["L0001"]["text"], "Pin alpha before the bump")


class Recall(Fixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.file = self.repo / "lessons" / "promoted.json"
        self.scoped("S", "Touch the alpha module in scripts/registry.py.\n")

    def write(self, *entries):
        self.file.parent.mkdir(exist_ok=True)
        self.file.write_text(json.dumps({"version": 1, "lessons": list(entries)}))

    def brief(self):
        return run(self.root, "brief", "--actor", "C", "--task", "S", "--file", str(self.brief_file()))

    def test_matching_promoted_lessons_come_back_marked(self):
        self.write({"id": "L0007", "text": "From another squad", "trigger": {"paths": ["scripts/*.py"]}},
                   {"id": "L0008", "text": "Does not match", "trigger": {"words": ["omega"]}})
        before = self.last_seq()
        rc, data, err = self.brief()
        self.assertEqual(rc, 0, err)
        self.assertEqual(data["lessons"], [{"id": "L0007", "text": "From another squad",
                                            "trigger": {"paths": ["scripts/*.py"]}, "promoted": True, "score": 3}])
        self.assertEqual(self.kinds_after(before), ["brief"])  # a repository lesson is not a registry recall
        self.ack("S")
        rc, data, err = run(self.root, "start", "--actor", "A", "--task", "S")
        self.assertEqual([(l["id"], l["promoted"]) for l in data["lessons"]], [("L0007", True)])

    def test_a_lesson_already_shown_by_the_registry_is_not_repeated(self):
        self.lesson("L0001", words=["alpha"], text="Pin alpha")
        self.write({"id": "L0001", "text": "Pin alpha", "trigger": {"words": ["alpha"]}},
                   {"id": "L0002", "text": "Other squad's alpha", "trigger": {"words": ["alpha"]}})
        rc, data, err = self.brief()
        self.assertEqual([(l["id"], l.get("promoted", False)) for l in data["lessons"]],
                         [("L0001", False), ("L0002", True)], err)
        self.assertEqual(self.store.state()["lesson_recalls"][-1]["lessons"], ["L0001"])
        self.store.append("lesson.retire", "C", {"lesson": "L0001", "reason": "Promoted, the repository keeps it"})
        rc, data, err = self.brief()
        self.assertEqual([(l["id"], l["promoted"]) for l in data["lessons"]], [("L0001", True), ("L0002", True)])

    def test_five_from_the_registry_and_five_promoted(self):
        for n in range(1, 7):
            self.lesson("L%04d" % n, words=["alpha"], text="Registry rule %d" % n)
        self.write(*({"id": "L%04d" % n, "text": "Promoted rule %d" % n, "trigger": {"words": ["alpha"]}}
                     for n in range(11, 17)))
        rc, data, err = self.brief()
        self.assertEqual(rc, 0, err)
        self.assertEqual([l.get("promoted", False) for l in data["lessons"]], [False] * 5 + [True] * 5)
        self.assertEqual(len(self.store.state()["lesson_recalls"][-1]["lessons"]), 5)

    def test_a_linked_lessons_folder_is_not_read(self):
        outside = self.base / "outside"; outside.mkdir()
        (outside / "promoted.json").write_text(json.dumps({"version": 1, "lessons": [
            {"id": "L0009", "text": "Planted outside", "trigger": {"words": ["alpha"]}}]}))
        (self.repo / "lessons").symlink_to(outside, target_is_directory=True)
        rc, data, err = self.brief()
        self.assertEqual(rc, 0, err)
        self.assertIsNone((data or {}).get("lessons"))
        self.assertIn("lessons/promoted.json ignored", data["promoted_warning"])

    def test_a_malformed_file_warns_and_never_breaks_brief_or_start(self):
        self.lesson("L0001", words=["alpha"])
        for body in ("{", '{"version": 1, "lessons": [{"id": "L1", "text": "x", "trigger": {}}]}', "\xff"):
            with self.subTest(body=body):
                self.file.parent.mkdir(exist_ok=True); self.file.write_text(body, encoding="latin-1")
                before = self.last_seq()
                rc, data, err = self.brief()
                self.assertEqual(rc, 0, err)
                self.assertEqual([l["id"] for l in data["lessons"]], ["L0001"])
                self.assertIn("lessons/promoted.json ignored", data["promoted_warning"])
                self.assertIn("lessons/promoted.json ignored", err)
                self.assertEqual(self.kinds_after(before), ["brief", "lesson.recall"])
        self.file.unlink()
        self.store.append("lesson.retire", "C", {"lesson": "L0001", "reason": "Gone"})
        self.ack("S")
        rc, data, err = run(self.root, "start", "--actor", "A", "--task", "S")
        self.assertEqual((rc, data, err.count("ignored")), (0, None, 0), err)  # absent file: as before


if __name__ == "__main__":
    unittest.main()
