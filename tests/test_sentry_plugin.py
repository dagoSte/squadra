"""Check the installed Sentry bridge with synthetic data and exact request generations."""
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tokenize
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'plugins/sentry/bridge'))
import sentry_plugin as bridge
import plugin_actions as actions
import plugin_store
import plugins
from registry import Store


class FixedClock(datetime):
    @staticmethod
    def now(_):
        return datetime(2026, 10, 13, 7, 0, tzinfo=timezone.utc)


class SentryFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sq-sentry-')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.home = self.folder / 'home'; self.home.mkdir(mode=0o700)
        self.root = self.folder / 'registry'
        self.store = Store(self.root, create=True); self.addCleanup(self.store.close)
        self.store.append('init', 'system', {'name': 'Synthetic Sentry', 'project': str(self.folder),
                                            'limits': {'plan': 3, 'code': 2, 'build': 3}})
        for name, role in [('C', 'coordinator'), ('A', 'builder')]:
            self.store.append('agent', name, {'id': name, 'role': role, 'identity': name, 'client': 'manual',
                                            'repo': str(self.folder), 'skills': [], 'session': ''})
        for name in ('T', 'OTHER'):
            self.store.append('task', 'C', {'id': name, 'owner': 'A', 'title': 'Analyse observed checkout failure',
                                           'repo': str(self.folder), 'base': 'a'*40, 'scope': {},
                                           'depends': [], 'proof': None, 'expected': None})
            self.store.append('message', 'C', {'id': 'assign-'+name, 'to': 'A', 'body': 'Synthetic assignment', 'task': name})
            self.store.append('ack', 'A', {'message': 'assign-'+name})
        self.key = self.store.events()[0]['hash']
        plugin_store.install(self.home, 'sentry', ROOT)
        self.plugin = self.home / '.squadra/plugins/sentry'
        self.spec = json.loads((self.plugin / 'examples/issues.json').read_text())

    def write(self, name, value):
        path = self.folder / name
        path.write_text(json.dumps(value)); path.chmod(0o600)
        return path

    def imported(self, value=None):
        return bridge.import_data(self.home, self.root, value or self.spec, 'example-org', 'example-app')['ids']

    def select(self, ids):
        actions.save(self.home, 'sentry', 'analyse', self.key, ids)
        return bridge.pending(self.home, self.root)

    def result(self, name, **kwargs):
        return {'id': name, 'task': 'T', 'note': 'Analysis task linked by the coordinator', **kwargs}

    def record(self, snapshot, *results):
        return bridge.record(self.home, self.root, snapshot, {'schema': 1, 'results': list(results)})

    def refuse(self, function, code):
        with self.assertRaises(plugins.Refused) as caught: function()
        self.assertEqual(caught.exception.code, code)

    @property
    def request(self): return self.plugin / 'actions/analyse.json'

    @property
    def journal(self): return self.plugin / ('observations/' + self.key + '.json')

    def panel(self): return plugins.load_panel(self.home, 'sentry', self.key)

    def cli(self, args, expected=0):
        args = list(args)
        if '--root' not in args: args += ['--root', str(self.root)]
        proc = subprocess.run([sys.executable, '-I', '-B', str(self.plugin / 'bridge/sentry_plugin.py'), *args], cwd=self.folder,
                              env={**os.environ, 'HOME': str(self.home)}, capture_output=True, text=True, timeout=15)
        self.assertEqual(proc.returncode, expected, proc.stdout + proc.stderr)
        return json.loads(proc.stderr if expected else proc.stdout)


