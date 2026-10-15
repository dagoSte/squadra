"""Check saved action requests using private files and local HTTP, without executing them."""
from copy import deepcopy
import errno
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import dashboard
import plugin_actions as actions
import plugin_settings as settings
import plugins
from registry import Store
from test_brand import POLICY
from test_plugins import Fixtures, KEY, manifest, panel, audited_state
from test_plugin_settings import tree

ROOT = Path(__file__).resolve().parents[1]


def declaration():
    m = manifest(); m['actions'] = [{'id': 'send', 'label': {'it': 'Invia per revisione', 'en': 'Send for review'}}]
    return m


def selectable():
    p = panel(); p['items'][0]['selectable'] = True
    p['items'].append({**deepcopy(p['items'][0]), 'id': 'two'})
    return p


class ActionFixture(Fixtures):
    def setUp(self):
        super().setUp(); self.write('plugin.json', declaration()); self.write('panels/' + KEY + '.json', selectable())

    def save(self, ids, key=KEY, action='send'):
        return actions.save(self.home, 'example', action, key, ids)

    def error(self, call, code):
        with self.assertRaises(plugins.Refused) as caught: call()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)
        self.assertNotIn(str(self.home), repr(caught.exception))

    @property
    def request_file(self):
        return self.base / 'actions/send.json'


class Schemas(ActionFixture, unittest.TestCase):
    def test_actions_closed_schema_legacy_empty_and_label_bounds(self):
        for m in (manifest(), {**manifest(), 'actions': []}, declaration()):
            self.write('plugin.json', m); self.assertEqual(plugins.discover(self.home)['plugins'], [m])
        m = declaration(); m['actions'][0]['label']['it'] = 'é' * 2048
        self.write('plugin.json', m); self.assertEqual(plugins.discover(self.home)['plugins'], [m])
        invalid = []
        for value in (None, {}, True, [None], declaration()['actions'] * 2):
            invalid.append({**manifest(), 'actions': value})
        for key, value in [('id', '../x'), ('id', 'A'), ('id', 'a' * 33), ('id', []), ('label', {'it': 'x'}),
                           ('label', {'it': 'é' * 2049, 'en': 'ok'}), ('extra', True)]:
            m = declaration(); m['actions'][0][key] = value; invalid.append(m)
        for m in invalid:
            with self.subTest(m=m):
                self.write('plugin.json', m); self.assertEqual(plugins.discover(self.home)['plugins'], [])

    def test_selectable_strict_bool_and_draft_only_with_legacy_control(self):
        self.assertEqual(plugins._panel(panel()), panel())
        for status in plugins.STATUSES:
            p = selectable(); p['items'][0]['status'] = status; p['items'][0]['selectable'] = False
            self.assertEqual(plugins._panel(p), p)
            if status != 'draft':
                p['items'][0]['selectable'] = True; self.error(lambda: plugins._panel(p), 'bad_value')
        for value in ('true', 1, 0, None, {}, []):
            p = selectable(); p['items'][0]['selectable'] = value
            self.error(lambda: plugins._panel(p), 'bad_value')
        p = selectable(); p['items'][0]['other'] = True
        self.error(lambda: plugins._panel(p), 'unknown_key')

    def test_invalid_ids_no_effect_and_undeclared_action(self):
        for ids in (None, {}, 'one', [True], [''], ['one', 'one'], ['a' * 4097], ['\ud800'], ['one'] * 501):
            before = tree(self.home); self.error(lambda: self.save(ids), 'bad_value'); self.assertEqual(tree(self.home), before)
        for action, code in (('other', 'undeclared_action'), ('../send', 'bad_name'), ('send%2fother', 'bad_name')):
            before = tree(self.home); self.error(lambda: self.save(['one'], action=action), code); self.assertEqual(tree(self.home), before)
        self.error(lambda: self.save([]), 'no_pending'); self.assertFalse((self.base / 'actions').exists())

    def test_current_panel_eligibility_unknown_ambiguous_and_stale_ids(self):
        before = tree(self.home); self.error(lambda: self.save(['other']), 'not_selectable'); self.assertEqual(tree(self.home), before)
        for change in ('missing', 'unselectable', 'sent', 'duplicate'):
            p = selectable()
            if change == 'missing': p['items'] = p['items'][1:]
            if change == 'unselectable': del p['items'][0]['selectable']
            if change == 'sent': p['items'][0].update(status='sent', selectable=False)
            if change == 'duplicate': p['items'].append(deepcopy(p['items'][0]))
            self.write('panels/' + KEY + '.json', p)
            before = tree(self.home); self.error(lambda: self.save(['one']), 'not_selectable'); self.assertEqual(tree(self.home), before)
        (self.base / 'panels' / (KEY + '.json')).unlink(); self.error(lambda: self.save(['one']), 'not_selectable')
        self.write('panels/' + KEY + '.json', selectable()); self.assertEqual(self.save(['one'])['ids'], ['one'])

    def test_stored_envelope_size_and_500_ids_admitted_before_side_effect(self):
        p = selectable(); template = p['items'][0]
        p['items'] = [{**template, 'id': str(i)} for i in range(500)]
        self.write('panels/' + KEY + '.json', p)
        self.assertEqual(len(self.save([str(i) for i in range(500)])['ids']), 500)
        self.save([])
        ids = [str(i) + 'a' * 4090 for i in range(17)]
        p['items'] = [{**template, 'id': name} for name in ids]; self.write('panels/' + KEY + '.json', p)
        before = tree(self.home); self.error(lambda: self.save(ids), 'too_large'); self.assertEqual(tree(self.home), before)


