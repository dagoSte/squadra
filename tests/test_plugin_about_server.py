"""About metadata: character limits, closed schemas and real loopback discovery."""
from copy import deepcopy
import hashlib
import http.client
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import plugin_settings
import plugins
from dashboard import make_server
from registry import Store
from test_brand import POLICY
from test_plugins import Fixtures, KEY, manifest, panel


def about():
    return {'publisher': 'Example publisher', 'version': '1.2.3-preview+4',
            'tagline': {'it': 'Dati locali', 'en': 'Local data'},
            'sections': [{'id': 'purpose', 'title': {'it': 'A cosa serve', 'en': 'Purpose'},
                          'text': {'it': 'Riga uno\nRiga due', 'en': '<script>inert</script> https://example.invalid/'},
                          'items': [{'it': 'Un elemento', 'en': 'One item'}]}]}


def at(value, path):
    for key in path:
        value = value[key]
    return value


class AboutFixture(Fixtures):
    def install(self, value):
        m = manifest(); m['about'] = value
        (self.base / 'plugin.json').write_text(json.dumps(m), encoding='utf-8')
        return m

    def accepted(self, value):
        m = self.install(value)
        self.assertEqual(plugins.discover(self.home), {'plugins': [m], 'refused': []})
        self.assertEqual(plugin_settings.discover(self.home), {'plugins': [m], 'refused': []})
        self.assertEqual(plugins.load_panel(self.home, 'example', KEY), panel())

    def rejected(self, value, code='bad_value'):
        self.install(value); self.refused(code)
        self.assertEqual(plugins.discover(self.home), {'plugins': [], 'refused': [{'name': 'example', 'code': code}]})


