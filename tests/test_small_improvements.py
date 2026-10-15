"""Information is explicit; mandatory receipts and cache safety remain enforced."""
from copy import deepcopy
import datetime as dt
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import contextlib

import test_squadra as workflows
from test_usage_cache import fixture, KEY
import test_usage_live_cache_integration as cache_integration
from registry import Error, attention, presence, table, waits
from comms import communications
from squadra import dispatch, parser
import usage
import usage_cache

ROOT = Path(__file__).resolve().parents[1]


class Information(unittest.TestCase):
    def setUp(self):
        self.f = workflows.Workflow()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store = self.f.store

    def call(self, *args):
        return dispatch(parser().parse_args(list(args)), self.store)

    def send(self, ident, **extra):
        self.store.append('message', 'C', dict(id=ident, to='A', body='Progress only', **extra))

    def test_information_and_ordinary_messages_use_the_real_waits_and_presence(self):
        self.f.ack()
        self.send('normal'); self.send('false', info=False)
        self.send('info', info=True); self.send('info-read', info=True)
        before = self.store.state()['tasks']['T']['status']
        self.store.append('ack', 'A', {'message': 'info-read'})
        state = self.store.state(); at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)
        ack_ids = {w['id'] for w in waits(state, at) if w['kind'] == 'ack'}
        self.assertEqual(ack_ids, {'normal', 'false'})
        self.assertEqual({w['id'] for w in attention(state)['waiting'] if w['kind'] == 'ack'}, ack_ids)
        self.assertEqual(state['tasks']['T']['status'], before)
        self.assertIsNone(state['messages']['info']['ack'])
        self.assertEqual(next(p for p in presence(state, at) if p['agent'] == 'A')['last_answer'],
                         state['messages']['info-read']['ack']['utc'])
        self.assertEqual(self.call('doctor')['pending_ack'], ['normal', 'false'])
        self.assertIn('Messaggi senza ACK: 2.', table(state))
        for compact in (False, True):
            self.assertIn('Informazioni senza conferma richiesta: 2', table(state, compact=compact))
        self.store.append('ack', 'A', {'message': 'normal'})
        self.assertEqual(self.call('doctor')['pending_ack'], ['false'])

    def test_send_inbox_manual_and_queue_transport_identify_information(self):
        self.assertIn('--info', parser()._subparsers._group_actions[0].choices['send'].format_help())
        body = self.f.base / 'info.txt'; body.write_text('Status for the coordinator')
        self.call('send', '--actor', 'C', '--to', 'A', '--id', 'notice', '--info', '--body-file', str(body))
        info = next(m for m in self.call('inbox', '--agent', 'A') if m['id'] == 'notice')
        self.assertIs(info['info'], True); self.assertIsNone(info['ack'])
        manual = self.call('transport', '--actor', 'C', '--message', 'notice', '--via', 'manual')
        self.assertIn('no ACK required', manual['instruction'])
        self.assertNotIn('ACK notice', manual['instruction'])
        self.store.append('agent', 'A', dict(id='A', role='builder', identity='author', client='codex',
                          repo=str(self.f.repo), skills=[], session='synthetic-session'))
        calls = []
        def queue(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, '--thread --message' if '--help' in argv else 'queued', '')
        with patch('subprocess.run', side_effect=queue):
            receipt = self.call('transport', '--actor', 'C', '--message', 'notice', '--via', 'codex-queue')
        self.assertEqual(receipt['ack'], 'not required; information only')
        self.assertIn('no ACK required', calls[-1][-1]); self.assertNotIn('ACK via squadra ack', calls[-1][-1])
        self.store.project()
        self.assertIn('INFO', (self.store.root / 'squadra-posta.md').read_text())

    def test_writer_refuses_protected_fields_and_nonboolean_information(self):
        cases = [dict(info=v) for v in (1, 'true', None)]
        cases += [dict(info=True, **{key: value}) for key, value in
                  [('task', 'T'), ('ping', True), ('awaited', True), ('pool', [])]]
        for n, extra in enumerate(cases):
            with self.subTest(extra=extra), self.assertRaisesRegex(Error, 'info|Information'):
                self.send('bad-' + str(n), **extra)
        with self.assertRaisesRegex(Error, 'ACK'):
            self.store.append('start', 'A', {'task': 'T'})
        self.send('ordinary-info', info=True, related_task='T')
        with self.assertRaisesRegex(Error, 'ACK'):
            self.store.append('start', 'A', {'task': 'T'})
        self.f.ack(); self.store.append('start', 'A', {'task': 'T'})
        commands = [
            ['task','--actor','C','--id','Other','--owner','A','--title','Other',
             '--repo',str(self.f.repo),'--scope-file',str(self.f.scope)],
            ['deliver','--actor','A','--task','T'], ['ping','--actor','C','--to','A']]
        for argv in commands:
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors), self.assertRaises(SystemExit):
                parser().parse_args(argv + ['--info'])
            self.assertIn('unrecognized arguments: --info', errors.getvalue())

    def test_single_and_pool_handoffs_refuse_information_in_both_batch_orders(self):
        original = next(e['data'] for e in self.store.events() if e['kind'] == 'task')
        for pool in (False, True):
            ident = 'Pool' if pool else 'Single'
            task = {**deepcopy(original), 'id': ident, 'reviewer': None if pool else 'R'}
            if pool: task['pool'] = ['R', 'C']
            self.store.batch([('task', 'C', task, None), ('message', 'C',
                             dict(id='assign-'+ident, to='A', task=ident, body='Build'), None)])
            self.f.ack(ident); self.assertEqual(self.f.proof(task=ident)['status'], 'passed')
            saved = []
            with patch.object(self.store, 'batch', side_effect=lambda ops: saved.extend(ops)):
                self.call('deliver', '--actor', 'A', '--task', ident)
            for reverse in (False, True):
                ops = [(k, a, {**d, 'info': True} if k == 'message' else d, eid) for k,a,d,eid in saved]
                if reverse: ops.reverse()
                before = len(self.store.events())
                with self.subTest(pool=pool, reverse=reverse), self.assertRaisesRegex(Error, 'info|Information'):
                    self.store.batch(ops)
                self.assertEqual(len(self.store.events()), before, 'entire batch rolls back')
            self.store.batch(saved)
            t = self.store.state()['tasks'][ident]
            with self.assertRaisesRegex(Error, 'ACK|claim'):
                self.store.append('review', 'R', dict(task=ident, stage='code', verdict='accepted',
                                  binding=t['candidate']['sha256'], report=self.store.artifact(self.f.report), covers=[]))
            handoff = t['delivery']['handoffs'][0] if pool else t['delivery']['handoff']
            self.store.append('ack', 'R', {'message': handoff})
            self.assertIsNotNone(self.store.state()['messages'][handoff]['ack'])

    def test_information_communications_keep_transports_without_pending_age(self):
        self.f.ack()
        self.send('info', info=True)
        self.store.append('transport', 'C', dict(message='info', via='manual', delivered=False))
        state = self.store.state(); at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)
        view = communications(state, self.store.events(), waits(state, at), at)
        message = next(e for e in view['edges'] if e['kind'] == 'message' and e['message'] == 'info')
        self.assertEqual(message['state'], 'info'); self.assertIn('manual', message['flags'])
        pair = view['pairs'][0]
        self.assertEqual((pair['info'], pair['sent'], pair['queued'], pair['overdue']), (1,0,0,0))
        self.assertIsNone(pair['oldest_pending_seconds'])


