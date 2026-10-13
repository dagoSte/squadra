"""Isolated bundle imports, read-only traps and registry preservation."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import test_sentry_plugin as fixture


class SentryBundle(fixture.SentryFixture, unittest.TestCase):
    def probe(self, args, expected=0, error=None):
        proc = subprocess.run([sys.executable, '-I', '-B', str(fixture.ROOT/'tests/sentry_bridge_probe.py'),
                               str(self.plugin/'bridge'), json.dumps([*args, '--root', str(self.root)])],
                              cwd=self.folder, env={k:v for k,v in (os.environ|{'HOME':str(self.home)}).items()
                                                   if k!='PYTHONPATH'}, capture_output=True,text=True,timeout=15)
        self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
        data=json.loads(proc.stdout)
        self.assertEqual(data['rc'],expected,data)
        self.assertEqual(data['error'],error,data)
        if error is None:
            self.assertEqual(data['trap_calls'],[])
            self.assertTrue(data['readonly_calls']);self.assertTrue(all(data['readonly_calls']))
        self.assertEqual(set(data['modules']),{'sentry_plugin','plugins','plugin_actions','plugin_settings','registry'})
        self.assertTrue(all(Path(p).parent==(self.plugin/'bridge').resolve() for p in data['modules'].values()))
        return data

    def test_all_installed_commands_preserve_registry_with_and_without_sidecars(self):
        def inventory():
            return {str(p.relative_to(self.root)):(hashlib.sha256(p.read_bytes()).hexdigest(),p.stat().st_mtime_ns,p.stat().st_size)
                    for p in self.root.rglob('*') if p.is_file() and p.name!='registry.sqlite3-shm'}
        self.store.artifact_bytes(b'Synthetic evidence','report.txt','test')
        self.store.project();rows=self.store.events()
        for writer_open in (True,False):
            if not writer_open:
                self.store.close()
                self.assertFalse((self.root/'registry.sqlite3-wal').exists())
                self.assertFalse((self.root/'registry.sqlite3-shm').exists())
            def command(args):
                before=inventory();reply=self.probe(args);after=inventory()
                if 'registry.sqlite3-wal' not in before and 'registry.sqlite3-wal' in after:
                    self.assertEqual(after.pop('registry.sqlite3-wal')[2],0)
                self.assertEqual(before,after)
                store=fixture.Store(self.root,read_only=True)
                try:self.assertEqual(store.events(),rows)
                finally:store.close()
                return json.loads(reply['stdout'])
            value=self.write('issues.json',self.spec)
            imported=command(['import','--organization','example-org','--project','example-app','--file',str(value)])
            command(['refresh']);command(['pending'])
            first=imported['ids'][0]
            if writer_open:
                fixture.actions.save(self.home,'sentry','analyse',self.key,[first])
                pending=command(['pending']);snapshot=self.write('snapshot.json',pending)
                result=self.write('result.json',{'schema':1,'results':[self.result(first)]})
                command(['record','--snapshot',str(snapshot),'--file',str(result)])
            else:
                command(['record','--snapshot',str(snapshot),'--file',str(result)])
        self.assertFalse(list(self.plugin.rglob('__pycache__')))

    def test_missing_registry_is_not_created_by_any_command(self):
        value=self.write('input.json',self.spec)
        for exists in (False,True):
            missing=self.folder/('empty' if exists else 'absent')
            if exists:missing.mkdir()
            for command in ('import','refresh','pending','record'):
                args=[command,'--root',str(missing)]
                if command in ('import','record'):args+=['--file',str(value)]
                if command=='import':args+=['--organization','example-org','--project','example-app']
                if command=='record':args+=['--snapshot',str(value)]
                self.assertEqual(self.cli(args,1),{'error':'registry_invalid'})
                self.assertEqual(missing.exists(),exists)
                if exists:self.assertEqual(list(missing.iterdir()),[])

    def test_mutants_trip_the_actual_write_and_readonly_traps(self):
        source=self.plugin/'bridge/sentry_plugin.py';original=source.read_text()
        for mutation,error,calls,opens in (
            (original.replace('Store(root, read_only=True)','Store(root, read_only=False)'), 'read_only trap',[],[False]),
            (original.replace('            events = store.events()', "            store.append('probe', 'probe', {})\n            events = store.events()"),
             'write trap: append',['append'],[True]),
        ):
            self.assertNotEqual(original,mutation);source.write_text(mutation)
            data=self.probe(['pending'],1,error)
            self.assertEqual(data['trap_calls'],calls);self.assertEqual(data['readonly_calls'],opens)
        source.write_text(original);self.probe(['pending'])

    def test_input_files_refuse_links_permissions_oversize_and_duplicate_json(self):
        path=self.write('input.json',self.spec)
        args=['import','--organization','example-org','--project','example-app','--file',str(path)]
        path.chmod(0o644);self.assertEqual(self.cli(args,1),{'error':'unsafe_permissions'})
        path.chmod(0o600);path.write_text('{"schema":1,"schema":1}')
        self.assertEqual(self.cli(args,1),{'error':'invalid_json'})
        path.write_bytes(b'x'*(fixture.bridge.LIMIT+1));self.assertEqual(self.cli(args,1),{'error':'too_large'})
        path.unlink();path.symlink_to(self.plugin/'examples/issues.json')
        self.assertEqual(self.cli(args,1),{'error':'symlink'})