class About(AboutFixture, unittest.TestCase):
    def test_optional_about_and_each_section_content_shape(self):
        self.assertEqual(plugins.discover(self.home)['plugins'], [manifest()])
        self.assertEqual(plugin_settings.discover(self.home)['plugins'], [manifest()])
        for fields in (('text',), ('items',), ('text', 'items')):
            with self.subTest(fields=fields):
                a = about(); s = a['sections'][0]
                for key in ('text', 'items'):
                    if key not in fields: del s[key]
                self.accepted(a)

    def test_publisher_character_limits_types_and_controls(self):
        for value in ('A', '🌍' * 64, 'Example Company Ltd.', '編集者'):
            a = about(); a['publisher'] = value; self.accepted(a)
        for value in ('', 'a' * 65, '🌍' * 65, True, 1, None, [], {}, '\ud800'):
            with self.subTest(value=str(value)[:40]):
                a = about(); a['publisher'] = value; self.rejected(a)
        for code in list(range(32)) + list(range(127, 160)):
            with self.subTest(control=code):
                a = about(); a['publisher'] = 'Example' + chr(code); self.rejected(a)

    def test_version_pattern_and_character_limits(self):
        for value in ('0', 'A', 'a' * 32, '1.2.3-rc.1+build-9', 'V1+2'):
            a = about(); a['version'] = value; self.accepted(a)
        for value in ('', 'a' * 33, '-1', '+1', '.1', '1_2', '1/2', '1 2', '1\n', 'é', '１', 1, None, [], {}, True):
            with self.subTest(value=value):
                a = about(); a['version'] = value; self.rejected(a)

    def test_bilingual_lengths_are_characters_not_utf8_bytes(self):
        cases = [(('tagline',), 1, 200), (('sections', 0, 'title'), 1, 80),
                 (('sections', 0, 'text'), 0, 2000), (('sections', 0, 'items', 0), 0, 300)]
        for path, minimum, maximum in cases:
            for language in ('it', 'en'):
                with self.subTest(path=path, language=language):
                    for length in (minimum, maximum):
                        a = about(); at(a, path)[language] = '🌍' * length; self.accepted(a)
                    for value in ['a' * (maximum + 1), '🌍' * (maximum + 1), None, True, 1, [], {}, '\ud800'] + ([''] if minimum else []):
                        a = about(); at(a, path)[language] = value; self.rejected(a)

    def test_section_and_item_count_boundaries(self):
        for count in (1, 8):
            a = about(); a['sections'] = [{**deepcopy(a['sections'][0]), 'id': 's' + str(i)} for i in range(count)]; self.accepted(a)
        for sections in ([], [about()['sections'][0]] * 9, None, {}, '', 1, True, [None]):
            a = about(); a['sections'] = sections; self.rejected(a)
        for count in (1, 12):
            a = about(); a['sections'][0]['items'] *= count; self.accepted(a)
        for items in ([], about()['sections'][0]['items'] * 13, None, {}, '', 1, True, [None], ['plain string']):
            a = about(); a['sections'][0]['items'] = items; self.rejected(a)

    def test_section_ids_unique_bounded_and_exact_pattern(self):
        for value in ('a', 'a' * 32, 'a-1', 'section-2'):
            a = about(); a['sections'][0]['id'] = value; self.accepted(a)
        for value in ('', 'a' * 33, 'A', 'a_b', '1a', '-a', 'a/b', 'a b', 'a\n', 'é', None, 1, [], {}):
            with self.subTest(value=value):
                a = about(); a['sections'][0]['id'] = value; self.rejected(a)
        a = about(); a['sections'].append(deepcopy(a['sections'][0])); self.rejected(a)
        a['sections'][1]['id'] = 'another'; self.accepted(a)

    def test_section_requires_text_or_items_and_validates_both_when_present(self):
        a = about(); s = a['sections'][0]; del s['text']; del s['items']; self.rejected(a)
        for field, value in (('text', None), ('items', [])):
            a = about(); a['sections'][0][field] = value; self.rejected(a)
        a = about(); a['sections'][0]['text'] = {'it': '', 'en': ''}; a['sections'][0]['items'] = [{'it': '', 'en': ''}]; self.accepted(a)

    def test_closed_keys_at_every_object_level(self):
        paths = [(), ('tagline',), ('sections', 0), ('sections', 0, 'title'), ('sections', 0, 'text'), ('sections', 0, 'items', 0)]
        for path in paths:
            with self.subTest(path=path):
                a = about(); at(a, path)['unexpected'] = 'PRIVATE refused marker'; self.rejected(a, 'unknown_key')
        for value in (None, [], '', 1, True): self.rejected(value)
        for key in ('publisher', 'version', 'tagline', 'sections'):
            a = about(); del a[key]; self.rejected(a)
        for key in ('id', 'title'):
            a = about(); del a['sections'][0][key]; self.rejected(a)
        for path in paths[1:]:
            if path == ('sections', 0): continue
            for language in ('it', 'en'):
                a = about(); del at(a, path)[language]; self.rejected(a)
            for value in (None, [], 'text', 1, True):
                a = about(); at(a, path[:-1])[path[-1]] = value; self.rejected(a)

    def test_duplicate_json_keys_nonfinite_and_unicode_refuse_fixed_codes(self):
        m = self.install(about()); body = json.dumps(m)
        for key, value in [('about', m['about']), ('publisher', m['about']['publisher']), ('version', m['about']['version']),
                           ('tagline', m['about']['tagline']), ('sections', m['about']['sections']), ('id', 'purpose'),
                           ('title', m['about']['sections'][0]['title']), ('text', m['about']['sections'][0]['text']),
                           ('items', m['about']['sections'][0]['items']), ('it', 'Dati locali'), ('it', 'A cosa serve'),
                           ('it', 'Riga uno\nRiga due'), ('it', 'Un elemento')]:
            with self.subTest(key=key, value_type=type(value).__name__):
                pair = json.dumps(key) + ': ' + json.dumps(value); self.assertIn(pair, body)
                (self.base / 'plugin.json').write_text(body.replace(pair, pair + ', ' + pair, 1))
                self.refused('invalid_json')
        for invalid in (b'\xff', b'{"about":NaN}', b'{"about":' + b'[' * 2000 + b']' * 1999 + b'}'):
            (self.base / 'plugin.json').write_bytes(invalid); self.refused('invalid_json')
        a = about(); a['tagline']['en'] = '\ud800'
        m = manifest(); m['about'] = a; (self.base / 'plugin.json').write_text(json.dumps(m)); self.refused('bad_value')

    def test_manifest_64k_exact_limit_and_growth_after_stat(self):
        self.assertEqual(plugins.PANEL_BYTES, 2 * 1024 * 1024); self.assertEqual(plugin_settings.LIMIT, 16384)
        a = about(); a['sections'] = [{'id': 's' + str(i), 'title': {'it': 'T', 'en': 'T'}, 'text': {'it': 'é' * 2000, 'en': 'é' * 2000}} for i in range(3)]
        m = self.install(a); body = json.dumps(m, ensure_ascii=False).encode(); self.assertGreater(len(body), 16384)
        path = self.base / 'plugin.json'; path.write_bytes(body)
        self.assertEqual(plugins.discover(self.home)['plugins'], [m])
        exact = body + b' ' * (65536 - len(body)); path.write_bytes(exact)
        self.assertEqual(plugins.discover(self.home)['plugins'], [m])
        path.write_bytes(exact + b' '); self.refused('too_large')
        self.assertEqual(plugins.MANIFEST_BYTES, 65536)
        path.write_bytes(body); inode = path.stat().st_ino; real = os.read
        def growing(fd, size):
            return b' ' * size if os.fstat(fd).st_ino == inode else real(fd, size)
        with patch.object(plugins.os, 'read', side_effect=growing): self.refused('too_large')

    def test_about_keeps_nofollow_regular_file_and_read_only_boundaries(self):
        self.install(about()); target = self.base / 'plugin.json'; saved = self.base / 'saved'; target.rename(saved)
        try:
            target.symlink_to(saved); self.refused('symlink'); target.unlink()
            target.mkdir(); self.refused('not_regular'); target.rmdir()
            os.mkfifo(target); self.refused('not_regular'); target.unlink()
        finally:
            if target.is_symlink(): target.unlink()
            saved.rename(target)
        def inventory():
            return {str(p): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in self.home.rglob('*') if p.is_file()}
        before = inventory(); self.assertEqual(plugins.discover(self.home)['plugins'][0]['about'], about()); self.assertEqual(inventory(), before)


