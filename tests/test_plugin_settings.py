"""Declared settings: real private files/HTTP, never a live plugin or credential."""
from contextlib import redirect_stderr
from copy import deepcopy
import fcntl
import http.client
import io
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import dashboard
import plugin_settings as settings
import plugins
from registry import Store
from test_plugins import Fixtures, manifest, panel
from test_brand import POLICY

ROOT = Path(__file__).resolve().parents[1]
SECRET = 'Synthetic-Only!"\\Key-123'


def declared():
    m = manifest()
    m['settings'] = [
        {'id': 'address', 'kind': 'url', 'label': {'it': 'Indirizzo', 'en': 'Address'}, 'help': {'it': 'Solo HTTPS', 'en': 'HTTPS only'}},
        {'id': 'agent_key', 'kind': 'secret', 'label': {'it': 'Chiave', 'en': 'Key'}, 'help': {'it': '<b>Testo</b>', 'en': '<b>Text</b>'}},
        {'id': 'label', 'kind': 'text', 'label': {'it': 'Nome', 'en': 'Name'}}]
    return m


def tree(path):
    return {str(p.relative_to(path)): (p.read_bytes(), p.stat().st_mode & 0o777, p.stat().st_mtime_ns)
            for p in path.rglob('*') if p.is_file() and not p.is_symlink()}


class SettingsFixture(Fixtures):
    def setUp(self):
        super().setUp()
        self.write('plugin.json', declared())

    def save(self, updates):
        return settings.save(self.home, 'example', updates)

    def read(self):
        return settings.read(self.home, 'example')

    def error(self, call, code):
        with self.assertRaises(plugins.Refused) as caught:
            call()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)
        self.assertNotIn(SECRET, repr(caught.exception))


class Declarations(SettingsFixture, unittest.TestCase):
    def test_closed_declaration_schema_and_boundaries(self):
        cases = []
        for fields in ([], declared()['settings'] * 2, {}, [None]):
            m = declared(); m['settings'] = fields; cases.append(m)
        for key, value in [('id', '../escape'), ('id', 'A'), ('id', '_' + 'a'), ('id', 'a' * 33), ('kind', 'password'), ('kind', {}), ('label', {'it': 'only'})]:
            m = declared(); m['settings'][0][key] = value; cases.append(m)
        m = declared(); m['settings'][1]['id'] = 'address'; cases.append(m)
        m = declared(); m['settings'][0]['unexpected'] = 'x'; cases.append(m)
        m = declared(); m['settings'][0]['help']['extra'] = 'x'; cases.append(m)
        m = declared(); m['settings'][0]['label']['it'] = 'é' * 513; cases.append(m)
        for m in cases:
            with self.subTest(m=m):
                self.write('plugin.json', m)
                self.assertEqual(plugins.discover(self.home)['plugins'], [])
                self.assertEqual(len(plugins.discover(self.home)['refused']), 1)
        m = declared(); m['settings'][0]['label']['it'] = 'é' * 512; self.write('plugin.json', m)
        self.assertEqual(plugins.discover(self.home)['plugins'], [m])
        self.write('plugin.json', manifest())
        self.assertEqual(settings.discover(self.home)['plugins'], [manifest()])
        self.error(lambda: self.save({}), 'not_configurable')

    def test_all_value_limits_and_test_only_loopback(self):
        for kind, value in [('url', 'https://example.invalid/'), ('url', 'https://example.invalid/' + 'a' * (2048 - 24)),
                            ('secret', '!'), ('secret', '~' * 4096), ('text', ''), ('text', 'é' * 256)]:
            settings.validate_value(kind, value)
        for kind, value in [('url', 'http://example.invalid/'), ('url', 'http://127.0.0.1:1234/'), ('url', 'https://user:pass@example.invalid'),
                            ('url', 'https://example.invalid/#'), ('url', 'https://example.invalid:99999/'), ('url', 'https:///'),
                            ('url', 'https://exam ple.invalid'), ('url', 'https://example.invalid/\\x'), ('url', 'https://example.invalid/\n'),
                            ('url', 'https://example.invalid/' + 'a' * 2048), ('url', 1), ('secret', ''), ('secret', 'a b'),
                            ('secret', '\x7f'), ('secret', 'é'), ('secret', 'a' * 4097), ('secret', '\n'), ('text', 'a' * 257),
                            ('text', '\x00'), ('text', '\u202e'), ('text', '\ud800')]:
            with self.subTest(kind=kind, value=str(value)[:30]):
                self.error(lambda: settings.validate_value(kind, value), 'bad_value')
        settings.validate_value('url', 'http://127.0.0.1:1234/', allow_loopback=True)
        for value in ('http://localhost:1234/', 'http://127.0.0.1:0/', 'http://127.0.0.1:1234/path'):
            self.error(lambda: settings.validate_value('url', value, allow_loopback=True), 'bad_value')


