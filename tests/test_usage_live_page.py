"""Conditional Usage updates keep the last measured page and its input state."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_usage_page import state_fixture

ROOT = Path(__file__).resolve().parents[1]


class UsageLivePage(unittest.TestCase):
    def run_contract(self, scenario):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required; the Usage live contract cannot be skipped')
        with tempfile.TemporaryDirectory(prefix='sq-usage-live-page-') as temporary:
            home = Path(temporary)
            fixture = home / 'state.json'
            fixture.write_text(json.dumps(state_fixture()), encoding='utf-8')
            result = subprocess.run([node, str(ROOT / 'tests/usage_live_contract.js'),
                                     str(ROOT / 'assets/dashboard.html'), str(fixture), scenario,
                                     str(ROOT / 'tests/fixtures/usage-prices.json')],
                                    env=os.environ | {'HOME': str(home), 'CODEX_HOME': str(home / 'codex'),
                                                      'CLAUDE_CONFIG_DIR': str(home / 'claude')},
                                    text=True, capture_output=True, timeout=25)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('usage live contract passed: ' + scenario, result.stdout)

    def test_js_usage_updates_with_identical_registry_state_and_conditional_headers(self):
        self.run_contract('independent')

    def test_js_usage_unchanged_busy_keep_numbers_form_focus_and_scroll(self):
        self.run_contract('envelopes')

    def test_js_usage_first_busy_stays_loading_and_explicit_retry_recovers(self):
        self.run_contract('firstBusy')

    def test_js_usage_invalid_revision_retry_serial_hidden_and_closed(self):
        self.run_contract('validationAndSerial')
