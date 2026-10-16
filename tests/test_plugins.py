"""Check plugin data formats with private files and local HTTP."""
from copy import deepcopy
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import plugins
from dashboard import make_server
from registry import Store
from test_brand import POLICY

ROOT = Path(__file__).resolve().parents[1]
KEY = 'a' * 64


def manifest():
    return {'schema': 1, 'name': 'example', 'title': {'it': 'Esempio', 'en': 'Example'},
            'description': {'it': 'Dati locali', 'en': 'Local data'}}


def panel():
    return {'schema': 1, 'updated': '2026-10-11T12:00:00Z', 'summary': {'it': 'Riepilogo', 'en': 'Summary'},
            'note': {'it': 'Nota', 'en': 'Note'}, 'items': [
                {'id': 'one', 'title': '<img src=x onerror=alert(1)>', 'text': 'https://example.invalid/ <script>unsafe</script>',
                 'status': 'draft', 'meta': [{'label': {'it': 'Origine', 'en': 'Source'}, 'value': 'Local only'}]}]}


def audited_state(test, fetch):
    """Wait for the independently tested background audit before counting warm reads."""
    deadline = time.monotonic() + 10
    while True:
        status, headers, state = fetch()
        test.assertEqual(status, 200)
        if state.get('audit') == {'status': 'complete'}:
            return status, headers, state
        test.assertEqual(state.get('audit'), {'status': 'running'})
        test.assertLess(time.monotonic(), deadline, 'The background audit must finish')
        time.sleep(.01)


class Fixtures:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sq-plugins-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'home'
        self.base = self.home / '.squadra/plugins/example'
        (self.base / 'panels').mkdir(parents=True)
        self.write('plugin.json', manifest())
        self.write('panels/' + KEY + '.json', panel())

    def write(self, name, value):
        (self.base / name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def refused(self, code):
        with self.assertRaises(plugins.Refused) as caught:
            plugins.load_panel(self.home, 'example', KEY)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)
        self.assertNotIn(str(self.home), repr(caught.exception))


