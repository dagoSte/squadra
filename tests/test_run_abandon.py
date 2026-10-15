"""Abandonment is an explicit starter declaration, never a successful process receipt."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import test_squadra as workflow
import registry
import runtime
import squadra


class RunAbandonTests(unittest.TestCase):
    setUp = workflow.Workflow.setUp
    git = workflow.Workflow.git
    task = workflow.Workflow.task
    ack = workflow.Workflow.ack
    cli = workflow.Workflow.cli

    def orphan(self, run_id='orphan', **extra):
        if self.store.state()['tasks']['T']['status'] == 'waiting-ack':
            self.ack()
        self.store.append('run.start', 'A', {'id': run_id, 'task': 'T', 'mode': 'proof', **extra})
        return run_id

    def declaration(self, run_id='orphan'):
        observation = {'form': registry.ABANDON_FORM, 'run': run_id, 'observed_at': registry.now(),
                       'processes': 'none', 'results': 'none',
                       'observation': 'Starter observed the failed launch; no child or output was produced.'}
        path = self.base / (run_id + '-observation.json')
        path.write_text(json.dumps(observation))
        return {'id': run_id, 'starter': 'author', 'reason': 'Launch did not start a process',
                'evidence': self.store.artifact(path), 'observation': observation,
                'legacy': not self.store.state()['runs'][run_id].get('lease_version')}

    def test_legacy_orphan_is_terminal_without_becoming_a_proof(self):
        self.orphan(); before = self.store.events()
        self.store.append('run.cancel', 'C', {'id': 'orphan'})
        self.store.append('run.abandon', 'A', self.declaration())
        state = self.store.state(); run = state['runs']['orphan']
        self.assertEqual(run['status'], 'abandoned')
        self.assertIsNone(run['native_rc'])
        self.assertTrue(run['cancel_requested'])
        self.assertTrue(run['abandonment']['legacy'])
        self.assertEqual(state['tasks']['T']['proofs'], [])
        self.assertEqual(self.store.events()[:len(before)], before)
        self.assertNotIn('run.end', [e['kind'] for e in self.store.events()])
        with self.assertRaisesRegex(registry.Error, 'No complete native proof'):
            registry.candidate_checks(state['tasks']['T'], registry.snapshot(self.repo),
                                      require_review=False, delivering=True)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(squadra.main(['--root', str(self.root), 'doctor']), 0)
        doctor = json.loads(output.getvalue())
        self.assertEqual(doctor['unknown_running'], [])
        self.assertEqual(doctor['abandoned'][0]['id'], 'orphan')
        self.assertEqual(doctor['abandoned'][0]['results'], 'none')
        self.assertIsNone(doctor['abandoned'][0]['native_rc'])

    def test_only_exact_starter_can_declare_even_coordinator_or_alias_cannot(self):
        self.orphan(); data = self.declaration(); before = len(self.store.events())
        for actor in ('C', 'R', 'A-alias'):
            with self.subTest(actor=actor), self.assertRaises(registry.Error):
                self.store.append('run.abandon', actor, data)
        self.assertEqual(len(self.store.events()), before)
        self.store.append('run.abandon', 'A', data)
        self.assertEqual(self.store.state()['runs']['orphan']['status'], 'abandoned')

    def test_held_lease_refuses_even_with_no_process_and_valid_declaration(self):
        self.orphan(); data = self.declaration()
        with registry.run_lease(self.root, 'orphan'):
            with self.assertRaisesRegex(registry.Error, 'lease is held'):
                self.store.append('run.abandon', 'A', data)
        self.store.append('run.abandon', 'A', data)
        self.assertEqual(self.store.state()['runs']['orphan']['status'], 'abandoned')

    def test_child_inherits_lease_until_it_exits_after_parent_releases(self):
        self.orphan(); data = self.declaration(); ready = self.base / 'child-ready'
        child = None
        try:
            with registry.run_lease(self.root, 'orphan') as fd:
                child = subprocess.Popen([sys.executable, '-B', '-c',
                    'import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text("ready"); time.sleep(15)',
                    str(ready)], pass_fds=(fd,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                deadline = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(ready.exists(), 'child must be alive before parent lease closes')
            with self.assertRaisesRegex(registry.Error, 'lease is held'):
                self.store.append('run.abandon', 'A', data)
        finally:
            if child is not None:
                child.terminate(); child.wait(timeout=5)
        self.store.append('run.abandon', 'A', data)
        self.assertEqual(self.store.state()['runs']['orphan']['status'], 'abandoned')

    def test_completed_duplicate_and_late_events_refused(self):
        self.orphan(); data = self.declaration()
        self.store.append('run.abandon', 'A', data)
        for kind, payload in [('run.abandon', data), ('run.end', {'id': 'orphan', 'status': 'passed', 'native_rc': 0}),
                              ('run.progress', {'id': 'orphan'}), ('run.cancel', {'id': 'orphan'})]:
            with self.subTest(kind=kind), self.assertRaisesRegex(registry.Error, 'already ended'):
                self.store.append(kind, 'A', payload)
        self.orphan('completed')
        data = self.declaration('completed')
        self.store.append('run.end', 'A', {'id': 'completed', 'status': 'failed', 'native_rc': 1})
        with self.assertRaisesRegex(registry.Error, 'already ended'):
            self.store.append('run.abandon', 'A', data)

    def test_cli_requires_reason_and_observation_but_plain_cancel_stays_request(self):
        self.orphan()
        self.assertEqual(self.cli('cancel', '--actor', 'A', '--run', 'orphan', '--abandon'), 1)
        self.assertEqual(self.cli('cancel', '--actor', 'A', '--run', 'orphan'), 0)
        self.assertEqual(self.store.state()['runs']['orphan']['status'], 'running')
        self.assertTrue(self.store.state()['runs']['orphan']['cancel_requested'])
        data = self.declaration()
        self.assertEqual(self.cli('cancel', '--actor', 'A', '--run', 'orphan', '--abandon',
                                 '--reason', data['reason'], '--evidence', data['evidence']['original']), 0)
        self.assertEqual(self.store.state()['runs']['orphan']['status'], 'abandoned')

    def test_declared_observation_must_match_immutable_artifact(self):
        self.orphan(); data = self.declaration()
        with self.assertRaises(registry.Error):
            self.store.append('run.abandon', 'A', data | {'evidence': {}})
        wrong = data | {'observation': data['observation'] | {'observation': 'Changed claim'}}
        with self.assertRaisesRegex(registry.Error, 'differs from evidence'):
            self.store.append('run.abandon', 'A', wrong)
        artifact = self.root / 'artifacts' / data['evidence']['sha256']
        artifact.write_bytes(artifact.read_bytes() + b' ')
        with self.assertRaisesRegex(registry.Error, 'artifact changed'):
            self.store.append('run.abandon', 'A', data)
        self.assertEqual(self.store.state()['runs']['orphan']['status'], 'running')

    def test_false_missing_or_ambiguous_observation_is_refused(self):
        self.orphan(); data = self.declaration()
        for field, value in [('processes', 'unknown'), ('results', 'present'), ('observation', ''),
                             ('run', 'another'), ('observed_at', '2026-10-15T10:00:00')]:
            wrong = data['observation'] | {field: value}
            with self.subTest(field=field), self.assertRaises(registry.Error):
                self.store.append('run.abandon', 'A', data | {'observation': wrong})
        self.store.append('run.abandon', 'A', data)
        self.assertEqual(self.store.state()['runs']['orphan']['status'], 'abandoned')

    def test_known_live_group_is_refused_without_sending_signal(self):
        self.orphan('new', lease_version=1); data = self.declaration('new')
        _, _, metadata, _ = registry.run_lease_paths(self.root, 'new')
        with registry.run_lease(self.root, 'new'):
            registry.atomic(metadata, registry.canonical({'run': 'new', 'phase': 'spawned', 'pgid': 12345}).encode())
        with patch('registry.os.killpg') as signal:
            with self.assertRaisesRegex(registry.Error, 'still live'):
                self.store.append('run.abandon', 'A', data)
            signal.assert_called_once_with(12345, 0)
        with patch('registry.os.killpg', side_effect=ProcessLookupError):
            self.store.append('run.abandon', 'A', data)
        self.assertFalse(self.store.state()['runs']['new']['abandonment']['legacy'])

    def test_new_runtime_missing_or_launching_metadata_is_not_legacy(self):
        self.orphan('new', lease_version=1); data = self.declaration('new')
        with self.assertRaisesRegex(registry.Error, 'observation is missing'):
            self.store.append('run.abandon', 'A', data)
        _, _, metadata, _ = registry.run_lease_paths(self.root, 'new')
        registry.atomic(metadata, registry.canonical({'run': 'new', 'phase': 'launching', 'pgid': None}).encode())
        with self.assertRaisesRegex(registry.Error, 'launch is uncertain'):
            self.store.append('run.abandon', 'A', data)
        registry.atomic(metadata, registry.canonical({'run': 'new', 'phase': 'not-started', 'pgid': None}).encode())
        self.store.append('run.abandon', 'A', data)
        self.assertEqual(self.store.state()['runs']['new']['status'], 'abandoned')

    def test_terminal_receipt_survives_failed_journal_write_and_blocks_abandonment(self):
        self.ack(); original = self.store.append
        def append(kind, *args, **kwargs):
            if kind == 'run.end': raise registry.Error('synthetic journal lock failure')
            return original(kind, *args, **kwargs)
        with patch.object(self.store, 'append', side_effect=append):
            with self.assertRaisesRegex(registry.Error, 'synthetic journal lock failure'):
                runtime.execute(self.store, 'A', 'T', argv=[sys.executable, '-c', 'pass'], run_id='receipt')
        run = self.store.state()['runs']['receipt']
        self.assertEqual(run['status'], 'running')
        _, _, _, receipt = registry.run_lease_paths(self.root, 'receipt')
        self.assertEqual(json.loads(receipt.read_text())['native_rc'], 0)
        with self.assertRaisesRegex(registry.Error, 'terminal result receipt exists'):
            self.store.append('run.abandon', 'A', self.declaration('receipt'))

    def test_nonempty_result_output_is_not_discarded(self):
        self.orphan(); data = self.declaration(); folder = self.root / 'runs' / 'orphan'
        folder.mkdir(parents=True)
        (folder / 'stdout.log').write_text('Actual output')
        with self.assertRaisesRegex(registry.Error, 'output exists'):
            self.store.append('run.abandon', 'A', data)
        self.assertEqual((folder / 'stdout.log').read_text(), 'Actual output')

    def test_project_failure_before_child_start_leaves_abandonable_new_orphan(self):
        self.ack()
        with patch.object(self.store, 'publish', side_effect=registry.Error('projection unavailable')):
            with self.assertRaisesRegex(registry.Error, 'projection unavailable'):
                runtime.execute(self.store, 'A', 'T', argv=[sys.executable, '-c', 'pass'], run_id='notstarted')
        self.store.append('run.abandon', 'A', self.declaration('notstarted'))
        self.assertEqual(self.store.state()['runs']['notstarted']['status'], 'abandoned')

    def test_runtime_lease_covers_start_project_cleanup_and_terminal_append(self):
        self.ack(); phases = []; original_append = self.store.append; original_project = self.store.publish
        def probe(phase):
            with self.assertRaisesRegex(registry.Error, 'lease is held'):
                with registry.run_lease(self.root, 'owned'): pass
            phases.append(phase)
        def append(kind, *args, **kwargs):
            if kind in {'run.start', 'run.end'}: probe(kind)
            return original_append(kind, *args, **kwargs)
        def project(*args, **kwargs):
            probe('project'); return original_project(*args, **kwargs)
        terminate = runtime.terminate
        def cleanup(proc):
            probe('cleanup'); return terminate(proc)
        with patch.object(self.store, 'append', side_effect=append), \
                patch.object(self.store, 'publish', side_effect=project), patch('runtime.terminate', side_effect=cleanup):
            result = runtime.execute(self.store, 'A', 'T', argv=[sys.executable, '-c', 'pass'], run_id='owned')
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(phases, ['run.start', 'project', 'cleanup', 'run.end', 'project'])
        with registry.run_lease(self.root, 'owned'): pass
        self.assertEqual(self.store.state()['runs']['owned']['status'], 'passed')

    def test_symlinked_lease_is_refused(self):
        self.orphan(); data = self.declaration()
        folder, lease, metadata, _ = registry.run_lease_paths(self.root, 'orphan')
        folder.mkdir(mode=0o700)
        target = self.base / 'target'; target.write_text('{}'); target.chmod(0o600)
        lease.symlink_to(target)
        with self.assertRaises(OSError):
            self.store.append('run.abandon', 'A', data)
        self.assertEqual(target.read_text(), '{}')

    def test_dashboard_has_explicit_terminal_no_results_and_legacy_explanation(self):
        page = (SCRIPTS.parent / 'assets/dashboard.html').read_text()
        self.assertIn('"status.abandoned":{it:', page)
        self.assertIn('Abandoned: no results', page)
        self.assertIn('page.runs.abandonedLegacy', page)
        self.assertIn("link('artifact',r.abandonment.evidence)", page)

    def test_abandonment_is_work_activity_and_not_a_communication(self):
        from panel import ORDER, activity
        from comms import communications
        self.orphan()
        self.store.append('run.abandon', 'A', self.declaration())
        event = self.store.events()[-1]
        with self.subTest(view='activity'):
            chart = activity([event], event['utc'])
            self.assertEqual(len(chart['buckets']), 1)
            counts = chart['buckets'][0][1:]
            self.assertEqual(counts[ORDER.index('work')], 1)
            self.assertEqual(sum(counts), 1)
            self.assertEqual(chart['heartbeats'], 0)
        with self.subTest(view='communications'):
            projected = communications(self.store.state(), [event], [], event['utc'])
            self.assertEqual(projected['unknown_kinds'], {})
            self.assertEqual(projected['edges'], [])


if __name__ == '__main__':
    unittest.main()
