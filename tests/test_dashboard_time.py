"""Elapsed dashboard times use the verified history and the existing refresh."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import test_panel as panel
from registry import Store

ROOT = Path(__file__).resolve().parents[1]


class DashboardTime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='sq-time-')
        cls.base = Path(cls.temp.name)
        cls.state = panel.PanelJavaScript.build(cls.base)
        cls.fixture = cls.base / 'state.json'
        cls.fixture.write_text(json.dumps(cls.state))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def contract(self, scenario):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node required; dashboard JS contracts cannot be skipped')
        result = subprocess.run([node, str(ROOT / 'tests/time_contract.js'),
                                 str(ROOT / 'assets/dashboard.html'), str(self.fixture), scenario],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('time contract passed: ' + scenario, result.stdout)

    def test_api_project_start_comes_from_the_full_verified_history(self):
        events = Store(self.base / 'record').events()
        self.assertGreater(len(events), 120)
        self.assertEqual(len(self.state['events']), 120)
        self.assertNotIn(events[0], self.state['events'])
        self.assertEqual(self.state.get('project_started'), events[0]['utc'])
        self.assertNotEqual(self.state['project_started'], self.state['activity']['since'])

    def test_dashboard_time_rounding_matches_board_in_both_languages(self):
        self.contract('rounding')

    def test_dashboard_time_done_cards_table_details_and_story(self):
        self.contract('views')

    def test_dashboard_time_project_refresh_keeps_closed_totals_and_io(self):
        self.contract('refresh')

    def test_dashboard_time_phases_count_elapsed_intervals_only_once(self):
        self.contract('phases')

    def test_dashboard_time_plan_read_wait_is_not_construction(self):
        self.contract('plan_wait')

    def test_dashboard_time_missing_evidence_never_invents_a_hint(self):
        self.contract('missing')

    def test_dashboard_time_each_fixed_hint_in_both_languages(self):
        self.contract('hints')
