"""The suite gets enough time; cancellation cannot become a green receipt."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import selftest


class Selftest(unittest.TestCase):
    def exercise(self, interruption=None, rc=0, text="Ran 450 tests in 259.000s\n\nOK\n"):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "run"
            proc = Mock(returncode=rc)
            proc.wait.side_effect = interruption
            proc.wait.return_value = rc

            def launch(argv, **kwargs):
                kwargs["stderr"].write(text.encode())
                self.assertNotEqual(kwargs["env"]["HOME"], str(Path.home()))
                self.assertEqual(Path(kwargs["env"]["HOME"]).resolve(), (output / "home").resolve())
                return proc

            with patch.object(selftest.subprocess, "Popen", side_effect=launch), \
                    patch.object(selftest, "terminate") as terminate:
                result = selftest.run(output)
            self.assertEqual(json.loads((output / "RESULT.json").read_text()), result)
            proc.wait.assert_called_once_with(timeout=900)
            self.assertEqual(result["timeout_seconds"], 900)
            self.assertEqual(terminate.call_count, int(interruption is not None))
            return result

    def test_full_suite_has_margin_and_a_passed_receipt(self):
        result = self.exercise()
        self.assertEqual((result["status"], result["tests"], result["native_rc"]), ("passed", 450, 0))

    def test_keyboard_interrupt_stays_interrupted_even_if_child_exits_zero(self):
        self.assertEqual(self.exercise(KeyboardInterrupt())["status"], "interrupted")

    def test_timeout_joins_child_and_retains_partial_count(self):
        result = self.exercise(subprocess.TimeoutExpired(["synthetic-suite"], 900), -15, "partial log\n")
        self.assertEqual((result["status"], result["tests"], result["native_rc"]), ("interrupted", 0, -15))

    def test_failed_and_empty_runs_stay_failed(self):
        self.assertEqual(self.exercise(rc=1)["status"], "failed")
        self.assertEqual(self.exercise(text="no tests\n")["status"], "failed")
