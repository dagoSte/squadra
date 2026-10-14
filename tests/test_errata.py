"""errata: lessons recorded in the registry, shown again at brief and start, asked for after a red."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest

import test_squadra as support
import test_fix_challenge as fixes
import test_light_route as light
from fix_challenge import execute as run_challenge, prepare, register
from registry import Error, Store, snapshot
from squadra import deliver, main

ROOT = Path(__file__).resolve().parents[1]
KINDS = {"lesson", "lesson.retire", "lesson.recall", "lesson.ineffective"}


def run(root, *args):
    """The CLI's exit status, its JSON result (or None) and stderr; argparse's exit counts as a status."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main(["--root", str(root), *args])
        except SystemExit as exc:
            rc = exc.code
    text = out.getvalue()
    return rc, (json.loads(text[:text.index("\n}") + 2]) if text.startswith("{") else None), err.getvalue()


class Fixture:
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task
    ack = support.Workflow.ack
    cli = support.Workflow.cli
    proof = support.Workflow.proof
    delivered = support.Workflow.delivered

    def seq(self, event_id):
        return self.store.db.execute("SELECT seq FROM events WHERE id=?", (event_id,)).fetchone()[0]

    def kinds_after(self, seq):
        return [e["kind"] for e in self.store.events() if e["seq"] > seq]

    def last_seq(self):
        return self.store.events()[-1]["seq"]

    def scoped(self, name, text):
        scope = self.base / (name + "-scope.md"); scope.write_text(text)
        data = {"id": name, "title": "Synthetic change", "owner": "A", "repo": str(self.repo),
                "base": snapshot(self.repo)["head"], "scope": self.store.artifact(scope), "proof": None,
                "expected": None, "depends": [], "full_gate": False}
        self.store.batch([("task", "C", data, None), ("message", "C", {"id": "assign-" + name, "to": "A",
                          "task": name, "body": "Scoped work"}, None)])

    def lesson(self, name, paths=(), words=(), task="T", actor="A", text="Bump every mirror, then search for the old one"):
        trigger = {k: list(v) for k, v in (("paths", paths), ("words", words)) if v}
        return self.store.append("lesson", actor, {"id": name, "text": text, "trigger": trigger,
                                                  "source": {"task": task, "event": 1, "eligible": True}})

    def brief_file(self, text="Brief for this task."):
        path = self.base / "brief.md"; path.write_text(text); return path


