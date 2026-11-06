"""R314B.L0: per-task review budget, resource holder per task, agreed proof argv, bind check, lanes fallback."""
import contextlib
import io
from pathlib import Path
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import test_squadra as support
from registry import Error, Store, plain, review_limit, snapshot
from squadra import main
import runtime


class L0(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task
    ack = support.Workflow.ack
    proof = support.Workflow.proof
    delivered = support.Workflow.delivered
    cli = support.Workflow.cli

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--root", str(self.root), *args])
        return code, out.getvalue(), err.getvalue()

    def review(self, verdict="accepted", text="Independent read"):
        self.report.write_text(text + "\n")
        return self.cli("review", "--actor", "R", "--task", "T", "--verdict", verdict, "--report", str(self.report))

    # P1
    def test_owner_extension_adds_exactly_the_granted_rounds_to_one_task(self):
        self.delivered()
        self.assertEqual(self.review("changes-required", "first"), 0)
        self.assertEqual(self.review("changes-required", "second"), 0)
        self.assertEqual(self.review("accepted", "third, over the global budget"), 1)
        code, _, err = self.run_cli("decision", "--actor", "C", "--reason", "One more read", "--extend-budget", "T",
                                    "--stage", "code", "--by", "1")
        self.assertEqual(code, 1)
        self.assertIn("owner's words", err)
        self.assertEqual(self.cli("decision", "--actor", "C", "--reason", "One more read", "--extend-budget", "T",
                                  "--stage", "code", "--by", "1", "--owner-words", "Una rilettura in piu'",
                                  "--channel", "typed-here"), 0)
        state = self.store.state()
        self.assertEqual(review_limit(state, "T", "code"), 3)
        self.assertEqual(state["workspace"]["limits"]["code"], 2, "the global limit is unchanged")
        self.assertEqual(self.review("changes-required", "third, granted"), 0)
        self.assertEqual(self.review("accepted", "fourth, beyond the grant"), 1)
        self.assertEqual(self.store.state()["tasks"]["T"]["rounds"]["code"], 3, "spent rounds are never reset")

    def test_an_extension_belongs_to_its_task_only(self):
        self.task("U")
        self.assertEqual(self.cli("decision", "--actor", "C", "--reason", "Grant", "--extend-budget", "U", "--stage", "code",
                                  "--by", "2", "--owner-words", "Si", "--channel", "typed-here"), 0)
        state = self.store.state()
        self.assertEqual((review_limit(state, "U", "code"), review_limit(state, "T", "code"),
                          review_limit(state, "U", "plan")), (4, 2, 3))
        with self.assertRaisesRegex(Error, "1 to 5"):
            self.store.append("decision", "C", {"reason": "Too much", "owner_words": "Si", "channel": "typed-here",
                                                "budget_extension": {"task": "U", "stage": "code", "by": 9}})

    # P2
    def test_same_identity_for_another_task_waits_and_same_task_is_idempotent(self):
        self.task("U")
        self.assertEqual(self.cli("resource", "acquire", "--actor", "C", "--name", "cpu-heavy", "--task", "T",
                                  "--reason", "Suite for T"), 0)
        before = len(self.store.events())
        code, out, _ = self.run_cli("resource", "acquire", "--actor", "C", "--name", "cpu-heavy", "--task", "T",
                                    "--reason", "Suite for T again")
        self.assertEqual(code, 0)
        self.assertIn("already_held", out)
        self.assertEqual(len(self.store.events()), before, "an idempotent acquire records nothing")
        code, _, err = self.run_cli("resource", "acquire", "--actor", "C", "--name", "cpu-heavy", "--task", "U",
                                    "--reason", "Suite for U")
        self.assertEqual(code, 1)
        self.assertIn("another task of this identity", err)
        self.assertEqual(plain(self.store.state()["resources"]["cpu-heavy"])["task"], "T")
        code, _, err = self.run_cli("resource", "release", "--actor", "C", "--name", "cpu-heavy", "--task", "U",
                                    "--reason", "Wrong task")
        self.assertEqual(code, 1)
        self.assertIn("another task", err)

        def release_soon():
            time.sleep(0.5)
            other = Store(self.root)
            try:
                other.append("resource.release", "C", {"name": "cpu-heavy", "reason": "T finished", "task": "T"})
            finally:
                other.close()

        worker = threading.Thread(target=release_soon)
        worker.start()
        code, _, err = self.run_cli("resource", "acquire", "--actor", "C", "--name", "cpu-heavy", "--task", "U",
                                    "--reason", "Suite for U", "--wait", "20")
        worker.join(10)
        self.assertEqual(code, 0, err)
        self.assertEqual(plain(self.store.state()["resources"]["cpu-heavy"])["task"], "U")

    def test_K2_another_seat_of_the_identity_never_gets_a_grant_it_cannot_release(self):
        identity = self.store.state()["agents"]["C"]["identity"]
        self.store.append("agent", "C-alias", {"id": "C-alias", "role": "builder", "identity": identity, "client": "claude",
                          "repo": str(self.repo), "skills": [], "session": "synthetic-alias"})
        self.assertEqual(self.cli("resource", "acquire", "--actor", "C", "--name", "cpu-heavy", "--task", "T",
                                  "--reason", "Suite for T"), 0)
        code, out, err = self.run_cli("resource", "acquire", "--actor", "C-alias", "--name", "cpu-heavy", "--task", "T",
                                      "--reason", "Second suite for T", "--wait", "0")
        self.assertNotIn("already_held", out)
        self.assertEqual(code, 1)
        self.assertIn("another seat of this identity", err)
        self.assertEqual(plain(self.store.state()["resources"]["cpu-heavy"])["owner"], "C")

    # P5
    def agreed(self, name="P", proof=None):
        data = {"id": name, "title": "Agreed proof", "owner": "A", "repo": str(self.repo), "base": snapshot(self.repo)["head"],
                "scope": self.store.artifact(self.scope), "proof": proof or [sys.executable, "-c", "print('agreed')"], "expected": 0,
                "depends": [], "full_gate": False}
        self.store.batch([("task", "C", data, None), ("message", "C", {"id": "assign-" + name, "to": "A", "task": name,
                          "body": "Scoped work"}, None)])
        self.ack(name)
        return data["proof"]

    def test_prove_without_argv_runs_the_agreed_proof(self):
        argv = self.agreed()
        code, _, err = self.run_cli("prove", "--actor", "A", "--task", "P")
        self.assertEqual(code, 0, err)
        run = next(r for r in self.store.state()["runs"].values() if r["task"] == "P")
        self.assertEqual(run["argv"], argv)
        code, _, err = self.run_cli("prove", "--actor", "A", "--task", "P", sys.executable, "-c", "print('other')")
        self.assertEqual(code, 1)
        self.assertIn("differs from agreed", err)

    # P3
    def test_prove_refuses_the_full_suite_when_local_bind_is_unavailable(self):
        self.agreed(proof=[sys.executable, "-B", "tools/parallel_tests.py"])
        with patch.object(runtime, "local_bind_available", create=True,
                          return_value=(False, "Local bind refused: sandbox limit")):
            code, _, err = self.run_cli("prove", "--actor", "A", "--task", "P")
        self.assertEqual(code, 1)
        self.assertIn("sandbox limit", err)
        self.assertFalse([r for r in self.store.state()["runs"].values() if r["task"] == "P"], "no run is recorded")

    def test_K3_a_proof_that_needs_no_localhost_runs_with_a_warning(self):
        self.agreed()
        with patch.object(runtime, "local_bind_available", create=True,
                          return_value=(False, "Local bind refused: sandbox limit")):
            code, _, err = self.run_cli("prove", "--actor", "A", "--task", "P")
        self.assertEqual(code, 0, err)
        self.assertIn("sandbox limit", err)
        self.assertTrue([r for r in self.store.state()["runs"].values() if r["task"] == "P"], "the targeted proof ran")

    # D1
    def test_task_paths_keeps_the_parent_when_the_analysis_fails(self):
        failing = types.ModuleType("lanes")

        def analyze(*args, **kwargs):
            raise Error("Cannot read Git history for lanes")

        failing.analyze, failing.partition = analyze, lambda *a, **k: None
        with patch.dict(sys.modules, {"lanes": failing}):
            code, out, err = self.run_cli("task", "--actor", "C", "--owner", "C", "--id", "F", "--title", "Fallback",
                                          "--repo", str(self.repo), "--scope-file", str(self.scope), "--reviewer", "R",
                                          "--paths", "*.py")
        self.assertEqual(code, 0, err)
        self.assertIn("only the parent task is created", err)
        self.assertEqual(sorted(t for t in self.store.state()["tasks"] if t.startswith("F")), ["F"])
        self.assertIn("assign-F", self.store.state()["messages"])


if __name__ == "__main__":
    unittest.main()
