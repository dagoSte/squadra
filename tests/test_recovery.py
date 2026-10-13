"""Check Git work recovery and refusal of invalid saved data before opening it."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

import test_squadra as support
from recovery import save, restore_check, verify_package
from registry import Error, Store, snapshot


class Recovery(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task
    cli = support.Workflow.cli

    def pack(self, **kw):
        path=self.base/'saved';result=save(self.store,path,temporary=True,**kw)
        return path,result

    def test_mixed_staged_unstaged_deleted_binary_executable_symlink_roundtrip(self):
        (self.repo/'delete.txt').write_text('delete me');self.git('add','.');self.git('commit','-qm','second base')
        self.git('branch','second-branch')
        (self.repo/'code.py').write_text('VALUE=2\n');self.git('add','code.py')
        (self.repo/'code.py').write_text('VALUE=3\n');(self.repo/'delete.txt').unlink()
        (self.repo/'new').mkdir();(self.repo/'new/binary').write_bytes(b'\0\xffsafe binary')
        exe=self.repo/'run';exe.write_text('echo synthetic\n');exe.chmod(0o755)
        (self.repo/'link').symlink_to('code.py')
        (self.repo/'.gitignore').write_text('ignored.txt\n');(self.repo/'ignored.txt').write_text('explicitly included')
        path,result=self.pack(includes=[str(self.repo/'ignored.txt')])
        self.assertEqual(result['destination'],'local checkpoint')
        restored=restore_check(path,self.base/'restored');self.assertEqual(restored['status'],'passed')
        root=Path(restored['repositories'][0]['restored'])
        self.assertEqual((root/'code.py').read_text(),'VALUE=3\n')
        staged=subprocess.check_output(['git','show',':code.py'],cwd=root);self.assertEqual(staged,b'VALUE=2\n')
        self.assertFalse((root/'delete.txt').exists());self.assertTrue((root/'run').stat().st_mode & 0o111)
        self.assertEqual(os.readlink(root/'link'),'code.py');self.assertEqual((root/'ignored.txt').read_text(),'explicitly included')
        refs=subprocess.check_output(['git','show-ref'],cwd=root);self.assertIn(b'second-branch',refs)
        self.assertTrue((self.base/'restored/RESUME.md').is_file())
        self.assertTrue(restored['restored_paused'])
        recovered=Store(self.base/'restored/registry')
        try:
            self.assertTrue(recovered.state()['paused'])
            self.assertEqual(recovered.events()[-1]['kind'],'restore.freeze')
            with self.assertRaisesRegex(Error,'paused'):recovered.append('start','A',{'task':'T'})
        finally:recovered.close()
        self.assertFalse(self.store.state()['paused'])

    def test_all_registered_checkouts_included(self):
        second=self.base/'second';shutil.copytree(self.repo,second)
        (second/'other.txt').write_text('other author WIP')
        self.store.append('agent','B',{'id':'B','role':'builder','identity':'builder-two','client':'manual','repo':str(second)})
        path,result=self.pack();self.assertEqual(len(result['repositories']),2)
        restored=restore_check(path,self.base/'restored')
        self.assertTrue(any((Path(x['restored'])/'other.txt').is_file() for x in restored['repositories']))

    def test_untracked_secret_excluded_and_tracked_secret_refused(self):
        marker='SECRET-MARKER-unique\n';(self.repo/'.env').write_text(marker)
        path,result=self.pack();self.assertIn('.env',result['repositories'][0]['excluded'])
        self.assertFalse(any(marker.encode() in p.read_bytes() for p in path.rglob('*') if p.is_file() and not p.is_symlink()))
        self.git('add','.env')
        with self.assertRaisesRegex(Error,'Tracked secret'):
            save(self.store,self.base/'refused',temporary=True)
        self.assertEqual(json.loads((self.base/'refused/FAILED.json').read_text())['status'],'incomplete')

    def test_private_key_header_detected_in_ordinary_untracked_filename(self):
        (self.repo/'notes.txt').write_text('-----BEGIN PRIVATE KEY-----\nfake\n')
        path,result=self.pack();self.assertIn('notes.txt',result['repositories'][0]['excluded'])
        self.assertFalse((path/'repo-0/files/notes.txt').exists())

    def test_tamper_refused_before_sqlite_or_git_open(self):
        path,result=self.pack();(path/'repo-0/worktree.patch').write_bytes(b'tamper')
        with patch('recovery.Store',side_effect=AssertionError('SQLite opened')),patch('recovery.git',side_effect=AssertionError('Git opened')):
            with self.assertRaisesRegex(Error,'tampered'):restore_check(path,self.base/'restored')
        self.assertFalse((self.base/'restored').exists())

    def test_added_file_or_checksum_escape_refused(self):
        path,result=self.pack();(path/'extra').write_text('extra')
        with self.assertRaisesRegex(Error,'tampered'):verify_package(path)
        (path/'extra').unlink();checks=json.loads((path/'CHECKSUMS.json').read_text());checks['../escape']={}
        (path/'CHECKSUMS.json').write_text(json.dumps(checks))
        with self.assertRaisesRegex(Error,'Escaping'):verify_package(path)

    def test_existing_or_in_source_destination_and_symlink_escape_refused(self):
        with self.assertRaises(Error):save(self.store,self.repo/'inside',temporary=True)
        path,result=self.pack()
        with self.assertRaises(Error):save(self.store,path,temporary=True)
        with self.assertRaises(Error):restore_check(path,path/'nested')
        (self.repo/'escape').symlink_to('../outside')
        with self.assertRaisesRegex(Error,'Escaping'):save(self.store,self.base/'escaped',temporary=True)

    def test_torn_snapshot_is_incomplete(self):
        from recovery import snapshot as original
        count=0
        def racing(*args,**kw):
            nonlocal count
            count+=1
            if count==2:(self.repo/'code.py').write_text('changed during capture')
            return original(*args,**kw)
        with patch('recovery.snapshot',side_effect=racing):
            with self.assertRaisesRegex(Error,'Torn'):self.pack()
        self.assertFalse((self.base/'saved/CHECKSUMS.json').exists())

    def test_partial_and_offsite_declarations_are_not_verified_external_backup(self):
        path,result=self.pack(repos=[str(self.repo)],declared_offsite='Owner reports separate disk')
        self.assertIn('partial',result['scope']);self.assertFalse(result['offsite_verified'])
        self.assertEqual(result['destination'],'local checkpoint')

    def test_partial_destination_cannot_be_another_registered_checkout(self):
        second=self.base/'second';shutil.copytree(self.repo,second)
        self.store.append('agent','B',{'id':'B','role':'builder','identity':'other','client':'manual','repo':str(second)})
        with self.assertRaisesRegex(Error,'outside all'):
            save(self.store,second/'forbidden',repos=[str(self.repo)],temporary=True)
        self.assertFalse((second/'forbidden').exists())

    def test_tracked_and_head_escaping_symlinks_are_refused(self):
        (self.base/'outside').write_text('private fixture')
        (self.repo/'link').symlink_to('../outside');self.git('add','link');self.git('commit','-qm','Unsafe HEAD link')
        with self.assertRaisesRegex(Error,'symlink'):
            save(self.store,self.base/'unsafe-head',temporary=True)
        (self.repo/'link').unlink();self.git('add','link')
        with self.assertRaisesRegex(Error,'HEAD symlink'):
            save(self.store,self.base/'deleted-unsafe-head',temporary=True)
        self.git('commit','-qm','Delete unsafe HEAD link')
        (self.repo/'link').symlink_to('../outside');self.git('add','link')
        with self.assertRaisesRegex(Error,'symlink'):
            save(self.store,self.base/'unsafe-index',temporary=True)
        (self.repo/'link').unlink();(self.repo/'link').write_text('regular worktree, unsafe staged link remains')
        with self.assertRaisesRegex(Error,'index symlink'):
            save(self.store,self.base/'masked-unsafe-index',temporary=True)

    def test_confined_head_and_index_links_with_regular_worktree_roundtrip(self):
        (self.repo/'head-link').symlink_to('code.py');self.git('add','head-link');self.git('commit','-qm','Confined HEAD link')
        (self.repo/'index-link').symlink_to('code.py');self.git('add','index-link')
        (self.repo/'index-link').unlink();(self.repo/'index-link').write_text('regular unstaged replacement')
        path,result=self.pack();r=restore_check(path,self.base/'roundtrip-links');repo=Path(r['repositories'][0]['restored'])
        self.assertEqual(os.readlink(repo/'head-link'),'code.py')
        self.assertEqual((repo/'index-link').read_text(),'regular unstaged replacement')
        self.assertEqual(subprocess.check_output(['git','show',':index-link'],cwd=repo),b'code.py')
        self.assertIn(b'120000',subprocess.check_output(['git','ls-files','--stage','index-link'],cwd=repo))

    def test_sparse_checkout_refused(self):
        self.git('config','core.sparseCheckout','true')
        with self.assertRaisesRegex(Error,'Sparse'):self.pack()

    def test_live_registry_inside_source_not_duplicated_in_repo_payload(self):
        inner=Store(self.repo/'.squadra',create=True)
        try:
            shutil.copytree(self.root/'artifacts',inner.root/'artifacts')
            for event in self.store.events():inner.append(event['kind'],event['actor'],event['data'])
            path=self.base/'nested-registry-pack';save(inner,path,temporary=True)
            restored=restore_check(path,self.base/'restored')
            self.assertFalse((Path(restored['repositories'][0]['restored'])/'.squadra').exists())
            self.assertEqual(restored['registry_integrity'],'passed')
        finally:inner.close()
