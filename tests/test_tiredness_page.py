"""Real dashboard routes and numeric advisory rendering for window handovers."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
import test_usage_routes as routes
from test_usage_page import state_fixture


class TirednessRoutes(unittest.TestCase):
    setUp = routes.UsageRoutes.setUp
    frozen = routes.UsageRoutes.frozen
    start_server = routes.UsageRoutes.start_server

    def test_routes_show_current_context_without_registry_writes_or_claude_percentage(self):
        server, token = self.start_server()
        before = self.frozen()
        def get(path, headers=None):
            req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}' + path,
                                         headers={'X-Squadra-Token': token, **(headers or {})})
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.load(response)
        fatigue = get('/api/state', {'X-Squadra-Tiredness': '1'})
        row = fatigue['tiredness']['tokens']
        self.assertEqual(row['context_tokens'], 950)
        self.assertNotIn('context_fraction', row)
        self.assertTrue(row['tired'])
        headers = {'X-Squadra-Tiredness': '1', 'X-Squadra-View': 'compact', 'X-Squadra-State-Token': ''}
        compact = get('/api/state', headers)
        headers['X-Squadra-State-Token'] = compact['change_token']
        unchanged = get('/api/state', headers)
        self.assertTrue(unchanged['not_modified'])
        self.assertEqual(unchanged['tiredness']['tokens']['context_tokens'], 950)
        self.assertNotIn('tiredness', get('/api/state'))
        balance = get('/api/balance')
        measured = next(w for w in balance['windows'] if w['identity'] == 'claude-window-' + routes.SESSION)
        self.assertEqual(measured['tiredness']['context_tokens'], 950)
        self.assertEqual(self.frozen(), before)
        self.assertNotIn(routes.CANARY, json.dumps([fatigue, balance]))


class TirednessPage(unittest.TestCase):
    def test_dom_notices_in_windows_and_balance_and_no_percentage_without_window(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required for the dashboard contract')
        prefix = (ROOT / 'tests/panel_contract.js').read_text().split('const scenarios={')[0]
        code = prefix + r'''
(async()=>{
 const app=await boot();await app.tab('Finestre');await settle();
 const content=app.doc.getElementById('content');
 assert.ok(one(content,e=>e.getAttribute('data-tiredness')==='windows'));
 assert.match(text(content),/conviene ripartire/);assert.match(text(content),/950 token/);
 assert.match(text(content),/handover prepare/);
 const sample={age_hours:9,context_tokens:950,tired:true,reasons:['age']};
 let line=app.run('tiredText('+JSON.stringify(sample)+')');assert.doesNotMatch(line,/%/);
 sample.context_fraction=.6;sample.context_window=100000;
 line=app.run('tiredText('+JSON.stringify(sample)+')');assert.match(line,/60%/);
 app.run("setLanguage('en')");await settle();
 line=app.run('tiredText('+JSON.stringify(sample)+')');assert.match(line,/consider restarting/);assert.match(line,/context window/);
 app.run('const testBalance={windows:[{identity:"window-C",roles:["executor"],quota:{},tokens_per_hour:null,owned_open:0,read_steps:0,available:true,tiredness:'+JSON.stringify(sample)+'}]}');
 const balance=app.run('balanceWindowsPlain(testBalance)');
 assert.ok(one(balance,e=>e.getAttribute('data-tiredness')==='balance'));assert.match(text(balance),/consider restarting/);
 const count=app.fetched.filter(x=>x==='/api/tiredness').length;await app.refresh();await settle();
 assert.equal(app.fetched.filter(x=>x==='/api/tiredness').length,count,'no extra request when opening the window tab');
 console.log('tiredness page passed');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        state = state_fixture()
        state['tiredness'] = {'C': {'age_hours': 9, 'context_tokens': 950, 'tired': True, 'reasons': ['age']}}
        state['agents']['C'] = {'id': 'C', 'identity': 'window-C', 'role': 'executor',
                                'client': 'claude', 'session': routes.SESSION, 'model': '', 'skills': []}
        with tempfile.TemporaryDirectory(prefix='sq-tired-page-') as tmp:
            folder = Path(tmp)
            fixture = folder / 'state.json'
            fixture.write_text(json.dumps(state))
            script = folder / 'tiredness.js'
            script.write_text(code)
            done = subprocess.run([node, str(script), str(ROOT / 'assets/dashboard.html'), str(fixture)],
                                  capture_output=True, text=True, timeout=30,
                                  env={**os.environ, 'HOME': str(folder)})
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertIn('tiredness page passed', done.stdout)


if __name__ == '__main__':
    unittest.main()
