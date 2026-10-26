"""An agreed test count is a count of executed tests: skipped tests never count, and each category is recorded apart."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_review_pool_gate_refusals import Fixture
import test_squadra as support
import registry
from registry import snapshot
from runtime import execute


def printing(text):
    return [sys.executable, '-c', 'import sys; sys.stderr.write(' + repr(text) + ')']


class Counts(unittest.TestCase):
    git = support.Workflow.git
    task = support.Workflow.task

    def setUp(self):
        support.Workflow.setUp(self)
        patcher = patch.dict(os.environ, {'HOME': str(self.home)}); patcher.start(); self.addCleanup(patcher.stop)

    def counted(self, name, expected):
        data = {'id': name, 'title': 'Counted work', 'owner': 'A', 'repo': str(self.repo),
                'base': snapshot(self.repo)['head'], 'scope': self.store.artifact(self.scope),
                'proof': None, 'expected': expected, 'depends': [], 'full_gate': False}
        self.store.batch([('task', 'C', data, None), ('message', 'C', {'id': 'assign-' + name, 'to': 'A', 'task': name,
                          'body': 'Scoped work'}, None)])
        self.store.append('ack', 'A', {'message': 'assign-' + name})

    def prove(self, name, argv):
        result = execute(self.store, 'A', name, argv=argv)
        return result, self.store.state()['runs'][result['id']]

    def test_unittest_skips_are_not_executed_tests(self):
        self.counted('U', 10)
        result, run = self.prove('U', printing('Ran 10 tests in 0.010s\n\nOK (skipped=9)\n'))
        self.assertEqual(result['status'], 'failed', result)
        self.assertIn('executed 1 of 10 agreed', result['error'])
        self.assertIn('skipped 9', result['error'])
        self.assertEqual(run['counts'], {'passed': 1, 'skipped': 9})
        self.assertEqual(self.store.state()['tasks']['U']['proofs'][-1]['status'], 'failed')

    def test_a_real_unittest_run_with_skips_and_expected_failures_records_each_category(self):
        folder = Path(tempfile.mkdtemp(prefix='squadra-rf4b-suite-', dir=self.base))
        (folder / 'test_synthetic.py').write_text(
            'import unittest\n'
            'class T(unittest.TestCase):\n'
            '    def test_a(self): pass\n'
            '    def test_b(self): pass\n'
            '    @unittest.skip("capability missing")\n'
            '    def test_c(self): pass\n'
            '    @unittest.expectedFailure\n'
            '    def test_d(self): self.fail("known")\n')
        argv = [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', str(folder)]
        self.counted('R4', 4)
        result, run = self.prove('R4', argv)
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(run['counts'], {'passed': 2, 'skipped': 1, 'xfailed': 1})
        self.counted('R3', 3)
        result, run = self.prove('R3', argv)
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(run['counts'], {'passed': 2, 'skipped': 1, 'xfailed': 1})

    def test_pytest_skips_are_not_executed_tests(self):
        self.counted('P', 10)
        result, run = self.prove('P', printing('===== 1 passed, 9 skipped in 0.10s =====\n'))
        self.assertEqual(result['status'], 'failed', result)
        self.assertIn('executed 1 of 10 agreed', result['error'])
        self.assertEqual(run['counts'], {'passed': 1, 'skipped': 9})
        self.counted('P1', 1)
        result, run = self.prove('P1', printing('===== 1 passed, 9 skipped in 0.10s =====\n'))
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(run['counts'], {'passed': 1, 'skipped': 9})

    def test_a_plain_complete_run_keeps_its_receipt(self):
        self.counted('K', 2)
        result, run = self.prove('K', printing('Ran 2 tests in 0.001s\n\nOK\n'))
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(run['counts'], {'passed': 2})

    def test_counts_match_never_counts_skips_or_accepts_failures(self):
        counts_match = registry.counts_match
        self.assertTrue(counts_match({'passed': 2, 'xfailed': 1}, 3))
        self.assertFalse(counts_match({'passed': 1, 'skipped': 9}, 10))
        self.assertTrue(counts_match({'passed': 1, 'skipped': 9}, 1))
        self.assertFalse(counts_match({'passed': 3, 'failed': 1}, 4))
        self.assertFalse(counts_match({}, 1))


class SharedCounts(Fixture, unittest.TestCase):
    def setUp(self):
        Fixture.setUp(self)
        patcher = patch.dict(os.environ, {'HOME': str(self.home)}); patcher.start(); self.addCleanup(patcher.stop)

    def test_a_shared_proof_cannot_count_skips_for_any_member(self):
        argv = printing('Ran 2 tests in 0.001s\n\nOK (skipped=1)\n')
        for name in ('COUNT-A', 'COUNT-B'):
            self.full_task(name, proof=argv, expected=2)
        result = execute(self.store, 'A', 'COUNT-A', argv=argv, also=['COUNT-B'])
        self.assertEqual(result['status'], 'failed', result)
        self.assertIn('executed 1 of 2 agreed', result['error'])
        self.assertEqual(self.store.task('COUNT-B')['proofs'], [])
        for name in ('ONE-A', 'ONE-B'):
            self.full_task(name, proof=argv, expected=1)
        result = execute(self.store, 'A', 'ONE-A', argv=argv, also=['ONE-B'])
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(self.store.task('ONE-B')['proofs'][0]['counts'], {'passed': 1, 'skipped': 1})


if __name__ == '__main__':
    unittest.main()
