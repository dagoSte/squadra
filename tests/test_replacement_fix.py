"""Frozen counterexamples for independent findings F1, F2 and F3 on f820ea1."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

import test_replacement as support
from registry import Error, TASK_FIELDS, atomic, canonical, contract_matches, run_lease_paths, validate
from light_route import POLICY, contract, check_contract, measure


class ReplacementFix(unittest.TestCase):
    setUp = support.Replacement.setUp
    git = support.Replacement.git
    task = support.Replacement.task
    ack = support.Replacement.ack
    cli = support.Replacement.cli
    proof = support.Replacement.proof
    req = support.Replacement.req
    replace = support.Replacement.replace

    def plan_case(self, verdict=None, repeat=False):
        self.ack()
        artifact = self.store.artifact(self.scope)
        self.store.append('plan', 'A', {'task': 'T', 'artifact': artifact})
        if verdict:
            self.assertEqual(self.cli('review', '--actor', 'R', '--task', 'T', '--stage', 'plan',
                                     '--verdict', verdict, '--report', str(self.report)), 0)
        old = self.store.state()['tasks']['T']
        before = {'id': 'before', 'task': 'T', 'mode': 'build'}
        if verdict != 'accepted':
            with self.assertRaisesRegex(Error, 'Current plan has no independent approval'):
                validate(self.store.state(), 'run.start', 'A', before)
        else:
            validate(self.store.state(), 'run.start', 'A', before)
        result = self.replace(self.req(force=True)); successor = result['successors']['T']; owner = 'B'
        if repeat:
            result = self.replace(self.req({'B': 'D'}, sessions={'D': 'third'}, force=True))
            successor = result['successors'][successor]; owner = 'D'
        state = self.store.state(); task = state['tasks'][successor]
        self.assertEqual(task.get('plan'), artifact, 'F1: successor lost its required plan artifact')
        self.assertEqual(state['tasks']['T']['reviews'], old['reviews'], 'Old review provenance must remain')
        self.assertEqual(task['rounds']['plan'], old['rounds']['plan'], 'Takeover must retain consumed rounds')
        self.store.append('ack', owner, {'message': 'assign-' + successor})
        with self.assertRaisesRegex(Error, 'Current plan has no independent approval'):
            validate(self.store.state(), 'run.start', owner, {'id': 'after', 'task': successor, 'mode': 'build'})
        self.assertEqual(self.cli('review', '--actor', 'R', '--task', successor, '--stage', 'plan',
                                 '--verdict', 'accepted', '--report', str(self.report)), 0)
        validate(self.store.state(), 'run.start', owner, {'id': 'approved', 'task': successor, 'mode': 'build'})
        self.assertNotEqual(self.store.audit()['cache'], 'mismatch')

    def test_F1_pending_plan_remains_a_gate(self):
        self.plan_case()

    def test_F1_rejected_plan_remains_a_gate(self):
        self.plan_case('changes-required')

    def test_F1_accepted_plan_requires_new_approval(self):
        self.plan_case('accepted')

    def test_F1_repeated_takeover_preserves_plan_and_consumed_rounds(self):
        self.plan_case('accepted', repeat=True)

    def test_F2_live_process_keeps_identity_alias_reservations(self):
        self.ack()
        process = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'],
                                   stdin=subprocess.PIPE, start_new_session=True)
        try:
            self.store.append('run.start', 'A', {'id': 'live', 'task': 'T', 'mode': 'proof', 'lease_version': 1})
            folder, _, metadata, _ = run_lease_paths(self.root, 'live'); folder.mkdir(mode=0o700)
            atomic(metadata, canonical({'run': 'live', 'phase': 'spawned', 'pgid': process.pid}).encode())
            self.store.append('resource.acquire', 'A-alias', {'name': 'database', 'reason': 'Exclusive database'})
            with self.assertRaises(Error):
                self.store.append('resource.acquire', 'R', {'name': 'database', 'reason': 'Other job'})
            result = self.replace(self.req(force=True))
            self.assertEqual(result['unresolved_runs'], ['live'])
            self.assertIsNone(process.poll())
            with self.assertRaises(Error, msg='F2: force released a live process reservation'):
                self.store.append('resource.acquire', 'R', {'name': 'database', 'reason': 'Other job'})
            self.assertEqual(self.store.state()['resources']['database']['owner'], 'A-alias')
            self.assertIsNone(process.poll())
        finally:
            process.stdin.close(); process.wait(timeout=5)

    def test_F2_unknown_run_keeps_reservation_through_repeated_takeover(self):
        self.ack()
        self.store.append('run.start', 'A', {'id': 'unknown', 'task': 'T', 'mode': 'proof'})
        self.store.append('resource.acquire', 'A', {'name': 'cpu-heavy', 'task': 'T', 'reason': 'Build'})
        self.replace(self.req(force=True))
        self.replace(self.req({'B': 'D'}, sessions={'D': 'third'}, force=True))
        self.assertEqual(self.store.state()['resources'].get('cpu-heavy', {}).get('owner'), 'A',
                         'F2: uncertainty does not release a reservation')

    def light(self, owner='A', depends=None):
        task = {k: v for k, v in self.store.state()['tasks']['T'].items() if k in TASK_FIELDS}
        task.update(id='L', owner=owner, reviewer='R', route='light', light_policy=POLICY,
                    proof=['python3', '-c', 'pass'], requirements=[{'id': 'L1', 'text': 'Preserve docs'}],
                    depends=depends or [])
        task['contract'] = contract(task)
        self.store.append('task', 'C', task)
        check_contract(self.store.state()['tasks']['L'])
        return task['contract']

    def check_light(self, name, previous):
        task = self.store.state()['tasks'][name]
        self.assertEqual(task['contract'], contract(task), 'F3: final declarations must bind the light contract')
        check_contract(task)
        self.assertEqual(task['light_policy'], POLICY)
        self.assertFalse(contract_matches(task, {'contract': previous}), 'Old proof binding must be invalidated')
        (self.repo / 'code.py').write_text('VALUE = 2\n')
        with self.assertRaisesRegex(Error, 'light_protected'):
            measure(task)
        self.assertNotEqual(self.store.audit()['cache'], 'mismatch')

    def test_F3_early_reader_contract_uses_final_reader(self):
        previous = self.light()
        self.replace(self.req({'R': 'B'}))
        self.check_light('L', previous)

    def test_F3_successor_contract_uses_rewritten_dependencies(self):
        previous = self.light(depends=['T'])
        result = self.replace()
        self.assertEqual(self.store.state()['tasks'][result['successors']['L']]['depends'], [result['successors']['T']])
        self.check_light(result['successors']['L'], previous)

    def test_F3_non_replaced_dependent_contract_is_refreshed(self):
        self.store.append('agent', 'D', {'id': 'D', 'identity': 'elsewhere', 'role': 'builder',
                          'client': 'codex', 'repo': str(self.repo), 'skills': [], 'session': ''})
        previous = self.light(owner='D', depends=['T'])
        self.replace()
        self.check_light('L', previous)


if __name__ == '__main__':
    unittest.main(verbosity=2)
