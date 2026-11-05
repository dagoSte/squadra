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


if __name__ == '__main__':
    unittest.main()