class LessonRefusals(Fixture, unittest.TestCase):
    def test_lesson_event_refusals(self):
        base = {"id": "L0001", "text": "Do it right", "trigger": {"paths": ["*.py"]},
                "source": {"task": "T", "event": 1, "eligible": True}}
        cases = {
            "lesson_text_required": [{"text": ""}, {"text": "   "}, {"text": None}, {"text": "a\x07b"}, {"text": "x" * 1001}],
            "lesson_trigger_required": [{"trigger": {}}, {"trigger": {"paths": [], "words": []}}, {"trigger": None},
                                        {"trigger": {"other": ["x"]}}],
            "lesson_trigger_invalid": [{"trigger": {"paths": ["a\nb"]}}, {"trigger": {"words": ["x"]}},
                                       {"trigger": {"words": [" padded "]}}, {"trigger": {"paths": ["p"] * 11}},
                                       {"trigger": {"words": "version"}}],
            "lesson_source_required": [{"source": {"task": "NOPE", "event": 1, "eligible": True}},
                                       {"source": {"task": "T", "event": 0, "eligible": True}},
                                       {"source": {"task": "T", "event": 10 ** 6, "eligible": True}},
                                       {"source": {"task": "T", "event": 1}},
                                       {"source": {"task": "T", "event": "1", "eligible": True}}],
            "lesson_id_invalid": [{"id": "X0001"}, {"id": "L12"}],
            "lesson_fields_invalid": [{"extra": 1}],
        }
        for code, variants in cases.items():
            for variant in variants:
                with self.subTest(code=code, variant=variant), self.assertRaisesRegex(Error, code):
                    self.store.append("lesson", "A", {**base, **variant})
        try:
            self.store.append("lesson", "A", base)
        except Error as exc:
            self.fail("a valid lesson is refused: %s" % exc)
        with self.assertRaisesRegex(Error, "lesson_id_taken"):
            self.store.append("lesson", "A", base)

    def test_retire_recall_and_ineffective_refusals(self):
        with self.assertRaisesRegex(Error, "lesson_unknown"):
            self.store.append("lesson.retire", "C", {"lesson": "L0009", "reason": "Never existed"})
        self.lesson("L0001", paths=["*.py"]); self.lesson("L0002", paths=["docs/*.md"])
        with self.assertRaisesRegex(Error, "lesson_reason_required"):
            self.store.append("lesson.retire", "C", {"lesson": "L0001", "reason": " "})
        with self.assertRaisesRegex(Error, "lesson_retire_not_allowed"):
            self.store.append("lesson.retire", "R", {"lesson": "L0001", "reason": "Not mine to retire"})
        self.store.append("lesson.retire", "A", {"lesson": "L0002", "reason": "Mirrors are generated now"})
        with self.assertRaisesRegex(Error, "lesson_already_retired"):
            self.store.append("lesson.retire", "C", {"lesson": "L0002", "reason": "Again"})
        for bad in ({"task": "T", "at": "build", "lessons": ["L0001"]}, {"task": "T", "at": "start", "lessons": []},
                    {"task": "T", "at": "start", "lessons": ["L0001", "L0001"]},
                    {"task": "T", "at": "start", "lessons": ["L0002"]}, {"task": "T", "at": "start", "lessons": ["L0009"]},
                    {"task": "NOPE", "at": "start", "lessons": ["L0001"]},
                    {"task": "T", "at": "start", "lessons": ["L0001"] * 6}):
            with self.subTest(recall=bad), self.assertRaisesRegex(Error, "lesson_recall_invalid"):
                self.store.append("lesson.recall", "C", bad)
        recall = self.seq(self.store.append("lesson.recall", "C", {"task": "T", "at": "brief", "lessons": ["L0001"]}))
        for bad, code in (({"lesson": "L0001", "recall": recall + 100, "reason": "Came back"}, "lesson_recall_unknown"),
                          ({"lesson": "L0002", "recall": recall, "reason": "Not shown there"}, "lesson_recall_unknown"),
                          ({"lesson": "L0001", "recall": recall, "reason": ""}, "lesson_reason_required"),
                          ({"lesson": "L0009", "recall": recall, "reason": "Unknown"}, "lesson_unknown")):
            with self.subTest(mark=bad), self.assertRaisesRegex(Error, code):
                self.store.append("lesson.ineffective", "R", bad)
        self.store.append("lesson.ineffective", "R", {"lesson": "L0001", "recall": recall, "reason": "Came back in T"})
        self.assertEqual(self.store.state()["lessons"]["L0001"]["ineffective"][0]["recall"], recall)

    def test_cli_lesson_needs_a_red_of_its_task_or_any_source(self):
        self.delivered()
        self.assertEqual(self.cli("review", "--actor", "R", "--task", "T", "--verdict", "changes-required",
                                  "--report", str(self.report)), 0)
        events = self.store.events()
        red = next(e["seq"] for e in events if e["kind"] == "review")
        assignment = next(e["seq"] for e in events if e["kind"] == "task")
        self.task("T2")
        other = self.last_seq()
        lesson = ["lesson", "--actor", "R", "--task", "T", "--text", "Check every mirror", "--path", "*.py"]
        for args, code in ((lesson + ["--source-event", str(assignment)], "lesson_source_not_eligible"),
                           (lesson + ["--source-event", str(other), "--any-source"], "lesson_source_unrelated"),
                           (lesson[:3] + lesson[5:] + ["--source-event", str(red)], "lesson_source_required"),
                           (["lesson", "--task", "T", "--source-event", str(red), "--text", "x", "--word", "mirror"],
                            "lesson_actor_required"),
                           (["lesson", "--actor", "R", "--task", "T", "--source-event", str(red), "--text", " ", "--word", "mirror"],
                            "lesson_text_required"),
                           (["lesson", "--actor", "R", "--task", "T", "--source-event", str(red), "--text", "Check"],
                            "lesson_trigger_required")):
            with self.subTest(code=code):
                rc, _, err = run(self.root, *args)
                self.assertEqual(rc, 1, err); self.assertIn(code, err)
        rc, data, err = run(self.root, *lesson, "--source-event", str(red))
        self.assertEqual((rc, (data or {}).get("source_eligible")), (0, True), err)
        rc, data, err = run(self.root, *lesson, "--source-event", str(assignment), "--any-source")
        self.assertEqual((rc, (data or {}).get("source_eligible")), (0, False), err)
        lessons = self.store.state().get("lessons", {})
        self.assertEqual([lessons[n]["source"]["eligible"] for n in sorted(lessons)], [True, False])