class SentryBridge(SentryFixture, unittest.TestCase):
    def test_store_install_reinstall_keeps_runtime_data_and_private_modes(self):
        self.imported(); self.select(self.imported())
        def inventory():
            return {str(p.relative_to(self.plugin)): (p.read_bytes(), p.stat().st_mode & 0o777)
                    for p in self.plugin.rglob('*') if p.is_file()}
        before = inventory()
        self.assertEqual(plugin_store.install(self.home, 'sentry', ROOT)['state'], 'complete')
        self.assertEqual(before, inventory())
        for path in self.plugin.rglob('*'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)
        self.assertFalse((self.plugin / 'settings.json').exists())
        manifest = json.loads((self.plugin / 'plugin.json').read_text())
        self.assertNotIn('settings', manifest)
        self.assertEqual(manifest['results']['label']['en'], 'App errors')
        (self.plugin / 'README.md').write_text('Local changes')
        self.refuse(lambda: plugin_store.install(self.home, 'sentry', ROOT), 'modified_installation')

    def test_observation_identity_and_links_survive_repeated_import_and_omission(self):
        first = self.imported()[0]; snapshot = self.select([first]); self.record(snapshot, self.result(first))
        self.assertEqual(self.imported(), [first])
        changed = deepcopy(self.spec); changed['issues'][0]['title'] = 'Changed observed title'
        second = self.imported(changed)[0]; self.assertNotEqual(first, second)
        empty = deepcopy(self.spec); empty['issues'] = []; self.imported(empty)
        items = {x['id']: x for x in self.panel()['items']}
        self.assertEqual(set(items), {first, second}); self.assertFalse(items[first]['selectable'])
        self.assertTrue(items[second]['selectable']); self.assertEqual(items[first]['refs'], [{'task': 'T'}])
        self.assertEqual(items[first]['meta'][3]['value'], 'unresolved')
        self.assertEqual(items[first]['status'], 'sent')

    def test_missing_fields_are_unavailable_and_counts_require_a_window(self):
        self.spec['issues'] = [{'id': 'X', 'title': 'Synthetic issue'}]; self.imported()
        item = self.panel()['items'][0]
        self.assertEqual(item['text'], 'Non disponibile / Unavailable')
        self.assertEqual(item['meta'][5]['value'], 'Non disponibile / Unavailable')
        self.spec['issues'][0]['count'] = 0
        self.refuse(lambda: self.imported(), 'count_window_required')
        self.spec['issues'][0].update(count=False, window='Last day')
        self.refuse(lambda: self.imported(), 'bad_value')

    def test_missing_configuration_and_mcp_never_clear_a_request_or_invent_success(self):
        self.refuse(lambda: bridge.import_data(self.home, self.root, self.spec), 'not_configured')
        self.refuse(lambda: bridge.refresh(self.home, self.root), 'not_configured')
        first = self.imported()[0]; self.select([first]); before = self.request.read_bytes()
        unavailable = dict(self.spec, availability='unavailable', issues=[])
        self.imported(unavailable)
        self.assertIn('MCP unavailable', self.panel()['note']['en'])
        self.assertEqual(len(self.panel()['items']), 1); self.assertEqual(self.request.read_bytes(), before)
        self.assertEqual(self.cli(['refresh'])['availability'], 'unavailable')
        unavailable['issues'] = self.spec['issues']
        self.refuse(lambda: self.imported(unavailable), 'capability_unavailable')

    def test_scope_schema_and_limits_refuse_before_a_journal_is_written(self):
        for value, code in ((dict(self.spec, project='foreign'), 'scope_mismatch'),
                            (dict(self.spec, raw_event={}), 'unknown_key'),
                            (dict(self.spec, issues=self.spec['issues']*51), 'bad_value'),
                            (dict(self.spec, issues=self.spec['issues']*2), 'duplicate_issue'),
                            (dict(self.spec, observed='not-a-date'), 'bad_value')):
            with self.subTest(code=code): self.refuse(lambda: self.imported(value), code)
        self.assertFalse(self.journal.exists())
        self.imported()
        value = dict(self.spec, observed='2026-10-12T07:00:00Z')
        self.refuse(lambda: self.imported(value), 'stale_observation')
        foreign = dict(self.spec, project='other')
        self.refuse(lambda: bridge.import_data(self.home, self.root, foreign, 'example-org', 'other'), 'scope_mismatch')
        for name in ('raw_event', 'user', 'request', 'headers', 'password'):
            value = deepcopy(self.spec); value['issues'][0][name] = 'REJECTED'
            self.refuse(lambda: self.imported(value), 'unknown_key')

    def test_every_text_field_refuses_credential_shapes_without_outputting_them(self):
        payloads = ['sntry'+prefix+'_'+'SYNTHETIC_ONLY' for prefix in ('u','s','a','i')]
        payloads += ['https://synthetic-key@o0.ingest.sentry.io/1', 'https://key:secret@sentry.example.invalid/1',
                     'Authorization: Bearer SYNTHETIC_ONLY', 'Bearer SYNTHETIC_ONLY',
                     'api_key=SYNTHETIC_ONLY', 'password = SYNTHETIC_ONLY', 'token: SYNTHETIC_ONLY']
        paths = [('organization',), ('project',), ('observed',), ('availability',)]
        paths += [('issues',0,k) for k in ('id','title','status','severity','window','first_seen','last_seen','context')]
        for payload in payloads:
            for path in paths:
                with self.subTest(shape=payload.split(':')[0][:12], field=path):
                    value=deepcopy(self.spec); target=value
                    for name in path[:-1]: target=target[name]
                    target[path[-1]]=payload
                    self.refuse(lambda: self.imported(value), 'sensitive_text')
        self.assertFalse(self.journal.exists())
        value=deepcopy(self.spec); value['issues'][0]['title']=payloads[0]
        reply=self.cli(['import','--organization','example-org','--project','example-app',
                        '--file',str(self.write('rejected.json',value))],1)
        self.assertEqual(reply, {'error':'sensitive_text', 'refused':[{'index':1, 'error':'sensitive_text'}]})
        self.assertNotIn(payloads[0],json.dumps(reply))

    def test_email_addresses_in_title_context_and_association_are_refused(self):
        for field in ('title','context'):
            value=deepcopy(self.spec);value['issues'][0][field]='Error for synthetic.person@example.invalid'
            self.refuse(lambda:self.imported(value),'personal_text')
        first=self.imported()[0];snapshot=self.select([first])
        self.refuse(lambda:self.record(snapshot,self.result(first,note='synthetic.person@example.invalid')),'personal_text')
        self.assertTrue(self.request.exists())

    def test_import_refuses_only_sensitive_items_with_safe_one_based_indices(self):
        first = self.imported()[0]; self.select([first]); pending = self.request.read_bytes()
        for title, code in (
            ('Missing key: user_id in payload', 'sensitive_text'),
            ('TypeError: token: undefined', 'sensitive_text'),
            ('ValueError: invalid dsn: check settings', 'sensitive_text'),
            ('Authorization: header missing', 'sensitive_text'),
            ('Bearer SYNTHETIC_ONLY', 'sensitive_text'),
            ('Error for synthetic.person@example.invalid', 'personal_text'),
        ):
            with self.subTest(title=title):
                value = deepcopy(self.spec)
                value['issues'] = [value['issues'][0], {'id': 'REJECTED', 'title': title},
                                   {'id': 'GOOD', 'title': 'Another sanitised issue'},
                                   {'id': 'REJECTED-CONTEXT', 'title': 'Issue', 'context': title}]
                reply = self.cli(['import', '--organization', 'example-org', '--project', 'example-app',
                                  '--file', str(self.write('partial.json', value))])
                self.assertTrue(reply['saved']); self.assertEqual(len(reply['ids']), 2)
                self.assertEqual(reply['ids'][0], first)
                self.assertEqual(reply['refused'], [{'index': 2, 'error': code}, {'index': 4, 'error': code}])
                self.assertNotIn(title, json.dumps(reply))
                stored = json.loads(self.journal.read_text())['issues']
                self.assertEqual(set(stored), set(reply['ids']))
                self.assertEqual({x['observation']['issue']['id'] for x in stored.values()},
                                 {self.spec['issues'][0]['id'], 'GOOD'})
                self.assertEqual(self.request.read_bytes(), pending)

    def test_all_refused_import_preserves_existing_observations_panel_and_request(self):
        self.select(self.imported())
        paths = [self.journal, self.plugin / 'panels' / (self.key + '.json'), self.request]
        before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths]
        value = deepcopy(self.spec); value['observed'] = '2026-10-14T07:00:00Z'
        value['issues'] = [{'id': 'SECRET', 'title': 'Bearer SYNTHETIC_ONLY'},
                           {'id': 'EMAIL', 'title': 'synthetic.person@example.invalid'}]
        reply = self.cli(['import', '--organization', 'example-org', '--project', 'example-app',
                          '--file', str(self.write('all-refused.json', value))], 1)
        self.assertEqual(reply, {'error': 'sensitive_text', 'refused': [
            {'index': 1, 'error': 'sensitive_text'}, {'index': 2, 'error': 'personal_text'}]})
        self.assertEqual([(path.read_bytes(), path.stat().st_mtime_ns) for path in paths], before)

    def test_schema_error_in_one_issue_refuses_the_whole_import(self):
        self.select(self.imported())
        paths = [self.journal, self.plugin / 'panels' / (self.key + '.json'), self.request]
        before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths]
        for label, bad in (
            ('count without window', {'id': 'BAD', 'title': 'Ordinary failure', 'count': 3}),
            ('bad timestamp', {'id': 'BAD', 'title': 'Ordinary failure', 'first_seen': 'yesterday'}),
            ('missing title', {'id': 'BAD'}),
            ('not an object', 'plain string issue'),
        ):
            with self.subTest(label=label):
                value = deepcopy(self.spec)
                value['observed'] = '2026-10-14T07:00:00Z'
                value['issues'] = [value['issues'][0], bad,
                                   {'id': 'GOOD', 'title': 'Another sanitised issue'}]
                reply = self.cli(['import', '--organization', 'example-org', '--project', 'example-app',
                                  '--file', str(self.write('mixed-schema.json', value))], 1)
                self.assertNotIn('refused', reply)
                self.assertEqual([(path.read_bytes(), path.stat().st_mtime_ns) for path in paths], before)

    def test_sensitive_association_and_snapshot_text_never_reaches_storage(self):
        first=self.imported()[0];snapshot=self.select([first]);before=self.journal.read_bytes()
        for field in ('id','task','note','run'):
            result=self.result(first);result[field]='api_key=SYNTHETIC_ONLY'
            self.refuse(lambda:self.record(snapshot,result),'sensitive_text')
        for field in ('key','requested'):
            changed=deepcopy(snapshot);changed['request'][field]='Bearer SYNTHETIC_ONLY'
            self.refuse(lambda:self.record(changed),'sensitive_text')
        self.assertEqual(self.journal.read_bytes(),before)

    def test_task_and_run_associations_use_only_the_open_verified_registry(self):
        first=self.imported()[0];snapshot=self.select([first])
        before=self.store.events()
        self.refuse(lambda:self.record(snapshot,self.result(first,task='FOREIGN')),'unknown_task')
        self.refuse(lambda:self.record(snapshot,self.result(first,run='a'*32)),'foreign_run')
        self.store.append('run.start','A',{'id':'a'*32,'task':'OTHER','mode':'proof','status':'running'})
        self.refuse(lambda:self.record(snapshot,self.result(first,run='a'*32)),'foreign_run')
        self.store.append('run.start','A',{'id':'b'*32,'task':'T','mode':'proof','status':'running'})
        before=self.store.events();reply=self.record(snapshot,self.result(first,run='b'*32))
        self.assertTrue(reply['consumed']);self.assertEqual(self.store.events(),before)
        self.assertEqual(self.panel()['items'][0]['refs'],[{'task':'T','run':'b'*32}])
        self.assertEqual(self.panel()['items'][0]['status'],'sent')
        self.assertIn('does not resolve',self.panel()['note']['en'])

    def test_partial_selection_is_durable_and_only_complete_same_request_is_consumed(self):
        self.spec['issues'].append({'id':'SECOND','title':'Second synthetic issue'})
        first,second=self.imported();snapshot=self.select([first,second])
        self.assertFalse(self.record(snapshot,self.result(first))['complete'])
        self.assertTrue(self.request.exists())
        self.assertTrue(self.record(snapshot,self.result(second))['consumed'])
        self.assertFalse(self.request.exists())
        self.assertFalse(self.record(snapshot,self.result(first))['consumed'])
        self.refuse(lambda:self.record(snapshot,self.result(first,task='OTHER')),'association_conflict')

    def test_replacement_cancel_and_same_second_reselection_keep_new_generations(self):
        self.spec['issues'].append({'id':'SECOND','title':'Second synthetic issue'})
        first,second=self.imported()
        for variant in ('replace','cancel-reselect','a-b-a','cancel'):
            with self.subTest(variant=variant),patch.object(actions,'datetime',FixedClock):
                # Start with an unassociated observation for each independent generation.
                self.spec['issues'][0]['title']='Generation '+variant
                current=self.imported()[0];old=self.select([current])
                if variant=='replace': self.select([second])
                elif variant=='a-b-a': self.select([second]);self.select([current])
                else:
                    actions.save(self.home,'sentry','analyse',self.key,[])
                    if variant=='cancel-reselect': self.select([current])
                expected=self.request.read_bytes() if self.request.exists() else None
                self.assertFalse(self.record(old,self.result(current))['consumed'])
                self.assertEqual(self.request.read_bytes() if self.request.exists() else None,expected)
                if self.request.exists(): actions.save(self.home,'sentry','analyse',self.key,[])

    def test_durable_association_recovers_after_panel_write_failure(self):
        first=self.imported()[0];snapshot=self.select([first]);original=bridge.files.atomic
        calls=[]
        def fail_panel(parent,name,body):
            calls.append(name)
            if len(calls)==2: raise OSError('synthetic panel failure')
            return original(parent,name,body)
        with patch.object(bridge.files,'atomic',side_effect=fail_panel):
            with self.assertRaises(OSError):self.record(snapshot,self.result(first))
        self.assertTrue(self.request.exists())
        self.assertIn('association',json.loads(self.journal.read_text())['issues'][first])
        self.assertTrue(self.record(snapshot)['consumed'])
        self.assertEqual(self.panel()['items'][0]['status'],'sent')

    def test_failed_durable_write_keeps_the_request_and_has_no_false_outcome(self):
        first=self.imported()[0];snapshot=self.select([first]);before=self.journal.read_bytes()
        with patch.object(bridge.files,'atomic',side_effect=OSError('synthetic failed write')):
            with self.assertRaises(OSError):self.record(snapshot,self.result(first))
        self.assertEqual(self.journal.read_bytes(),before);self.assertTrue(self.request.exists())

    def test_foreign_request_is_left_alone_and_snapshot_cannot_change_meaning(self):
        first=self.imported()[0];snapshot=self.select([first])
        wrong=deepcopy(snapshot);wrong['key']='0'*64
        self.refuse(lambda:self.record(wrong),'registry_mismatch')
        wrong=deepcopy(snapshot);wrong['issues'][first]['observation']['issue']['title']='Rewritten'
        self.refuse(lambda:self.record(wrong),'observation_changed')
        data=json.loads(self.request.read_text());data['key']='0'*64
        self.request.write_text(json.dumps(data));before=self.request.read_bytes()
        self.assertIsNone(bridge.pending(self.home,self.root));self.assertEqual(self.request.read_bytes(),before)

    def test_unsafe_runtime_paths_missing_lock_and_malformed_files_refuse(self):
        first=self.imported()[0];self.select([first])
        for path in (self.home,self.home/'.squadra',self.home/'.squadra/plugins',self.plugin,self.plugin/'actions'):
            mode=path.stat().st_mode & 0o777;path.chmod(0o777)
            self.refuse(lambda:bridge.pending(self.home,self.root),'unsafe_permissions');path.chmod(mode)
        lock=self.plugin/'actions/analyse.lock';body=lock.read_bytes();lock.unlink()
        self.refuse(lambda:bridge.pending(self.home,self.root),'request_invalid')
        lock.write_bytes(body);lock.chmod(0o600)
        original=self.request.read_bytes();self.request.unlink();self.request.symlink_to(self.journal)
        self.refuse(lambda:bridge.pending(self.home,self.root),'symlink')
        self.request.unlink();self.request.write_bytes(b'{');self.request.chmod(0o600)
        self.refuse(lambda:bridge.pending(self.home,self.root),'request_invalid')
        self.request.write_bytes(original)

    def test_registry_corruption_and_absence_are_never_repaired_or_created(self):
        path=self.folder/'absent'
        self.refuse(lambda:bridge.pending(self.home,path),'registry_invalid');self.assertFalse(path.exists())
        self.store.db.execute("DROP TRIGGER no_update")
        self.store.db.execute("UPDATE events SET data='{}' WHERE seq=1");self.store.db.commit()
        self.refuse(lambda:self.imported(),'registry_invalid')
        self.assertFalse(self.journal.exists())

    def test_catalogue_sorts_both_bundles_in_both_languages_and_metadata_is_honest(self):
        from datetime import date
        for language in ('it','en'):
            value=plugin_store.catalogue(self.home,ROOT,lang=language)
            self.assertEqual(value['refused'],[])
            self.assertEqual([x['name'] for x in value['plugins']],['sentry','memoranda','playwright'])
            rows = [row for row in value['plugins'] if row['name'] == 'sentry']
            self.assertEqual(len(rows), 1)
            row=rows[0];self.assertEqual(row['store']['price'],'free');self.assertTrue(row['store']['mcp'])
            self.assertEqual(row['store']['licence'], 'MIT')
            self.assertIn('FREE · Sponsored by memoranda', row['description']['en'])
            self.assertIn('Gratis · Sviluppo finanziato da memoranda', row['description']['it'])
            self.assertEqual(row['store']['accepted_version'],row['about']['version'])
            date.fromisoformat(row['store']['review_date'])
            self.assertEqual(row['store']['source'],row['about']['homepage'])
        for name in ('plugins','plugin_actions','plugin_settings','registry'):
            self.assertEqual((self.plugin/'bridge'/f'{name}.py').read_bytes(),(ROOT/'scripts'/f'{name}.py').read_bytes())
        self.assertEqual((self.plugin/'SQUADRA-LICENSE').read_bytes(),(ROOT/'LICENSE').read_bytes())

    def test_docs_fix_scope_release_boundary_and_sanitisation_instructions(self):
        for name in ('README.md','README.it.md','AGENTS.md','LICENSE'):
            self.assertTrue((self.plugin/name).read_text().strip())
        en=(self.plugin/'README.md').read_text();it=(self.plugin/'README.it.md').read_text();agents=(self.plugin/'AGENTS.md').read_text()
        self.assertIn('synthetic MCP transport only',en);self.assertIn('trasporto MCP sintetico',it)
        self.assertIn('agent sanitises first',en);self.assertIn('L’agente depura',it)
        self.assertIn('backstop',agents);self.assertIn('already-created task',agents)
        self.assertIn('within existing permissions',agents);self.assertIn('never widens permissions',agents)
        self.assertIn('Store screenshots',en);self.assertIn('the first one is Playwright',en)
        self.assertIn('2026-10-13',en);self.assertNotIn('browser_navigate',agents)
        self.assertIn('Development funded by memoranda. Free for all users.', en)
        self.assertIn('Sviluppo finanziato da memoranda. Plugin gratuito per tutti.', it)
        self.assertEqual((self.plugin/'LICENSE').read_bytes(), (ROOT/'LICENSE').read_bytes())
        for document in (en, it, agents):
            self.assertNotIn('paid distribution route', document)
            self.assertNotIn('Utilizzo offerto', document)
        self.assertNotIn('AGENTS.md',(self.plugin/'bridge/sentry_plugin.py').read_text())
        self.assertFalse(list(self.plugin.rglob('__pycache__')))

    def test_publication_links_resolve_to_the_shipped_bundle_offline(self):
        from urllib.parse import urlsplit
        bundle = ROOT / 'plugins/sentry'
        manifest = json.loads((bundle / 'plugin.json').read_text())
        listing = json.loads((bundle / 'store.json').read_text())
        for url in (manifest['about']['homepage'], listing['source']):
            parsed = urlsplit(url)
            self.assertEqual((parsed.scheme, parsed.netloc, parsed.query, parsed.fragment),
                             ('https', 'github.com', '', ''))
            prefix = '/dagoSte/squadra/tree/main/'
            self.assertTrue(parsed.path.startswith(prefix))
            relative = Path(parsed.path.removeprefix(prefix))
            self.assertEqual(relative, Path('plugins') / manifest['name'])
            self.assertEqual((ROOT / relative).resolve(), bundle.resolve())
            for name in ('plugin.json', 'store.json', 'README.md', 'LICENSE'):
                self.assertEqual((ROOT / relative / name).read_bytes(), (self.plugin / name).read_bytes())


class SentryPage(SentryFixture, unittest.TestCase):
    def test_dashboard_js_contract_it_en_selection_and_task_navigation(self):
        self.spec['issues'].append({'id':'SECOND','title':'Second synthetic issue'})
        first,second=self.imported();self.record(self.select([first]),self.result(first))
        state=self.store.state();state['events']=self.store.events()
        state['plugins']=actions.discover(self.home,self.key);state['plugins']['key']=self.key
        fixture=self.write('state.json',state);payload=self.write('panel.json',self.panel())
        node=shutil.which('node');self.assertIsNotNone(node,'Node required; no skipped dashboard contract')
        run=subprocess.run([node,str(ROOT/'tests/sentry_page_contract.js'),str(ROOT/'assets/dashboard.html'),
                            str(fixture),'sentry',str(payload)],capture_output=True,text=True,timeout=45)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)
        self.assertIn('Sentry dashboard JS contract passed',run.stdout)
