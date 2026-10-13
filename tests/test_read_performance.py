"""Keep dashboard text measurement grouped and limited to visible content."""
from pathlib import Path
import shutil
import subprocess
import unittest

import test_read_dialog as read_tests

ROOT = Path(__file__).resolve().parents[1]


class ReadPerformanceJavaScript(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        read_tests.ReadDialogJavaScript.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        read_tests.ReadDialogJavaScript.tearDownClass.__func__(cls)

    def contract(self, scenario):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node required; dashboard JS contracts cannot be skipped')
        result = subprocess.run(
            [node, str(ROOT / 'tests/read_performance_contract.js'),
             str(ROOT / 'assets/dashboard.html'), str(self.fixture), scenario],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('read performance contract passed: ' + scenario, result.stdout)

    def test_dashboard_read_groups_all_geometry_before_control_updates(self):
        self.contract('batch')

    def test_dashboard_read_skips_hidden_ancestors_and_measures_opened_details(self):
        self.contract('hidden')

    def test_dashboard_read_groups_semantic_cell_wrapping_and_reused_text(self):
        self.contract('cells')

    def test_dashboard_read_only_measures_an_open_story_dialog(self):
        self.contract('dialog')
