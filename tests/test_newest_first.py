"""Every dashboard list and picker over time shows the newest first; Node is required."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import test_panel as panel

ROOT = Path(__file__).resolve().parents[1]


class NewestFirst(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='sq-newest-first-')
        base = Path(cls.temp.name)
        (base / 'home').mkdir()
        (base / 'record').mkdir()
        with patch.dict(os.environ, {'HOME': str(base / 'home')}):
            state = panel.PanelJavaScript.build(base / 'record')
        cls.fixture = base / 'state.json'
        cls.fixture.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def contract(self, scenario):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node required; dashboard JS contracts cannot be skipped')
        result = subprocess.run([node, str(ROOT / 'tests/newest_first_contract.js'), str(ROOT / 'assets/dashboard.html'),
                                 str(self.fixture), scenario], capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('newest first contract passed: ' + scenario, result.stdout)

    def test_task_recency_is_the_latest_lifecycle_time_on_the_record(self):
        self.contract('helper')

    def test_task_lists_and_pickers_show_the_most_recent_task_first(self):
        self.contract('tasks')

    def test_communications_task_picker_shows_the_most_recent_task_first(self):
        self.contract('comms')

    def test_events_story_decisions_waits_and_imports_show_the_latest_first(self):
        self.contract('events')

    def test_explicit_sorts_time_axes_and_window_order_stay(self):
        self.contract('kept')


if __name__ == '__main__':
    unittest.main()
