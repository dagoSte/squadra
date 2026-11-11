"""The Windows view shows and refreshes observed settings without hiding its existing columns."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_usage_page import state_fixture

ROOT = Path(__file__).resolve().parents[1]


class ModelEffortPage(unittest.TestCase):
    def test_window_settings_are_visible_in_both_languages_and_update_without_state_changes(self):
        node = shutil.which('node')
        self.assertIsNotNone(node)
        prefix = (ROOT / 'tests/panel_contract.js').read_text().split('const scenarios={')[0]
        code = prefix + r'''
(async()=>{
 const app=await boot();await app.tab('Finestre');await settle();
 let content=app.doc.getElementById('content');
 assert.match(text(content),/Modello ed effort rilevati/);assert.match(text(content),/gpt-6-astra.*max/);
 assert.match(text(content),/Motivo non esposto dal client/);assert.match(text(content),/Turno Codex/);
 app.run("setLanguage('en')");await settle();content=app.doc.getElementById('content');
 assert.match(text(content),/Observed model and effort/);assert.match(text(content),/Reason not exposed by client/);
 app.fixture.model_configurations.A.effort='high';await app.refresh();
 assert.match(text(app.doc.getElementById('content')),/gpt-6-astra.*high/);
 console.log('model effort windows passed');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        state = state_fixture()
        state['agents']['A'] = {'id': 'A', 'identity': 'real-window', 'role': 'executor', 'client': 'codex',
                                'session': '', 'model': '', 'skills': []}
        state['model_configurations'] = {'A': {'model': 'gpt-6-astra', 'effort': 'max',
            'source': 'codex_turn_context', 'observed_at': '2026-10-15T11:30:00Z',
            'switch': {'model': 'gpt-6-luna', 'effort': 'low', 'reason': 'client_reason_unavailable'}}}
        with tempfile.TemporaryDirectory(prefix='sq-model-page-') as tmp:
            folder = Path(tmp)
            fixture, script = folder / 'state.json', folder / 'models.js'
            fixture.write_text(json.dumps(state))
            script.write_text(code)
            run = subprocess.run([node, str(script), str(ROOT / 'assets/dashboard.html'), str(fixture)],
                                 capture_output=True, text=True, timeout=25,
                                 env={**os.environ, 'HOME': str(folder)})
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn('model effort windows passed', run.stdout)


if __name__ == '__main__':
    unittest.main()
