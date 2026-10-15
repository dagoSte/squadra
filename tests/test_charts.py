"""Usage charts: identical tables to the 9b4f6d2 base, motion only on change, reduced motion, exact values on focus."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from registry import Store
from dashboard_test_support import make_server
from test_reads_value import seed


class UsageCharts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='sq-charts-')
        base = Path(cls.temp.name).resolve()
        store = Store(base / 'registry', create=True)
        seed(store, base)
        store.project()
        server, token = make_server(store.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            req = urllib.request.Request('http://127.0.0.1:%d/api/state' % server.server_port, headers={'X-Squadra-Token': token})
            with urllib.request.urlopen(req, timeout=10) as result:
                (base / 'state.json').write_bytes(result.read())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            store.close()
        cls.state = base / 'state.json'

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_contract(self, scenario):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required; dashboard contracts cannot be skipped')
        result = subprocess.run([node, str(ROOT / 'tests/charts_contract.js'), str(ROOT / 'assets/dashboard.html'),
                                 str(self.state), scenario], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('charts contract passed: ' + scenario, result.stdout)
        return result.stdout

    def test_js_charts_same_tables_as_the_base_drawing(self):
        out = self.run_contract('tables')
        line = next(l for l in out.splitlines() if l.startswith('TABLES '))
        expected = json.loads((ROOT / 'tests/fixtures/charts-usage-tables-9b4f6d2.json').read_text(encoding='utf-8'))
        self.assertEqual(json.loads(line[7:]), expected, 'drawing changes, data does not')

    def test_js_charts_kinds_rounded_gradient_ring_and_unbroken_monotone_line(self):
        self.run_contract('kinds')

    def test_js_charts_motion_on_first_draw_and_change_only(self):
        self.run_contract('motion')

    def test_js_charts_reduced_motion_same_picture_without_motion(self):
        self.run_contract('reduced')

    def test_js_charts_hover_and_keyboard_show_exact_values(self):
        self.run_contract('focus')

    def test_js_charts_inline_only_theme_tokens_and_phone_layout(self):
        self.run_contract('inline')


if __name__ == '__main__':
    unittest.main()
