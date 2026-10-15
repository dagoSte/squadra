"""Combined background audit, conditional state and detail-transfer boundaries."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import dashboard
from registry import Error, Store
import test_dashboard_performance as fixtures
import test_panel as panel


class AuditTransferAPI(unittest.TestCase):
    setUp = fixtures.DashboardPerformance.setUp
    stop_server = fixtures.DashboardPerformance.stop_server
    get = fixtures.DashboardPerformance.get
    stamp = fixtures.DashboardPerformance.stamp
    task_id = fixtures.DashboardPerformance.task_id
    run_id = fixtures.DashboardPerformance.run_id
    source_path = fixtures.DashboardPerformance.source_path
    source_hash = fixtures.DashboardPerformance.source_hash

    def compact(self, token=''):
        status, _, raw = self.get(headers={'X-Squadra-View': 'compact', 'X-Squadra-State-Token': token})
        return status, json.loads(raw)

    def test_audit_completion_changes_token_without_registry_event_and_keeps_details(self):
        entered, release = threading.Event(), threading.Event()
        original = Store.audit
        def delayed(store, cancelled=None):
            entered.set()
            while not release.wait(.01):
                if cancelled and cancelled():
                    raise Error('Registry audit cancelled')
            return original(store, cancelled=cancelled)
        with patch.object(Store, 'audit', delayed):
            try:
                status, first = self.compact()
                self.assertEqual(status, 200, first)
                self.assertTrue(entered.wait(2))
                self.assertEqual(first['audit'], {'status': 'running'})
                status, same = self.compact(first['change_token'])
                self.assertEqual(status, 200, same)
                self.assertTrue(same['not_modified'])
                self.assertEqual(same['change_token'], first['change_token'])
                code, _, raw = self.get('/api/detail/view', {'X-Squadra-State-Head': first['registry_head']['hash'],
                    'X-Squadra-Section': 'history', 'X-Squadra-Record': ''})
                self.assertEqual(code, 200, raw)
                self.assertEqual(json.loads(raw)['patch']['events'], self.store.events()[-120:])
                code, _, raw = self.get('/api/detail/task', {'X-Squadra-Record': self.task_id})
                self.assertEqual(code, 200, raw)
                self.assertEqual(json.loads(raw)['candidate']['files'], self.fingerprint['files'])
            finally:
                release.set()
            deadline = time.monotonic() + 5
            while True:
                status, done = self.compact(first['change_token'])
                self.assertEqual(status, 200, done)
                if done.get('audit', {}).get('status') == 'complete':
                    break
                self.assertLess(time.monotonic(), deadline, done)
                time.sleep(.01)
            self.assertNotEqual(done['change_token'], first['change_token'])
            self.assertEqual(done['registry_head'], first['registry_head'])
            self.assertEqual(done['event_count'], first['event_count'])
            status, unchanged = self.compact(done['change_token'])
            self.assertEqual(status, 200, unchanged)
            self.assertTrue(unchanged['not_modified'])
            self.assertNotIn('tasks', unchanged)

    def test_latched_failure_refuses_matching_token_and_same_head_detail(self):
        entered, release = threading.Event(), threading.Event()
        def failed(store, cancelled=None):
            entered.set()
            release.wait(5)
            raise Error('Synthetic combined audit failure')
        with patch.object(Store, 'audit', failed):
            try:
                status, first = self.compact()
                self.assertEqual(status, 200, first)
                self.assertTrue(entered.wait(2))
                self.assertEqual(first['audit'], {'status': 'running'})
            finally:
                release.set()
            deadline = time.monotonic() + 5
            while True:
                status, value = self.compact(first['change_token'])
                if status == 409:
                    break
                self.assertLess(time.monotonic(), deadline, value)
                time.sleep(.01)
            self.assertEqual(value, {'error': 'registry_invalid'})
            self.assertNotIn('not_modified', value)
            code, _, raw = self.get('/api/detail/view', {'X-Squadra-State-Head': first['registry_head']['hash'],
                'X-Squadra-Section': 'story', 'X-Squadra-Record': self.task_id})
            self.assertEqual(code, 409, raw)
            self.assertNotIn('patch', json.loads(raw))
            self.assertEqual(self.compact(first['change_token'])[0], 409)


class AuditTransferDOM(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='sq-audit-transfer-')
        cls.fixture = Path(cls.temp.name) / 'state.json'
        cls.fixture.write_text(json.dumps(panel.PanelJavaScript.build(Path(cls.temp.name))))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def contract(self, scenario):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required; JS contracts cannot be skipped')
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([node, str(root / 'tests/dashboard_audit_transfer_contract.js'),
            str(root / 'assets/dashboard.html'), str(self.fixture), scenario],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('audit transfer contract passed: ' + scenario, result.stdout)

    def test_audit_transition_preserves_hydrated_same_head_details_and_polling(self):
        self.contract('transition')

    def test_failure_discards_late_story_body_and_stops_polling(self):
        self.contract('late_story')

    def test_unchanged_state_keeps_usage_charts_and_balance_polling(self):
        self.contract('charts_poll')
