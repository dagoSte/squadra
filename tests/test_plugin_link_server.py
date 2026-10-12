"""Homepage and local PNG boundaries; only private files and loopback HTTP."""
import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import struct
import sys
import threading
import unittest
from unittest.mock import patch
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import plugins
import plugin_settings
from dashboard import make_server
from registry import Store
from test_brand import POLICY
from test_plugin_about_server import about
from test_plugins import Fixtures, KEY, manifest, panel


def chunk(kind, body):
    return struct.pack('!I', len(body)) + kind + body + struct.pack('!I', zlib.crc32(kind + body))


def png(width=1, height=1, size=None):
    header = chunk(b'IHDR', struct.pack('!IIBBBBB', width, height, 8, 6, 0, 0, 0))
    pixels = zlib.compress((b'\x00' + b'\x00\x00\x00\xff' * width) * height)
    body = b'\x89PNG\r\n\x1a\n' + header + chunk(b'IDAT', pixels)
    end = chunk(b'IEND', b'')
    if size is not None:
        body += chunk(b'tEXt', b'Comment\x00' + b'a' * (size - len(body) - len(end) - 20))
    return body + end


class Links(Fixtures, unittest.TestCase):
    def install(self, url):
        m = manifest(); m['about'] = about(); m['about']['homepage'] = url
        self.write('plugin.json', m)
        return m

    def reject(self, url):
        self.install(url)
        self.assertEqual(plugins.discover(self.home), {'plugins': [], 'refused': [{'name': 'example', 'code': 'bad_value'}]})
        self.refused('bad_value')

    def test_valid_urls_and_character_limit_preserve_exact_value(self):
        prefix = 'https://example.invalid/'
        for url in ('https://memoranda.dev', 'HTTPS://example.invalid/', 'https://example.invalid:8443/a?x=1&y=2',
                    'https://[::1]/', 'https://esempio.it/città', prefix + '%23anchor',
                    prefix + 'a' * (200 - len(prefix)), prefix + 'é' * (200 - len(prefix))):
            with self.subTest(url=url):
                m = self.install(url)
                self.assertEqual(plugins.discover(self.home), {'plugins': [m], 'refused': []})
                self.assertEqual(plugins.load_panel(self.home, 'example', KEY), panel())
        self.reject(prefix + 'a' * (201 - len(prefix)))
        self.reject(prefix + 'é' * (201 - len(prefix)))

    def test_hostile_schemes_authorities_fragments_and_types(self):
        urls = ('http://example.invalid', 'javascript:alert(1)', 'data:text/html,test', '//example.invalid',
                'https:example.invalid', 'https:///x', 'https://', 'https://?x', 'https://user@host',
                'https://user:secret@host', 'https://@host', 'https://host#', 'https://host/#part',
                'https://host:bad/', 'https://host:65536/', 'https://[broken/', 'https://host\\evil/',
                'https://host%00.invalid/', 'https://<host>/', 'https://host\uff0fother/',
                '', None, True, 1, {}, [])
        for url in urls:
            with self.subTest(url=url): self.reject(url)

    def test_whitespace_controls_and_bidi_at_each_url_position(self):
        chars = list(map(chr, range(33))) + list(map(chr, range(127, 160)))
        chars += ['\u00a0', '\u2000', '\u2028', '\u2029', '\u3000', '\u061c', '\u200e', '\u200f']
        chars += list(plugins.BIDI_CONTROLS)
        for char in chars:
            for url in (char + 'https://example.invalid', 'https://ex' + char + 'ample.invalid',
                        'https://example.invalid/' + char):
                with self.subTest(character=ord(char), url=url): self.reject(url)

    def test_old_shape_and_closed_manifest_still_refuse_forged_icon(self):
        self.assertEqual(plugins.discover(self.home), {'plugins': [manifest()], 'refused': []})
        for key in ('homepage', 'icon'):
            m = manifest(); m[key] = 'https://example.invalid'; self.write('plugin.json', m)
            self.refused('unknown_key')
        m = self.install('https://example.invalid'); m['about']['icon'] = 'data:image/png;base64,AAAA'
        self.write('plugin.json', m); self.refused('unknown_key')
        raw = json.dumps(self.install('https://example.invalid')).replace('"homepage":', '"homepage": "https://other.invalid", "homepage":')
        (self.base / 'plugin.json').write_text(raw); self.refused('invalid_json')


