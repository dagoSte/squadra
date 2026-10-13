"""Check long-text previews and reading dialogs in the dashboard."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import test_panel as panel

ROOT = Path(__file__).resolve().parents[1]


class ReadDialogJavaScript(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='squadra-read-')
        cls.fixture = Path(cls.temp.name) / 'state.json'
        cls.fixture.write_text(json.dumps(panel.PanelJavaScript.build_cards(Path(cls.temp.name) / 'fixture')))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def contract(self, scenario):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node required; dashboard JS contracts cannot be skipped')
        result = subprocess.run([node, str(ROOT / 'tests/read_contract.js'),
                                 str(ROOT / 'assets/dashboard.html'), str(self.fixture), scenario],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('read contract passed: ' + scenario, result.stdout)

    def test_dashboard_read_three_lines_measured_at_different_widths(self):
        self.contract('measured')

    def test_dashboard_read_ignores_glyph_ink_but_keeps_the_fourth_line(self):
        self.contract('ink')

    def test_dashboard_read_keeps_chart_axes_plots_and_tables_unchanged(self):
        self.contract('charts')

    def test_dashboard_read_modal_literal_text_keyboard_and_poll_focus(self):
        self.contract('modal')

    def test_dashboard_read_languages_and_no_extra_requests(self):
        self.contract('languages')

    def test_dashboard_read_all_views_and_semantic_cells(self):
        self.contract('views')

    def test_dashboard_read_closed_details_measure_on_open(self):
        self.contract('closed')

    def test_dashboard_read_communication_body_and_polling(self):
        self.contract('comms')

    def test_dashboard_read_plugin_overview_items_and_metadata(self):
        self.contract('plugin')
