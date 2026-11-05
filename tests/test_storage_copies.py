"""A copy of a registry never purges the recovery points of the registry it was copied from."""
from pathlib import Path
import shutil
import sys
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1] / 'scripts')]
import test_storage_policy as support
import storage_policy as policy
from registry import Store

EXPIRE_ALL = {'checkpoints': 0, 'saves': 0, 'hours': 0}


class RegistryCopies(unittest.TestCase):
    setUp = support.StoragePolicy.setUp
    git = support.StoragePolicy.git
    task = support.StoragePolicy.task
    proof = support.StoragePolicy.proof
    cli = support.StoragePolicy.cli

    def recorded_checkpoint(self, path):
        code, _, err = self.cli('checkpoint', '--actor', 'C', '--to', str(path))
        self.assertEqual(code, 0, err)
        return path

    def copy(self, name):
        target = self.base / name
        shutil.copytree(self.root, target, symlinks=True)
        return Store(target)

    def test_a_copy_lists_the_originals_points_as_foreign_and_never_purges_them(self):
        outside = self.recorded_checkpoint(self.base / 'original-point')
        inside = self.root / 'checkpoints'
        inside.mkdir()
        legacy = inside / 'legacy'
        self.store.checkpoint(legacy)
        # A record made before points carried their root: no root field, path inside the original.
        policy.record(self.store, 'C', 'checkpoint', path=str(legacy), event_count=1, event_head='0' * 64)
        copy = self.copy('registry-copy')
        self.addCleanup(copy.close)
        preview = policy.plan(copy, policy=EXPIRE_ALL)
        self.assertNotIn(str(outside), [r['path'] for r in preview['entries']])
        self.assertNotIn(str(legacy), [r['path'] for r in preview['entries']])
        foreign = {r['path'] for r in preview['ignored'] if 'another registry' in r['reason']}
        self.assertEqual(foreign, {str(outside), str(legacy)})
        policy.purge(copy, 'C', 'Purge inside the copy', apply=True, policy=EXPIRE_ALL)
        self.assertTrue(outside.is_dir(), "A copy deleted the original's checkpoint")
        self.assertTrue(legacy.is_dir(), "A copy deleted a checkpoint inside the original")

    def test_the_original_still_purges_its_own_points(self):
        outside = self.recorded_checkpoint(self.base / 'own-point')
        point = policy.records(self.store)[1][str(outside)]
        self.assertEqual(Path(point['root']).resolve(), self.root.resolve())
        result = policy.purge(self.store, 'C', 'Own expired point', apply=True, policy=EXPIRE_ALL)
        self.assertIn(str(outside), result['removed'])

    def test_save_records_carry_the_registry_root(self):
        code, _, err = self.cli('save', '--actor', 'C', '--to', str(self.base / 'package'), '--temporary')
        self.assertEqual(code, 0, err)
        saved = self.store.state()['saves'][-1]
        self.assertEqual(Path(saved['root']).resolve(), self.root.resolve())
        copy = self.copy('copy-with-save')
        self.addCleanup(copy.close)
        self.assertNotIn(str(self.base / 'package'), [r['path'] for r in policy.plan(copy, policy=EXPIRE_ALL)['entries']])


if __name__ == '__main__':
    unittest.main()