class Files(Fixtures, unittest.TestCase):
    def test_valid_and_optional_note(self):
        self.assertEqual(plugins.discover(self.home), {'plugins': [manifest()], 'refused': []})
        self.assertEqual(plugins.load_panel(self.home, 'example', KEY), panel())
        p = panel(); p.pop('note'); self.write('panels/' + KEY + '.json', p)
        self.assertEqual(plugins.load_panel(self.home, 'example', KEY), p)

    def test_empty_root_and_missing_exact_key_never_fall_back(self):
        self.assertEqual(plugins.discover(Path(self.temp.name)), {'plugins': [], 'refused': []})
        self.assertIsNone(plugins.load_panel(self.home, 'example', 'b' * 64))
        (self.base / 'panels' / ('b' * 64 + '.json')).write_text('private-other-registry')
        self.assertEqual(plugins.load_panel(self.home, 'example', KEY), panel())
        (self.base / 'panels' / (KEY + '.json')).unlink()
        self.assertIsNone(plugins.load_panel(self.home, 'example', KEY))
        shutil.rmtree(self.base / 'panels')
        self.assertIsNone(plugins.load_panel(self.home, 'example', KEY))

    def test_bad_name_and_key(self):
        for name in ('../other', 'UPPER', 'a' * 33, '%2e%2e', '', 'a/b'):
            with self.subTest(name=name), self.assertRaises(plugins.Refused) as caught:
                plugins.load_panel(self.home, name, KEY)
            self.assertEqual(caught.exception.code, 'bad_name')
        (self.base.parent / 'UPPER').mkdir()
        self.assertEqual(plugins.discover(self.home)['refused'], [{'name': 'UPPER', 'code': 'bad_name'}])
        with self.assertRaises(plugins.Refused) as caught: plugins.load_panel(self.home, 'example', '../other')
        self.assertEqual(caught.exception.code, 'registry_invalid')

    def test_missing_manifest_and_mismatched_name(self):
        m = manifest(); m['name'] = 'another'; self.write('plugin.json', m); self.refused('name_mismatch')
        (self.base / 'plugin.json').unlink(); self.refused('not_found')
        self.assertEqual(plugins.discover(self.home)['refused'], [{'name': 'example', 'code': 'not_found'}])

    def test_schema_types_unknown_keys_and_nested_unknown_fields(self):
        for version in (True, 0, 2, '1', None):
            with self.subTest(schema=version):
                m = manifest(); m['schema'] = version; self.write('plugin.json', m); self.refused('bad_schema')
        self.write('plugin.json', manifest())
        for target in ('manifest', 'title', 'description', 'panel', 'summary', 'note', 'item', 'meta', 'label'):
            with self.subTest(target=target):
                m, p = manifest(), panel()
                value = {'manifest': m, 'title': m['title'], 'description': m['description'], 'panel': p,
                         'summary': p['summary'], 'note': p['note'], 'item': p['items'][0],
                         'meta': p['items'][0]['meta'][0], 'label': p['items'][0]['meta'][0]['label']}[target]
                value['unknown'] = 'do not print this'
                self.write('plugin.json', m); self.write('panels/' + KEY + '.json', p); self.refused('unknown_key')

    def test_value_types_missing_fields_statuses_and_timestamps(self):
        cases = []
        for key in ('schema', 'updated', 'summary', 'items'):
            p = panel(); p.pop(key); cases.append(p)
        for stamp in ('yesterday', '2026-10-11', '2026-10-11T12:00:00', '2026-10-11T12:00:00+01:00', 1):
            p = panel(); p['updated'] = stamp; cases.append(p)
        for bad in (True, {}, 'approve', 2):
            p = panel(); p['items'][0]['status'] = bad; cases.append(p)
        for key, bad in [('items', {}), ('summary', {'en': 'only'}), ('note', []), ('items', [None])]:
            p = panel(); p[key] = bad; cases.append(p)
        for key in ('id', 'title', 'text', 'meta'):
            p = panel(); p['items'][0][key] = None; cases.append(p)
        for p in cases:
            with self.subTest(value=p):
                self.write('panels/' + KEY + '.json', p); self.refused('bad_value')
        for status in sorted(plugins.STATUSES):
            p = panel(); p['items'][0]['status'] = status; self.write('panels/' + KEY + '.json', p)
            self.assertEqual(plugins.load_panel(self.home, 'example', KEY), p)

    def test_json_duplicates_nonfinite_invalid_unicode_and_deep_input(self):
        for body in (b'{', b'\xff', b'{"schema":1,"schema":1}', b'{"schema":NaN}', b'[' * 2000 + b']' * 1999):
            with self.subTest(body=body[:30]):
                (self.base / 'plugin.json').write_bytes(body); self.refused('invalid_json')
        self.write('plugin.json', manifest())
        p = panel(); p['items'][0]['text'] = '\ud800'; (self.base / 'panels' / (KEY + '.json')).write_text(json.dumps(p))
        self.refused('bad_value')

    def test_exact_byte_limits_and_growth_after_stat(self):
        for file, data, limit in [('plugin.json', manifest(), plugins.MANIFEST_BYTES),
                                  ('panels/' + KEY + '.json', panel(), plugins.PANEL_BYTES)]:
            with self.subTest(file=file):
                body = json.dumps(data).encode(); padded = body + b' ' * (limit - len(body))
                (self.base / file).write_bytes(padded)
                self.assertEqual(plugins.load_panel(self.home, 'example', KEY), panel())
                (self.base / file).write_bytes(padded + b' '); self.refused('too_large')
                (self.base / file).write_bytes(body)
        real_read = os.read
        def growing(fd, size):
            if os.fstat(fd).st_size < 1000: return real_read(fd, size)
            return b' ' * size
        (self.base / 'panels' / (KEY + '.json')).write_bytes(b' ' * 1000)
        with patch.object(plugins.os, 'read', side_effect=growing): self.refused('too_large')

    def test_string_bytes_and_item_count_bounds(self):
        p = panel(); p['items'][0]['text'] = 'é' * 2048; self.write('panels/' + KEY + '.json', p)
        self.assertEqual(plugins.load_panel(self.home, 'example', KEY), p)
        p['items'][0]['text'] += 'é'; self.write('panels/' + KEY + '.json', p); self.refused('bad_value')
        p = panel(); p['items'] *= 500; self.write('panels/' + KEY + '.json', p)
        self.assertEqual(len(plugins.load_panel(self.home, 'example', KEY)['items']), 500)
        p['items'].append(deepcopy(p['items'][0])); self.write('panels/' + KEY + '.json', p); self.refused('bad_value')

    def test_symlink_at_every_component(self):
        for relative in ('.squadra', '.squadra/plugins', '.squadra/plugins/example',
                         '.squadra/plugins/example/plugin.json', '.squadra/plugins/example/panels',
                         '.squadra/plugins/example/panels/' + KEY + '.json'):
            with self.subTest(path=relative):
                path = self.home / relative; saved = path.with_name(path.name + '-saved'); path.rename(saved)
                try:
                    path.symlink_to(saved, target_is_directory=saved.is_dir()); self.refused('symlink')
                finally: path.unlink(); saved.rename(path)

    def test_directory_fifo_and_io_failures(self):
        path = self.base / 'plugin.json'; path.unlink(); path.mkdir(); self.refused('not_regular'); path.rmdir()
        os.mkfifo(path); self.refused('not_regular'); path.unlink(); self.write('plugin.json', manifest())
        shutil.rmtree(self.base / 'panels'); (self.base / 'panels').write_text('not a directory'); self.refused('not_directory')
        with patch.object(plugins.os, 'open', side_effect=PermissionError('private path')):
            self.assertEqual(plugins.discover(self.home), {'plugins': [], 'refused': [{'name': None, 'code': 'io_error'}]})

    def test_directory_replacement_cannot_redirect_an_open_descriptor(self):
        real_open = os.open; old = self.base.with_name('saved'); outside = Path(self.temp.name) / 'outside'; outside.mkdir()
        (outside / 'plugin.json').write_text('private outside sentinel')
        def replace_after_open(name, flags, **kwargs):
            fd = real_open(name, flags, **kwargs)
            if name == 'example': self.base.rename(old); self.base.symlink_to(outside, target_is_directory=True)
            return fd
        with patch.object(plugins.os, 'open', side_effect=replace_after_open):
            self.assertEqual(plugins.load_panel(self.home, 'example', KEY), panel())
        self.assertEqual((outside / 'plugin.json').read_text(), 'private outside sentinel')

    def test_reads_never_write_or_run_plugin_code(self):
        script = self.base / 'plugin.py'; script.write_text("raise RuntimeError('must not execute')")
        files = list(self.home.rglob('*')); before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in files if p.is_file()}
        plugins.discover(self.home); plugins.load_panel(self.home, 'example', KEY)
        self.assertEqual(before, {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in files if p.is_file()})


