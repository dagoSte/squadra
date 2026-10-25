"""A precheck batch owns queued and active runs until terminal evidence is handled."""
import json
import os
from pathlib import Path
import stat
import subprocess
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_precheck as support
import precheck
import registry
import runtime


class PrecheckOwnership(unittest.TestCase):
    setUp = support.Precheck.setUp
    git = support.Precheck.git
    ack = support.Precheck.ack
    task = support.Precheck.task
    declare = support.Precheck.declare
    get = support.Precheck.get
    runs = support.Precheck.runs

    def held(self, run_id):
        try:
            with registry.run_lease(self.root, run_id):
                return False
        except registry.Error as exc:
            if 'lease is held' not in str(exc):
                raise
            return True

    def declaration(self, store, run_id):
        run = store.state()['runs'][run_id]
        observation = {'form': registry.ABANDON_FORM, 'run': run_id, 'observed_at': registry.now(),
                       'processes': 'none', 'results': 'none',
                       'observation': 'Synthetic queued check has no process or result yet.'}
        evidence = store.artifact_bytes(registry.canonical(observation).encode(), 'observation.json',
                                        'synthetic precheck ownership observation')
        return {'id': run_id, 'starter': 'author', 'reason': 'Synthetic no-process observation',
                'evidence': evidence, 'observation': observation, 'legacy': not run.get('lease_version')}

    def attempt_abandon(self, run_id):
        with_store = registry.Store(self.root)
        try:
            try:
                with_store.append('run.abandon', 'A', self.declaration(with_store, run_id))
                return True, None
            except registry.Error as exc:
                return False, str(exc)
        finally:
            with_store.close()

    def test_all_leases_precede_starts_and_survive_terminal_transaction(self):
        self.declare([support.check('one', support.py('pass')), support.check('two', support.py('pass'))])
        original = self.store.batch
        phases = []

        def batch(operations):
            if operations[0][0] == 'run.start':
                ids = [data['id'] for _, _, data, _ in operations]
                self.assertTrue(all(self.held(run_id) for run_id in ids))
                for _, _, data, _ in operations:
                    self.assertEqual(data.get('lease_version'), 1)
                    folder, lock, metadata, receipt = registry.run_lease_paths(self.root, data['id'])
                    self.assertEqual(json.loads(metadata.read_text()), {
                        'run': data['id'], 'phase': 'not-started', 'pgid': None})
                    self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
                    for path in (lock, metadata):
                        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                    self.assertFalse(receipt.exists())
                phases.append('starts-owned')
            if operations[0][0] == 'run.end':
                for _, _, data, _ in operations:
                    self.assertTrue(self.held(data['id']))
                    receipt = registry.run_lease_paths(self.root, data['id'])[3]
                    self.assertTrue(receipt.is_file())
                    terminal = json.loads(receipt.read_text())
                    self.assertEqual(terminal, {'run': data['id'], 'status': data['status'],
                                                'native_rc': data['native_rc'], 'ended': data['ended']})
                    self.assertEqual(stat.S_IMODE(receipt.stat().st_mode), 0o600)
                phases.append('ends-owned')
            result = original(operations)
            self.assertTrue(all(self.held(data['id']) for _, _, data, _ in operations))
            return result

        with patch.object(self.store, 'batch', batch):
            _, summary = precheck.run(self.store, 'A', self.get())
        self.assertEqual(phases, ['starts-owned', 'ends-owned'])
        self.assertEqual([c['status'] for c in summary['checks']], ['passed', 'passed'])
        self.assertTrue(all(not self.held(c['run']) for c in summary['checks']))

    def test_truthful_queued_abandonment_is_refused_and_both_checks_finish(self):
        later = self.base / 'later-executed'
        self.declare([support.check('first', support.py('pass')),
                      support.check('queued', support.py(f"open({str(later)!r}, 'w').write('ran')"))])
        original = precheck._check
        observations = []

        def check(*args, **kwargs):
            if not observations:
                queued = next(r for r in self.runs('precheck') if r['check'] == 'queued')
                self.assertFalse((self.root / 'runs' / queued['id']).exists())
                observations.append(self.attempt_abandon(queued['id']))
            return original(*args, **kwargs)

        error = None
        with patch('precheck._check', check):
            try:
                precheck.run(self.store, 'A', self.get())
            except registry.Error as exc:
                error = str(exc)
        self.assertEqual(observations[0][0], False)
        self.assertIn('lease is held', observations[0][1])
        self.assertIsNone(error)
        self.assertTrue(later.exists())
        self.assertEqual([r['status'] for r in self.runs('precheck')], ['passed', 'passed'])
        self.assertFalse(any(e['kind'] == 'run.abandon' for e in self.store.events()))

    def test_child_inherits_its_lease_and_cleanup_keeps_the_whole_batch_owned(self):
        ready, release = self.base / 'child-ready', self.base / 'child-release'
        code = ("import json,os,time\nfrom pathlib import Path\n"
                "held=[]\n"
                "for fd in range(3,256):\n"
                " try:\n"
                "  st=os.fstat(fd)\n"
                "  if any((p.stat().st_dev,p.stat().st_ino)==(st.st_dev,st.st_ino) "
                f"for p in Path({str(self.root / 'run-leases')!r}).glob('*.lock')): held.append(fd)\n"
                " except OSError: pass\n"
                f"Path({str(ready)!r} + '-tmp').write_text(json.dumps(held))\n"
                f"Path({str(ready)!r} + '-tmp').replace({str(ready)!r})\n"
                "until=time.monotonic()+8\n"
                f"while not Path({str(release)!r}).exists() and time.monotonic()<until: time.sleep(.01)\n")
        command = support.py(code)
        self.declare([support.check('active', command, 10), support.check('queued', support.py('pass'))], total=15)
        popen, terminate = subprocess.Popen, runtime.terminate
        observed = {'cleanup': []}

        def launch(argv, *args, **kwargs):
            proc = popen(argv, *args, **kwargs)
            if argv == command:
                try:
                    limit = time.monotonic() + 5
                    while not ready.exists() and proc.poll() is None and time.monotonic() < limit:
                        time.sleep(.01)
                    observed['inherited'] = json.loads(ready.read_text()) if ready.exists() else []
                    observed['passed'] = kwargs.get('pass_fds', ())
                    current = next(r for r in self.runs('precheck') if r['check'] == 'active')
                    observed['active_held'] = self.held(current['id'])
                finally:
                    release.write_text('continue')
            return proc

        def cleanup(proc):
            observed['cleanup'].append(all(self.held(r['id']) for r in self.runs('precheck')))
            return terminate(proc)

        with patch('precheck.subprocess.Popen', launch), patch('runtime.terminate', cleanup):
            _, summary = precheck.run(self.store, 'A', self.get())
        self.assertTrue(observed['inherited'])
        self.assertEqual(len(observed['passed']), 1)
        self.assertEqual(observed['inherited'], list(observed['passed']))
        self.assertTrue(observed['active_held'])
        self.assertEqual(observed['cleanup'], [True, True])
        self.assertEqual([r['status'] for r in summary['checks']], ['passed', 'passed'])

    def test_recording_failure_keeps_terminal_receipts_that_refuse_abandonment(self):
        self.declare([support.check('one', support.py('pass')), support.check('two', support.py('pass'))])
        original = self.store.batch

        def batch(operations):
            if operations[0][0] == 'run.end':
                raise registry.Error('synthetic terminal transaction refusal')
            return original(operations)

        with patch.object(self.store, 'batch', batch), self.assertRaisesRegex(registry.Error, 'could not be recorded'):
            precheck.run(self.store, 'A', self.get())
        for run in self.runs('precheck'):
            self.assertEqual(run.get('lease_version'), 1)
            self.assertEqual(run['status'], 'running')
            receipt = registry.run_lease_paths(self.root, run['id'])[3]
            self.assertTrue(receipt.is_file())
            self.assertEqual(json.loads(receipt.read_text())['native_rc'], 0)
            self.assertFalse(self.held(run['id']))
            accepted, reason = self.attempt_abandon(run['id'])
            self.assertFalse(accepted)
            self.assertIn('terminal result receipt', reason)
        self.assertFalse(any(e['kind'] in {'run.end', 'run.abandon'} for e in self.store.events()))

    def test_cannot_record_any_start_until_every_lease_is_acquired(self):
        self.declare([support.check('one', support.py('pass')), support.check('two', support.py('pass'))])
        first, second = '1' * 32, '2' * 32
        before = self.store.events()
        with registry.run_lease(self.root, second), patch('precheck.uuid.uuid4', side_effect=[
                SimpleNamespace(hex=first), SimpleNamespace(hex=second)]):
            with self.assertRaisesRegex(registry.Error, 'lease is held'):
                precheck.run(self.store, 'A', self.get())
        self.assertEqual(self.store.events(), before)
        self.assertFalse(self.held(first))
        self.assertFalse((self.root / 'runs' / first).exists())

    def test_skipped_check_has_a_private_interrupted_receipt(self):
        self.declare([support.check('writer', support.py("open('unexpected.txt','w').write('changed')")),
                      support.check('queued', support.py('pass'))])
        with self.assertRaisesRegex(registry.Error, 'source changed'):
            precheck.run(self.store, 'A', self.get())
        queued = next(r for r in self.runs('precheck') if r['check'] == 'queued')
        self.assertEqual(queued['status'], 'interrupted')
        receipt = registry.run_lease_paths(self.root, queued['id'])[3]
        self.assertTrue(receipt.is_file())
        terminal = json.loads(receipt.read_text())
        self.assertEqual((terminal['status'], terminal['native_rc']), ('interrupted', None))
        self.assertEqual(stat.S_IMODE(receipt.stat().st_mode), 0o600)
        self.assertFalse((self.root / 'runs' / queued['id']).exists())

    def test_metadata_setup_cannot_start_a_process_after_the_deadline(self):
        marker = self.base / 'must-not-start'
        self.declare([support.check('only', support.py(f"open({str(marker)!r},'w').write('ran')"), 1)], total=1)
        clock, atomic = time.monotonic, registry.atomic
        extra = [0]

        def setup(path, body):
            result = atomic(path, body)
            if json.loads(body).get('phase') == 'launching':
                extra[0] = 2
            return result

        with patch('precheck.atomic', setup, create=True), patch('precheck.time.monotonic', lambda: clock() + extra[0]):
            with self.assertRaisesRegex(registry.Error, 'deadline reached before start'):
                precheck.run(self.store, 'A', self.get())
        self.assertFalse(marker.exists())
        self.assertEqual(self.runs('precheck')[0]['status'], 'interrupted')
        _, summary = precheck.run(self.store, 'A', self.get())
        self.assertEqual(summary['checks'][0]['status'], 'passed')
        self.assertTrue(marker.exists())

    def test_original_starter_can_recover_a_real_legacy_precheck(self):
        self.store.append('run.start', 'A', {'id': 'legacy-precheck', 'task': 'T', 'mode': 'precheck'})
        with self.assertRaisesRegex(registry.Error, 'original run starter'):
            self.store.append('run.abandon', 'C', self.declaration(self.store, 'legacy-precheck'))
        self.store.append('run.abandon', 'A', self.declaration(self.store, 'legacy-precheck'))
        run = self.store.state()['runs']['legacy-precheck']
        self.assertEqual(run['status'], 'abandoned')
        self.assertTrue(run['abandonment']['legacy'])
        self.assertIsNone(run['native_rc'])


if __name__ == '__main__':
    unittest.main()
