"""R315-FIX.POSTA: the Windows tab shows who follows its mail, from the same state reply, without writes."""
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


class ListeningRoute(unittest.TestCase):
    setUp = routes.UsageRoutes.setUp
    frozen = routes.UsageRoutes.frozen
    start_server = routes.UsageRoutes.start_server

    def test_windows_tab_reply_carries_listening_without_registry_writes(self):
        server, token = self.start_server()
        before = self.frozen()

        def get(path, headers=None):
            req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}' + path,
                                         headers={'X-Squadra-Token': token, **(headers or {})})
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.load(response)
        reply = get('/api/state', {'X-Squadra-Tiredness': '1'})
        self.assertIn('listening', reply)
        self.assertTrue(reply['listening'])
        self.assertTrue(all(row['state'] in {'listening', 'stopped', 'never', 'not-visible'}
                            for row in reply['listening'].values()))
        self.assertNotIn('listening', get('/api/state'))
        self.assertEqual(self.frozen(), before)


class ListeningPage(unittest.TestCase):
    def test_windows_tab_shows_listening_and_stopped_listeners(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required for the dashboard contract')
        prefix = (ROOT / 'tests/panel_contract.js').read_text().split('const scenarios={')[0]
        code = prefix + r'''
(async()=>{
 const app=await boot();await app.tab('Finestre');await settle();
 const content=app.doc.getElementById('content');
 assert.ok(one(content,e=>e.getAttribute('data-listening')==='windows'));
 assert.match(text(content),/Ascolto della posta/);
 assert.match(text(content),/in ascolto/);assert.match(text(content),/ascolto fermo da 12 min/);
 app.run("setLanguage('en')");await settle();
 assert.match(text(app.doc.getElementById('content')),/listener stopped 12 min ago/);
 console.log('listening page passed');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        state = state_fixture()
        for name, identity in (('C', 'window-C'), ('D', 'window-D')):
            state['agents'][name] = {'id': name, 'identity': identity, 'role': 'executor',
                                     'client': 'claude', 'session': '', 'model': '', 'skills': []}
        state['listening'] = {'C': {'state': 'listening', 'since': '2026-11-09T10:00:00+00:00'},
                              'D': {'state': 'stopped', 'since': '2026-11-09T09:48:00+00:00', 'minutes': 12}}
        with tempfile.TemporaryDirectory(prefix='sq-listen-page-') as tmp:
            folder = Path(tmp)
            fixture = folder / 'state.json'
            fixture.write_text(json.dumps(state))
            script = folder / 'listening.js'
            script.write_text(code)
            done = subprocess.run([node, str(script), str(ROOT / 'assets/dashboard.html'), str(fixture)],
                                  capture_output=True, text=True, timeout=30, env={**os.environ, 'HOME': str(folder)})
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertIn('listening page passed', done.stdout)


if __name__ == '__main__':
    unittest.main()
