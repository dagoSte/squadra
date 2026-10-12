"""Check that closed task cards are measured when opened, without a refresh."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import test_panel as panel

ROOT = Path(__file__).resolve().parents[1]


class ClosedCards(unittest.TestCase):
    """Model hidden details as having no layout size; Node is required."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="squadra-closed-cards-")
        cls.fixture = Path(cls.temp.name) / "cards.json"
        cls.fixture.write_text(json.dumps(panel.PanelJavaScript.build_cards(Path(cls.temp.name) / "cards"), ensure_ascii=False), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_js_opening_the_closed_group_measures_its_cards_on_the_next_frame(self):
        node = shutil.which("node")
        if not node:
            self.fail("Node is required: the closed-group contract was not measured")
        argv = [node, str(ROOT / "tests/closed_cards_contract.js"), str(ROOT / "assets/dashboard.html"), str(self.fixture), "closed"]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=120, env=os.environ)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("closed cards contract passed: closed", result.stdout)


if __name__ == "__main__":
    unittest.main()
