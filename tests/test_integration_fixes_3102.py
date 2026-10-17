"""Integration fixes for 3.10.2: a hung git is a plain refusal, never a traceback."""
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import registry  # noqa: E402


def hung(*args, **kwargs):
    raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout", 30))


class HungGit(unittest.TestCase):
    def test_registry_root_protection_refuses_plainly_when_git_hangs(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(registry.subprocess, "run", side_effect=hung):
            with self.assertRaises(registry.Error) as refused:
                registry.protect_registry_root(Path(tmp) / "reg")
        self.assertIn("timed out", str(refused.exception))

    def test_git_probe_returns_the_exit_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertNotEqual(registry.git_probe("-C", str(Path(tmp) / "missing"), "status").returncode, 0)
        self.assertEqual(registry.git_probe("--version").returncode, 0)


if __name__ == "__main__":
    unittest.main()
