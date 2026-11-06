"""purge --apply verifies each recovery point a bounded number of times, not once per target."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1] / 'scripts')]
import test_storage_policy as support
import storage_policy as policy


class PurgeCost(unittest.TestCase):
    setUp = support.StoragePolicy.setUp
    git = support.StoragePolicy.git
    task = support.StoragePolicy.task
    proof = support.StoragePolicy.proof
    checkpoint = support.StoragePolicy.checkpoint
    cli = support.StoragePolicy.cli

    def test_checkpoint_verification_does_not_grow_with_the_number_of_targets(self):
        points = [self.checkpoint('point-%d' % i) for i in range(4)]
        calls = []
        original = policy.verify_checkpoint

        def counted(path):
            calls.append(Path(path).name)
            return original(path)

        with patch.object(policy, 'verify_checkpoint', side_effect=counted):
            result = policy.purge(self.store, 'C', 'Remove expired synthetic data', apply=True,
                                  policy={'checkpoints': 1, 'saves': 0, 'hours': 0})
        self.assertEqual(len(result['removed']), 3)
        # preview, locked replan and one verification for the per-target re-checks: 3 passes over 4 points
        self.assertLessEqual(len(calls), 3 * len(points))
        self.assertTrue(points[-1].is_dir())

    def test_D4_records_are_read_once_per_phase_not_once_per_target(self):
        for count in (1, 5):
            with self.subTest(targets=count):
                points = [self.checkpoint('lot-%d-point-%d' % (count, i)) for i in range(count)]
                with patch.object(policy, 'records', wraps=policy.records) as reads:
                    result = policy.purge(self.store, 'C', 'Remove this expired batch', apply=True,
                                          policy={'checkpoints': 0, 'saves': 0, 'hours': 0})
                self.assertEqual(set(result['removed']), {str(p) for p in points})
                # Preview, locked replan, then one post-intent protection snapshot for the batch.
                self.assertEqual(reads.call_count, 3)

    def candidates(self, prefix, count):
        """Frozen candidates with distinct bytes, eligible for purge: delivered, then superseded."""
        from squadra import deliver
        base = self.git('rev-parse', 'HEAD').decode().strip()
        for i in range(count):
            name = '%s%d' % (prefix, i)
            data = {'id': name, 'title': 'Frozen synthetic task', 'owner': 'A', 'repo': str(self.repo),
                    'base': base, 'scope': self.store.artifact(self.scope), 'proof': None, 'expected': None,
                    'depends': [], 'full_gate': False, 'delivery_policy': 'frozen-v1'}
            self.store.append('task', 'C', data)
            self.store.append('message', 'C', {'id': 'assign-' + name, 'to': 'A', 'task': name, 'body': 'Synthetic'})
            self.store.append('ack', 'A', {'message': 'assign-' + name})
            (self.repo / 'code.py').write_text('VALUE = %r\n' % name)
            self.proof(task=name)
            deliver(self.store, 'A', name)
            self.store.append('task', 'C', {**data, 'id': name + 'N', 'title': 'Synthetic successor'})
            self.assertEqual(self.cli('supersede', '--actor', 'C', '--task', name, '--by', name + 'N',
                                      '--reason', 'Synthetic final read owns it')[0], 0)

    def test_R4_own_candidate_retirements_do_not_reread_protections_per_target(self):
        for count in (1, 3):
            with self.subTest(candidates=count):
                self.candidates('lot%d-' % count, count)
                config = {'checkpoints': 0, 'saves': 0, 'hours': 0}
                targets = [r for r in policy.plan(self.store, policy=config)['entries']
                           if r['kind'] == 'candidate' and r['eligible']]
                self.assertEqual(len(targets), count)
                with patch.object(policy, 'records', wraps=policy.records) as reads:
                    result = policy.purge(self.store, 'C', 'Retire superseded candidates', apply=True, policy=config)
                self.assertTrue({r['path'] for r in targets} <= set(result['removed']))
                # Preview, locked replan, one batch snapshot: the purge's own retire events never force a reread.
                self.assertEqual(reads.call_count, 3)


if __name__ == '__main__':
    unittest.main()
