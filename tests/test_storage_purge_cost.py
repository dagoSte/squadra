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


if __name__ == '__main__':
    unittest.main()
