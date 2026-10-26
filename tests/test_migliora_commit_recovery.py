"""Regressions for the independent reader's cross-device/index recovery findings."""
import contextlib
import errno
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import threading
import unittest
from unittest.mock import patch

import test_review_regressions as regression_support
from registry import Store, Error


class CommitRecovery(unittest.TestCase):
    setUp = regression_support.ReviewFixes.setUp
    cli = regression_support.ReviewFixes.cli
    refuse = regression_support.ReviewFixes.refuse
    state = regression_support.ReviewFixes.state
    task = regression_support.ReviewFixes.task
    start = regression_support.ReviewFixes.start
    deliver = regression_support.ReviewFixes.deliver
    review = regression_support.ReviewFixes.review
    rule = regression_support.ReviewFixes.rule
    git = regression_support.ReviewFixes.git

    def stage_foreign(self):
        (self.repo / 'foreign.txt').write_text('Another seat owns this')
        self.git('add', 'foreign.txt')

    def check_partial_receipt(self):
        result = self.state()['outputs'][-1]['migliora']
        self.assertEqual(result['commit'], self.git('rev-parse', 'HEAD'))
        self.assertTrue(result['committed']); self.assertFalse(result['applied'])
        self.assertTrue(result['index_reconciled'])
        self.assertEqual(result['files'], ['AGENTS.md'])
        self.assertEqual(self.git('diff', '--cached', '--name-only'), 'foreign.txt')
        self.assertFalse((self.repo / '.git/index.lock').exists())
        self.git('commit', '-qm', 'Other seat commit')
        self.assertEqual(self.git('diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD'), 'foreign.txt')
        self.assertTrue(self.git('show', 'HEAD:AGENTS.md'))

    def test_private_index_stays_on_repo_device_when_tmpdir_is_elsewhere(self):
        import migliora
        rule = self.rule(); self.stage_foreign()
        index = self.repo / '.git/index'
        elsewhere = self.base / 'other-device'; elsewhere.mkdir()
        real_replace = os.replace; indices = []
        def cross_device(source, target, *args, **kwargs):
            if Path(target) == index:
                indices.append(Path(source))
                if Path(source).parent.parent != index.parent:
                    raise OSError(errno.EXDEV, 'Cross-device link')
            return real_replace(source, target, *args, **kwargs)
        with patch('tempfile.tempdir', str(elsewhere)), patch.object(migliora.os, 'replace', side_effect=cross_device):
            self.cli('migliora', 'apply', '--actor', 'A', '--task', 'T', '--rule-file', str(rule))
        self.assertTrue(indices)
        self.assertTrue(all(p.parent.parent == index.parent for p in indices))
        self.assertTrue(self.state()['outputs'][-1]['migliora']['applied'])
        self.assertEqual(self.git('diff', '--cached', '--name-only'), 'foreign.txt')
        self.cli('migliora', 'commit', '--actor', 'A', '--task', 'T', '--rule-file', str(rule))

    def test_exdev_after_head_moves_is_recorded_and_index_is_recovered(self):
        import migliora
        rule = self.rule(); self.stage_foreign(); index = self.repo / '.git/index'
        real_replace, failed = os.replace, []
        def exdev(source, target, *args, **kwargs):
            # The first index replacement fails; the recorded retry recovers the index.
            if Path(target) == index and not failed:
                failed.append(target)
                raise OSError(errno.EXDEV, 'Injected cross-device link')
            return real_replace(source, target, *args, **kwargs)
        with patch.object(migliora.os, 'replace', side_effect=exdev):
            self.assertIn('needs reconciliation', self.refuse('migliora', 'apply', '--actor', 'A', '--task', 'T', '--rule-file', str(rule)))
        self.assertEqual(len(failed), 1)
        self.assertTrue(any(m.startswith('Post-commit error: ') and 'Injected cross-device link' in m
                            for m in self.state()['outputs'][-1]['migliora']['mismatch']))
        self.check_partial_receipt()

    def test_post_commit_verification_error_keeps_receipt_and_clean_index(self):
        import migliora
        rule = self.rule(); self.stage_foreign(); real_git = migliora.git_process
        def fail_verification(repo, *args, **kwargs):
            if args == ('show', 'HEAD:AGENTS.md'):
                raise OSError(errno.EIO, 'Injected verification failure after commit')
            return real_git(repo, *args, **kwargs)
        with patch.object(migliora, 'git_process', side_effect=fail_verification):
            self.assertIn('needs reconciliation', self.refuse('migliora', 'apply', '--actor', 'A', '--task', 'T', '--rule-file', str(rule)))
        self.check_partial_receipt()

    def test_executor_error_after_native_commit_is_recorded(self):
        import migliora
        rule = self.rule(); self.stage_foreign(); real_process = migliora.run_process
        def fail_after_commit(argv, *args, **kwargs):
            result = real_process(argv, *args, **kwargs)
            if argv[:2] == ['git', 'commit']:
                raise OSError(errno.EIO, 'Injected executor failure after HEAD moved')
            return result
        with patch.object(migliora, 'run_process', side_effect=fail_after_commit):
            self.assertIn('needs reconciliation', self.refuse('migliora', 'apply', '--actor', 'A', '--task', 'T', '--rule-file', str(rule)))
        self.check_partial_receipt()

    def test_reconcile_retry_is_nonzero_and_does_not_commit_again(self):
        rule = self.rule()
        hook = self.repo / '.git/hooks/pre-commit'
        hook.write_text('#!/bin/sh\nprintf generated > generated.txt\ngit add generated.txt\n'); hook.chmod(0o755)
        self.refuse('migliora', 'apply', '--actor', 'A', '--task', 'T', '--rule-file', str(rule))
        head = self.git('rev-parse', 'HEAD')
        self.assertIn('needs reconciliation', self.refuse('migliora', 'commit', '--actor', 'A', '--task', 'T', '--rule-file', str(rule)))
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)

    def test_zero_wait_tolerates_a_brief_shared_status_mutex(self):
        import fcntl
        from resource_wait import acquire, queue_folder, queue_file
        store = Store(self.root)
        try:
            with queue_folder(self.root, 'cpu-heavy') as folder:
                fd = queue_file(folder, 'mutex'); fcntl.flock(fd, fcntl.LOCK_SH)
                timer = threading.Timer(.05, lambda: fcntl.flock(fd, fcntl.LOCK_UN))
                timer.start()
                try:
                    with contextlib.redirect_stderr(io.StringIO()):
                        acquire(store, 'A', 'cpu-heavy', 'Status mutex probe', 0)
                finally:
                    timer.join(); os.close(fd)
            self.assertEqual(store.view()['resources']['cpu-heavy']['owner'], 'A')
        finally:
            store.close()

    def test_zero_wait_mutex_grace_is_bounded_and_writes_no_acquisition(self):
        import fcntl
        from resource_wait import acquire, queue_folder, queue_file
        store = Store(self.root)
        try:
            with queue_folder(self.root, 'cpu-heavy') as folder:
                fd = queue_file(folder, 'mutex'); fcntl.flock(fd, fcntl.LOCK_SH)
                try:
                    start = time.monotonic()
                    with self.assertRaisesRegex(Error, 'queue mutex busy'):
                        acquire(store, 'A', 'cpu-heavy', 'Bounded grace', 0)
                    self.assertLess(time.monotonic() - start, 1.5)
                    self.assertNotIn('cpu-heavy', store.view()['resources'])
                finally:
                    os.close(fd)
        finally:
            store.close()

    def test_documented_chmod_recipe_removes_owned_readonly_trees(self):
        root = Path(__file__).resolve().parents[1]
        for name in ('README.md', 'README.it.md', 'references/recovery.md'):
            self.assertIn('chmod -R u+w', (root / name).read_text())
        for name in ('registry', 'checkpoint', 'quarantine'):
            tree = self.base / ('retired-' + name); (tree / 'repo').mkdir(parents=True)
            (tree / 'repo/value').write_text('Synthetic retained bytes')
            (tree / 'repo/value').chmod(0o444); (tree / 'repo').chmod(0o555); tree.chmod(0o555)
            subprocess.run(['chmod', '-R', 'u+w', str(tree)], check=True)
            shutil.rmtree(tree)
            self.assertFalse(tree.exists())
