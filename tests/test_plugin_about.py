"""Check plugin introductions with synthetic data and deliberate code defects."""
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dashboard import make_server
from registry import Store

ROOT = Path(__file__).resolve().parents[1]


class PluginAboutJavaScript(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='sq-plugin-about-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.folder = Path(cls.temp.name)
        root = cls.folder / 'registry'
        store = Store(root, create=True)
        try:
            store.append('init', 'system', {'name': 'Plugin example', 'project': str(cls.folder),
                         'temporary': True, 'limits': {'plan': 3, 'code': 2, 'build': 3}})
        finally:
            store.close()
        home = cls.folder / 'home'
        home.mkdir()
        with patch.dict(os.environ, {'HOME': str(home)}):
            server, token = make_server(root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            try:
                conn.request('GET', '/api/state', headers={'X-Squadra-Token': token})
                response = conn.getresponse()
                if response.status != 200:
                    raise AssertionError('Synthetic dashboard state unavailable')
                cls.fixture = cls.folder / 'state.json'
                cls.fixture.write_bytes(response.read())
            finally:
                conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def run_contract(self, scenario, page=None):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node required; dashboard contracts must never be skipped')
        return subprocess.run([node, str(ROOT / 'tests/plugin_about_contract.js'),
                               str(page or ROOT / 'assets/dashboard.html'), str(self.fixture), scenario],
                              capture_output=True, text=True, timeout=30)

    def assert_contract(self, scenario):
        result = self.run_contract(scenario)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('plugin about contract passed: ' + scenario, result.stdout)

    def test_dashboard_about_defaults_and_declared_tabs(self):
        self.assert_contract('defaults')

    def test_dashboard_about_keyboard_aria_and_focus(self):
        self.assert_contract('keyboard')

    def test_dashboard_about_hostile_text_languages_and_themes(self):
        self.assert_contract('literal_languages')

    def test_dashboard_about_polling_and_late_responses(self):
        self.assert_contract('polling')

    def test_dashboard_about_settings_drafts_and_secret_cleanup(self):
        self.assert_contract('settings')

    def test_dashboard_about_absence_preserves_existing_page(self):
        self.assert_contract('fallback')

    def test_dashboard_about_manifest_changes_and_reentry(self):
        self.assert_contract('lifecycle')

    def test_dashboard_about_mutants_fail_by_assertion(self):
        source = (ROOT / 'assets/dashboard.html').read_text()
        mutants = [
            ('unsafe-text', "function pluginText(tag,text,cls){const e=document.createElement(tag);e.textContent=text", "function pluginText(tag,text,cls){const e=document.createElement(tag);e.innerHTML=text", 'literal_languages'),
            ('wrong-default', "pluginView.section=p.about?'overview':null", "pluginView.section=p.about?'memories':null", 'defaults'),
            ('missing-tablist', "tabs.setAttribute('role','tablist')", "tabs.setAttribute('role','group')", 'defaults'),
            ('missing-selection', "b.setAttribute('aria-selected',String(section===pluginView.section))", "b.setAttribute('aria-selected','false')", 'keyboard'),
            ('hidden-polling', "else if(pluginPanelVisible()){pluginView.loading", "else if(pluginSelected()){pluginView.loading", 'polling'),
            ('english-only', "function pluginLocal(value){return value?.[UI_LANG]||''}", "function pluginLocal(value){return value?.en||''}", 'literal_languages'),
            ('no-legacy-panel', "(!pluginSections().length||pluginView.section===pluginResultSection())", "(pluginView.section===pluginResultSection())", 'fallback'),
        ]
        for name, before, after, scenario in mutants:
            with self.subTest(mutant=name):
                self.assertEqual(source.count(before), 1, 'unique mutation seam: ' + name)
                page = self.folder / (name + '.html')
                page.write_text(source.replace(before, after))
                result = self.run_contract(scenario, page)
                self.assertNotEqual(result.returncode, 0, 'mutant survived: ' + name)
                self.assertIn('AssertionError', result.stderr, result.stdout + result.stderr)
                self.assertNotIn('SyntaxError', result.stderr)
