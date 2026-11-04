"""Real frozen candidates, checkpoints, compaction and concurrent publication."""
from concurrent.futures import ThreadPoolExecutor
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
import unittest
from unittest.mock import patch

import test_squadra as support
import frozen_candidate as frozen
import registry_checkpoint
from registry import Store, plain, snapshot
from squadra import deliver
import storage_reuse as reuse
from recovery import save, verify_package, restore_check


class StorageFrozen(unittest.TestCase):
    git = support.Workflow.git
    ack = support.Workflow.ack
    proof = support.Workflow.proof

    def setUp(self):
        support.Workflow.setUp(self)

        def writable_directories():
            for parent, _, _ in os.walk(self.base):
                Path(parent).chmod(0o700)
        self.addCleanup(writable_directories)

    def task(self, name='T', owner='A', deps=None, full_gate=False):
        data = {'id': name, 'title': 'Synthetic sharing', 'owner': owner, 'repo': str(self.repo),
                'base': snapshot(self.repo)['head'], 'scope': self.store.artifact(self.scope),
                'proof': None, 'expected': None, 'depends': deps or [], 'full_gate': full_gate,
                'delivery_policy': 'frozen-v1'}
        self.store.batch([('task', 'C', data, None), ('message', 'C', {'id': 'assign-' + name,
                          'to': owner, 'task': name, 'body': 'Scoped sharing'}, None)])

    def prepare(self):
        (self.repo / 'stable.txt').write_bytes(b'repeated immutable content\n' * 5000)
        self.git('add', 'stable.txt'); self.git('commit', '-qm', 'Shared file')

    def send(self, task='T'):
        self.ack(task)
        self.assertEqual(self.proof(task=task)['status'], 'passed')
        deliver(self.store, 'A', task)
        return Path(self.store.task(task)['delivery']['frozen']['repo'])

    def cleanup_readonly(self, path):
        self.addCleanup(lambda: frozen.remove(path) if path.exists() else None)

    def test_no_clone_freeze_reuses_worktree_and_loose_git_objects(self):
        self.prepare()
        with patch.object(reuse, '_clone', side_effect=OSError(errno.ENOTSUP, 'no COW')):
            first = self.send()
            (self.repo / 'code.py').write_text('VALUE = 2\n')
            self.git('add', 'code.py'); self.git('commit', '-qm', 'Changed file')
            self.task('U')
            second = self.send('U')
        self.cleanup_readonly(self.root / 'candidates')
        self.assertEqual((first / 'stable.txt').stat().st_ino, (second / 'stable.txt').stat().st_ino)
        self.assertNotEqual((first / 'code.py').stat().st_ino, (second / 'code.py').stat().st_ino)
        self.assertEqual(list((first / '.git/objects/pack').glob('*.pack')), [])
        objects = {p.relative_to(first / '.git/objects'): p for p in (first / '.git/objects').glob('??/*') if p.is_file()}
        shared = [(source, second / '.git/objects' / rel) for rel, source in objects.items()
                  if (second / '.git/objects' / rel).is_file()]
        self.assertTrue(shared)
        self.assertTrue(all(a.stat().st_ino == b.stat().st_ino for a, b in shared))
        self.assertEqual(frozen.current(self.store.task('U'), self.root), snapshot(self.repo))
        before = (first / 'stable.txt').read_bytes()
        frozen.remove(second.parent)
        self.assertEqual((first / 'stable.txt').read_bytes(), before)
        self.assertEqual(stat.S_IMODE((first / 'stable.txt').stat().st_mode), 0o444)

    def test_checkpoint_reuses_prior_destination_and_original_modes(self):
        self.prepare()
        source = self.send()
        self.cleanup_readonly(self.root / 'candidates')
        first, second = self.base / 'cp1', self.base / 'cp2'
        self.cleanup_readonly(first); self.cleanup_readonly(second)
        with patch.object(reuse, '_clone', side_effect=OSError(errno.ENOTSUP, 'no COW')):
            frozen.checkpoint(self.store, first)
            frozen.checkpoint(self.store, second, reuse=(first,))
        sha = self.store.task('T')['candidate']['sha256']
        a, b = first / 'candidates' / sha / 'repo/stable.txt', second / 'candidates' / sha / 'repo/stable.txt'
        self.assertEqual(a.stat().st_ino, b.stat().st_ino)
        self.assertEqual(a.read_bytes(), (source / 'stable.txt').read_bytes())
        self.assertEqual(frozen.current(self.store.task('T'), second), self.store.task('T')['candidate'])
        frozen.remove(first)
        self.assertEqual(stat.S_IMODE(b.stat().st_mode), 0o444)
        self.assertEqual(b.read_bytes(), (source / 'stable.txt').read_bytes())

    def test_shared_logical_bytes_do_not_consume_the_new_copy_quota(self):
        self.prepare()
        with patch.object(reuse, '_clone', side_effect=OSError(errno.ENOTSUP, 'no COW')):
            first = self.send()
            quota = frozen.size(first.parent) // 2
            self.assertGreater((first / 'stable.txt').stat().st_size, quota)
            (self.repo / 'code.py').write_text('VALUE = 9\n')
            self.git('add', 'code.py'); self.git('commit', '-qm', 'Small new allocation')
            self.task('U')
            with patch.object(frozen, 'MAX_COPY_BYTES', quota):
                second = self.send('U')
        self.cleanup_readonly(self.root / 'candidates')
        self.assertEqual((first / 'stable.txt').stat().st_ino, (second / 'stable.txt').stat().st_ino)

    def test_immutable_tree_reuse_keeps_artifacts_and_live_leases_independent(self):
        source, first, second = self.base / 'files', self.base / 'copy1', self.base / 'copy2'
        source.mkdir()
        payload = source / 'artifact'; payload.write_bytes(b'archived content')
        payload.chmod(0o600)
        registry_checkpoint.copy_immutable_tree(source, first)
        with patch.object(registry_checkpoint, '_native_clone', return_value=False), \
                patch.object(reuse, '_clone', side_effect=OSError(errno.ENOTSUP, 'no COW')):
            result = registry_checkpoint.copy_immutable_tree(source, second, reuse=(first,))
        target = second / payload.name
        self.assertEqual(target.stat().st_nlink, 1)
        self.assertNotEqual(target.stat().st_ino, (first / payload.name).stat().st_ino)
        payload.write_bytes(b'new mutable bytes')
        self.assertEqual(target.read_bytes(), b'archived content')
        self.assertTrue(registry_checkpoint.verify_tree(second, result['files']))

    def test_compact_dry_run_and_apply_preserve_snapshots_checksums_and_modes(self):
        self.prepare(); candidate = self.send()
        self.cleanup_readonly(self.root / 'candidates')
        first, second = self.base / 'checkpoint1', self.base / 'checkpoint2'
        self.cleanup_readonly(first); self.cleanup_readonly(second)
        self.store.checkpoint(first); self.store.checkpoint(second)
        original = (first / 'CHECKSUMS.json').read_bytes(), (second / 'CHECKSUMS.json').read_bytes()
        before = {str(p): (p.stat().st_ino, stat.S_IMODE(p.stat().st_mode), p.read_bytes())
                  for folder in (first, second) for p in folder.rglob('*') if p.is_file()}
        plan = reuse.compact(self.root, [first, second])
        self.assertTrue(plan['dry_run'])
        self.assertGreater(plan['reused'], 0)
        self.assertEqual(before, {str(p): (p.stat().st_ino, stat.S_IMODE(p.stat().st_mode), p.read_bytes())
                                for folder in (first, second) for p in folder.rglob('*') if p.is_file()})
        with patch.object(reuse, '_clone', side_effect=OSError(errno.ENOTSUP, 'no COW')):
            result = reuse.compact(self.root, [first, second], dry_run=False)
        self.assertGreater(result['hardlink'], 0)
        self.assertEqual(original, ((first / 'CHECKSUMS.json').read_bytes(), (second / 'CHECKSUMS.json').read_bytes()))
        for path, (_, mode, body) in before.items():
            self.assertEqual(Path(path).read_bytes(), body)
            self.assertEqual(stat.S_IMODE(Path(path).stat().st_mode), mode)
        self.assertEqual(frozen.current(self.store.task('T'), self.root), self.store.task('T')['candidate'])
        self.assertEqual((candidate / 'stable.txt').read_bytes(), (self.repo / 'stable.txt').read_bytes())

    def test_unknown_or_tampered_compact_target_refuses_before_changes(self):
        target = self.base / 'unknown'; target.mkdir()
        payload = target / 'file'; payload.write_bytes(b'keep')
        with self.assertRaises(reuse.ReuseError):
            reuse.compact(self.root, [target], dry_run=False)
        self.assertEqual(payload.read_bytes(), b'keep')
        checks = target / 'CHECKSUMS.json'; checks.write_text(json.dumps({'file': '0' * 64}))
        before = payload.stat().st_ino
        with self.assertRaises(reuse.ReuseError):
            reuse.compact(self.root, [target], dry_run=False)
        self.assertEqual(payload.stat().st_ino, before)

    def test_compacted_save_package_still_verifies_and_really_restores(self):
        self.prepare(); self.send()
        self.cleanup_readonly(self.root / 'candidates')
        first, second = self.base / 'save1', self.base / 'save2'
        restored = self.base / 'restored'
        for path in (first, second, restored):
            self.cleanup_readonly(path)
        save(self.store, first, temporary=True)
        save(self.store, second, temporary=True)
        original = (second / 'MANIFEST.json').read_bytes()
        with patch.object(reuse, '_clone', side_effect=OSError(errno.ENOTSUP, 'no COW')):
            result = reuse.compact(self.root, [first, second], dry_run=False)
        self.assertGreater(result['hardlink'], 0)
        self.assertEqual((second / 'MANIFEST.json').read_bytes(), original)
        self.assertEqual(verify_package(second)['schema'], 1)
        self.assertEqual(restore_check(second, restored)['status'], 'passed')

    def test_archived_runs_compact_independently_and_missing_archive_refuses(self):
        self.ack(); run = self.proof()
        path = self.root / 'runs' / run['id']
        before = {p.name: p.read_bytes() for p in path.iterdir() if p.is_file()}
        with patch.object(reuse, '_clone', side_effect=OSError(errno.ENOTSUP, 'no COW')):
            result = reuse.compact(self.root, [path], dry_run=False)
        self.assertEqual(result['hardlink'], 0)
        self.assertEqual(before, {p.name: p.read_bytes() for p in path.iterdir() if p.is_file()})
        (path / 'unarchived').write_bytes(b'not an archived log')
        with self.assertRaisesRegex(reuse.ReuseError, 'not archived'):
            reuse.compact(self.root, [path], dry_run=False)

    def test_freeze_and_checkpoint_serialize_without_partial_candidate(self):
        self.prepare()
        self.ack(); self.assertEqual(self.proof()['status'], 'passed')
        entered, proceed, checking = threading.Event(), threading.Event(), threading.Event()
        original_fetch = frozen.fetch

        def held_fetch(*args):
            entered.set()
            if not proceed.wait(10):
                raise AssertionError('Checkpoint did not start')
            return original_fetch(*args)

        def freeze():
            store = Store(self.root)
            try:
                return deliver(store, 'A', 'T')
            finally:
                store.close()

        target = self.base / 'checkpoint-concurrent'

        def checkpoint():
            store = Store(self.root)
            try:
                checking.set()
                return store.checkpoint(target)
            finally:
                store.close()

        with patch.object(frozen, 'fetch', side_effect=held_fetch), ThreadPoolExecutor(max_workers=2) as workers:
            build = workers.submit(freeze)
            self.assertTrue(entered.wait(10))
            copy = workers.submit(checkpoint)
            self.assertTrue(checking.wait(10))
            proceed.set()
            build.result(timeout=20); copy.result(timeout=20)
        self.cleanup_readonly(self.root / 'candidates'); self.cleanup_readonly(target)
        copied = Store(target, read_only=True)
        try:
            state = copied.state()
            task = state['tasks']['T']
            if task.get('delivery', {}).get('frozen'):
                self.assertEqual(frozen.current(task, target), self.store.task('T')['candidate'])
            self.assertFalse(list((target / 'candidates').glob('.freeze-*')))
        finally:
            copied.close()


if __name__ == '__main__':
    unittest.main()
