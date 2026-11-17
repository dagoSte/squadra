"""Only assertion-red/base and green/candidate tests satisfy red-first requirements."""
from copy import deepcopy
import json
import unittest

from test_errata import Fixture, run
from challenge_requirements import evidence, validate_task
from fix_challenge import execute
from registry import Error, Store, snapshot
from squadra import deliver

TEST = '''import pathlib, unittest
class Requirement(unittest.TestCase):
    def test_value(self):
        self.assertEqual(pathlib.Path('code.py').read_text(), 'VALUE = 2\\n')
'''
ID = 'test_requirement.Requirement.test_value'


class Requirements(Fixture, unittest.TestCase):
    def configure(self, requirements=None, body=TEST):
        requirements = requirements or [
            {'id': 'A', 'text': 'The new value is two', 'tests': [ID]},
            {'id': 'D', 'text': 'Documentation follows the code', 'exception': 'Documentation is inspected by the reader'}]
        path = self.base / 'requirements.json'; path.write_text(json.dumps(requirements))
        rc, _, err = run(self.root, 'task', '--actor', 'C', '--id', 'RF', '--title', 'New behavior',
                         '--owner', 'A', '--repo', str(self.repo), '--scope-file', str(self.scope),
                         '--reviewer', 'R', '--requirements-file', str(path), '--red-first')
        self.assertEqual(rc, 0, err)
        self.ack('RF')
        (self.repo / 'tests').mkdir(exist_ok=True)
        (self.repo / 'tests/test_requirement.py').write_text(body)
        (self.repo / 'code.py').write_text('VALUE = 2\n')
        return self.store.state()['tasks']['RF']

    def challenge(self, label='challenge'):
        return execute(self.store, 'A', 'RF', self.base / label, requirements=True)

    def reviewed(self):
        task = self.store.state()['tasks']['RF']
        covers = [r['id'] for r in task['requirements']]
        self.assertEqual(self.proof(task='RF', covers=covers)['status'], 'passed')
        handoff = deliver(self.store, 'A', 'RF')['handoff']
        self.store.append('ack', 'R', {'message': handoff})
        args = ['review', '--actor', 'R', '--task', 'RF', '--verdict', 'accepted', '--report', str(self.report)]
        for name in covers:
            args.extend(['--covers', name])
        rc, _, err = run(self.root, *args)
        self.assertEqual(rc, 0, err)

    def test_real_cli_challenge_and_normal_proof_close_the_same_frozen_candidate(self):
        self.configure()
        rc, result, err = run(self.root, 'challenge', '--actor', 'A', '--task', 'RF', '--requirements')
        self.assertEqual(rc, 0, err)
        self.assertEqual(result['status'], 'matched')
        record = self.store.state()['tasks']['RF']['requirement_result']
        self.assertEqual(record['exceptions'], {'D': 'Documentation is inspected by the reader'})
        self.assertEqual([r['assertions'] for r in record['runs']], [[ID], []])
        self.reviewed()
        task = self.store.state()['tasks']['RF']
        self.assertTrue(task['delivery']['packet_data']['red_first'])
        self.assertEqual(self.cli('close', '--actor', 'C', '--task', 'RF'), 0)
        reopened = Store(self.root); self.addCleanup(reopened.close)
        self.assertEqual(reopened.state()['tasks']['RF']['status'], 'done')
        self.assertTrue(reopened.audit())

    def test_a_green_suite_and_accepted_review_do_not_substitute_for_the_challenge(self):
        self.configure(); self.reviewed()
        rc, _, err = run(self.root, 'close', '--actor', 'C', '--task', 'RF')
        self.assertEqual(rc, 1)
        self.assertIn('requirement_challenge_required_or_stale', err)
        with self.assertRaisesRegex(Error, 'requirement_challenge_required_or_stale'):
            self.store.append('close', 'C', {'task': 'RF'})

    def test_skip_error_or_a_test_that_was_already_green_cannot_satisfy_red_first(self):
        cases = [TEST.replace("self.assertEqual(pathlib.Path('code.py').read_text(), 'VALUE = 2\\n')", 'raise RuntimeError("setup is not a red test")'),
                 TEST.replace('    def test_value', '    @unittest.skip("not exercised")\n    def test_value'),
                 TEST.replace("self.assertEqual(pathlib.Path('code.py').read_text(), 'VALUE = 2\\n')", 'self.assertTrue(True)')]
        self.configure()
        for index, body in enumerate(cases):
            with self.subTest(index=index):
                (self.repo / 'tests/test_requirement.py').write_text(body)
                result = self.challenge('bad-' + str(index))
                self.assertEqual(result['status'], 'failed')
                task = self.store.state()['tasks']['RF']
                with self.assertRaisesRegex(Error, 'requirement_challenge_not_matched'):
                    evidence(task, snapshot(self.repo), self.root)

    def test_every_declared_test_must_be_assertion_red_on_the_base(self):
        other = 'test_requirement.Requirement.test_already_green'
        body = TEST + '    def test_already_green(self):\n        self.assertTrue(True)\n'
        self.configure([{'id': 'A', 'text': 'Both tests discriminate', 'tests': [ID, other]}], body)
        self.assertEqual(self.challenge()['status'], 'failed')

    def test_a_changed_candidate_invalidates_the_old_challenge(self):
        self.configure(); self.assertEqual(self.challenge()['status'], 'matched')
        (self.repo / 'code.py').write_text('VALUE = 3\n')
        with self.assertRaisesRegex(Error, 'requirement_challenge_required_or_stale'):
            evidence(self.store.state()['tasks']['RF'], snapshot(self.repo), self.root)

    def test_explicit_requirement_exceptions_are_visible_and_need_no_fake_test(self):
        self.configure([{'id': 'D', 'text': 'Explain the convention', 'exception': 'Documentation-only change'}])
        result = self.challenge()
        self.assertEqual(result['status'], 'excepted')
        self.assertEqual(result['exceptions'], {'D': 'Documentation-only change'})
        self.reviewed()
        self.assertEqual(self.cli('close', '--actor', 'C', '--task', 'RF'), 0)

    def test_requirement_and_defect_challenges_do_not_overwrite_each_other(self):
        self.configure()
        from fix_challenge import register
        agreement = self.base / 'fix.json'
        agreement.write_text(json.dumps({'ids': [ID], 'files': ['tests/test_requirement.py']}))
        register(self.store, 'C', 'RF', agreement, 'The defect keeps its separate agreement')
        self.assertEqual(self.challenge()['status'], 'matched')
        self.assertEqual(execute(self.store, 'A', 'RF', self.base / 'defect')['status'], 'matched')
        task = self.store.state()['tasks']['RF']
        self.assertTrue(task['requirement_result']['requirements'])
        self.assertNotIn('requirements', task['fix_result'])
        self.reviewed()
        self.assertEqual(self.cli('close', '--actor', 'C', '--task', 'RF'), 0)

    def test_invalid_contracts_are_rejected_only_when_red_first_is_requested(self):
        valid = {'id': 'A', 'text': 'A requirement', 'tests': [ID]}
        variants = [{}, {'tests': []}, {'tests': 'wrong'}, {'tests': [ID, ID]},
                    {'tests': ['not-a-test']}, {'exception': ''}, {'exception': '  '}, {'exception': 'hidden\x00reason'},
                    {'tests': [ID], 'exception': 'Cannot use both'}]
        for extra in variants:
            requirement = {'id': 'A', 'text': 'A requirement', **extra}
            with self.subTest(extra=extra), self.assertRaises(Error):
                validate_task({'red_first': True, 'requirements': [requirement]})
            validate_task({'requirements': [requirement]})
        validate_task({'red_first': True, 'requirements': [valid]})
        with self.assertRaisesRegex(Error, 'red_first_invalid'):
            validate_task({'red_first': 1, 'requirements': [valid]})
        self.assertNotIn('red_first', self.store.state()['tasks']['T'])

    def test_tampered_native_observations_are_not_an_exception_or_a_pass(self):
        self.configure(); self.challenge()
        task = self.store.state()['tasks']['RF']
        record = deepcopy(task['requirement_result'])
        record['exceptions']['D'] = 'A different excuse'
        from challenge_requirements import validate_record
        with self.assertRaisesRegex(Error, 'requirement_exceptions_changed'):
            validate_record(task, record, snapshot(self.repo), self.root)
        record = deepcopy(task['requirement_result'])
        record['runs'][0]['assertions'] = []
        with self.assertRaisesRegex(Error, 'fix_result_binding_changed'):
            validate_record(task, record, snapshot(self.repo), self.root)


if __name__ == '__main__':
    unittest.main()
