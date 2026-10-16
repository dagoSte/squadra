"""Distinguish client task refusals from actual registry-integrity failures."""
import unittest
from unittest.mock import patch

import test_system_skills as support
import dashboard


class ClientRefusals(unittest.TestCase):
    setUp = support.RoutesAndPage.setUp
    stop = support.RoutesAndPage.stop
    request = support.RoutesAndPage.request

    def test_balance_unknown_task_is_a_fixed_client_refusal(self):
        with patch.object(dashboard.usage, 'collect', wraps=dashboard.usage.collect) as collect:
            for task in ('NOPE', 'CLIENT-CANARY%2F%E2%98%83'):
                with self.subTest(task=task):
                    code, body, _ = self.request('/api/balance', **{'X-Squadra-Record': task})
                    self.assertEqual((code, body), (409, {'error': 'unknown_task'}))
            collect.assert_not_called()

    def test_forecast_unknown_or_missing_task_is_a_fixed_client_refusal(self):
        for task in ('NOPE', 'CLIENT-CANARY%2F%E2%98%83', '', None):
            with self.subTest(task=task):
                headers = {} if task is None else {'X-Squadra-Record': task}
                code, body, _ = self.request('/api/forecast', **headers)
                self.assertEqual((code, body), (409, {'error': 'unknown_task'}))

    def test_corruption_takes_precedence_over_client_input_refusals(self):
        self.assertEqual(self.request('/api/state')[0], 200)
        self.store.db.execute('DROP TRIGGER no_update')
        self.store.db.execute("UPDATE events SET data='{}' WHERE seq=1")
        for route in ('/api/balance', '/api/forecast'):
            for task in ('NOPE', 'T', ''):
                with self.subTest(route=route, task=task):
                    code, body, _ = self.request(route, **{'X-Squadra-Record': task})
                    self.assertEqual((code, body), (409, {'error': 'registry_invalid'}))
        code, body, _ = self.request('/api/balance', **{'X-Squadra-Balance-Reference': 'invalid'})
        self.assertEqual((code, body), (409, {'error': 'registry_invalid'}))

    def test_known_optional_tasks_and_other_fixed_client_codes_keep_working(self):
        before = {str(p.relative_to(self.store.root)): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in self.store.root.rglob('*')
                  if p.is_file() and not p.name.endswith(('-wal', '-shm'))}
        for route in ('/api/balance', '/api/forecast'):
            code, body, headers = self.request(route, **{'X-Squadra-Record': 'T'})
            self.assertEqual(code, 200, body)
            self.assertIn("default-src 'none'", headers['Content-Security-Policy'])
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertEqual(self.request(route, **{'X-Squadra-Token': 'wrong'})[0], 401)
            self.assertEqual(self.request(route, Host='foreign.invalid')[0], 403)
        self.assertEqual(self.request('/api/balance')[0], 200)
        self.assertEqual(self.request('/api/balance', **{'X-Squadra-Record': ''})[0], 200)
        code, body, _ = self.request('/api/balance', **{'X-Squadra-Balance-Reference': 'invalid'})
        self.assertEqual((code, body), (409, {'error': 'reference_invalid'}))
        for route in ('/api/detail/task', '/api/detail/run'):
            code, body, _ = self.request(route, **{'X-Squadra-Record': 'NOPE'})
            self.assertEqual((code, body), (404, {'error': 'record not found'}))
        code, state, _ = self.request('/api/state')
        self.assertEqual(code, 200)
        code, body, _ = self.request('/api/detail/view', **{
            'X-Squadra-State-Head': state['registry_head']['hash'], 'X-Squadra-Section': 'NOPE'})
        self.assertEqual((code, body), (400, {'error': 'unknown_detail'}))
        after = {str(p.relative_to(self.store.root)): (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in self.store.root.rglob('*')
                 if p.is_file() and not p.name.endswith(('-wal', '-shm'))}
        self.assertEqual(after, before, 'Client errors and successful reads never write the registry')


if __name__ == '__main__':
    unittest.main()
