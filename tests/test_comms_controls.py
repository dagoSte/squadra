"""Check communication controls and empty plugin navigation with a simplified DOM."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import test_comms
import test_panel

ROOT = Path(__file__).resolve().parents[1]


class CommunicationControls(unittest.TestCase):
    def run_case(self, scenario, shell=False):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required for the dashboard contract')
        with tempfile.TemporaryDirectory(prefix='sq-comms-controls-') as folder:
            base = Path(folder)
            if shell:
                fixture = base / 'state.json'
                fixture.write_text(json.dumps(test_panel.PanelJavaScript.build(base)))
            else:
                builder = test_comms.CommsPage()
                self.addCleanup(builder.doCleanups)
                fixture = builder.fixture(base)
            script = 'comms_shell_contract.js' if shell else 'comms_controls_contract.js'
            result = subprocess.run([node, str(ROOT / 'tests' / script), str(ROOT / 'assets/dashboard.html'),
                                     str(fixture), scenario], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('passed: ' + scenario, result.stdout)

    def test_sticky_filters_search_active_state_and_reset(self):
        self.run_case('filters')

    def test_pair_table_selection_counts_focus_and_languages(self):
        self.run_case('pairs')

    def test_footer_credit_link_and_languages(self):
        self.run_case('footer', shell=True)

    def test_empty_plugin_page_keyboard_drawer_and_no_fetch(self):
        self.run_case('empty', shell=True)