class Storage(ActionFixture, unittest.TestCase):
    def test_save_replace_retry_cancel_preserve_order_and_wire_projection(self):
        first = self.save(['two', 'one']); self.assertEqual(first['ids'], ['two', 'one'])
        self.assertRegex(first['requested'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        body = json.loads(self.request_file.read_text())
        self.assertEqual(body, {'schema': 1, 'key': KEY, **first})
        before = tree(self.home); self.assertEqual(self.save(['two', 'one']), first); self.assertEqual(tree(self.home), before)
        second = self.save(['one']); self.assertEqual(second['ids'], ['one'])
        self.assertEqual(actions.read(self.home, 'example', 'send', KEY), second)
        for name, mode in [('actions', 0o700), ('actions/send.lock', 0o600), ('actions/send.json', 0o600)]:
            self.assertEqual(stat.S_IMODE((self.base / name).stat().st_mode), mode)
        self.assertEqual(self.request_file.stat().st_nlink, 1)
        self.assertIsNone(self.save([])); self.assertFalse(self.request_file.exists())
        before = tree(self.home); self.error(lambda: self.save([]), 'no_pending'); self.assertEqual(tree(self.home), before)
        self.assertEqual(actions.read(self.home, 'example', 'send', KEY), None)

    def test_cancel_pending_even_if_panel_removed_and_foreign_registry_isolated(self):
        original = self.save(['one']); other = 'b' * 64
        self.write('panels/' + other + '.json', selectable())
        self.assertIsNone(actions.read(self.home, 'example', 'send', other))
        before = tree(self.home)
        self.error(lambda: self.save(['two'], key=other), 'registry_mismatch')
        self.error(lambda: self.save([], key=other), 'registry_mismatch')
        self.assertEqual(tree(self.home), before)
        (self.base / 'panels' / (KEY + '.json')).unlink()
        self.assertEqual(actions.read(self.home, 'example', 'send', KEY), original)
        self.assertIsNone(self.save([]))

    def test_symlink_every_component_and_no_outside_write(self):
        self.save(['one'])
        for relative in ('.squadra', '.squadra/plugins', '.squadra/plugins/example', '.squadra/plugins/example/plugin.json',
                         '.squadra/plugins/example/panels', '.squadra/plugins/example/panels/' + KEY + '.json',
                         '.squadra/plugins/example/actions', '.squadra/plugins/example/actions/send.lock',
                         '.squadra/plugins/example/actions/send.json'):
            with self.subTest(path=relative):
                target = self.home / relative; saved = target.with_name(target.name + '-saved'); target.rename(saved)
                old = tree(saved) if saved.is_dir() else saved.read_bytes()
                try:
                    target.symlink_to(saved, target_is_directory=saved.is_dir())
                    self.error(lambda: self.save(['two']), 'symlink')
                    self.assertEqual(tree(saved) if saved.is_dir() else saved.read_bytes(), old)
                finally: target.unlink(); saved.rename(target)

    def test_unsafe_modes_owners_hardlinks_fifo_and_directories(self):
        self.save(['one'])
        for name in ('actions', 'actions/send.lock', 'actions/send.json'):
            target = self.base / name; mode = stat.S_IMODE(target.stat().st_mode)
            try:
                target.chmod(0o755 if target.is_dir() else 0o644)
                self.error(lambda: self.save(['two']), 'unsafe_permissions')
            finally: target.chmod(mode)
        with patch.object(settings.os, 'getuid', return_value=-1): self.error(lambda: self.save(['two']), 'unsafe_permissions')
        for name in ('send.lock', 'send.json'):
            target = self.base / 'actions' / name; saved = target.with_name(name + '-saved'); target.rename(saved)
            try:
                os.link(saved, target); self.error(lambda: self.save(['two']), 'unsafe_permissions'); target.unlink()
                os.mkfifo(target); self.error(lambda: self.save(['two']), 'not_regular'); target.unlink()
                target.mkdir(); self.error(lambda: self.save(['two']), 'not_regular'); target.rmdir()
            finally: saved.rename(target)

    def test_corrupt_bounded_closed_state_and_missing_lock_refuse_without_repair(self):
        self.save(['one']); good = self.request_file.read_bytes(); obj = json.loads(good)
        bad = [b'{', good.replace(b'"schema":1', b'"schema":1,"schema":1')]
        for field, value in [('schema', True), ('key', 'wrong'), ('ids', []), ('ids', ['one', 'one']),
                             ('requested', '2026-99-99T00:00:00Z'), ('requested', '2026-10-11T00:00:00+00:00'), ('extra', 1)]:
            bad.append(json.dumps({**obj, field: value}).encode())
        for body in bad:
            self.request_file.write_bytes(body); before = tree(self.home)
            self.error(lambda: self.save(['two']), 'request_invalid'); self.assertEqual(tree(self.home), before)
            found = actions.discover(self.home, KEY)
            self.assertIsNone(found['plugins'][0]['actions'][0]['pending'])
            self.assertEqual(found['refused'], [{'name': 'example', 'code': 'request_invalid'}])
        self.request_file.write_bytes(good + b' ' * (65536 - len(good)))
        self.assertEqual(actions.read(self.home, 'example', 'send', KEY)['ids'], ['one'])
        self.request_file.write_bytes(self.request_file.read_bytes() + b' ')
        self.error(lambda: self.save(['two']), 'too_large')
        self.request_file.write_bytes(good); (self.base / 'actions/send.lock').unlink()
        before = tree(self.home); self.error(lambda: self.save(['two']), 'request_invalid'); self.assertEqual(tree(self.home), before)

    def test_lock_is_process_safe_and_consumer_compare_delete_keeps_replacement(self):
        self.save(['one']); lock = self.base / 'actions/send.lock'
        code = "import fcntl,os,sys; f=open(sys.argv[1],'rb'); fcntl.flock(f,fcntl.LOCK_EX); print('locked',flush=True); sys.stdin.readline()"
        child = subprocess.Popen([sys.executable, '-B', '-c', code, str(lock)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'locked'); before = tree(self.home)
            self.error(lambda: self.save(['two']), 'busy'); self.assertEqual(tree(self.home), before)
        finally:
            child.communicate('\n', timeout=5); self.assertEqual(child.returncode, 0)
        fd = os.open(self.base / 'actions', os.O_RDONLY | os.O_DIRECTORY)
        try:
            with actions.locked(fd, 'send'): snapshot = actions.current(fd, 'send')
            replacement = self.save(['two'])
            with actions.locked(fd, 'send'):
                current = actions.current(fd, 'send')
                if (current['requested'], current['ids']) == (snapshot['requested'], snapshot['ids']): os.unlink('send.json', dir_fd=fd)
            self.assertEqual(actions.read(self.home, 'example', 'send', KEY), replacement)
            with actions.locked(fd, 'send'):
                self.assertEqual(actions.current(fd, 'send')['ids'], replacement['ids'])
                os.unlink('send.json', dir_fd=fd); os.fsync(fd)
            self.assertIsNone(actions.read(self.home, 'example', 'send', KEY))
        finally: os.close(fd)

    def test_lock_recheck_catches_panel_and_manifest_changes(self):
        self.save(['one']); original = fcntl.flock
        for change in ('panel', 'manifest'):
            self.write('plugin.json', declaration()); self.write('panels/' + KEY + '.json', selectable())
            def changed(fd, flags):
                original(fd, flags)
                if change == 'panel':
                    p = selectable(); p['items'][1].update(status='sent', selectable=False); self.write('panels/' + KEY + '.json', p)
                else: self.write('plugin.json', manifest())
            old = self.request_file.read_bytes()
            with patch.object(actions.fcntl, 'flock', side_effect=changed):
                self.error(lambda: self.save(['two']), 'not_selectable' if change == 'panel' else 'undeclared_action')
            self.assertEqual(self.request_file.read_bytes(), old)

    def test_atomic_short_writes_and_failures_preserve_target_and_remove_own_temp(self):
        self.save(['one']); original = os.write
        with patch.object(settings.os, 'write', side_effect=lambda fd,b: original(fd,b[:3])): self.save(['two'])
        for function in ('write', 'fsync', 'replace'):
            before = self.request_file.read_bytes()
            with patch.object(settings.os, function, side_effect=OSError(errno.ENOSPC, 'PRIVATE SENTINEL')):
                self.error(lambda: self.save(['one']), 'save_failed')
            self.assertEqual(self.request_file.read_bytes(), before)
            self.assertFalse(list((self.base / 'actions').glob('.tmp-*')))
        self.assertEqual(self.save(['one'])['ids'], ['one'])

    def test_post_publication_fsync_error_is_unconfirmed_and_cancel_does_not_recreate(self):
        self.save(['one']); original = os.fsync; calls = []
        def fail_directory(fd):
            calls.append(fd)
            if stat.S_ISDIR(os.fstat(fd).st_mode): raise OSError(errno.EIO, 'private backend')
            return original(fd)
        with patch.object(settings.os, 'fsync', side_effect=fail_directory):
            self.error(lambda: self.save(['two']), 'save_failed')
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(actions.read(self.home, 'example', 'send', KEY)['ids'], ['two'])
        with patch.object(settings.os, 'fsync', side_effect=fail_directory):
            self.error(lambda: self.save([]), 'save_failed')
        self.assertFalse(self.request_file.exists())
        self.error(lambda: self.save([]), 'no_pending')

    def test_two_process_writers_and_no_plugin_execution(self):
        self.save(['one'])
        code = """import json,sys
sys.path.insert(0,sys.argv[1])
import plugin_actions as a, plugins
sys.stdin.readline()
try: result={'saved':a.save(sys.argv[2],'example','send',sys.argv[3],[sys.argv[4]])}
except plugins.Refused as e: result={'error':e.code}
print(json.dumps(result))
"""
        children = [subprocess.Popen([sys.executable, '-B', '-c', code, str(ROOT/'scripts'), str(self.home), KEY, item],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    for item in ('one', 'two')]
        try:
            for child in children: child.stdin.write('go\n'); child.stdin.flush()
            results = []
            for child in children:
                out, err = child.communicate(timeout=5); self.assertEqual(child.returncode, 0, err); results.append(json.loads(out))
            self.assertTrue(any('saved' in result for result in results))
            for result, item in zip(results, ('one', 'two')):
                if 'saved' in result: self.assertEqual(result['saved']['ids'], [item])
                else: self.assertEqual(result, {'error': 'busy'})
            self.assertIn(actions.read(self.home, 'example', 'send', KEY)['ids'], [['one'], ['two']])
        finally:
            for child in children:
                if child.poll() is None: child.terminate(); child.communicate(timeout=5)
        (self.base/'__init__.py').write_text("raise AssertionError('plugin must never execute')")
        with patch.object(subprocess, 'Popen', side_effect=AssertionError('no child')), \
             patch.object(os, 'system', side_effect=AssertionError('no shell')), \
             patch.object(socket, 'create_connection', side_effect=AssertionError('no connection')):
            self.assertEqual(self.save(['two'])['ids'], ['two'])

    def test_directory_replacement_cannot_redirect_open_descriptors(self):
        self.save(['one']); outside = Path(self.temp.name) / 'outside'; outside.mkdir()
        original = os.open; saved = self.base.with_name('saved')
        def renamed(name, flags, *args, **kwargs):
            fd = original(name, flags, *args, **kwargs)
            if name == 'example': self.base.rename(saved); self.base.symlink_to(outside, target_is_directory=True)
            return fd
        with patch.object(plugins.os, 'open', side_effect=renamed): self.save(['two'])
        self.assertEqual(json.loads((saved / 'actions/send.json').read_text())['ids'], ['two'])
        self.assertEqual(list(outside.iterdir()), [])


class Http(ActionFixture, unittest.TestCase):
    def setUp(self):
        super().setUp(); self.registry = Path(self.temp.name) / 'registry'
        s = Store(self.registry, create=True)
        try:
            s.append('init', 'system', {'name':'Actions', 'project':str(self.home), 'temporary':True, 'limits':{'plan':3,'code':2,'build':3}})
            self.key = s.events()[0]['hash']; self.events = s.events()
        finally: s.close()
        self.write('panels/' + self.key + '.json', selectable())
        with patch.dict(os.environ, {'HOME':str(self.home)}): self.server, self.token = dashboard.make_server(self.registry)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start(); self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=5); self.assertFalse(self.thread.is_alive())

    def request(self, ids=None, *, path='/api/plugin/example/action/send', method='POST', headers=None, body=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        h = {'Host':'127.0.0.1:' + str(self.server.server_port), 'X-Squadra-Token':self.token, 'Content-Type':'application/json'}
        if headers:
            for k,v in headers.items():
                if v is None: h.pop(k,None)
                else: h[k]=v
        data = json.dumps({'ids':ids}).encode() if body is None and method=='POST' else body
        try:
            conn.request(method,path,data,h); r=conn.getresponse(); return r.status,dict(r.getheaders()),json.loads(r.read())
        finally: conn.close()

    def test_live_wire_pending_replacement_cancel_and_registry_unchanged(self):
        original=Store.events; calls=[]
        def counted(store): calls.append(1); return original(store)
        with patch.object(Store,'events',counted):
            _,headers,state=audited_state(self, lambda: self.request(path='/api/state',method='GET'))
            self.assertEqual(len(calls),2); self.assertIsNone(state['plugins']['plugins'][0]['actions'][0]['pending'])
            self.assertEqual(headers['Content-Security-Policy'],POLICY)
            status,_,body=self.request(['two','one']); self.assertEqual(status,200)
            self.assertEqual(set(body),{'saved','request'}); self.assertTrue(body['saved'])
            self.assertEqual(set(body['request']),{'ids','requested'}); self.assertEqual(body['request']['ids'],['two','one'])
            self.assertEqual(len(calls),2,'POST reuses verified event-1 identity after the startup audit')
        saved=json.loads(self.request_file.read_text()); self.assertEqual(saved['key'],self.key)
        state=self.request(path='/api/state',method='GET')[2]
        self.assertEqual(state['plugins']['plugins'][0]['actions'][0]['pending'],body['request'])
        self.assertEqual(self.request(['one'])[2]['request']['ids'],['one'])
        self.assertEqual(self.request([])[::2],(200,{'saved':True,'request':None}))
        self.assertEqual(self.request([])[::2],(409,{'error':'no_pending'}))
        s=Store(self.registry)
        try: self.assertEqual(s.events(),self.events)
        finally: s.close()
        self.assertFalse((self.base/'settings.json').exists())

    def test_guard_order_host_token_origin_and_fixed_errors(self):
        origin='http://127.0.0.1:'+str(self.server.server_port)
        for headers,code,status in [({'Host':'foreign.invalid','X-Squadra-Token':None},'host_refused',403),
                                    ({'X-Squadra-Token':None,'Origin':'https://foreign.invalid'},'token_required',401),
                                    ({'X-Squadra-Token':'wrong'},'token_required',401),
                                    ({'Origin':'https://foreign.invalid'},'origin_refused',403),
                                    ({'Origin':'null'},'origin_refused',403),
                                    ({'Content-Type':'text/plain'},'content_type',415),
                                    ({'Transfer-Encoding':'chunked'},'invalid_length',400)]:
            before=tree(self.home); self.assertEqual(self.request(['one'],headers=headers)[::2],(status,{'error':code})); self.assertEqual(tree(self.home),before)
        self.assertEqual(self.request(['one'],headers={'Origin':origin})[0],200)
        self.assertEqual(self.request(['one'],path='/api/plugin/example/action/unknown')[::2],(409,{'error':'undeclared_action'}))
        self.assertEqual(self.request(['one'],path='/api/plugin/example/action/%2e%2e')[::2],(400,{'error':'bad_name'}))

    def test_strict_json_no_client_key_and_byte_boundaries(self):
        for data,code in [(b'{','invalid_json'),(b'\xff','invalid_json'),(b'{"ids":[],"ids":[]}','invalid_json'),
                          (b'{"ids":NaN}','invalid_json'),(b'{"ids":["one"],"key":"'+KEY.encode()+b'"}','unknown_key'),
                          (b'{"ids":["one"],"requested":"x"}','unknown_key'),(b'[]','bad_value'),(b'{"ids":[1]}','bad_value')]:
            before=tree(self.home); self.assertEqual(self.request(body=data)[::2],(400,{'error':code})); self.assertEqual(tree(self.home),before)
        raw=b'{"ids":["one"]}'; raw+=b' '*(65536-len(raw))
        self.assertEqual(self.request(body=raw)[0],200)
        self.assertEqual(self.request(body=raw+b' ')[::2],(413,{'error':'too_large'}))

    def test_raw_duplicate_headers_truncation_and_timeout(self):
        port=self.server.server_port
        def raw(extra,body=b'{}',length='2',omit_length=False):
            c=socket.create_connection(('127.0.0.1',port),timeout=5)
            request=f'POST /api/plugin/example/action/send HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\nX-Squadra-Token: {self.token}\r\nContent-Type: application/json\r\n'
            if not omit_length: request+='Content-Length: '+length+'\r\n'
            try:
                c.sendall((request+extra+'\r\n').encode()+body); c.shutdown(socket.SHUT_WR)
                r=http.client.HTTPResponse(c); r.begin(); return r.status,json.loads(r.read())
            finally:c.close()
        for extra,code,status in [(f'Host: 127.0.0.1:{port}\r\n','host_refused',403),
                                 (f'X-Squadra-Token: {self.token}\r\n','token_required',401),
                                 ('Origin: null\r\nOrigin: null\r\n','origin_refused',403),
                                 ('Content-Length: 2\r\n','invalid_length',400)]:
            self.assertEqual(raw(extra),(status,{'error':code}))
        self.assertEqual(raw('',omit_length=True),(411,{'error':'length_required'}))
        self.assertEqual(raw('',length='bad'),(400,{'error':'invalid_length'}))
        self.assertEqual(raw('',length='20'),(400,{'error':'invalid_body'}))
        with patch.object(dashboard,'SETTINGS_BODY_TIMEOUT',0.03):
            c=socket.create_connection(('127.0.0.1',port),timeout=5)
            try:
                c.sendall(f'POST /api/plugin/example/action/send HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\nX-Squadra-Token: {self.token}\r\nContent-Type: application/json\r\nContent-Length: 20\r\n\r\n'.encode())
                r=http.client.HTTPResponse(c);r.begin();self.assertEqual((r.status,json.loads(r.read())),(408,{'error':'body_timeout'}))
            finally:c.close()

    def test_foreign_pending_hidden_and_corrupt_registry_refuses_post(self):
        self.save(['one']) # Different synthetic registry key, never exposed through this HTTP server.
        pending=self.request(path='/api/state',method='GET')[2]['plugins']['plugins'][0]['actions'][0]['pending']
        self.assertIsNone(pending)
        self.assertEqual(self.request(['two'])[::2],(409,{'error':'registry_mismatch'}))
        before=tree(self.home); s=Store(self.registry)
        try:
            s.db.execute('DROP TRIGGER no_update'); s.db.execute("UPDATE events SET data='{}' WHERE seq=1"); s.db.commit()
        finally:s.close()
        self.assertEqual(self.request(['two'])[::2],(409,{'error':'registry_invalid'})); self.assertEqual(tree(self.home),before)

    def test_lost_response_keeps_saved_request_and_explicit_retry_reads_it(self):
        handler=self.server.RequestHandlerClass; original=handler.answer; first=[True]
        def dropped(obj,status,body,*args):
            if obj.command=='POST' and status==200 and first[0]: first[0]=False;raise OSError('private lost reply')
            return original(obj,status,body,*args)
        with patch.object(handler,'answer',dropped):
            with self.assertRaises(http.client.RemoteDisconnected): self.request(['one'])
        pending=self.request(path='/api/state',method='GET')[2]['plugins']['plugins'][0]['actions'][0]['pending']
        self.assertEqual(pending['ids'],['one']); self.assertEqual(self.request(['one'])[2],{'saved':True,'request':pending})


class PublicDeclaration(unittest.TestCase):
    def test_published_json_examples_have_real_v1_shape(self):
        text=(ROOT/'references/work.md').read_text()
        examples=[json.loads(body) for body in re.findall(r'```json\n(.*?)\n```',text,re.S) if '"form": "da-ricordare/1"' in body]
        self.assertGreaterEqual(len(examples),2)
        for value in examples:
            self.assertEqual(set(value),{'form','task','answer','items'}); self.assertEqual(value['form'],'da-ricordare/1')
            self.assertIn(value['answer'],('yes','no')); self.assertIsInstance(value['items'],list)
            for item in value['items']:
                self.assertIn(item['type'],('decision','constraint','trap')); self.assertIn(item['certainty'],('sure','likely'))
                self.assertTrue(item['evidence'])
                if item['private_content']=='yes': self.assertNotIn('sentence',item)
                else: self.assertLessEqual(len(item['sentence']),1500)
                if item['type']=='decision' and item['private_content']=='no': self.assertTrue(item['who'])
        self.assertTrue(any(v['answer']=='no' and not v['items'] for v in examples))
