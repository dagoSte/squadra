"""A current plan takes priority without rewriting the recorded code phase."""
import contextlib
import io
import unittest

import test_board
from registry import attention
from squadra import main


class PlanPriority(unittest.TestCase):
    def setUp(self):
        self.f = test_board.Board('test_empty_board_both_languages_and_read_only')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def code_phase(self, verdict=None, name="T"):
        f = self.f
        f.task(name); f.start(name); f.deliver(name)
        if verdict:
            f.review(verdict=verdict, name=name)

    def plan(self, name="T"):
        f = self.f
        f.cli("plan", "--actor", "A", "--task", name, "--file", str(f.plan))

    def step(self, status, phase, role, who, name="T"):
        state = self.f.state()
        item = next(s for s in attention(state)["next_steps"] if s["task"] == name)
        self.assertEqual(state["tasks"][name]["status"], status)
        self.assertEqual((item["status"], item["phase"], item["role"], item["who"]),
                         (status, phase, role, [who]))

    def pending_plan(self, status, name="T"):
        f = self.f
        self.step(status, "plan-read", "reviewer", "R", name)
        self.assertIn(f"| R (reader) | Work {name} | plan review |", f.cli("board", "--lang", "en"))
        self.assertIn(f"| R (lettore) | Work {name} | piano in lettura |", f.cli("board"))
        prefix = f.cli("board").rstrip() + "\n\n"
        self.assertTrue(f.cli("status").startswith(prefix))
        live_row = next(line for line in prefix.splitlines() if f"| Work {name} | piano in lettura |" in line)
        saved = prefix.replace(live_row, f"| R (lettore) | Work {name} | piano in lettura | — |")
        self.assertTrue((f.root / "stato.md").read_text().startswith(saved))

    def test_new_plan_after_code_fix_request_returns_holder_to_reader(self):
        f = self.f
        self.code_phase("changes-required")
        self.assertIn("| A (builder) | Work T | to be fixed |", f.cli("board", "--lang", "en"))
        self.plan(); self.pending_plan("changes-required")
        f.review("plan")
        self.step("changes-required", "changes-required", "builder", "A")
        self.assertIn("| A (builder) | Work T | to be fixed |", f.cli("board", "--lang", "en"))
        f.cli("start", "--actor", "A", "--task", "T")
        f.deliver(); f.review(); f.cli("close", "--actor", "C", "--task", "T")
        self.assertIn("Done 1 of 1", f.cli("board", "--lang", "en"))

    def test_new_plan_after_code_acceptance_is_not_ready_to_close(self):
        f = self.f
        self.code_phase("accepted")
        self.assertIn("| C (coordinator) | Work T | ready to close |", f.cli("board", "--lang", "en"))
        self.plan()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            rc = main(["--root", str(f.root), "close", "--actor", "C", "--task", "T"])
        self.assertEqual(rc, 1)
        self.assertIn("Current plan review pending", err.getvalue())
        self.pending_plan("reviewed")
        self.assertNotIn("ready to close", f.cli("board", "--lang", "en"))
        f.review("plan")
        self.step("reviewed", "reviewed", "coordinator", "C")
        self.assertIn("| C (coordinator) | Work T | ready to close |", f.cli("board", "--lang", "en"))
        f.cli("close", "--actor", "C", "--task", "T")
        self.assertIn("Done 1 of 1", f.cli("board", "--lang", "en"))

    def test_accepted_plan_returns_delivered_code_to_its_reader(self):
        f = self.f
        self.code_phase(); self.plan(); self.pending_plan("delivered")
        f.review("plan")
        self.step("delivered", "delivered", "reviewer", "R")
        self.assertIn("| R (reader) | Work T | delivered: code review |", f.cli("board", "--lang", "en"))
        f.review(); f.cli("close", "--actor", "C", "--task", "T")

    def rejected_plan(self, verdict, phase, label):
        f = self.f
        for index, (code_verdict, status) in enumerate([(None, "delivered"), ("accepted", "reviewed"),
                                                       ("changes-required", "changes-required")]):
            name = f"T{index}"
            with self.subTest(code_status=status, plan_verdict=verdict):
                self.code_phase(code_verdict, name); self.plan(name)
                f.review("plan", verdict, name)
                self.step(status, phase, "builder", "A", name)
                self.assertIn(f"| A (builder) | Work {name} | {label} |", f.cli("board", "--lang", "en"))

    def test_rejected_plan_returns_every_code_phase_to_builder(self):
        self.rejected_plan("changes-required", "plan-fix", "plan to be fixed")

    def test_blocked_plan_returns_every_code_phase_to_builder(self):
        self.rejected_plan("blocked", "plan-blocked", "plan review blocked")

    def test_normal_early_plan_and_closed_task_keep_their_lifecycle(self):
        f = self.f
        f.task(); f.start(); self.plan(); self.pending_plan("working")
        f.review("plan"); self.step("working", "working", "builder", "A")
        f.deliver(); self.step("delivered", "delivered", "reviewer", "R")
        f.review(); self.step("reviewed", "reviewed", "coordinator", "C")
        f.cli("close", "--actor", "C", "--task", "T")
        final = attention(f.state())["next_steps"][0]
        self.assertEqual((final["status"], final["phase"], final["who"]), ("done", "done", []))
        self.assertIn("| A (builder) | Work T | closed |", f.cli("board", "--lang", "en"))

    def test_old_accepted_plan_cannot_cover_a_new_plan_after_code_review(self):
        f = self.f
        f.task(); f.start(); self.plan(); f.review("plan"); f.deliver(); f.review()
        f.plan.write_text("Synthetic plan v2\n")
        self.plan(); self.pending_plan("reviewed")
        f.review("plan"); self.step("reviewed", "reviewed", "coordinator", "C")
        f.cli("close", "--actor", "C", "--task", "T")


if __name__ == "__main__":
    unittest.main()