class Icons(Fixtures, unittest.TestCase):
    def assert_icon(self, body):
        (self.base / 'icon.png').write_bytes(body)
        expected = {**manifest(), 'icon': 'data:image/png;base64,' + base64.b64encode(body).decode('ascii')}
        self.assertEqual(plugins.discover(self.home), {'plugins': [expected], 'refused': []})
        self.assertEqual(plugin_settings.discover(self.home), {'plugins': [expected], 'refused': []})

    def assert_refusal(self, code):
        self.assertEqual(plugins.discover(self.home), {'plugins': [manifest()], 'refused': [{'name': 'example', 'code': code}]})
        self.assertEqual(plugins.load_panel(self.home, 'example', KEY), panel())

    def test_absent_and_valid_png_exact_bytes_and_dimension_edges(self):
        self.assertEqual(plugins.discover(self.home), {'plugins': [manifest()], 'refused': []})
        for width, height in ((1, 1), (256, 1), (1, 256), (256, 256)):
            with self.subTest(width=width, height=height): self.assert_icon(png(width, height))

    def test_invalid_signature_truncated_ihdr_and_dimensions(self):
        valid = png()
        bodies = [b'', b'<svg onload="alert(1)"></svg>', b'private-not-png', valid[1:], b'x' + valid[1:]]
        bodies += [valid[:n] for n in (8, 16, 23, 24, 32)]
        bodies += [valid[:8] + struct.pack('!I', n) + valid[12:] for n in (0, 12, 14, 2**32 - 1)]
        bodies += [valid[:12] + b'IDAT' + valid[16:]]
        for width, height in ((0, 1), (1, 0), (257, 1), (1, 257), (2**32 - 1, 1)):
            bodies.append(valid[:16] + struct.pack('!II', width, height) + valid[24:])
        for body in bodies:
            with self.subTest(header=body[:24]):
                (self.base / 'icon.png').write_bytes(body); self.assert_refusal('invalid_icon')

    def test_exact_byte_limit_and_growth_during_read(self):
        body = png(size=32768); self.assertEqual(len(body), 32768); self.assert_icon(body)
        (self.base / 'icon.png').write_bytes(png(size=32769)); self.assert_refusal('too_large')
        (self.base / 'icon.png').write_bytes(png())
        real_read = os.read
        def growing(fd, size):
            if os.fstat(fd).st_ino == (self.base / 'icon.png').stat().st_ino: return b'x' * size
            return real_read(fd, size)
        with patch.object(plugins.os, 'read', side_effect=growing): self.assert_refusal('too_large')

    def test_symlink_fifo_directory_and_io_error_leave_plugin_listed(self):
        path = self.base / 'icon.png'; target = Path(self.temp.name) / 'private.png'; target.write_bytes(png())
        path.symlink_to(target)
        try: self.assert_refusal('symlink')
        finally: path.unlink()
        path.symlink_to(target.with_name('missing'))
        try: self.assert_refusal('symlink')
        finally: path.unlink()
        path.mkdir(); self.assert_refusal('not_regular'); path.rmdir()
        os.mkfifo(path)
        try: self.assert_refusal('not_regular')
        finally: path.unlink()
        path.write_bytes(png()); real_open = os.open
        def denied(name, *args, **kwargs):
            if name == 'icon.png': raise PermissionError('PRIVATE SENTINEL')
            return real_open(name, *args, **kwargs)
        with patch.object(plugins.os, 'open', side_effect=denied): self.assert_refusal('io_error')

    def test_directory_replacement_keeps_open_descriptor_and_reads_are_inert(self):
        body = png(); (self.base / 'icon.png').write_bytes(body)
        script = self.base / 'plugin.py'; script.write_text('raise AssertionError("never execute")')
        before = {str(p): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                  for p in self.home.rglob('*') if p.is_file()}
        plugins.discover(self.home)
        self.assertEqual(before, {str(p): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                                 for p in self.home.rglob('*') if p.is_file()})
        saved = self.base.with_name('saved'); outside = Path(self.temp.name) / 'outside'; outside.mkdir()
        (outside / 'icon.png').write_bytes(png(2, 2)); real_open = os.open
        def replace(name, flags, **kwargs):
            fd = real_open(name, flags, **kwargs)
            if name == 'example': self.base.rename(saved); self.base.symlink_to(outside, target_is_directory=True)
            return fd
        with patch.object(plugins.os, 'open', side_effect=replace):
            found = plugins.discover(self.home)
        self.assertEqual(base64.b64decode(found['plugins'][0]['icon'].split(',')[1]), body)
        self.assertEqual(found['refused'], [])


