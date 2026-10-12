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
        original=self.base/'original';shutil.copytree(self.repo,original)
        (self.repo/'code.py').write_text('VALUE = 2\n');self.git('add','code.py');self.git('commit','-qm','fix')
        positive=self.base/'positive';shutil.copytree(self.repo,positive)
        oracle=self.base/'oracle.py';oracle.write_text('def check(repo):\n assert "VALUE = 2" in (repo/"code.py").read_text(), "wrong value"\n')
        return oracle,original,positive

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
