"""The suite gets enough time; cancellation cannot become a green receipt."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from registry import Error
import selftest
import squadra

PASSED = {"summary.json": {"collected": 450, "executed": 450, "passed": True}}


class Selftest(unittest.TestCase):
    def exercise(self, interruption=None, rc=0, receipts=PASSED, poll=0, **kwargs):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "run"
            proc = Mock(returncode=rc)
            proc.wait.side_effect = interruption
            proc.wait.return_value = rc
            proc.poll.return_value = poll

            def launch(argv, **options):
                self.argv = argv
                for name, value in receipts.items():
                    path = output / "parallel" / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(value))
                self.assertNotEqual(options["env"]["HOME"], str(Path.home()))
                self.assertEqual(Path(options["env"]["HOME"]).resolve(), (output / "home").resolve())
                return proc

            with patch.object(selftest.subprocess, "Popen", side_effect=launch), \
                    patch.object(selftest, "terminate") as terminate:
                result = selftest.run(output, **kwargs)
            self.assertEqual(json.loads((output / "RESULT.json").read_text()), result)
            timeout = kwargs.get("timeout", selftest.TIMEOUT_SECONDS)
            self.assertEqual(proc.wait.call_args_list[0], call(timeout=timeout))
            self.assertEqual(result["timeout_seconds"], timeout)
            self.assertEqual(terminate.call_count, int(interruption is not None))
            self.proc = proc
            return result

    def test_full_suite_has_margin_and_a_passed_receipt(self):
        result = self.exercise()
        self.assertEqual((result["status"], result["tests"], result["native_rc"]), ("passed", 450, 0))

    def test_keyboard_interrupt_stays_interrupted_even_if_child_exits_zero(self):
        self.assertEqual(self.exercise(KeyboardInterrupt())["status"], "interrupted")

    def test_timeout_joins_child_and_retains_partial_count(self):
        result = self.exercise(subprocess.TimeoutExpired(["synthetic-suite"], 900), -15, {})
        self.assertEqual((result["status"], result["tests"], result["native_rc"]), ("interrupted", 0, -15))

    def test_failed_and_empty_runs_stay_failed(self):
        self.assertEqual(self.exercise(rc=1)["status"], "failed")
        for collected, executed in [(0, 0), (5, 4)]:
            summary = {"summary.json": {"collected": collected, "executed": executed, "passed": True}}
            self.assertEqual(self.exercise(receipts=summary)["status"], "failed")

    def test_the_parallel_runner_gets_a_configurable_limit(self):
        self.assertGreaterEqual(selftest.TIMEOUT_SECONDS, 1800)
        self.assertEqual(squadra.parser().parse_args(["selftest"]).timeout, selftest.TIMEOUT_SECONDS)
        result = self.exercise(timeout=60)
        self.assertEqual(self.argv[1:], ["-B", "tools/parallel_tests.py", "--timeout", "60",
                                         "--output", str(Path(result["folder"]) / "parallel")])
        self.assertEqual((result["status"], result["tests"], result["collected"]), ("passed", 450, 450))
        fake = Mock(return_value={"tests": 3, "collected": 4, "status": "interrupted", "folder": "/synthetic"})
        with patch.object(selftest, "run", fake), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(squadra.main(["selftest", "--timeout", "60", "--output", "/synthetic"]), 1)
        fake.assert_called_once_with("/synthetic", 60)

    def test_interruption_cancels_the_workers_first_and_counts_finished_modules(self):
        receipts = {"discovery/result.json": {"ids": ["a", "b", "c", "d"]},
                    "module-000/result.json": {"executed": 3}, "module-001/started.jsonl": "d"}
        result = self.exercise(subprocess.TimeoutExpired(["runner"], 900), -2, receipts, poll=None)
        self.assertEqual((result["status"], result["tests"], result.get("collected")), ("interrupted", 3, 4))
        self.proc.send_signal.assert_called_once_with(signal.SIGINT)
        self.assertEqual(self.proc.wait.call_args_list[1], call(timeout=selftest.GRACE_SECONDS))

    def test_output_inside_the_source_is_refused_before_anything_runs(self):
        inside = Path(selftest.__file__).resolve().parents[1] / "selftest-output-probe"
        self.addCleanup(shutil.rmtree, inside, True)
        with patch.object(selftest.subprocess, "Popen", side_effect=AssertionError("suite started")):
            with self.assertRaisesRegex(Error, "outside the Squadra source"):
                selftest.run(inside)
        self.assertFalse(inside.exists())


if __name__ == "__main__":
    unittest.main()
