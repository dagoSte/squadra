"""Existing unregistered checkpoints become visible to retention only when they come from this registry."""
from pathlib import Path
import shutil
import sys
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1] / 'scripts')]
import test_storage_policy as support
import storage_policy as policy
from registry import Error, Store
from recovery import save

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

    def test_D2_legacy_save_is_verified_adopted_with_root_and_retained_by_kind(self):
        package = self.base / 'legacy-save'
        manifest = save(self.store, package, temporary=True)
        events = self.store.events()
        preview = policy.adopt(self.store, paths=[package])
        self.assertEqual(self.store.events(), events, 'Preview never records an adoption')
        self.assertTrue(preview['entries'][0]['adoptable'])
        self.assertEqual(preview['entries'][0]['kind'], 'save')
        result = policy.adopt(self.store, 'C', 'Adopt old save', paths=[package], apply=True)
        self.assertEqual(result['adopted'], [str(package)])
        point = policy.records(self.store)[1][str(package)]
        self.assertEqual(point['root'], str(self.root))
        self.assertEqual(point['kind'], 'save')
        for key in ('event_count', 'event_head'):
            self.assertEqual(point[key], manifest['registry'][key])
        self.assertEqual(point['utc'], events[manifest['registry']['event_count'] - 1]['utc'])
        # Save retention is separate from checkpoint retention.
        protected = policy.plan(self.store, policy={'checkpoints': 0, 'saves': 1, 'hours': 0})
        self.assertFalse(next(r for r in protected['entries'] if r['path'] == str(package))['eligible'])
        again = policy.adopt(self.store, 'C', 'Again', paths=[package], apply=True)
        self.assertEqual(again['adopted'], [])

    def test_D2_scan_handles_saves_checkpoints_tampered_and_foreign_saves(self):
        folder = self.base / 'old-points'
        folder.mkdir()
        good = folder / 'save'
        save(self.store, good, temporary=True)
        self.store.checkpoint(folder / 'checkpoint')
        tampered = folder / 'tampered'
        shutil.copytree(good, tampered, symlinks=True)
        (tampered / 'MANIFEST.json').write_text('{}')
        other_root = self.base / 'other-registry'
        shutil.copytree(self.root, other_root, symlinks=True)
        other = Store(other_root)
        try:
            other.append('output', 'C', {'text': 'Foreign branch', 'next': 'None', 'artifacts': []})
            save(other, folder / 'foreign', temporary=True)
        finally:
            other.close()
        result = policy.adopt(self.store, 'C', 'Scan old points', scan=folder, apply=True)
        self.assertEqual({Path(p).name for p in result['adopted']}, {'save', 'checkpoint'})
        bad = {Path(r['path']).name: r for r in result['entries'] if not r['adoptable']}
        self.assertIn('tampered', bad['tampered']['reason'])
        self.assertIn('this registry', bad['foreign']['reason'])

    def test_D2_a_copy_never_purges_an_adopted_save_of_the_original(self):
        package = self.base / 'old-save'
        save(self.store, package, temporary=True)
        policy.adopt(self.store, 'C', 'Adopt own save', paths=[package], apply=True)
        copy_root = self.base / 'registry-copy'
        shutil.copytree(self.root, copy_root, symlinks=True)
        copy = Store(copy_root)
        try:
            result = policy.purge(copy, 'C', 'Expired copy data', apply=True, policy=EXPIRE_ALL)
            self.assertNotIn(str(package), result['removed'])
            self.assertTrue(package.is_dir())
        finally:
            copy.close()


    def test_resume_suggests_the_sentence_for_unregistered_checkpoints_and_stops_once_adopted(self):
        import json
        inside = self.root / 'checkpoints'
        inside.mkdir()
        self.store.checkpoint(inside / 'old')
        code, out, err = self.cli('doctor', '--resume')
        report = json.JSONDecoder().raw_decode(out)[0]['resume']
        self.assertEqual(report['storage']['unregistered_checkpoints'], [str(inside / 'old')])
        self.assertTrue(any('Use squadra to compact the storage of the registry at ' + str(self.root) in n
                            for n in report['notices']))
        policy.adopt(self.store, 'C', 'Adopt', paths=[inside / 'old'], apply=True)
        code, out, err = self.cli('doctor', '--resume')
        report = json.JSONDecoder().raw_decode(out)[0]['resume']
        self.assertFalse(report['storage']['suggest'])
        self.assertFalse(any('compact the storage' in n for n in report['notices']))


if __name__ == '__main__':
    unittest.main()
