"""Real HTTP readers share a lock while writers and consumers stay exclusive."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import test_plugin_actions as fixture
from test_plugin_actions import actions, plugins
from test_plugin_settings import tree


class ActionReadLocks(fixture.ActionFixture, unittest.TestCase):
    stop = fixture.Http.stop
    request = fixture.Http.request

    def setUp(self):
        super().setUp()
        self.registry = Path(self.temp.name) / 'registry'
        store = fixture.Store(self.registry, create=True)
        try:
            store.append('init', 'system', {'name': 'Read locks', 'project': str(self.home),
                                          'temporary': True, 'limits': {'plan': 3, 'code': 2, 'build': 3}})
            self.key = store.events()[0]['hash']
        finally:
            store.close()
        self.write('panels/' + self.key + '.json', fixture.selectable())
        with patch.dict(os.environ, {'HOME': str(self.home)}):
            self.server, self.token = fixture.dashboard.make_server(self.registry)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def discovery(self, path):
        code, _, body = self.request(path=path, method='GET')
        self.assertEqual(code, 200)
        return body['plugins'] if path == '/api/state' else body

    def test_two_http_readers_share_pending_without_blocking_each_other(self):
        pending = self.request(['one'])[2]['request']
        original = actions.current
        for first_path, second_path in (('/api/state', '/api/plugins'), ('/api/plugins', '/api/state')):
            with self.subTest(first=first_path):
                entered, release = threading.Event(), threading.Event()
                first = True

                def held_read(*args):
                    nonlocal first
                    if first:
                        first = False
                        entered.set()
                        if not release.wait(5):
                            raise AssertionError('reader was not released')
                    return original(*args)

                before = tree(self.home)
                with patch.object(actions, 'current', side_effect=held_read), ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(self.discovery, first_path)
                    try:
                        self.assertTrue(entered.wait(5), 'first reader holds the real file lock')
                        second = self.discovery(second_path)
                        self.assertEqual(second['refused'], [], 'another reader must not report busy')
                        self.assertEqual(second['plugins'][0]['actions'][0]['pending'], pending)
                        self.assertEqual(self.request(['two'])[::2], (409, {'error': 'busy'}))
                        fd = os.open(self.base / 'actions', os.O_RDONLY | os.O_DIRECTORY)
                        try:
                            with self.assertRaises(plugins.Refused) as caught:
                                with actions.locked(fd, 'send'):
                                    self.fail('consumer must retain an exclusive lock')
                            self.assertEqual(caught.exception.code, 'busy')
                        finally:
                            os.close(fd)
                        self.assertEqual(tree(self.home), before)
                    finally:
                        release.set()
                    found = future.result(timeout=5)
                    self.assertEqual(found['refused'], [])
                    self.assertEqual(found['plugins'][0]['actions'][0]['pending'], pending)
        self.assertEqual(self.request(['two'])[0], 200, 'writer succeeds after both readers finish')

    def test_http_reader_under_exclusive_lock_reports_unconfirmed_then_recovers(self):
        pending = self.request(['one'])[2]['request']
        before = tree(self.home)
        fd = os.open(self.base / 'actions', os.O_RDONLY | os.O_DIRECTORY)
        try:
            with actions.locked(fd, 'send'):
                for path in ('/api/state', '/api/plugins'):
                    found = self.discovery(path)
                    self.assertIsNone(found['plugins'][0]['actions'][0]['pending'])
                    self.assertIn({'name': 'example', 'code': 'busy'}, found['refused'])
        finally:
            os.close(fd)
        self.assertEqual(tree(self.home), before)
        for path in ('/api/state', '/api/plugins'):
            found = self.discovery(path)
            self.assertEqual(found['refused'], [])
            self.assertEqual(found['plugins'][0]['actions'][0]['pending'], pending)
