"""migliora records only the index recovery that happened, and holds no registry read view while its slow steps run."""
import errno
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_review_regressions as regression_support
from registry import Error, Store


class Migliora(unittest.TestCase):
    cli = regression_support.ReviewFixes.cli
    refuse = regression_support.ReviewFixes.refuse
    state = regression_support.ReviewFixes.state
    task = regression_support.ReviewFixes.task
    start = regression_support.ReviewFixes.start
    rule = regression_support.ReviewFixes.rule
    git = regression_support.ReviewFixes.git

    def setUp(self):
        home = tempfile.TemporaryDirectory(prefix='squadra-rf4b-home-'); self.addCleanup(home.cleanup)
        patcher = patch.dict(os.environ, {'HOME': home.name, 'GIT_CONFIG_NOSYSTEM': '1'})
        patcher.start(); self.addCleanup(patcher.stop)
        regression_support.ReviewFixes.setUp(self)
        for key, value in (('user.name', 'Synthetic'), ('user.email', 's@example.invalid'), ('commit.gpgsign', 'false')):
            self.git('config', key, value)

    def test_a_failed_index_replacement_is_recorded_without_claiming_a_recovery(self):
        import migliora
        rule = self.rule()
        (self.repo / 'foreign.txt').write_text('Another seat owns this'); self.git('add', 'foreign.txt')
        index = self.repo / '.git/index'
        real_replace, renames = os.replace, []

        def refused(source, target, *args, **kwargs):
            if Path(target) == index:
                raise OSError(errno.EACCES, 'Injected persistent index replacement failure')
            return real_replace(source, target, *args, **kwargs)

        def rename(source, target, *args, **kwargs):
            renames.append(target)
            raise OSError(errno.EACCES, 'rename(2) fails as replace did')

        with patch.object(migliora.os, 'replace', side_effect=refused), patch.object(migliora.os, 'rename', side_effect=rename):
            self.assertIn('needs reconciliation', self.refuse('migliora', 'apply', '--actor', 'A', '--task', 'T',
                                                               '--rule-file', str(rule)))
        result = self.state()['outputs'][-1]['migliora']
        self.assertEqual(renames, [])
        self.assertTrue(result['committed']); self.assertFalse(result['applied'])
        self.assertEqual(result['status'], 'reconcile')
        self.assertFalse(result['index_reconciled'])
        self.assertTrue(any(m.startswith('Post-commit error: ') for m in result['mismatch']), result['mismatch'])
        self.assertTrue(any(m.startswith('Index recovery failed: ') for m in result['mismatch']), result['mismatch'])
        self.assertFalse(any('recover' in m.lower() and not m.startswith('Index recovery failed') for m in result['mismatch']),
                         result['mismatch'])
        self.assertFalse((self.repo / '.git/index.lock').exists())

    def test_apply_holds_no_registry_view_while_its_check_and_commit_run(self):
        import migliora
        rule = self.rule()
        store = Store(self.root)
        self.addCleanup(store.close)
        real, seen = migliora.run_process, []

        def observed(argv, *args, **kwargs):
            seen.append((argv[:2], len(store._views)))
            return real(argv, *args, **kwargs)

        with patch.object(migliora, 'run_process', side_effect=observed):
            result = migliora.apply(store, 'A', 'T', rule)
        self.assertTrue(result['applied'], result)
        self.assertEqual([views for _, views in seen], [0, 0], seen)
        self.assertEqual(seen[1][0], ['git', 'commit'])

    def test_finish_holds_no_registry_view_while_its_commit_runs(self):
        import migliora
        rule = self.rule()
        store = Store(self.root)
        self.addCleanup(store.close)
        migliora.apply(store, 'A', 'T', rule, defer_commit=True)
        real, seen = migliora.run_process, []

        def observed(argv, *args, **kwargs):
            seen.append((argv[:2], len(store._views)))
            return real(argv, *args, **kwargs)

        with patch.object(migliora, 'run_process', side_effect=observed):
            result = migliora.finish(store, 'A', 'T', rule)
        self.assertTrue(result['applied'], result)
        self.assertEqual(seen, [(['git', 'commit'], 0)])


if __name__ == '__main__':
    unittest.main()
