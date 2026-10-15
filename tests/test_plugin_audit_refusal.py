"""Keep registry refusals content-free across actual startup-audit orderings."""
from contextlib import contextmanager
import http.client
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from test_plugins import Fixtures, panel
import dashboard
from registry import Store
from test_brand import POLICY


class AuditRefusal(Fixtures, unittest.TestCase):
    routes = ('/api/state', '/api/reads', '/api/skills', '/api/forecast',
              '/api/usage', '/api/balance', '/api/detail/view', '/api/detail/task',
              '/api/detail/run', '/api/artifact/' + 'a' * 64,
              '/api/plugins', '/api/plugin/example')

    def setUp(self):
        super().setUp()
        self.root = Path(self.temp.name) / 'registry'
        self.store = Store(self.root, create=True)
        self.addCleanup(self.store.close)
        self.store.append('init', 'system', {'name': 'Audit refusal fixture',
            'project': str(self.home), 'limits': {'plan': 3, 'code': 2, 'build': 3}})
        self.write('panels/' + self.store.events()[0]['hash'] + '.json', panel())
        original = dashboard.VerifiedView

        def capture(root):
            self.view = original(root)
            return self.view

        with patch.object(dashboard, 'VerifiedView', capture), \
                patch.object(Path, 'home', return_value=self.home):
            self.server, self.token = dashboard.make_server(self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())

    def request(self, path, value=None, token=True, host=None):
        headers = {'Host': host or '127.0.0.1:' + str(self.server.server_port)}
        if token:
            headers['X-Squadra-Token'] = self.token
        if value is not None:
            headers['Content-Type'] = 'application/json'
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request('GET' if value is None else 'POST', path,
                               body=None if value is None else json.dumps(value), headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), json.loads(response.read())
        finally:
            connection.close()

    def corrupt(self):
        self.store.db.execute('DROP TRIGGER no_update')
        self.store.db.execute("UPDATE events SET data='{}' WHERE seq=1")

    @contextmanager
    def held_audit(self):
        entered, release = threading.Event(), threading.Event()
        original = Store.audit

        def held(store, cancelled=None):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('Synthetic audit barrier timed out')
            return original(store, cancelled=cancelled)

        with patch.object(Store, 'audit', held):
            try:
                status, _, body = self.request('/api/state')
                self.assertEqual(status, 200, body)
                self.assertEqual(body['audit'], {'status': 'running'})
                self.assertTrue(entered.wait(5), 'The actual audit worker must reach the barrier')
                worker = self.view.audit_thread
                self.assertIsNotNone(worker)
                yield release, worker
            finally:
                release.set()
                worker = self.view.audit_thread
                if worker is not None:
                    worker.join(5)
                    self.assertFalse(worker.is_alive())

    def refused(self, route, value=None):
        status, headers, body = self.request(route, value)
        self.assertEqual((status, body), (409, {'error': 'registry_invalid'}))
        self.assertEqual(headers['Content-Security-Policy'], POLICY)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['Content-Type'], 'application/json; charset=utf-8')

    def test_completed_corrupt_audit_refuses_every_api_and_never_writes(self):
        with self.held_audit() as (release, worker):
            self.corrupt()
            release.set()
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertIsNotNone(self.view.audit_failure)
            with patch.object(dashboard.plugin_settings, 'save') as settings, \
                    patch.object(dashboard.plugin_actions, 'save') as action:
                for route in (*self.routes, '/api/plugin-store'):
                    with self.subTest(route=route):
                        self.refused(route)
                self.refused('/api/plugin/example/settings', {'values': {}})
                self.refused('/api/plugin/example/action/send', {'ids': ['one']})
                settings.assert_not_called()
                action.assert_not_called()
            self.assertEqual(self.request('/api/plugins', token=False)[0], 401)
            self.assertEqual(self.request('/api/plugins', host='foreign.invalid')[0], 403)

    def test_foreground_corruption_is_content_free_before_the_audit_finishes(self):
        with self.held_audit():
            self.corrupt()
            self.assertIsNone(self.view.audit_failure)
            for route in self.routes:
                with self.subTest(route=route):
                    self.refused(route)

    def late_failure(self, route):
        with self.held_audit() as (release, worker):
            answer = self.server.RequestHandlerClass.answer
            reached = []

            def after_serialization(handler, status, body, content='application/json; charset=utf-8'):
                if status not in (401, 403, 405) and not reached:
                    reached.append(True)
                    self.corrupt()
                    release.set()
                    worker.join(5)
                    self.assertFalse(worker.is_alive())
                    self.assertIsNotNone(self.view.audit_failure)
                return answer(handler, status, body, content)

            # The request thread must not use the fixture writer's SQLite connection.
            self.store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            self.store.close()
            def corrupt_from_request():
                import sqlite3
                writer = sqlite3.connect(self.root / 'registry.sqlite3', isolation_level=None)
                try:
                    writer.execute('DROP TRIGGER no_update')
                    writer.execute("UPDATE events SET data='{}' WHERE seq=1")
                finally:
                    writer.close()
            with patch.object(self, 'corrupt', corrupt_from_request), \
                    patch.object(self.server.RequestHandlerClass, 'answer', after_serialization):
                self.refused(route)
            self.assertEqual(reached, [True], 'The failure must be latched only after reply serialization')

    def test_late_audit_failure_replaces_a_serialized_store_reply(self):
        self.late_failure('/api/plugin-store')

    def test_late_audit_failure_replaces_a_serialized_state_reply(self):
        self.late_failure('/api/state')

    def test_late_audit_failure_replaces_a_serialized_plugin_error(self):
        self.late_failure('/api/plugin/UPPER')

    def test_pending_healthy_audit_preserves_plugin_success_and_guards(self):
        with self.held_audit(), \
                patch.object(dashboard.plugin_settings, 'save', return_value={}) as settings, \
                patch.object(dashboard.plugin_actions, 'save', return_value=None) as action:
            for route in ('/api/plugins', '/api/plugin/example', '/api/plugin-store'):
                with self.subTest(route=route):
                    status, headers, _ = self.request(route)
                    self.assertEqual(status, 200)
                    self.assertEqual(headers['Content-Security-Policy'], POLICY)
            self.assertEqual(self.request('/api/plugin/example/settings', {'values': {}})[0], 200)
            self.assertEqual(self.request('/api/plugin/example/action/send', {'ids': ['one']})[0], 200)
            self.assertEqual((settings.call_count, action.call_count), (1, 1))
            self.assertEqual(self.request('/api/plugins', token=False)[0], 401)
            self.assertEqual(self.request('/api/plugins', host='foreign.invalid')[0], 403)

    def test_completed_healthy_audit_preserves_plugin_validation_codes(self):
        with self.held_audit() as (release, worker):
            release.set()
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(self.view.audit_info(), {'status': 'complete'})
            self.assertEqual(self.request('/api/plugin/absent')[::2], (404, {'error': 'not_found'}))
            self.assertEqual(self.request('/api/plugin/UPPER')[::2], (400, {'error': 'bad_name'}))
            self.write('panels/' + self.store.events()[0]['hash'] + '.json', {'PRIVATE SENTINEL': 1})
            self.assertEqual(self.request('/api/plugin/example')[::2], (409, {'error': 'unknown_key'}))


if __name__ == '__main__':
    unittest.main()