class Recall(Fixture, unittest.TestCase):
    def test_brief_and_start_show_the_best_five_and_record_each_showing(self):
        self.scoped("S", "Change scripts/registry.py and plugins/x/bridge/registry.py; keep docs/guide.md readable.\n")
        self.lesson("L0001", paths=["scripts/*.py"]); self.lesson("L0002", words=["registry"])
        self.lesson("L0003", paths=["plugins/*/bridge/registry.py"], words=["registry"])
        self.lesson("L0004", paths=["nothing/*"]); self.lesson("L0005", words=["guide"])
        self.lesson("L0006", words=["registry"]); self.lesson("L0007", paths=["docs/*.md"])
        self.lesson("L0008", words=["mirror"]); self.lesson("L0009", words=["plugins"])
        self.store.append("lesson.retire", "C", {"lesson": "L0006", "reason": "Out of date"})
        expected = ["L0003", "L0007", "L0001", "L0009", "L0005"]
        before = self.last_seq()
        rc, data, err = run(self.root, "brief", "--actor", "C", "--task", "S", "--file", str(self.brief_file()))
        self.assertEqual(rc, 0, err)
        self.assertEqual([l["id"] for l in (data or {}).get("lessons", [])], expected)
        self.assertEqual([l["score"] for l in data["lessons"]], [4, 3, 3, 1, 1])
        self.assertEqual(self.kinds_after(before), ["brief", "lesson.recall"])
        recall = self.store.state()["lesson_recalls"][-1]
        self.assertEqual((recall["task"], recall["at"], recall["lessons"], recall["actor"], recall["seq"]),
                         ("S", "brief", expected, "C", data["recall"]))
        self.ack("S")
        rc, data, err = run(self.root, "start", "--actor", "A", "--task", "S")
        self.assertEqual(([l["id"] for l in (data or {}).get("lessons", [])], self.store.state()["lesson_recalls"][-1]["at"]),
                         (expected, "start"))
        rc, listing, err = run(self.root, "lesson", "--list")
        self.assertEqual((listing["recall_events"], listing["recalled_pairs"]), (2, 5))
        self.assertEqual({l["id"]: l["status"] for l in listing["lessons"]}["L0006"], "retired")

    def test_an_ineffective_lesson_rises(self):
        self.scoped("S", "The alpha path needs care.\n")
        self.lesson("L0001", words=["alpha"]); self.lesson("L0002", words=["alpha"])
        rc, data, err = run(self.root, "brief", "--actor", "C", "--task", "S", "--file", str(self.brief_file()))
        self.assertEqual([l["id"] for l in (data or {}).get("lessons", [])], ["L0002", "L0001"])
        self.store.append("lesson.ineffective", "R", {"lesson": "L0001", "recall": data["recall"], "reason": "Came back"})
        self.ack("S")
        rc, data, err = run(self.root, "start", "--actor", "A", "--task", "S")
        self.assertEqual([(l["id"], l["score"], l["status"]) for l in data["lessons"]],
                         [("L0001", 2, "ineffective"), ("L0002", 1, "active")])

    def test_without_a_matching_lesson_brief_and_start_are_unchanged(self):
        before = self.last_seq()
        rc, data, err = run(self.root, "brief", "--actor", "C", "--task", "T", "--file", str(self.brief_file()))
        self.assertEqual((rc, data, self.kinds_after(before)), (0, None, ["brief"]))
        self.assertNotIn("lessons", self.store.state()); self.assertNotIn("lesson_recalls", self.store.state())
        self.assertNotIn("lessons", json.loads((self.root / "state.json").read_text()))
        self.lesson("L0001", paths=["nothing/*"], words=["absent"])
        before = self.last_seq(); self.ack()
        rc, data, err = run(self.root, "start", "--actor", "A", "--task", "T")
        self.assertEqual((rc, data, self.kinds_after(before)), (0, None, ["ack", "start"]))

    def test_list_and_export_expose_lessons_recalls_and_marks(self):
        self.scoped("S", "Touch scripts/registry.py.\n")
        self.lesson("L0001", paths=["scripts/*.py"])
        rc, data, err = run(self.root, "brief", "--actor", "C", "--task", "S", "--file", str(self.brief_file()))
        self.store.append("lesson.ineffective", "R", {"lesson": "L0001", "recall": data["recall"], "reason": "Came back"})
        self.assertEqual(run(self.root, "export")[0], 0)
        exported = json.loads((self.root / "state.json").read_text())
        self.assertEqual(exported["lessons"]["L0001"]["recalled_tasks"], ["S"])
        self.assertEqual(exported["lessons"]["L0001"]["ineffective"][0]["recall"], data["recall"])
        self.assertEqual(exported["lesson_recalls"][0]["lessons"], ["L0001"])
        rc, listing, err = run(self.root, "lesson", "--list")
        self.assertEqual({k: listing[k] for k in ("lesson_events", "recall_events", "recalled_pairs", "ineffective_marks")},
                         {"lesson_events": 1, "recall_events": 1, "recalled_pairs": 1, "ineffective_marks": 1})


