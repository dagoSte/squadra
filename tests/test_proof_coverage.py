"""A CLI proof asks for coverage before either checks or the proof start."""
import contextlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_squadra as support
from registry import snapshot
from squadra import main


class Coverage(unittest.TestCase):
    git = support.Workflow.git
    task = support.Workflow.task

    def setUp(self):
        support.Workflow.setUp(self)
        data = {'id': 'REQ', 'title': 'Explicit coverage', 'owner': 'A', 'repo': str(self.repo),
                'base': snapshot(self.repo)['head'], 'scope': self.store.artifact(self.scope),
                'proof': None, 'expected': None, 'depends': [], 'full_gate': False,
                'requirements': [{'id': 'R1', 'text': 'First'}, {'id': 'R2', 'text': 'Second'}]}
        self.store.batch([('task', 'C', data, None), ('message', 'C', {'id': 'assign-REQ', 'to': 'A',
                          'task': 'REQ', 'body': 'Synthetic contract'}, None),
                          ('ack', 'A', {'message': 'assign-REQ'}, None)])

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(['--root', str(self.root), *args])
        return rc, out.getvalue(), err.getvalue()

    def proof_cli(self, covers=(), code="print('proof')", task='REQ'):
        flags = [value for ident in covers for value in ('--covers', ident)]
        return self.cli('prove', '--actor', 'A', '--task', task, *flags, '--', sys.executable, '-c', code)

    def test_missing_covers_refuses_before_precheck_or_proof_start(self):
        marker = self.base / 'should-not-run'
        (self.repo / '.squadra-precheck.json').write_text(json.dumps({'schema': 1, 'total_seconds': 15,
            'checks': [{'name': 'marker', 'argv': [sys.executable, '-c',
                       'from pathlib import Path; Path(' + repr(str(marker)) + ').write_text("ran")'],
                       'timeout_seconds': 5}]}))
        self.git('add', '.'); self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'checks')
        before = len(self.store.events())
        rc, out, err = self.proof_cli()
        self.assertEqual(rc, 1, out + err)
        self.assertIn('--covers', err)
        self.assertIn('R1', err); self.assertIn('R2', err)
        self.assertFalse(marker.exists(), 'missing coverage must stop the declared precheck too')
        self.assertFalse(any(e['kind'] == 'run.start' for e in self.store.events()[before:]))

    def summary(self, out):
        return json.JSONDecoder().raw_decode(out)[0]['coverage_on_this_source']['REQ']

    def test_partial_coverage_aggregates_on_the_same_exact_source(self):
        rc, out, err = self.proof_cli(['R1'])
        self.assertEqual(rc, 0, err)
        summary = self.summary(out)
        self.assertEqual(summary['covered'], ['R1'])
        self.assertEqual(summary['uncovered'], ['R2'])
        self.assertEqual(summary['source'], snapshot(self.repo, self.root)['sha256'])
        rc, out, err = self.proof_cli(['R2'])
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.summary(out)['covered'], ['R1', 'R2'])
        self.assertEqual(self.summary(out)['uncovered'], [])
        rc, out, err = self.proof_cli()
        self.assertEqual(rc, 1, 'full historical coverage does not guess a new --covers')

    def test_explicit_complete_coverage_is_a_positive_control(self):
        rc, out, err = self.proof_cli(['R1', 'R2'])
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.summary(out)['covered'], ['R1', 'R2'])
        self.assertEqual(self.summary(out)['uncovered'], [])

    def test_changed_source_and_failed_proofs_do_not_contribute(self):
        rc, out, err = self.proof_cli(['R1'])
        self.assertEqual(rc, 0, err)
        (self.repo / 'code.py').write_text('VALUE = 2\n')
        rc, out, err = self.proof_cli(['R2'])
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.summary(out)['covered'], ['R2'])
        self.assertEqual(self.summary(out)['uncovered'], ['R1'])
        rc, out, err = self.proof_cli(['R1'], code='raise SystemExit(1)')
        self.assertEqual(rc, 1)
        rc, out, err = self.proof_cli(['R2'])
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.summary(out)['uncovered'], ['R1'])

    def test_unknown_coverage_refuses_before_start_and_no_requirements_stay_unchanged(self):
        before = len(self.store.events())
        rc, out, err = self.proof_cli(['unknown'])
        self.assertEqual(rc, 1)
        self.assertIn('Unknown requirement', err)
        self.assertEqual(len(self.store.events()), before)
        self.store.append('ack', 'A', {'message': 'assign-T'})
        rc, out, err = self.proof_cli(task='T')
        self.assertEqual(rc, 0, err)
        self.assertNotIn('coverage_on_this_source', json.JSONDecoder().raw_decode(out)[0])

    def test_shared_seam_uses_the_union_not_just_the_primary_task(self):
        from squadra import coverage_preflight
        state = {'tasks': {'one': {'requirements': [{'id': 'A'}]},
                           'two': {'requirements': [{'id': 'B'}]}}}
        coverage_preflight(state, ['one', 'two'], ['A', 'B'])
        with self.assertRaisesRegex(Exception, 'two.*B'):
            coverage_preflight(state, ['one', 'two'], ['A'])
        with self.assertRaisesRegex(Exception, '--covers.*A.*B'):
            coverage_preflight(state, ['one', 'two'], [])
        with self.assertRaisesRegex(Exception, 'Unknown requirement'):
            coverage_preflight(state, ['one', 'two'], ['C'])

    def test_invalid_contract_and_snapshot_references_use_current_proofs(self):
        from squadra import coverage_on_this_source
        from registry import current_proofs
        rc, out, err = self.proof_cli(['R1'])
        self.assertEqual(rc, 0, err)
        task = self.store.task('REQ')
        current = snapshot(self.repo, self.root)
        self.assertEqual(len(current_proofs(task, current)), 1)
        changed = dict(task, contract={'different': 'contract'})
        with patch.object(self.store, 'task', return_value=changed):
            summary = coverage_on_this_source(self.store, ['REQ'], current)['REQ']
        self.assertEqual(summary['covered'], [])
        self.assertEqual(summary['uncovered'], ['R1', 'R2'])