class AboutHTTP(AboutFixture, unittest.TestCase):
    def setUp(self):
        super().setUp(); self.registry = Path(self.temp.name) / 'registry'
        store = Store(self.registry, create=True)
        store.append('init', 'system', {'name': 'About', 'project': str(self.home), 'temporary': True, 'limits': {'plan': 3, 'code': 2, 'build': 3}})
        self.events = store.events(); store.close()
        with patch.dict(os.environ, {'HOME': str(self.home)}): self.server, self.token = make_server(self.registry)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        self.thread.start(); self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(5); self.assertFalse(self.thread.is_alive())

    def get(self, path='/api/state', *, token=True, host=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            headers = {'Host': host or '127.0.0.1:' + str(self.server.server_port)}
            if token: headers['X-Squadra-Token'] = self.token
            conn.request('GET', path, headers=headers); response = conn.getresponse()
            return response.status, dict(response.getheaders()), json.loads(response.read())
        finally: conn.close()

    def test_state_carries_exact_about_alongside_safe_settings_without_new_route(self):
        m = self.install(about()); m['settings'] = [{'id': 'key', 'kind': 'secret', 'label': {'it': 'Chiave', 'en': 'Key'}}]; self.write('plugin.json', m)
        plugin_settings.save(self.home, 'example', {'key': 'SyntheticSecret!123'})
        for route in ('/api/state', '/api/plugins'):
            status, headers, response = self.get(route); self.assertEqual(status, 200); self.assertEqual(headers['Content-Security-Policy'], POLICY)
            data = response['plugins'] if route == '/api/plugins' else response['plugins']['plugins']
            self.assertEqual(data[0]['about'], about()); self.assertEqual(data[0]['settings'][0], {**m['settings'][0], 'set': True})
            self.assertNotIn('SyntheticSecret!123', json.dumps(response))
        self.assertEqual(self.get('/api/plugin/example/about')[0], 400, 'no separate about endpoint')
        self.assertEqual(self.get(token=False)[0], 401); self.assertEqual(self.get(token=False, host='foreign.invalid')[0], 403)
        store = Store(self.registry, read_only=True)
        try: self.assertEqual(store.events(), self.events)
        finally: store.close()

    def test_invalid_about_is_refused_and_old_manifest_stays_compatible(self):
        a = about(); a['unexpected'] = 'PRIVATE invalid about'; self.install(a)
        status, _, state = self.get(); self.assertEqual(status, 200)
        self.assertEqual(state['plugins']['plugins'], []); self.assertEqual(state['plugins']['refused'], [{'name': 'example', 'code': 'unknown_key'}])
        self.assertNotIn('PRIVATE invalid about', json.dumps(state))
        self.assertEqual(self.get('/api/plugin/example')[::2], (409, {'error': 'unknown_key'}))
        self.write('plugin.json', manifest()); self.assertEqual(self.get()[2]['plugins']['plugins'], [manifest()])