class AskForALesson(Fixture, unittest.TestCase):
    def assert_hint(self, data, event, responsible):
        hint = (data or {}).get("errata")
        self.assertIsNotNone(hint, "the red must ask for a lesson")
        self.assertEqual((hint["record_lesson"], hint["task"], hint["source_event"], hint["responsible"]),
                         (True, "T", event, responsible))
        self.assertIn("--source-event %d" % event, hint["command"])
        self.assertIn('--text "<what to do differently next time>"', hint["command"])

    def test_a_failed_proof_asks_and_a_passed_one_does_not(self):
        self.ack()
        rc, data, err = run(self.root, "prove", "--actor", "A", "--task", "T", "--", sys.executable, "-c", "raise SystemExit(3)")
        self.assertEqual(rc, 1)
        failed = json.loads(err.split("Squadra: ", 1)[1]) if "Squadra: {" in err else {}
        end = [e["seq"] for e in self.store.events() if e["kind"] == "run.end"][-1]
        self.assert_hint(failed, end, "A")
        rc, data, err = run(self.root, "prove", "--actor", "A", "--task", "T", "--", sys.executable, "-c", "pass")
        self.assertEqual(rc, 0, err); self.assertNotIn("errata", data)
        self.assertNotIn("lessons", self.store.state())

    def test_a_code_read_asking_for_changes_asks(self):
        self.delivered()
        rc, data, err = run(self.root, "review", "--actor", "R", "--task", "T", "--verdict", "changes-required",
                            "--report", str(self.report))
        self.assertEqual(rc, 0, err)
        self.assert_hint(data, [e["seq"] for e in self.store.events() if e["kind"] == "review"][-1], "R")

    def test_an_accepted_read_and_a_plain_close_ask_nothing(self):
        self.delivered()
        rc, data, err = run(self.root, "review", "--actor", "R", "--task", "T", "--verdict", "accepted", "--report", str(self.report))
        self.assertEqual((rc, data), (0, None), err)
        rc, data, err = run(self.root, "close", "--actor", "C", "--task", "T")
        self.assertEqual(rc, 0, err); self.assertNotIn("errata", data)