class CacheNotice(unittest.TestCase):
    def setUp(self):
        self.f = cache_integration.UsageLiveCacheIntegration(); self.f.setUp(); self.addCleanup(self.f.doCleanups)

    def test_unsafe_home_notice_preserves_measured_data_and_cache_boundary(self):
        safe = self.f.collect(); self.assertNotIn('cache_off_unsafe_path', safe['coverage'].get('notices', []))
        self.f.populated()
        link = self.f.folder / 'home-link'; link.symlink_to(self.f.home, target_is_directory=True)
        usage._COLLECTORS.clear()
        with patch.dict(os.environ, HOME=str(link)):
            unsafe = self.f.collect()
        self.assertEqual(unsafe['coverage'].get('notices'), ['cache_off_unsafe_path'])
        for field in ('status','windows','models','tasks','days'):
            self.assertEqual(safe[field], unsafe[field], field)
        for field in ('status','reasons','sources','measured'):
            self.assertEqual(safe['coverage'][field], unsafe['coverage'][field], field)
        c = usage_cache.Cache(KEY, home=link)
        self.assertEqual(c.load(), {}); self.assertFalse(c.save(fixture()))
        self.assertEqual(c.notice, 'cache_off_unsafe_path')

    def test_missing_corrupt_and_nondirectory_cache_are_not_symlink_notices(self):
        c = usage_cache.Cache(KEY, home=self.f.home)
        self.assertEqual(c.load(), {}); self.assertIsNone(getattr(c, 'notice', None))
        self.assertTrue(c.save(fixture()))
        (c.folder/(KEY+'.json')).write_bytes(b'bad json')
        self.assertEqual(c.load(), {}); self.assertIsNone(getattr(c, 'notice', None))
        file = self.f.folder/'not-directory'; file.write_text('x')
        c = usage_cache.Cache(KEY, home=file)
        self.assertEqual(c.load(), {}); self.assertIsNone(getattr(c, 'notice', None))

    def test_each_symlinked_cache_directory_has_a_notice_without_changing_data(self):
        safe = self.f.collect()
        parts = ('.cache', 'squadra', 'usage-v1')
        for depth in range(len(parts)):
            with self.subTest(component=parts[depth]):
                home = self.f.folder/('home-'+str(depth)); home.mkdir(mode=0o700)
                parent = home
                for part in parts[:depth]:
                    parent /= part; parent.mkdir(mode=0o700)
                target = self.f.folder/('untouched-'+str(depth)); target.mkdir(mode=0o700)
                (parent/parts[depth]).symlink_to(target, target_is_directory=True)
                usage._COLLECTORS.clear()
                with patch.dict(os.environ, HOME=str(home)):
                    result = self.f.collect()
                self.assertEqual(result['coverage'].get('notices'), ['cache_off_unsafe_path'])
                for key in ('status','windows','models','tasks','days'):
                    self.assertEqual(result[key], safe[key], key)
                for key in ('status','reasons','sources','measured'):
                    self.assertEqual(result['coverage'][key], safe['coverage'][key], key)
                self.assertEqual(list(target.iterdir()), [], 'never writes through the link')


class DashboardNotice(unittest.TestCase):
    def test_information_labels_and_cache_notice_in_both_languages(self):
        from test_usage_page import state_fixture
        with tempfile.TemporaryDirectory(prefix='sq-small-ui-') as tmp:
            state = Path(tmp)/'state.json'; state.write_text(json.dumps(state_fixture()))
            result = subprocess.run(['node',str(ROOT/'tests/small_improvements_contract.js'),
                                     str(ROOT/'assets/dashboard.html'),str(state),'small',
                                     str(ROOT/'tests/fixtures/usage-prices.json')],
                                    capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('small improvements contract passed',result.stdout)


if __name__ == '__main__':
    unittest.main()
