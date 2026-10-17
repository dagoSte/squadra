"""Full audit boundaries and both authenticated projections, using synthetic registries."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import registry
import registry_cache as cache
import registry_current as current
import recovery
import squadra
from dashboard import VerifiedView
from runtime import execute
import test_squadra as workflows


class RegistryAuditTests(unittest.TestCase):
    def setUp(self):
        self.fixture=workflows.Workflow('test_assignment_needs_recipient_ack')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        for name in ('base','root','repo','store','scope','report','home'):
            setattr(self,name,getattr(self.fixture,name))
        env=patch.dict(os.environ,{'HOME':str(self.home),'CODEX_HOME':str(self.home/'codex'),
                                  'CLAUDE_CONFIG_DIR':str(self.home/'claude')})
        env.start(); self.addCleanup(env.stop)
        self.store.append('message','C',{'id':'old-message','to':'A','body':'Original retained content'})
        self.store.append('message','C',{'id':'new-message','to':'A','body':'Latest retained content'})
        self.store.append('ack','A',{'message':'new-message'})

    def rows(self):
        return [tuple(row) for row in self.store.db.execute('SELECT * FROM events ORDER BY seq')]

    def files(self):
        return {str(p.relative_to(self.root)):(hashlib.sha256(p.read_bytes()).hexdigest(),p.stat().st_mtime_ns)
                for p in self.root.rglob('*') if p.is_file() and not p.name.endswith('-shm')}

    def cli(self,*argv):
        out,err=io.StringIO(),io.StringIO()
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
            result=squadra.main(['--root',str(self.root),*argv])
        return result,out.getvalue(),err.getvalue()

    def tamper_preanchor(self,store=None):
        store=store or self.store
        row=store.db.execute("SELECT seq,data FROM events WHERE kind='message' ORDER BY seq LIMIT 1").fetchone()
        self.assertLess(row['seq'],store.db.execute('SELECT max(seq) FROM events').fetchone()[0])
        body=json.loads(row['data']); body['body']='Changed without matching its hash'
        store.db.execute('DROP TRIGGER no_update')
        store.db.execute('UPDATE events SET data=? WHERE seq=?',(registry.canonical(body),row['seq']))

    def reviewed(self,release_gate=False):
        name='G' if release_gate else 'T'
        if release_gate:
            data={'id':name,'title':'Release fixture','owner':'A','repo':str(self.repo),
                  'base':registry.snapshot(self.repo)['head'],'scope':self.store.artifact(self.scope),
                  'proof':None,'expected':None,'depends':[],'full_gate':False,'release_gate':True}
            self.store.batch([('task','C',data,None),('message','C',{'id':'assign-G','to':'A','task':name,'body':'Build'},None)])
        self.store.append('ack','A',{'message':'assign-'+name})
        result=execute(self.store,'A',name,argv=[sys.executable,'-B','-c',"print('fixture passed')"])
        self.assertEqual(result['status'],'passed',result)
        result,out,err=self.cli('deliver','--actor','A','--task',name); self.assertEqual(result,0,err+out)
        result,out,err=self.cli('review','--actor','R','--task',name,'--stage','code','--verdict','accepted','--report',str(self.report))
        self.assertEqual(result,0,err+out)
        return name

    def current_leaf(self):
        namespace='current:'+cache.namespace(('pending_messages',))
        for row in self.store.db.execute('SELECT digest,data FROM derived_radix_nodes WHERE namespace=?',(namespace,)):
            data=json.loads(row['data'])
            if data.get('kind')=='leaf' and data.get('key')=='old-message':
                return namespace,row['digest'],row['data']
        self.fail('Positive fixture lacks its pending-message current leaf')

    def test_audit_matches_normalized_state_and_current_replay_without_writes(self):
        self.reviewed(); before=self.files(); result=self.store.audit()
        self.assertEqual(result['cache'],'matched')
        self.assertEqual(result['state'],self.store.state())
        tree,meta=cache.load(self.store)
        self.assertEqual(cache.plain(tree.root),self.store.normalized_state(result['events']))
        expected=current.build(result['events'],registry.apply,registry.initial)
        self.assertEqual(cache.plain(tree.current_tree.root),expected)
        self.assertEqual(set(expected['pending_messages']),{'old-message'})
        self.assertEqual(expected['latest_output'],{})
        self.assertEqual(self.files(),before)

    def test_audit_rejects_preanchor_event_tamper(self):
        self.assertEqual(self.store.audit()['cache'],'matched')
        self.tamper_preanchor(); before=self.rows()
        with self.assertRaisesRegex(registry.Error,'integrity'): self.store.audit()
        self.assertEqual(self.rows(),before)

    def test_doctor_rejects_preanchor_event_tamper(self):
        self.assertEqual(self.cli('doctor')[0],0)
        self.tamper_preanchor(); before=self.rows(); result,out,err=self.cli('doctor')
        self.assertEqual(result,1); self.assertIn('integrity',err); self.assertEqual(self.rows(),before)

    def test_checkpoint_rejects_preanchor_event_tamper(self):
        self.store.checkpoint(self.base/'valid-checkpoint')
        self.tamper_preanchor(); before=self.rows()
        with self.assertRaisesRegex(registry.Error,'integrity'): self.store.checkpoint(self.base/'bad-checkpoint')
        self.assertFalse((self.base/'bad-checkpoint').exists()); self.assertEqual(self.rows(),before)

    def test_save_rejects_preanchor_event_tamper(self):
        result=recovery.save(self.store,self.base/'valid-save',repos=[self.repo],temporary=True)
        self.assertEqual(result['status'],'saved'); self.tamper_preanchor(); before=self.rows()
        with self.assertRaisesRegex(registry.Error,'integrity'):
            recovery.save(self.store,self.base/'bad-save',repos=[self.repo],temporary=True)
        self.assertFalse((self.base/'bad-save'/'MANIFEST.json').exists()); self.assertEqual(self.rows(),before)

    def test_restore_rechecks_chain_after_package_checksums_match(self):
        package=self.base/'saved'; recovery.save(self.store,package,repos=[self.repo],temporary=True)
        self.assertEqual(recovery.restore_check(package,self.base/'valid-restore')['status'],'passed')
        saved=registry.Store(package/'registry')
        try: self.tamper_preanchor(saved)
        finally: saved.close()
        (package/'CHECKSUMS.json').write_text(registry.canonical(recovery.inventory(package))+'\n')
        recovery.verify_package(package)
        with self.assertRaisesRegex(registry.Error,'integrity'): recovery.restore_check(package,self.base/'bad-restore')
        result=json.loads((self.base/'bad-restore'/'RESTORE-RESULT.json').read_text())
        self.assertEqual(result['status'],'failed'); self.assertNotIn('registry_integrity',result)

    def test_dashboard_startup_rejects_preanchor_event_tamper(self):
        good=VerifiedView(self.root)
        try: self.assertEqual(len(good.read()[0]),len(self.store.events()))
        finally: good.close()
        self.tamper_preanchor(); before=self.rows(); bad=VerifiedView(self.root)
        try:
            with self.assertRaisesRegex(registry.Error,'integrity'): bad.read()
        finally: bad.close()
        self.assertEqual(self.rows(),before)

    def test_ordinary_check_and_close_do_not_replay_whole_history(self):
        name=self.reviewed()
        with patch.object(registry.Store,'events',side_effect=AssertionError('Ordinary command replayed all events')):
            result,out,err=self.cli('check','--actor','C','--task',name); self.assertEqual(result,0,err+out)
            result,out,err=self.cli('close','--actor','C','--task',name); self.assertEqual(result,0,err+out)
        self.assertEqual(self.store.state()['tasks'][name]['status'],'done')

    def test_healthy_failed_command_still_records_its_diagnostic(self):
        before=self.rows()
        result,out,err=self.cli('close','--actor','C','--task','T')
        self.assertEqual(result,1)
        events=self.store.events()
        self.assertEqual(len(events),len(before)+1)
        self.assertEqual(events[-1]['kind'],'diagnostic')
        self.assertEqual(events[-1]['data']['command'],'close')

    def test_healthy_release_gate_close_positive_control(self):
        name=self.reviewed(release_gate=True)
        result,out,err=self.cli('close','--actor','C','--task',name)
        self.assertEqual(result,0,err+out)
        self.assertEqual(self.store.state()['tasks'][name]['status'],'done')

    def test_direct_store_release_gate_close_positive_control(self):
        name=self.reviewed(release_gate=True)
        before=len(self.rows())
        self.store.append('close','C',{'task':name})
        self.assertEqual(len(self.rows()),before+1)
        self.assertEqual(self.store.state()['tasks'][name]['status'],'done')
        self.assertFalse(self.store.db.in_transaction)

    def test_direct_store_release_gate_close_rejects_preanchor_tamper(self):
        name=self.reviewed(release_gate=True)
        self.tamper_preanchor(); before=self.rows()
        with self.assertRaisesRegex(registry.Error,'integrity'):
            self.store.append('close','C',{'task':name})
        self.assertEqual(self.rows(),before)
        self.assertFalse(self.store.db.in_transaction)

    def test_release_gate_close_rejects_preanchor_event_tamper(self):
        name=self.reviewed(release_gate=True)
        self.assertEqual(self.cli('check','--actor','C','--task',name)[0],0)
        self.tamper_preanchor(); before=self.rows()
        result,out,err=self.cli('close','--actor','C','--task',name)
        self.assertEqual(result,1); self.assertIn('integrity',err); self.assertEqual(self.rows(),before)

    def test_audit_detects_current_index_leaf_omission_and_substitution(self):
        self.assertEqual(self.store.audit()['cache'],'matched')
        namespace,digest,original=self.current_leaf(); before=self.rows()
        for mode in ('omitted','substituted'):
            with self.subTest(mode=mode):
                try:
                    if mode=='omitted':
                        self.store.db.execute('DELETE FROM derived_radix_nodes WHERE namespace=? AND digest=?',(namespace,digest))
                    else:
                        data=json.loads(original); data['value']['value']['scalar']='forged-message'
                        self.store.db.execute('UPDATE derived_radix_nodes SET data=? WHERE namespace=? AND digest=?',
                                              (cache.canonical(data),namespace,digest))
                    result=self.store.audit()
                    self.assertEqual(result['state']['messages']['old-message']['body'],'Original retained content')
                    self.assertEqual(result['cache'],'mismatch'); self.assertEqual(self.rows(),before)
                finally:
                    self.store.db.execute('INSERT OR REPLACE INTO derived_radix_nodes(namespace,digest,data) VALUES(?,?,?)',
                                          (namespace,digest,original))

    def test_audit_detects_authenticated_but_incomplete_current_root(self):
        expected=current.build(self.store.events(),registry.apply,registry.initial)
        forged=json.loads(json.dumps(expected)); forged['pending_messages'].pop('old-message')
        tree=cache.Tree.build(self.store.db,forged,domain='current')
        row=self.store.db.execute('SELECT body FROM derived_state_meta WHERE id=1').fetchone()
        meta=json.loads(row['body']); meta['current']=tree.flush()
        self.store.db.execute('UPDATE derived_state_meta SET body=?,digest=? WHERE id=1',
                              (cache.canonical(meta),cache.metadata_digest(meta)))
        result=self.store.audit(); self.assertEqual(result['cache'],'mismatch')
        self.assertIsNone(result['state']['messages']['old-message']['ack'])


if __name__=='__main__': unittest.main()