class DefectFixClose(unittest.TestCase):
    setUp = fixes.FixChallenge.setUp
    git = fixes.FixChallenge.git
    task = fixes.FixChallenge.task
    ack = fixes.FixChallenge.ack
    cli = fixes.FixChallenge.cli

    def test_closing_a_defect_fix_asks_for_a_lesson(self):
        (self.repo / "README.md").write_text("wrong\n")
        self.git("add", "README.md"); self.git("commit", "-qm", "documented baseline")
        light.LightRoute.register(self, fix=prepare(self.store, {}))
        tests = self.repo / "tests"; tests.mkdir()
        (tests / "test_text.py").write_text(
            "import pathlib, unittest\nclass Text(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(pathlib.Path('README.md').read_text(), 'right\\n')\n")
        config = self.base / "oracle.json"
        config.write_text(json.dumps({"ids": ["test_text.Text.test_value"], "files": ["tests/test_text.py"]}))
        register(self.store, "C", "L", config, "Correct the documented text")
        (self.repo / "README.md").write_text("right\n")
        self.assertEqual(run_challenge(self.store, "A", "L", self.base / "challenge")["status"], "matched")
        self.assertEqual(light.LightRoute.proof(self)["status"], "passed")
        self.store.append("ack", "R", {"message": deliver(self.store, "A", "L")["handoff"]})
        self.assertEqual(self.cli("review", "--actor", "R", "--task", "L", "--verdict", "accepted",
                                  "--report", str(self.report), "--covers", "R1"), 0)
        rc, data, err = run(self.root, "close", "--actor", "C", "--task", "L")
        self.assertEqual(rc, 0, err)
        hint = (data or {}).get("errata") or {}
        close = [e["seq"] for e in self.store.events() if e["kind"] == "close"][-1]
        self.assertEqual((hint.get("task"), hint.get("source_event"), hint.get("responsible")), ("L", close, "C"))


class Bridges(Fixture, unittest.TestCase):
    def test_every_bridge_reads_all_lesson_kinds_without_errata(self):
        self.lesson("L0001", paths=["*.py"])
        recall = self.seq(self.store.append("lesson.recall", "C", {"task": "T", "at": "brief", "lessons": ["L0001"]}))
        self.store.append("lesson.ineffective", "R", {"lesson": "L0001", "recall": recall, "reason": "Came back"})
        self.store.append("lesson.retire", "C", {"lesson": "L0001", "reason": "Replaced"})
        probe = ("import sys; sys.path.insert(0, sys.argv[1]); import registry\n"
                 "s = registry.Store(sys.argv[2], read_only=True); st = s.state(); s.close()\n"
                 "l = st['lessons']['L0001']\n"
                 "assert l['retired'] and l['ineffective'] and st['lesson_recalls'][0]['lessons'] == ['L0001']\n"
                 "assert 'errata' not in sys.modules\nprint('read')\n")
        for name in ("playwright", "memoranda", "sentry"):
            bridge = ROOT / "plugins" / name / "bridge"
            self.assertEqual((bridge / "registry.py").read_bytes(), (ROOT / "scripts/registry.py").read_bytes(), name)
            result = subprocess.run([sys.executable, "-I", "-B", "-c", probe, str(bridge), str(self.root)],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual((result.returncode, result.stdout.strip()), (0, "read"), name + result.stderr)
        reader = ("import sys\nfrom pathlib import Path\nsys.path.insert(0, sys.argv[1])\n"
                  "from squadra_memories.source import load_registry, snapshot, verified_events\n"
                  "module, entry = load_registry(); snapshot(Path(sys.argv[2]), Path(sys.argv[3]))\n"
                  "kinds = {e['kind'] for e in verified_events(module, entry, Path(sys.argv[3]))}\n"
                  "assert {'lesson', 'lesson.recall', 'lesson.ineffective', 'lesson.retire'} <= kinds, kinds\n"
                  "assert 'errata' not in sys.modules\nprint('verified')\n")
        result = subprocess.run([sys.executable, "-I", "-B", "-c", reader, str(ROOT / "plugins/memoranda/bridge"),
                                 str(self.root), str(self.base / "copy")], capture_output=True, text=True, timeout=30)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "verified"), result.stderr)


if __name__ == "__main__":
    unittest.main()
