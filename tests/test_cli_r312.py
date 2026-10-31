"""R312-CLI: a task without a reader is warned, and the documented `task --proof -- argv` form works."""
import contextlib
import io
import json
import sys
import unittest

import test_board as support
from squadra import main


class TaskCli(unittest.TestCase):
    setUp = support.Board.setUp
    cli = support.Board.cli
    state = support.Board.state

    def run_cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            rc = main(["--root", str(self.root), *args])
        return rc, out.getvalue(), err.getvalue()

    def base_task(self, name="T"):
        return ["task", "--actor", "C", "--id", name, "--title", "Work " + name, "--owner", "A",
                "--repo", str(self.repo), "--scope-file", str(self.scope)]

    # Element 8: `--proof -- argv`
    def test_documented_double_dash_form_keeps_task_named_options(self):
        argv = [sys.executable, "-B", "script.py", "--root", "X", "--task", "Y", "--reviewer", "Z", "--id=W"]
        rc, _, err = self.run_cli(*self.base_task(), "--reviewer", "R", "--proof", "--", *argv)
        self.assertEqual(rc, 0, err)
        task = self.state()["tasks"]["T"]
        self.assertEqual(task["proof"], argv)
        self.assertEqual(task["reviewer"], "R")

    def test_double_dash_inside_proof_argv_is_kept_after_the_first(self):
        argv = [sys.executable, "-m", "tool", "--", "--root", "X"]
        rc, _, err = self.run_cli(*self.base_task(), "--reviewer", "R", "--proof", "--", *argv)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.state()["tasks"]["T"]["proof"], argv)

    def test_plain_proof_form_still_works(self):
        rc, _, err = self.run_cli(*self.base_task(), "--reviewer", "R", "--proof", sys.executable, "-c", "pass")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.state()["tasks"]["T"]["proof"], [sys.executable, "-c", "pass"])

    def test_plain_form_still_refuses_task_options_swallowed_by_proof(self):
        rc, _, err = self.run_cli(*self.base_task(), "--proof", sys.executable, "-c", "pass", "--reviewer", "R")
        self.assertEqual(rc, 1)
        self.assertIn("Task option inside", err)
        self.assertNotIn("T", self.state()["tasks"])

    def test_proof_file_still_works(self):
        rc, _, err = self.run_cli(*self.base_task(), "--reviewer", "R", "--proof-file", str(self.proof))
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.state()["tasks"]["T"]["proof"], json.loads(self.proof.read_text()))

    def test_empty_double_dash_proof_is_refused(self):
        rc, _, err = self.run_cli(*self.base_task(), "--reviewer", "R", "--proof", "--")
        self.assertEqual(rc, 1, err)
        self.assertIn("nonempty argv", err)
        self.assertNotIn("T", self.state()["tasks"])

    def test_double_dash_proof_and_proof_file_stay_exclusive(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            main(["--root", str(self.root), *self.base_task(), "--proof-file", str(self.proof),
                  "--proof", "--", sys.executable, "-c", "pass"])
        self.assertNotIn("T", self.state()["tasks"])

    # Element 2: a task without a reader
    def test_task_without_reviewer_warns_and_still_records(self):
        rc, _, err = self.run_cli(*self.base_task(), "--proof-file", str(self.proof))
        self.assertEqual(rc, 0, err)
        self.assertIn("nessun lettore", err)
        self.assertIn("reassign", err)
        self.assertIn("--reviewer", err)
        task = self.state()["tasks"]["T"]
        self.assertIsNone(task["reviewer"])
        self.assertNotIn("pool", task)

    def test_english_workspace_gets_the_english_warning(self):
        self.root = self.base / "record-en"
        self.cli("init", "--name", "English fixture", "--project", str(self.repo), "--temporary", "--lang", "en")
        for name, role in [("C", "coordinator"), ("A", "builder")]:
            self.cli("agent", "--id", name, "--identity", "identity-" + name, "--role", role, "--client", "manual",
                     "--repo", str(self.repo))
        rc, _, err = self.run_cli(*self.base_task(), "--proof-file", str(self.proof))
        self.assertEqual(rc, 0, err)
        self.assertIn("no reader: a delivery without a reader cannot get one later with reassign; pass --reviewer now", err)

    def test_reader_or_pool_gives_no_warning(self):
        rc, _, err = self.run_cli(*self.base_task("T1"), "--reviewer", "R", "--proof-file", str(self.proof))
        self.assertEqual(rc, 0, err)
        self.assertNotIn("reassign", err)
        rc, _, err = self.run_cli(*self.base_task("T2"), "--reviewer", "R", "--reviewer", "Other",
                                  "--proof-file", str(self.proof))
        self.assertEqual(rc, 0, err)
        self.assertNotIn("reassign", err)

    def test_refused_task_without_reviewer_does_not_warn(self):
        rc, _, err = self.run_cli(*self.base_task(), "--proof", "--")
        self.assertEqual(rc, 1)
        self.assertNotIn("reassign", err)


if __name__ == "__main__":
    unittest.main()
