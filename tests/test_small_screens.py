"""Phone filter disclosure retains selection, focus, language and the single reset."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import test_comms

ROOT = Path(__file__).resolve().parents[1]


class SmallScreens(unittest.TestCase):
    def check_page(self, page, fixture):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node required; this contract must not be skipped")
        return subprocess.run([node, str(ROOT / "tests/small_screens_contract.js"), str(page), str(fixture)],
                              text=True, capture_output=True, timeout=30)

    def test_disclosure_and_full_width_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            builder = test_comms.CommsPage()
            try:
                fixture = builder.fixture(Path(folder))
                result = self.check_page(ROOT / "assets/dashboard.html", fixture)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("small screens contract passed", result.stdout)
            finally:
                builder.doCleanups()

    def test_removed_disclosure_state_and_width_override_are_detected(self):
        source = (ROOT / "assets/dashboard.html").read_text()
        mutants = [
            ('no-open-state', "filters.setAttribute('data-expanded',String(open))", "filters.setAttribute('data-expanded','false')"),
            ('narrow-plugin', '.sq-plugin-tagline,.sq-plugin-section{max-width:none}', '.sq-plugin-tagline,.sq-plugin-section{max-width:70ch}'),
        ]
        with tempfile.TemporaryDirectory() as folder:
            builder = test_comms.CommsPage()
            try:
                fixture = builder.fixture(Path(folder))
                for name, before, after in mutants:
                    with self.subTest(mutant=name):
                        self.assertEqual(source.count(before), 1)
                        page = Path(folder) / (name + '.html')
                        page.write_text(source.replace(before, after))
                        result = self.check_page(page, fixture)
                        self.assertNotEqual(result.returncode, 0, 'mutant survived')
                        self.assertIn('AssertionError', result.stderr)
                        self.assertNotIn('SyntaxError', result.stderr)
            finally:
                builder.doCleanups()
