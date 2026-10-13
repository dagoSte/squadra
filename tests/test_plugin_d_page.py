"""Explicit plugin draft selection, saved requests, and asynchronous page boundaries."""
from pathlib import Path
import shutil
import subprocess
import unittest

import test_plugin_about as about_fixture

ROOT = Path(__file__).resolve().parents[1]


class PluginDraftActionsJavaScript(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        about_fixture.PluginAboutJavaScript.setUpClass.__func__(cls)

    def run_contract(self, scenario, page=None):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node required; dashboard contracts must never be skipped')
        return subprocess.run([node, str(ROOT / 'tests/plugin_d_page_contract.js'),
                               str(page or ROOT / 'assets/dashboard.html'), str(self.fixture), scenario],
                              capture_output=True, text=True, timeout=30)

    def assert_contract(self, scenario):
        result = self.run_contract(scenario)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('plugin draft actions contract passed: ' + scenario, result.stdout)

    def test_dashboard_draft_action_eligibility_and_legacy_plugin(self):
        self.assert_contract('eligibility')

    def test_dashboard_draft_action_explicit_wire_and_saved_not_sent(self):
        self.assert_contract('explicit_wire')

    def test_dashboard_draft_action_pending_reload_replace_and_cancel(self):
        self.assert_contract('pending_replace_cancel')

    def test_dashboard_draft_action_refresh_prunes_without_selecting_arrivals(self):
        self.assert_contract('stale_refresh')

    def test_dashboard_draft_action_single_flight_and_changed_context(self):
        self.assert_contract('single_flight_context')

    def test_dashboard_draft_action_uncertain_replies_never_confirm_or_retry(self):
        self.assert_contract('uncertain_replies')

    def test_dashboard_draft_action_fixed_errors_in_both_languages(self):
        self.assert_contract('fixed_errors')

    def test_dashboard_draft_action_keyboard_focus_literal_text_and_polling(self):
        self.assert_contract('focus_languages_polling')

    def test_dashboard_draft_action_old_state_cannot_undo_save_or_cancel(self):
        self.assert_contract('stale_state_after_write')

    def test_dashboard_draft_action_mutants_fail_by_assertion(self):
        source = (ROOT / 'assets/dashboard.html').read_text()
        mutants = [
            ('eligibility', "if(av&&item.selectable===true&&item.status==='draft')", 'if(av&&item.selectable)', 'eligibility'),
            ('select-arrivals', 'v.selected=new Set([...v.selected].filter(id=>v.eligible.has(id)))', 'v.selected=new Set(v.eligible)', 'stale_refresh'),
            ('double-submit', 'if(pluginActionFlight||!pluginActionCurrent(v)||!pluginPanelVisible()||v.invalid)return', 'if(!pluginActionCurrent(v)||!pluginPanelVisible()||v.invalid)return', 'single_flight_context'),
            ('registry-context', 'if(pluginActionView?.context!==context)', 'if(!pluginActionView)', 'single_flight_context'),
            ('false-success', 'response.status!==200||reply.saved!==true', 'response.status!==200', 'uncertain_replies'),
            ('false-cancel', "if(saved!==null)throw Error('unconfirmed cancellation')", '', 'uncertain_replies'),
            ('automatic-send', 'function pluginActionsBefore(){', 'function pluginActionsBefore(){if(pluginActionView?.selected.size&&!window.mutantSent){window.mutantSent=true;pluginActionSend(pluginActionView,false);}', 'focus_languages_polling'),
            ('extra-poll', 'function pluginActionsBefore(){', "function pluginActionsBefore(){if(pluginActionView)api('/api/state');", 'focus_languages_polling'),
            ('stale-state', 'pluginActionReads.get(state)===pluginActionClock', 'true', 'stale_state_after_write'),
        ]
        for name, before, after, scenario in mutants:
            with self.subTest(mutant=name):
                self.assertEqual(source.count(before), 1, 'mutation seam must be unique')
                mutant = self.folder / (name + '.html')
                mutant.write_text(source.replace(before, after, 1))
                result = self.run_contract(scenario, mutant)
                self.assertNotEqual(result.returncode, 0, 'mutant survived: ' + name)
                self.assertIn('AssertionError', result.stderr, result.stdout + result.stderr)
