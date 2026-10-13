"""Check completion notices, audio preferences and accessible notification limits."""
from pathlib import Path
import shutil
import subprocess
import unittest

import test_plugin_about as about_fixture

ROOT = Path(__file__).resolve().parents[1]


class NotifyJavaScript(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        about_fixture.PluginAboutJavaScript.setUpClass.__func__(cls)

    def run_contract(self, scenario, page=None):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node required; dashboard contracts must never be skipped')
        return subprocess.run([node, str(ROOT / 'tests/notify_contract.js'),
                               str(page or ROOT / 'assets/dashboard.html'), str(self.fixture), scenario],
                              capture_output=True, text=True, timeout=30)

    def assert_contract(self, scenario):
        result = self.run_contract(scenario)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('notifications contract passed: ' + scenario, result.stdout)

    def test_notify_baseline_is_silent_and_empty(self):
        self.assert_contract('baseline')

    def test_notify_step_burst_and_final_completion(self):
        self.assert_contract('step_burst_complete')

    def test_notify_muting_persistence_and_storage_errors(self):
        self.assert_contract('mute_storage')

    def test_notify_gesture_audio_failures_and_hidden_tab(self):
        self.assert_contract('gesture_failures_hidden')

    def test_notify_toast_expiry_dismissal_stack_and_focus(self):
        self.assert_contract('toast_lifetime')

    def test_notify_literal_titles_and_both_languages(self):
        self.assert_contract('literal_i18n')

    def test_notify_recorded_done_only_and_reopened_work(self):
        self.assert_contract('transitions')

    def test_notify_mutants_fail_by_assertion(self):
        source = (ROOT / 'assets/dashboard.html').read_text()
        mutants = [
            ('no-baseline', 'if(!previous)return;', "if(!previous){notifyToast('notify.complete');return;}", 'baseline'),
            ('no-poll-hook', "if(typeof notifyPoll==='function')notifyPoll(state);", '', 'step_burst_complete'),
            ('per-task-burst', 'if(complete||done.length)notifySound(complete);', 'for(const t of done)notifySound(complete);', 'step_burst_complete'),
            ('mute-ignored', "if(!notifyEnabled||document.hidden||notifyContext?.state!=='running')return;", "if(document.hidden||notifyContext?.state!=='running')return;", 'mute_storage'),
            ('unsafe-title', 'toast.text.textContent=tr(toast.key,toast.vars)', 'toast.text.innerHTML=tr(toast.key,toast.vars)', 'literal_i18n'),
            ('long-toast', 'setTimeout(()=>notifyDismiss(toast),6000)', 'setTimeout(()=>notifyDismiss(toast),60000)', 'toast_lifetime'),
            ('unbounded-stack', 'while(notifyToasts.length>=3)', 'while(notifyToasts.length>=30)', 'toast_lifetime'),
            ('no-saved-preference', "notifyEnabled=window.localStorage.getItem('squadra-sounds')!=='off'", 'notifyEnabled=true', 'mute_storage'),
            ('historical-arrival', "previous.tasks.has(t.id)&&previous.tasks.get(t.id)!=='done'", "previous.tasks.get(t.id)!=='done'", 'transitions'),
        ]
        for scenario in sorted({row[3] for row in mutants}):
            self.assert_contract(scenario)
        for name, before, after, scenario in mutants:
            with self.subTest(mutant=name):
                self.assertEqual(source.count(before), 1, 'unique mutation seam: ' + name)
                page = self.folder / (name + '.html')
                page.write_text(source.replace(before, after, 1))
                result = self.run_contract(scenario, page)
                self.assertNotEqual(result.returncode, 0, 'mutant survived: ' + name)
                self.assertIn('AssertionError', result.stderr, result.stdout + result.stderr)
                self.assertNotIn('SyntaxError', result.stderr)