class Storage(SettingsFixture, unittest.TestCase):
    def test_save_read_clear_exact_bytes_modes_and_no_read_side_effects(self):
        before = tree(self.home); self.assertTrue(all(not s['set'] for s in self.read())); self.assertEqual(tree(self.home), before)
        result = self.save({'address': 'https://example.invalid/', 'agent_key': SECRET, 'label': ''})
        self.assertEqual(result, [{'id': 'address', 'set': True, 'value': 'https://example.invalid/'}, {'id': 'agent_key', 'set': True}, {'id': 'label', 'set': True, 'value': ''}])
        key = self.base / 'secrets/agent_key'; self.assertEqual(key.read_bytes(), SECRET.encode('ascii'))
        for name, mode in [('settings.json', 0o600), ('.settings.lock', 0o600), ('secrets', 0o700), ('secrets/agent_key', 0o600)]:
            self.assertEqual(stat.S_IMODE((self.base / name).stat().st_mode), mode)
        for p in self.home.rglob('*'):
            if p.is_file() and p != key: self.assertNotIn(SECRET.encode(), p.read_bytes(), str(p))
        before = tree(self.home)
        real_read=os.read
        def read_without_secret(fd, size):
            self.assertNotEqual(os.fstat(fd).st_ino,key.stat().st_ino,'secret bytes must not be read for projection')
            return real_read(fd,size)
        with patch.object(settings.os, 'read', side_effect=read_without_secret):
            self.assertEqual(self.read(), result)
        self.assertEqual(before, tree(self.home))
        self.save({'label': 'second'}); self.assertEqual(key.read_bytes(), SECRET.encode())
        self.save({'agent_key': None, 'address': None}); self.assertFalse(key.exists())
        self.assertEqual(self.read(), [{'id': 'address', 'set': False}, {'id': 'agent_key', 'set': False}, {'id': 'label', 'set': True, 'value': 'second'}])

    def test_rejected_patch_has_no_file_or_lock_side_effect(self):
        for values, code in [({'unknown': 'x'}, 'unknown_id'), ({'agent_key': SECRET, 'address': 'http://bad.invalid'}, 'bad_value'), ([], 'bad_value'), ({'agent_key': ''}, 'bad_value')]:
            with self.subTest(values=values):
                before = tree(self.home); self.error(lambda: self.save(values), code); self.assertEqual(tree(self.home), before)
                self.assertFalse((self.base / '.settings.lock').exists())

    def test_merged_byte_limit_before_any_mutation(self):
        m=declared();m['settings']=[{'id':key,'kind':'url','label':{'it':key,'en':key}} for key in ('one','two','three','four')]
        self.write('plugin.json',m)
        large='https://example.invalid/'+'🌍'*(2048-24)
        self.save({'one':large});self.save({'two':large})
        before=tree(self.home)
        self.error(lambda:self.save({'three':large}),'too_large')
        self.assertEqual(tree(self.home),before,'merged limit applies across successive small requests')
        self.assertLessEqual((self.base/'settings.json').stat().st_size,16384)

    def test_symlinked_settings_secrets_lock_and_components_refused(self):
        self.save({'agent_key': SECRET})
        for relative in ('.squadra', '.squadra/plugins', '.squadra/plugins/example', '.squadra/plugins/example/plugin.json',
                         '.squadra/plugins/example/settings.json', '.squadra/plugins/example/secrets',
                         '.squadra/plugins/example/secrets/agent_key', '.squadra/plugins/example/.settings.lock'):
            with self.subTest(path=relative):
                target=self.home/relative; old=target.with_name(target.name+'-saved'); target.rename(old)
                try:
                    target.symlink_to(old, target_is_directory=old.is_dir())
                    self.error(lambda: self.save({'agent_key': 'Replacement'}), 'symlink')
                    if relative.endswith('/agent_key'): self.assertEqual(old.read_bytes(), SECRET.encode())
                finally: target.unlink(); old.rename(target)

    def test_special_files_permissions_and_hardlinks(self):
        self.save({'agent_key': SECRET})
        for name in ('settings.json', 'secrets', 'secrets/agent_key', '.settings.lock'):
            target=self.base/name; old=stat.S_IMODE(target.stat().st_mode)
            try:
                target.chmod(0o755 if target.is_dir() else 0o644)
                self.error(lambda: self.save({}), 'unsafe_permissions')
            finally: target.chmod(old)
        target=self.base/'secrets/agent_key'; saved=self.base/'original'; target.rename(saved)
        try:
            os.mkfifo(target); self.error(self.read, 'not_regular'); target.unlink()
            target.mkdir(); self.error(self.read, 'not_regular'); target.rmdir()
            os.link(saved, target); self.error(self.read, 'unsafe_permissions'); target.unlink()
        finally: saved.rename(target)

    def test_corrupt_state_never_projects_a_secret_or_claims_saved(self):
        self.save({'agent_key': SECRET})
        p=self.base/'settings.json'; original=p.read_bytes()
        for body in (b'{', json.dumps({'schema':1,'updated':'bad','values':{},'secrets':[]}).encode(),
                     json.dumps({'schema':1,'updated':'2026-10-11T00:00:00Z','values':{'agent_key':SECRET},'secrets':[]}).encode()):
            p.write_bytes(body); self.error(self.read, 'settings_invalid')
            projected=settings.discover(self.home)['plugins'][0]
            self.assertEqual(projected['settings_error'], 'settings_invalid'); self.assertNotIn(SECRET, json.dumps(projected))
        p.write_bytes(original)
        (self.base/'secrets/agent_key').unlink(); self.error(self.read, 'settings_invalid')
        self.save({'agent_key': None}); self.assertFalse(self.read()[1]['set'])

    def test_partial_failure_is_not_success_and_explicit_repair_works(self):
        original=settings.atomic
        def fail_metadata(parent, name, body):
            if name=='settings.json': raise OSError(SECRET)
            return original(parent,name,body)
        with patch.object(settings,'atomic',side_effect=fail_metadata): self.error(lambda:self.save({'agent_key':SECRET}), 'save_failed')
        self.assertEqual((self.base/'secrets/agent_key').read_bytes(), SECRET.encode())
        self.error(self.read,'settings_invalid')
        self.save({'agent_key':'DeliberateReplacement'})
        self.assertTrue(self.read()[1]['set'])
        self.assertEqual((self.base/'secrets/agent_key').read_bytes(), b'DeliberateReplacement')

    def test_staging_and_fsync_failures_leave_no_temporary_secret(self):
        self.save({'agent_key':SECRET})
        for function in ('write','fsync','replace'):
            with self.subTest(function=function):
                with patch.object(settings.os,function,side_effect=OSError(SECRET)):
                    self.error(lambda:self.save({'agent_key':'Changed'}),'save_failed')
                self.assertEqual((self.base/'secrets/agent_key').read_bytes(),SECRET.encode())
                self.assertEqual(list(self.base.rglob('.tmp-*')),[])
        old=settings.os.fsync
        def fail_directory(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode): raise OSError(SECRET)
            return old(fd)
        with patch.object(settings.os,'fsync',side_effect=fail_directory): self.error(lambda:self.save({'agent_key':'CommittedButUnacknowledged'}),'save_failed')
        self.assertEqual((self.base/'secrets/agent_key').read_bytes(),b'CommittedButUnacknowledged')
        self.assertTrue(self.read()[1]['set'])

    def test_atomic_destination_race_does_not_follow_a_link(self):
        self.save({'agent_key':SECRET}); target=self.base/'secrets/agent_key'; outside=Path(self.temp.name)/'outside'; outside.write_text('outside')
        real=settings.os.fsync; changed=False
        def swap(fd):
            nonlocal changed
            if not changed and stat.S_ISREG(os.fstat(fd).st_mode):
                changed=True;target.unlink();target.symlink_to(outside)
            return real(fd)
        with patch.object(settings.os,'fsync',side_effect=swap): self.error(lambda:self.save({'agent_key':'Replacement'}),'save_failed')
        self.assertEqual(outside.read_text(),'outside');self.assertEqual(list(self.base.rglob('.tmp-*')),[])

    def test_process_lock_and_concurrent_updates(self):
        self.save({}); lock=os.open(self.base/'.settings.lock',os.O_RDONLY); fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        code="import sys;sys.path.insert(0,sys.argv[1]);import plugin_settings as s,plugins;\ntry:s.save(sys.argv[2],'example',{'label':'child'})\nexcept plugins.Refused as e:sys.exit(2 if e.code=='busy' else 3)"
        try:
            p=subprocess.run([sys.executable,'-B','-c',code,str(ROOT/'scripts'),str(self.home)],capture_output=True,timeout=10); self.assertEqual(p.returncode,2,p.stderr)
        finally: os.close(lock)
        code="import sys,time,json;sys.path.insert(0,sys.argv[1]);import plugin_settings as s,plugins\nfor i in range(100):\n try:s.save(sys.argv[2],'example',json.loads(sys.argv[3]));break\n except plugins.Refused as e:\n  if e.code!='busy':raise\n  time.sleep(.01)\nelse:sys.exit(3)"
        workers=[subprocess.Popen([sys.executable,'-B','-c',code,str(ROOT/'scripts'),str(self.home),json.dumps(v)],stdout=subprocess.PIPE,stderr=subprocess.PIPE) for v in ({'label':'concurrent'},{'address':'https://example.invalid/'})]
        for p in workers:
            stdout,stderr=p.communicate(timeout=10);self.assertEqual(p.returncode,0,stderr)
        self.assertEqual(self.read()[0]['value'],'https://example.invalid/');self.assertEqual(self.read()[2]['value'],'concurrent')