class Server(Fixtures, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.registry = Path(self.temp.name) / 'registry'
        s = Store(self.registry, create=True)
        s.append('init', 'system', {'name': 'Plugin tests', 'project': str(self.home), 'temporary': True,
                                  'limits': {'plan': 3, 'code': 2, 'build': 3}})
        self.key = s.events()[0]['hash']; s.close()
        self.write('panels/' + self.key + '.json', panel())
        with patch.dict(os.environ, {'HOME': str(self.home)}): self.server, self.token = make_server(self.registry)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())

    def get(self, path, token=True, host=None, method='GET'):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            headers = {'Host': host or '127.0.0.1:' + str(self.server.server_port)}
            if token: headers['X-Squadra-Token'] = self.token
            conn.request(method, path, headers=headers)
            response = conn.getresponse(); body = response.read()
            return response.status, dict(response.getheaders()), json.loads(body)
        finally: conn.close()

    def test_routes_guard_order_csp_and_methods(self):
        for path in ('/api/plugins', '/api/plugin/example'):
            with self.subTest(path=path):
                self.assertEqual(self.get(path, token=False)[0], 401)
                self.assertEqual(self.get(path, token=False, host='foreign.invalid')[0], 403)
                self.assertEqual(self.get(path, method='POST')[0], 405)
                status, headers, body = self.get(path)
                self.assertEqual(status, 200); self.assertEqual(headers['Content-Security-Policy'], POLICY)
                self.assertEqual(headers['Cache-Control'], 'no-store')
        for path in ('/api/plugin/../other', '/api/plugin/%2e%2e', '/api/plugin/example%2fother', '/api/plugin/UPPER'):
            self.assertEqual(self.get(path)[::2], (400, {'error': 'bad_name'}))
        self.assertEqual(self.get('/api/plugin/absent')[::2], (404, {'error': 'not_found'}))

    def test_unsafe_directories_are_visible_refusals_in_warm_api_state(self):
        self.assertEqual(self.get('/api/state')[0], 200)
        for folder in (self.home, self.home / '.squadra', self.base.parent, self.base):
            original = folder.stat().st_mode & 0o777
            with self.subTest(level=str(folder.relative_to(self.home))):
                folder.chmod(0o775)
                try:
                    expected = {'plugins': [], 'refused': [
                        {'name': 'example' if folder == self.base else None, 'code': 'unsafe_permissions'}]}
                    status, headers, found = self.get('/api/plugins')
                    self.assertEqual((status, found), (200, expected))
                    self.assertEqual(headers['Content-Security-Policy'], POLICY)
                    status, _, state = self.get('/api/state')
                    self.assertEqual(status, 200)
                    self.assertEqual(state['plugins'], {**expected, 'key': self.key})
                    self.assertEqual(self.get('/api/plugin/example')[::2], (409, {'error': 'unsafe_permissions'}))
                finally:
                    folder.chmod(original)
                self.assertEqual(self.get('/api/plugins')[2]['refused'], [])

    def test_discovery_in_state_and_key_reuse_without_another_full_read(self):
        original = Store.events; calls = []
        def counted(store): calls.append(1); return original(store)
        with patch.object(Store, 'events', counted):
            status, _, state = audited_state(self, lambda: self.get('/api/state'))
            self.assertEqual(state['plugins'], {'plugins': [manifest()], 'refused': [], 'key': self.key})
            count = len(calls)
            self.assertEqual(count, 2, 'One foreground history read and one background audit')
            for _ in range(3):
                self.assertEqual(self.get('/api/plugin/example')[2], {'name': 'example', 'key': self.key, 'panel': panel()})
                self.assertEqual(self.get('/api/plugins')[2], {'plugins': [manifest()], 'refused': []})
            self.assertEqual(len(calls), count, 'plugin reads reuse the verified first record')
        s = Store(self.registry)
        s.append('agent', 'C', {'id': 'C', 'role': 'coordinator', 'identity': 'synthetic', 'client': 'manual', 'repo': str(self.home), 'skills': [], 'session': ''})
        self.assertEqual(s.events()[0]['hash'], self.key); s.close()
        self.assertEqual(self.get('/api/state')[2]['plugins']['key'], self.key)

    def test_refusal_no_data_and_registry_corruption_are_content_free(self):
        (self.base / 'panels' / (self.key + '.json')).unlink()
        self.assertEqual(self.get('/api/plugin/example')[2], {'name': 'example', 'key': self.key, 'panel': None})
        self.write('panels/' + self.key + '.json', {'PRIVATE SENTINEL': 1})
        self.assertEqual(self.get('/api/plugin/example')[::2], (409, {'error': 'unknown_key'}))
        self.get('/api/state')
        s = Store(self.registry)
        try:
            # Deliberate corruption of this private fixture, past its append-only guard.
            s.db.execute("DROP TRIGGER no_update")
            s.db.execute("UPDATE events SET data='{}' WHERE seq=1"); s.db.commit()
        finally: s.close()
        self.assertEqual(self.get('/api/plugin/example')[::2], (409, {'error': 'registry_invalid'}))

    def test_two_live_registries_use_only_their_own_panel(self):
        other = Path(self.temp.name) / 'other'; s = Store(other, create=True)
        s.append('init', 'system', {'name': 'Other', 'project': str(self.home), 'temporary': True,
                                  'limits': {'plan': 3, 'code': 2, 'build': 3}})
        key = s.events()[0]['hash']; s.close(); self.assertNotEqual(key, self.key)
        with patch.dict(os.environ, {'HOME': str(self.home)}): server, token = make_server(other)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            try:
                conn.request('GET', '/api/plugin/example', headers={'X-Squadra-Token': token})
                response = conn.getresponse(); self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read()), {'name': 'example', 'key': key, 'panel': None})
            finally: conn.close()
        finally: server.shutdown(); server.server_close(); thread.join(timeout=5)

    def test_sqlite_read_failure_is_a_fixed_registry_refusal(self):
        s = Store(self.registry)
        try: s.db.execute('DROP TABLE events')
        finally: s.close()
        for path in ('/api/plugins', '/api/plugin/example'):
            with self.subTest(path=path):
                try: response = self.get(path)[::2]
                except http.client.RemoteDisconnected: response = None
                self.assertEqual(response, (409, {'error': 'registry_invalid'}))

    def test_shipped_plugin_javascript_contract(self):
        node = shutil.which('node'); self.assertIsNotNone(node, 'Node required, never skipped')
        fixture = Path(self.temp.name) / 'state.json'; fixture.write_text(json.dumps(self.get('/api/state')[2]))
        for scenario in ('render', 'refresh', 'shell', 'place', 'refusals', 'collapsed'):
            with self.subTest(scenario=scenario):
                result = subprocess.run([node, str(ROOT / 'tests/plugins_contract.js'), str(ROOT / 'assets/dashboard.html'), str(fixture), scenario], capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('plugins contract passed: ' + scenario, result.stdout)