class State(Fixtures, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.registry = Path(self.temp.name) / 'registry'
        store = Store(self.registry, create=True)
        try:
            store.append('init', 'system', {'name': 'Link tests', 'project': str(self.home), 'temporary': True,
                         'limits': {'plan': 3, 'code': 2, 'build': 3}})
            self.key = store.events()[0]['hash']
        finally: store.close()
        self.write('panels/' + self.key + '.json', panel())
        with patch.dict(os.environ, {'HOME': str(self.home)}): self.server, self.token = make_server(self.registry)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())

    def get(self, path, token=True, host=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            headers = {'Host': host or '127.0.0.1:' + str(self.server.server_port)}
            if token: headers['X-Squadra-Token'] = self.token
            conn.request('GET', path, headers=headers); response = conn.getresponse()
            return response.status, dict(response.getheaders()), json.loads(response.read())
        finally: conn.close()

    def test_actual_state_embeds_bytes_and_url_without_extra_event_read_or_route(self):
        m = manifest(); m['about'] = about(); m['about']['homepage'] = 'https://memoranda.dev'
        self.write('plugin.json', m); body = png(); (self.base / 'icon.png').write_bytes(body)
        store = Store(self.registry)
        try: before = store.events()
        finally: store.close()
        original = Store.events; calls = []
        def counted(store): calls.append(1); return original(store)
        with patch.object(Store, 'events', counted):
            status, headers, state = self.get('/api/state'); count = len(calls)
            self.assertEqual(status, 200); self.assertEqual(count, 1)
            expected = {**m, 'icon': 'data:image/png;base64,' + base64.b64encode(body).decode('ascii')}
            self.assertEqual(state['plugins'], {'key': self.key, 'plugins': [expected], 'refused': []})
            self.assertEqual(headers['Content-Security-Policy'], POLICY)
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertEqual(self.get('/api/plugins')[2]['plugins'], [expected])
            self.assertEqual(self.get('/api/plugin/example')[2]['panel'], panel())
            self.assertEqual(len(calls), count)
        self.assertEqual(self.get('/api/state', token=False)[0], 401)
        self.assertEqual(self.get('/api/state', host='foreign.invalid')[0], 403)
        self.assertEqual(self.get('/api/plugin/example/icon.png')[0], 400)
        store = Store(self.registry)
        try: self.assertEqual(store.events(), before)
        finally: store.close()

    def test_bad_icon_does_not_hide_plugin_and_removal_clears_previous_data(self):
        path = self.base / 'icon.png'; path.write_bytes(png())
        self.assertIn('icon', self.get('/api/state')[2]['plugins']['plugins'][0])
        path.write_bytes(b'PRIVATE INVALID ICON')
        self.assertEqual(self.get('/api/state')[2]['plugins'], {'key': self.key, 'plugins': [manifest()],
                         'refused': [{'name': 'example', 'code': 'invalid_icon'}]})
        self.assertEqual(self.get('/api/plugin/example')[2]['panel'], panel())
        path.unlink()
        self.assertEqual(self.get('/api/state')[2]['plugins'], {'key': self.key, 'plugins': [manifest()], 'refused': []})
