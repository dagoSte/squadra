"""Exercise the release catalogue over real loopback HTTP and the page DOM."""
import hashlib
import http.client
import json
from pathlib import Path
import shutil
import subprocess
import threading
from unittest.mock import patch

from test_plugin_store import Fixture, ROOT, store
import dashboard
from registry import Store


class StorePage(Fixture):
    def setUp(self):
        super().setUp()
        self.registry = self.base / 'registry'
        registry = Store(self.registry, create=True)
        try:
            registry.append('init', 'system', {'name': 'Store fixture', 'project': str(self.base),
                            'limits': {'plan': 3, 'code': 2, 'build': 3}})
        finally:
            registry.close()
        release = patch.object(store, 'RELEASE_ROOT', self.release)
        release.start(); self.addCleanup(release.stop)
        with patch.object(Path, 'home', return_value=self.home):
            self.server, self.token = dashboard.make_server(self.registry)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start(); self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(5)
        self.assertFalse(self.thread.is_alive())

    def request(self, path='/api/plugin-store', method='GET', headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, path, headers={'X-Squadra-Token': self.token, **(headers or {})})
            reply = connection.getresponse()
            return reply.status, dict(reply.getheaders()), reply.read()
        finally:
            connection.close()

    def test_route_requires_host_and_token_and_has_no_write_method(self):
        for headers, status in [({'Host': 'example.invalid'}, 403), ({'X-Squadra-Token': ''}, 401),
                                ({'X-Squadra-Token': 'wrong'}, 401), ({}, 200)]:
            with self.subTest(headers=headers), patch.object(store, 'catalogue', wraps=store.catalogue) as read:
                self.assertEqual(self.request(headers=headers)[0], status)
                self.assertEqual(read.call_count, int(status == 200))
        self.assertEqual(self.request(method='POST')[0], 405)
        self.assertFalse((self.home / '.squadra').exists())

    def test_catalogue_is_read_only_and_never_serves_instructions_or_programs(self):
        secret = 'PRIVATE INSTRUCTIONS MUST NOT BE SERVED'
        (self.source / 'AGENTS.md').write_text(secret)
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.registry.rglob('*') if p.is_file()}
        status, headers, body = self.request()
        self.assertEqual(status, 200); self.assertNotIn(secret.encode(), body)
        self.assertNotIn(b'programs/info.py', body)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['Content-Security-Policy'], self.request('/')[1]['Content-Security-Policy'])
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before})
        self.assertFalse((self.home / '.squadra').exists())
        for route in ['/api/plugin-store/AGENTS.md', '/plugins/example/AGENTS.md', '/api/plugin/example/AGENTS.md']:
            with self.subTest(route=route):
                status, _, body = self.request(route)
                self.assertGreaterEqual(status, 400); self.assertNotIn(secret.encode(), body)

    def test_installed_receipt_against_running_release_and_fresh_explicit_read(self):
        self.assertFalse(json.loads(self.request()[2])['plugins'][0]['installed'])
        self.install()
        value = json.loads(self.request()[2]); row = value['plugins'][0]
        self.assertTrue(row['installed']); self.assertEqual(row['receipt']['state'], 'complete')
        self.assertEqual(row['receipt']['release'], value['release'])
        self.json(self.release / '.codex-plugin/plugin.json', version='4.0.0')
        changed = json.loads(self.request()[2])
        self.assertEqual(changed['release']['version'], '4.0.0')
        self.assertEqual(changed['plugins'][0]['receipt'], row['receipt'])

    def test_store_is_independent_of_registry_cache_and_never_in_state_poll(self):
        with patch.object(store, 'catalogue', wraps=store.catalogue) as read:
            for _ in range(3): self.assertEqual(self.request('/api/state')[0], 200)
            self.assertEqual(read.call_count, 0)
        with patch.object(dashboard.VerifiedView, 'read', side_effect=AssertionError('not a registry view')):
            self.assertEqual(self.request()[0], 200)

    def test_fixed_error_and_invalid_entry_exclusion(self):
        with patch.object(store, 'catalogue', side_effect=OSError('PRIVATE filesystem detail')):
            self.assertEqual(self.request()[::2], (409, b'{"error":"store_unavailable"}'))
        self.json(self.source / 'store.json', mcp='yes')
        data = json.loads(self.request()[2])
        self.assertEqual(data['plugins'], []); self.assertEqual(len(data['refused']), 1)
        shutil.rmtree(self.source.parent)
        self.assertEqual(json.loads(self.request()[2])['plugins'], [])

    def browser(self, scenario):
        node = shutil.which('node'); self.assertIsNotNone(node, 'Node is required; no skipped coverage')
        state = json.loads(self.request('/api/state')[2])
        fixture = self.base / 'state.json'; fixture.write_text(json.dumps(state))
        payload = self.base / 'catalogue.json'; payload.write_text(json.dumps(self.catalogue()))
        run = subprocess.run([node, str(ROOT / 'tests/plugin_store_contract.js'),
                              str(ROOT / 'assets/dashboard.html'), str(fixture), scenario, str(payload)],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('store contract passed: ' + scenario, run.stdout)

    def test_page_labels_and_safe_links(self): self.browser('labels')
    def test_page_xss_and_unsafe_links(self): self.browser('xss')
    def test_page_mcp_then_localized_title(self): self.browser('order')
    def test_page_fetch_outside_poll_and_loading(self): self.browser('fetches')
    def test_page_empty_errors_and_loading(self): self.browser('states')
    def test_page_receipt_and_unmanaged_states(self): self.browser('receipts')
    def test_page_shell_and_focus(self): self.browser('shell')
    def test_page_retains_reading_place(self): self.browser('place')
