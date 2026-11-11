"""The same verified window settings reach CLI, Usage, Balance and conditional Windows replies."""
import json
import unittest
import urllib.request

import test_usage_routes as support

SESSION = '87654321-1234-4567-8123-123456789abc'


class ModelEffortRoutes(unittest.TestCase):
    setUp = support.UsageRoutes.setUp
    frozen = support.UsageRoutes.frozen
    start_server = support.UsageRoutes.start_server

    def get(self, server, token, path, headers=None):
        request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}' + path,
                                        headers={'X-Squadra-Token': token, **(headers or {})})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

    def test_window_settings_update_without_registry_changes_or_content_exposure(self):
        self.store.append('agent', 'observed', {'id': 'observed', 'identity': 'codex-thread-' + SESSION,
                          'role': 'executor', 'client': 'codex', 'model': '', 'session': SESSION,
                          'repo': str(self.base), 'skills': []})
        source = self.home / '.codex/sessions/2026/11/11' / ('rollout-' + SESSION + '.jsonl')
        source.parent.mkdir(parents=True)
        rows = [{'type': 'session_meta', 'payload': {'id': SESSION}},
                {'type': 'turn_context', 'timestamp': '2026-10-15T11:00:00Z',
                 'payload': {'model': 'gpt-6-luna', 'effort': 'low', 'instructions': support.CANARY}}]
        source.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        server, token = self.start_server()
        before = self.frozen()
        api = self.get(server, token, '/api/usage')
        window = next(w for w in api['windows'] if w['identity'] == 'codex-thread-' + SESSION)
        self.assertEqual(window['configuration']['effort'], 'low')
        first = self.get(server, token, '/api/state', {'X-Squadra-Tiredness': '1', 'X-Squadra-View': 'compact',
                                                     'X-Squadra-State-Token': ''})
        self.assertEqual(first['model_configurations']['observed'], window['configuration'])
        rows[-1]['timestamp'] = '2026-10-15T11:01:00Z'
        rows[-1]['payload']['effort'] = 'max'
        with source.open('a') as stream:
            stream.write(json.dumps(rows[-1]) + '\n')
        again = self.get(server, token, '/api/state', {'X-Squadra-Tiredness': '1',
                         'X-Squadra-View': 'compact', 'X-Squadra-State-Token': first['change_token']})
        self.assertTrue(again['not_modified'])
        self.assertEqual(again['model_configurations']['observed']['effort'], 'max')
        balanced = self.get(server, token, '/api/balance')
        window = next(w for w in balanced['windows'] if 'observed' in w['seats'])
        self.assertEqual(window['configuration']['effort'], 'max')
        self.assertNotIn('model_configurations', self.get(server, token, '/api/state'))
        self.assertNotIn(support.CANARY, json.dumps([api, first, again, balanced]))
        self.assertEqual(self.frozen(), before)


if __name__ == '__main__':
    unittest.main()