class HTTP(SettingsFixture, unittest.TestCase):
    def setUp(self):
        super().setUp(); self.registry=Path(self.temp.name)/'registry'
        store=Store(self.registry,create=True);store.append('init','system',{'name':'Settings','project':str(self.home),'temporary':True,'limits':{'plan':3,'code':2,'build':3}})
        self.key=store.events()[0]['hash'];store.close();self.write('panels/'+self.key+'.json',panel())
        with patch.dict(os.environ,{'HOME':str(self.home)}): self.server,self.token=dashboard.make_server(self.registry)
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.02},daemon=True);self.thread.start();self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown();self.server.server_close();self.thread.join(5);self.assertFalse(self.thread.is_alive())

    def request(self,values=None,*,headers=None,body=None,path='/api/plugin/example/settings',method='POST'):
        body=(json.dumps({'values':values or {}}).encode() if method=='POST' else b'') if body is None else body
        h={'Host':'127.0.0.1:'+str(self.server.server_port),'X-Squadra-Token':self.token,'Content-Type':'application/json','Content-Length':str(len(body))}
        for k,v in (headers or {}).items():
            if v is None:h.pop(k,None)
            else:h[k]=v
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        try:
            conn.putrequest(method,path,skip_host=True,skip_accept_encoding=True)
            for k,v in h.items():
                for item in v if isinstance(v,list) else [v]:conn.putheader(k,item)
            conn.endheaders(body);response=conn.getresponse();data=response.read()
            return response.status,dict(response.getheaders()),json.loads(data)
        finally:conn.close()

    def test_admission_order_fixed_errors_no_write(self):
        cases=[({'Host':'foreign.invalid','X-Squadra-Token':None},403,'host_refused'),({'Host':['127.0.0.1:'+str(self.server.server_port)]*2},403,'host_refused'),
               ({'X-Squadra-Token':None,'Origin':'https://foreign.invalid'},401,'token_required'),({'X-Squadra-Token':'wrong'},401,'token_required'),
               ({'X-Squadra-Token':[self.token,self.token]},401,'token_required'),({'Origin':'https://foreign.invalid','Content-Type':'text/plain'},403,'origin_refused'),
               ({'Origin':'null'},403,'origin_refused'),({'Origin':['http://127.0.0.1:'+str(self.server.server_port)]*2},403,'origin_refused'),
               ({'Content-Type':'application/x-www-form-urlencoded'},415,'content_type'),({'Content-Type':None},415,'content_type'),
               ({'Content-Type':['application/json']*2},415,'content_type'),({'Content-Type':'application/json; charset=latin1'},415,'content_type'),
               ({'Transfer-Encoding':'chunked'},400,'invalid_length'),({'Content-Length':None},411,'length_required'),({'Content-Length':['2','2']},400,'invalid_length'),
               ({'Content-Length':'-1'},400,'invalid_length'),({'Content-Length':'0'},400,'invalid_length'),({'Content-Length':'16385'},413,'too_large')]
        before=tree(self.home)
        for headers,status,code in cases:
            with self.subTest(headers={k:len(v) if k=='X-Squadra-Token' and v else v for k,v in headers.items()}):
                result=self.request({'agent_key':SECRET},headers=headers)
                self.assertEqual(result[::2],(status,{'error':code}));self.assertEqual(result[1]['Content-Security-Policy'],POLICY)
                self.assertFalse(any(k.lower().startswith('access-control-') for k in result[1]))
                self.assertEqual(tree(self.home),before)

    def test_envelope_ids_kinds_names_and_cross_origin_form(self):
        for body,code in [(b'{','invalid_json'),(b'\xff','invalid_json'),(b'{"values":{},"values":{}}','invalid_json'),(b'{"values":{"label":NaN}}','invalid_json'),
                          (b'{"values":{},"other":1}','unknown_key'),(b'{"values":[]}','bad_value'),(b'{"values":{"unknown":"x"}}','unknown_id'),
                          (b'{"values":{"agent_key":false}}','bad_value')]:
            with self.subTest(body=body):self.assertEqual(self.request(body=body)[::2],(400,{'error':code}))
        self.assertEqual(self.request(path='/api/plugin/../settings')[::2],(400,{'error':'bad_name'}))
        self.assertEqual(self.request(path='/api/plugin/absent/settings')[::2],(404,{'error':'not_found'}))
        self.assertEqual(self.request(body=b'agent_key=Synthetic',headers={'Origin':'https://foreign.invalid','Content-Type':'application/x-www-form-urlencoded'})[::2],(403,{'error':'origin_refused'}))
        self.assertEqual(self.request(body=b'agent_key=Synthetic',headers={'X-Squadra-Token':None,'Content-Type':'application/x-www-form-urlencoded'})[::2],(401,{'error':'token_required'}))
        self.assertFalse((self.base/'settings.json').exists())
        self.write('plugin.json',manifest());self.assertEqual(self.request()[::2],(409,{'error':'not_configurable'}))

    def test_save_state_clear_no_secret_echo_log_or_registry_write(self):
        store=Store(self.registry,read_only=True);before=store.events();store.close()
        logs=io.StringIO()
        with redirect_stderr(logs):
            response=self.request({'agent_key':SECRET,'address':'https://example.invalid/','label':'note'},headers={'Origin':'http://127.0.0.1:'+str(self.server.server_port),'Content-Type':'application/json; charset=utf-8'})
            self.assertEqual(response[0],200);self.assertNotIn(SECRET,json.dumps(response))
            for path in ('/api/state','/api/plugins','/api/plugin/example'):
                reply=self.request(path=path,method='GET');self.assertEqual(reply[0],200);self.assertNotIn(SECRET,json.dumps(reply))
                if path=='/api/state':
                    fields=reply[2]['plugins']['plugins'][0]['settings'];self.assertTrue(fields[1]['set']);self.assertNotIn('value',fields[1]);self.assertEqual(fields[0]['value'],'https://example.invalid/')
            self.assertEqual(self.request({'agent_key':None})[2]['settings'][1],{'id':'agent_key','set':False})
        self.assertNotIn(SECRET,logs.getvalue())
        store=Store(self.registry,read_only=True);self.assertEqual(store.events(),before);store.close()
        self.assertFalse((self.base/'secrets/agent_key').exists())

    def test_other_post_and_options_stay_refused(self):
        for method,path in [('POST','/api/plugins'),('POST','/api/plugin/example'),('POST','/api/state'),('OPTIONS','/api/plugin/example/settings')]:
            result=self.request(path=path,method=method);self.assertEqual(result[0],405);self.assertFalse(any(k.lower().startswith('access-control-') for k in result[1]));self.assertIn('except declared plugin settings',result[2]['error'])

    def test_exact_body_limit_truncation_and_absolute_timeout(self):
        body=b'{"values":{"label":"ok"}}';self.assertEqual(self.request(body=body+b' '*(16384-len(body)))[0],200)
        def start(length):
            sock=socket.create_connection(('127.0.0.1',self.server.server_port),timeout=2)
            request=f'POST /api/plugin/example/settings HTTP/1.0\r\nHost: 127.0.0.1:{self.server.server_port}\r\nX-Squadra-Token: {self.token}\r\nContent-Type: application/json\r\nContent-Length: {length}\r\n\r\n'
            sock.sendall(request.encode());return sock
        with start(10) as sock:
            sock.sendall(b'{}');sock.shutdown(socket.SHUT_WR);response=http.client.HTTPResponse(sock);response.begin();self.assertEqual(response.status,400);self.assertEqual(json.loads(response.read()),{'error':'invalid_body'})
        with patch.object(dashboard,'SETTINGS_BODY_TIMEOUT',.15):
            with start(100) as sock:
                started=time.monotonic();sock.sendall(b'{');time.sleep(.08);sock.sendall(b' ')
                response=http.client.HTTPResponse(sock);response.begin();self.assertEqual(response.status,408);self.assertEqual(json.loads(response.read()),{'error':'body_timeout'});self.assertLess(time.monotonic()-started,.8)

    def test_server_partial_save_and_lost_ack_do_not_expose_content(self):
        original=settings.atomic
        def fail(parent,name,body):
            if name=='settings.json':raise OSError(SECRET)
            return original(parent,name,body)
        with patch.object(settings,'atomic',side_effect=fail):self.assertEqual(self.request({'agent_key':SECRET})[::2],(500,{'error':'save_failed'}))
        state=self.request(path='/api/state',method='GET')[2];self.assertEqual(state['plugins']['plugins'][0]['settings_error'],'settings_invalid');self.assertNotIn(SECRET,json.dumps(state))
        self.assertEqual(self.request({'agent_key':None})[0],200)
        completed=threading.Event();save=settings.save
        def signal_saved(*args):
            result=save(*args);completed.set();return result
        with patch.object(settings,'save',side_effect=signal_saved):
            with socket.create_connection(('127.0.0.1',self.server.server_port),timeout=2) as sock:
                body=json.dumps({'values':{'agent_key':SECRET}}).encode()
                headers=f'POST /api/plugin/example/settings HTTP/1.0\r\nHost: 127.0.0.1:{self.server.server_port}\r\nX-Squadra-Token: {self.token}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n'
                sock.sendall(headers.encode()+body);sock.shutdown(socket.SHUT_RDWR)
            self.assertTrue(completed.wait(2))
        self.assertTrue(self.read()[1]['set']);self.assertEqual((self.base/'secrets/agent_key').read_bytes(),SECRET.encode())

    def test_javascript_settings_card_contract(self):
        node=shutil.which('node');self.assertIsNotNone(node,'Node required, not skipped')
        fixture=Path(self.temp.name)/'state.json';fixture.write_text(json.dumps(self.request(path='/api/state',method='GET')[2]))
        for scenario in ('card','save','poll','failure','schema'):
            with self.subTest(scenario=scenario):
                p=subprocess.run([node,str(ROOT/'tests/plugin_settings_contract.js'),str(ROOT/'assets/dashboard.html'),str(fixture),scenario],capture_output=True,text=True,timeout=30)
                self.assertEqual(p.returncode,0,p.stdout+p.stderr);self.assertIn('settings contract passed: '+scenario,p.stdout)
