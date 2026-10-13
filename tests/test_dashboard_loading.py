"""Loading and refresh states retain useful data and bound stalled reads."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import test_panel as panel

ROOT = Path(__file__).resolve().parents[1]


class DashboardLoading(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='sq-loading-')
        cls.fixture = Path(cls.temp.name) / 'state.json'
        cls.fixture.write_text(json.dumps(panel.PanelJavaScript.build(Path(cls.temp.name))))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def contract(self, case):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required; JS contracts cannot be skipped')
        result = subprocess.run([node, str(ROOT / 'tests/dashboard_loading_contract.js'),
                                 str(ROOT / 'assets/dashboard.html'), str(self.fixture), case],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('loading contract passed: ' + case, result.stdout)

    def test_initial_loading_is_accessible_and_bilingual(self):
        self.contract('initial')

    def test_empty_sections_wait_for_first_state_and_survive_later_refreshes(self):
        self.contract('sections')

    def test_timeout_includes_body_and_retry_recovers(self):
        self.contract('timeout')

    def test_refresh_keeps_data_pauses_hidden_and_never_overlaps(self):
        self.contract('refresh')

    def test_compact_search_waits_for_complete_results_and_ignores_old_reply(self):
        self.contract('search')

    def test_full_technical_data_is_available_on_demand(self):
        self.contract('details')
