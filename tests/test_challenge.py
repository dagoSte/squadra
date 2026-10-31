"""Check that assertions distinguish defects from test setup errors."""
from pathlib import Path
import shutil
import unittest

import test_squadra as support
from challenge import run
from registry import Error, snapshot


class Challenge(unittest.TestCase):
    setUp=support.Workflow.setUp
    git=support.Workflow.git
    task=support.Workflow.task

    def copies(self):
        # A commit starts Git's background auto-maintenance, which creates and deletes .git/objects/maintenance.lock
        # while copytree walks the tree. Turn it off for the fix commit, and never copy that one transient lock (it may
        # come from a maintenance run setUp's commits already started). Every other file is copied.
        self.git('config','maintenance.auto','false');self.git('config','gc.auto','0')
        objects=(self.repo/'.git'/'objects').resolve()
        def lock(folder,names):
            return {'maintenance.lock'} & set(names) if Path(folder).resolve()==objects else set()
        original=self.base/'original';shutil.copytree(self.repo,original,ignore=lock)
        (self.repo/'code.py').write_text('VALUE = 2\n');self.git('add','code.py');self.git('commit','-qm','fix')
        positive=self.base/'positive';shutil.copytree(self.repo,positive,ignore=lock)
        oracle=self.base/'oracle.py';oracle.write_text('def check(repo):\n assert "VALUE = 2" in (repo/"code.py").read_text(), "wrong value"\n')
        return oracle,original,positive

    def test_copies_skip_only_the_maintenance_lock(self):
        (self.repo/'dependencies.lock').write_text('pinned\n')
        (self.repo/'data.lock').mkdir();(self.repo/'data.lock'/'maintenance.lock').write_text('kept\n')
        (self.repo/'.git'/'objects'/'maintenance.lock').write_text('')
        _,original,positive=self.copies()
        for copy in (original,positive):
            with self.subTest(copy=copy.name):
                self.assertEqual((copy/'dependencies.lock').read_text(),'pinned\n')
                self.assertEqual((copy/'data.lock'/'maintenance.lock').read_text(),'kept\n')
                self.assertFalse((copy/'.git'/'objects'/'maintenance.lock').exists())

    def test_original_red_remedy_and_positive_green_unchanged_oracle(self):
        oracle,original,positive=self.copies();before=snapshot(original)
        result=run(oracle,original,self.repo,positive,self.base/'out')
        self.assertEqual(result['status'],'matched');self.assertEqual([r['native_rc'] for r in result['runs']],[1,0,0])
        self.assertEqual(result['runs'][0]['outcome'],'assertion');self.assertTrue(result['runs'][0]['executed_sha256'])
        self.assertEqual(snapshot(original),before)

    def test_instrument_error_or_empty_positive_never_qualifies(self):
        oracle,original,positive=self.copies();oracle.write_text('def check(repo):\n raise ImportError("missing dependency")\n')
        result=run(oracle,original,self.repo,positive,self.base/'out')
        self.assertEqual(result['status'],'failed');self.assertTrue(all(r['outcome']=='instrument-error' for r in result['runs']))

    def test_original_pass_is_not_a_demonstrated_defect(self):
        oracle,original,positive=self.copies();oracle.write_text('def check(repo):\n pass\n')
        self.assertEqual(run(oracle,original,self.repo,positive,self.base/'out')['status'],'failed')

    def test_timeout_and_dirty_source_refused_or_unqualified(self):
        oracle,original,positive=self.copies();oracle.write_text('import time\ndef check(repo):\n time.sleep(2)\n')
        result=run(oracle,original,self.repo,positive,self.base/'out',timeout=1)
        self.assertEqual(result['status'],'failed')
        for item in result['runs']:
            self.assertIn(item['status'],{'interrupted','cleanup-unknown'})
            self.assertNotEqual(item['native_rc'],0)
            if item['status']=='cleanup-unknown':self.assertTrue(item['cleanup_error'])
        (original/'untracked').write_text('unfrozen')
        with self.assertRaisesRegex(Error,'clean'):run(oracle,original,self.repo,positive,self.base/'dirty')
