"""A visible background full audit must not gate ordinary verified responses."""
import http.client
import json
import os
import threading
import time
import unittest
from unittest.mock import patch

import dashboard
from registry import Error, Store
import test_dashboard_performance as support


class BackgroundAudit(unittest.TestCase):
    setUp = support.DashboardPerformance.setUp
    stop_server = support.DashboardPerformance.stop_server
    get = support.DashboardPerformance.get
    stamp = support.DashboardPerformance.stamp
    task_id = support.DashboardPerformance.task_id
    run_id = support.DashboardPerformance.run_id
    source_path = support.DashboardPerformance.source_path
    source_hash = support.DashboardPerformance.source_hash

    def request_while_blocked(self, entered, release):
        ready = threading.Event()
        received = []

        def request():
            try:
                received.append(self.get())
            except Exception as exc:
                received.append(exc)
            finally:
                ready.set()

        caller = threading.Thread(target=request, daemon=True)
        caller.start()
        try:
            self.assertTrue(entered.wait(2), 'The full audit must actually start')
            self.assertTrue(ready.wait(2), 'The response must arrive before the full audit is released')
            self.assertFalse(isinstance(received[0], Exception), repr(received[0]))
            status, _, body = received[0]
            self.assertEqual(status, 200, body)
            value = json.loads(body)
            self.assertEqual(value['audit'], {'status': 'running'})
            self.assertEqual(value['tasks'][self.task_id]['candidate']['files'], self.fingerprint['files'])
            return value
        finally:
            release.set()
            caller.join(5)
            self.assertFalse(caller.is_alive())

    def wait_for(self, status, audit=None):
        deadline = time.monotonic() + 5
        while True:
            received, _, body = self.get()
            value = json.loads(body)
            if received == status and (audit is None or value.get('audit') == {'status': audit}):
                return value
            self.assertLess(time.monotonic(), deadline, (received, body[:200]))
            time.sleep(.01)

    def test_delayed_full_audit_does_not_block_first_response_and_completes_once(self):
        entered, release = threading.Event(), threading.Event()
        original = Store.audit
        calls = []

        def delayed(store, cancelled=None):
            calls.append(store)
            entered.set()
            while not release.wait(.01):
                if cancelled and cancelled():
                    raise Error('Registry audit cancelled')
            return original(store, cancelled=cancelled)

        with patch.object(Store, 'audit', delayed):
            first = self.request_while_blocked(entered, release)
            done = self.wait_for(200, 'complete')
            self.assertEqual({k: v for k, v in done.items() if k != 'audit'},
                             {k: v for k, v in first.items() if k != 'audit'})
            for _ in range(3):
                self.assertEqual(self.wait_for(200, 'complete'), done)
            self.assertEqual(len(calls), 1)

    def test_background_failure_latches_and_refuses_cached_state_and_details(self):
        entered, release = threading.Event(), threading.Event()

        def failed(store, cancelled=None):
            entered.set()
            release.wait(5)
            raise Error('Synthetic full audit failure')

        with patch.object(Store, 'audit', failed):
            self.request_while_blocked(entered, release)
            self.assertIn('Synthetic full audit failure', str(self.wait_for(409)))
        self.store.append('diagnostic', 'system', {'reason': 'A new event cannot erase a failed full audit'})
        for route, headers in (('/api/state', {}), ('/api/state', {'X-Squadra-View': 'compact'}),
                               ('/api/detail/task', {'X-Squadra-Record': self.task_id}),
                               ('/api/plugins', {}), ('/api/plugin/example', {}), ('/api/plugin-store', {}),
                               ('/api/usage', {}), ('/api/balance', {}), ('/api/reads', {}),
                               ('/api/skills', {}), ('/api/forecast', {}),
                               ('/api/artifact/' + self.artifact['sha256'], {})):
            status, _, body = self.get(route, headers)
            self.assertEqual(status, 409, body)
            self.assertNotIn(b'"tasks"', body)

    def test_plugin_requests_continue_while_pending_but_never_write_after_failure(self):
        entered, release = threading.Event(), threading.Event()

        def failed(store, cancelled=None):
            entered.set()
            release.wait(5)
            raise Error('Synthetic plugin audit failure')

        def post(route, value):
            connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
            try:
                connection.request('POST', route, body=json.dumps(value), headers={
                    'X-Squadra-Token': self.token, 'Content-Type': 'application/json'})
                response = connection.getresponse()
                return response.status, response.read()
            finally:
                connection.close()

        with patch.object(Store, 'audit', failed), \
                patch.object(dashboard.plugin_settings, 'save', return_value={}) as settings, \
                patch.object(dashboard.plugin_actions, 'save', return_value=None) as action:
            try:
                status, _, body = self.get()
                self.assertEqual(status, 200, body)
                self.assertTrue(entered.wait(2))
                self.assertEqual(json.loads(body)['audit'], {'status': 'running'})
                self.assertEqual(self.get('/api/plugins')[0], 200)
                self.assertEqual(post('/api/plugin/example/settings', {'values': {}})[0], 200)
                self.assertEqual(post('/api/plugin/example/action/send', {'ids': ['one']})[0], 200)
                self.assertEqual((settings.call_count, action.call_count), (1, 1))
            finally:
                release.set()
            self.wait_for(409)
            for route, value in (('/api/plugin/example/settings', {'values': {}}),
                                 ('/api/plugin/example/action/send', {'ids': ['one']})):
                status, body = post(route, value)
                self.assertEqual(status, 409, body)
                self.assertEqual(json.loads(body), {'error': 'registry_invalid'})
            self.assertEqual((settings.call_count, action.call_count), (1, 1))
            self.assertEqual(self.get('/api/plugins', authenticated=False)[0], 401)

    def test_replaced_database_does_not_inherit_an_obsolete_audit_failure(self):
        view = dashboard.VerifiedView(self.root)
        self.addCleanup(view.close)
        entered, release = threading.Event(), threading.Event()
        original = Store.audit
        calls = []

        def obsolete(store, cancelled=None):
            calls.append(store)
            if len(calls) == 1:
                entered.set()
                while not release.wait(.01):
                    if cancelled and cancelled():
                        raise Error('Obsolete snapshot failed after replacement')
                raise Error('Obsolete snapshot failed after replacement')
            return original(store, cancelled=cancelled)

        with patch.object(Store, 'audit', obsolete):
            view.read()
            self.assertTrue(entered.wait(2))
            self.store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            replacement_root = self.base / 'replacement-audit'
            replacement = Store(replacement_root, create=True)
            replacement.append('init', 'system', {'name': 'Replacement audit', 'project': str(self.base),
                'limits': {'plan': 3, 'code': 2, 'build': 3}})
            replacement.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            replacement.close()
            self.store.close()
            os.replace(replacement_root / 'registry.sqlite3', self.root / 'registry.sqlite3')
            _, state, _ = view.read()
            release.set()
            worker = view.audit_thread
            if worker is not None:
                worker.join(5)
                self.assertFalse(worker.is_alive())
            self.assertEqual(state['workspace']['name'], 'Replacement audit')
            self.assertEqual(view.audit_info(), {'status': 'complete'})
            self.assertEqual(len(calls), 2)

    def test_close_cancels_owned_worker_without_holding_the_view_lock(self):
        view = dashboard.VerifiedView(self.root)
        self.addCleanup(view.close)
        entered, ended = threading.Event(), threading.Event()

        def until_cancelled(store, cancelled=None):
            entered.set()
            self.assertIsNotNone(cancelled)
            while not cancelled():
                time.sleep(.01)
            ended.set()
            raise Error('Registry audit cancelled')

        with patch.object(Store, 'audit', until_cancelled):
            view.read()
            self.assertTrue(entered.wait(2))
            view.close()
        self.assertTrue(ended.is_set())
        self.assertIsNone(view.audit_thread)
        self.assertIsNone(view.monitor)
        with self.assertRaisesRegex(Error, 'closed'):
            view.read()

    def test_real_audit_cancellation_is_explicit_and_leaves_the_store_usable(self):
        with self.assertRaisesRegex(Error, 'cancelled'):
            self.store.audit(cancelled=lambda: True)
        self.assertFalse(self.store.db.in_transaction)
        result = self.store.audit()
        self.assertEqual(result['state']['event_count'], len(result['events']))


if __name__ == '__main__':
    unittest.main()
