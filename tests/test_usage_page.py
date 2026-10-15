"""Usage renders only normalized counters and keeps optional prices in the browser."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from registry import initial
from panel import activity


def state_fixture():
    value = initial()
    value.update(workspace={'name': 'Synthetic usage', 'project': 'Synthetic project'},
                 graph={'nodes': [], 'edges': []}, progress={}, events=[],
                 activity=activity([], '2026-10-15T11:30:00+00:00'),
                 attention={'for_owner': [], 'decided': [], 'waiting': [], 'next_steps': []},
                 waits=[], presence=[], plugins={'plugins': [], 'refused': [], 'key': 'a' * 64})
    return value


class UsagePage(unittest.TestCase):
    def run_contract(self, scenario):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required; the Usage DOM contract cannot be skipped')
        with tempfile.TemporaryDirectory(prefix='sq-usage-page-') as temporary:
            home = Path(temporary)
            fixture = home / 'state.json'
            fixture.write_text(json.dumps(state_fixture()), encoding='utf-8')
            result = subprocess.run([node, str(ROOT / 'tests/usage_contract.js'),
                                     str(ROOT / 'assets/dashboard.html'), str(fixture), scenario,
                                     str(ROOT / 'tests/fixtures/usage-prices.json')],
                                    env=os.environ | {'HOME': str(home), 'CODEX_HOME': str(home / 'codex'),
                                                      'CLAUDE_CONFIG_DIR': str(home / 'claude')},
                                    text=True, capture_output=True, timeout=25)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('usage contract passed: ' + scenario, result.stdout)

    def test_js_usage_charts_tables_and_languages(self):
        self.run_contract('charts')

    def test_js_usage_unknown_zero_partial_and_estimated(self):
        self.run_contract('coverage')

    def test_js_usage_serial_open_only_polling_focus_and_retry(self):
        self.run_contract('polling')

    def test_js_usage_local_prices_preserve_balance_and_shared_arithmetic(self):
        self.run_contract('prices')

    def test_js_usage_past_quota_history_keeps_values_without_current_advice(self):
        self.run_contract('quotaHistory')

    def test_js_usage_advice_evidence_and_content_canaries(self):
        self.run_contract('privacy')

    def test_js_usage_form_drafts_survive_refresh_language_and_storage_failure(self):
        self.run_contract('form')
