"""Existing unregistered checkpoints become visible to retention only when they come from this registry."""
from pathlib import Path
import shutil
import sys
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1] / 'scripts')]
import test_storage_policy as support
import storage_policy as policy
from registry import Error, Store

EXPIRE_ALL = {'checkpoints': 0, 'saves': 0, 'hours': 0}


class Adopt(unittest.TestCase):
    setUp = support.StoragePolicy.setUp
    git = support.StoragePolicy.git
    task = support.StoragePolicy.task
    proof = support.StoragePolicy.proof
    cli = support.StoragePolicy.cli

    def legacy(self, name):
        """A checkpoint made the 3.12 way: copied, never recorded."""
        path = self.base / name
        self.store.checkpoint(path)
        return path

    def test_dry_run_lists_and_apply_records_with_the_checkpoint_time(self):
        old = self.legacy('legacy-1')
        last = self.store.events()[-1]
        preview = policy.adopt(self.store, paths=[old])
        self.assertTrue(preview['dry_run'])
        self.assertEqual([r['adoptable'] for r in preview['entries']], [True])
        self.assertFalse(any(o.get('storage_action', {}).get('action') == 'checkpoint'
                             for o in self.store.state()['outputs']), 'A dry run never records')
        result = policy.adopt(self.store, 'C', 'Bring old checkpoints under retention', paths=[old], apply=True)
        self.assertEqual(result['adopted'], [str(old)])
        point = policy.records(self.store)[1][str(old)]
        self.assertEqual((point['event_count'], point['event_head'], point['utc']), (last['seq'], last['hash'], last['utc']))
        entries = {r['path']: r for r in policy.plan(self.store, policy=EXPIRE_ALL)['entries']}
        self.assertTrue(entries[str(old)]['eligible'])

    def test_only_the_coordinator_adopts_and_twice_is_refused(self):
        old = self.legacy('legacy-2')
        with self.assertRaisesRegex(Error, 'Coordinator'):
            policy.adopt(self.store, 'A', 'Not mine', paths=[old], apply=True)
        policy.adopt(self.store, 'C', 'First adoption', paths=[old], apply=True)
        again = policy.adopt(self.store, 'C', 'Second adoption', paths=[old], apply=True)
        self.assertEqual(again['adopted'], [])
        self.assertIn('Already registered', again['entries'][0]['reason'])

    def test_a_tampered_or_foreign_checkpoint_is_never_adopted(self):
        tampered = self.legacy('tampered')
        (tampered / 'events.jsonl').chmod(0o600)
        with (tampered / 'events.jsonl').open('ab') as handle:
            handle.write(b'\n')
        other_root = self.base / 'other-registry'
        shutil.copytree(self.root, other_root, symlinks=True)
        other = Store(other_root)
        try:
            other.append('output', 'C', {'text': 'Diverging history', 'next': 'None', 'artifacts': []})
            other.checkpoint(self.base / 'foreign')
        finally:
            other.close()
        result = policy.adopt(self.store, 'C', 'Try both', paths=[tampered, self.base / 'foreign'], apply=True)
        self.assertEqual(result['adopted'], [])
        reasons = {Path(r['path']).name: r['reason'] for r in result['entries']}
        self.assertIn('checksums', reasons['tampered'].lower())
        self.assertIn('not a checkpoint of this registry', reasons['foreign'].lower())

    def test_scan_adopts_checkpoint_folders_and_skips_other_folders(self):
        folder = self.base / 'old-checkpoints'
        folder.mkdir()
        self.store.checkpoint(folder / 'a')
        self.store.checkpoint(folder / 'b')
        (folder / 'notes').mkdir()
        code, out, err = self.cli('storage', 'adopt', '--scan', str(folder), '--actor', 'C', '--reason', 'Scan', '--apply')
        self.assertEqual(code, 0, err)
        adopted = sorted(Path(p).name for p in policy.records(self.store)[1])
        self.assertEqual(adopted, ['a', 'b'])

    def test_adopted_checkpoint_is_purged_only_by_the_normal_policy(self):
        first, second = self.legacy('keep-newest'), None
        policy.adopt(self.store, 'C', 'Adopt', paths=[first], apply=True)
        self.store.append('output', 'C', {'text': 'Later work', 'next': 'None', 'artifacts': []})
        second = self.legacy('newer')
        policy.adopt(self.store, 'C', 'Adopt', paths=[second], apply=True)
        result = policy.purge(self.store, 'C', 'Keep only the newest', apply=True,
                              policy={'checkpoints': 1, 'saves': 0, 'hours': 0})
        self.assertEqual(result['removed'], [str(first)])
        self.assertTrue(second.is_dir())


if __name__ == '__main__':
    unittest.main()
