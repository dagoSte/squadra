"""Recovery's Git process count stays bounded as unique tracked blobs increase."""
from contextlib import contextmanager
import hashlib
from unittest.mock import patch

import test_ops_seams as support
import recovery
import registry


class RecoveryProcessBudget(support.Squad):
    def populate(self, prefix='data'):
        self.git('config', 'gc.auto', '0')
        self.git('config', 'maintenance.auto', 'false')
        folder = self.repo / 'many'; folder.mkdir()
        names = []
        for number in range(2000):
            name = 'many/' + prefix + '_' + str(number) + '.txt'
            (self.repo / name).write_bytes(('Unique synthetic payload ' + str(number) + '\n').encode())
            names.append(name)
        self.git('add', 'many')
        self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'Many distinct tracked blobs')
        return names

    @contextmanager
    def budget(self, maximum):
        original = registry.git_process
        calls = []
        def counted(repo, *args, **kwargs):
            calls.append(args)
            self.assertLessEqual(len(calls), maximum,
                                 'Git subprocess count must be bounded, not one process per tracked blob: ' +
                                 repr(calls[-3:]))
            return original(repo, *args, **kwargs)
        with patch.object(registry, 'git_process', side_effect=counted), \
                patch.object(recovery, 'git_process', side_effect=counted):
            yield calls
            print(self.id() + ': Git subprocesses ' + str(len(calls)) + '/' + str(maximum))

    def test_checked_index_batches_two_thousand_unique_blobs(self):
        self.populate()
        expected = registry.digest(self.git('ls-files', '--stage', '-z'))
        with self.budget(8) as calls:
            self.assertEqual(recovery.checked_index(self.repo), expected)
        self.assertTrue(calls)

    def test_full_save_has_a_fixed_git_process_budget(self):
        names = self.populate()
        with self.budget(64) as calls:
            manifest = recovery.save(self.store, self.base / 'saved', repos=[str(self.repo)], temporary=True)
        self.assertEqual(manifest['status'], 'saved')
        self.assertEqual(len(manifest['repositories'][0]['snapshot']['files']), len(names) + 1)
        self.assertTrue(calls)

    def test_bulk_allowance_and_save_preserve_all_three_content_hashes_with_fixed_budget(self):
        names = self.populate('credentials')
        destination = self.base / 'saved'
        with self.budget(64) as calls:
            decision = recovery.allow_names(self.store, 'C', destination, [str(self.repo)], names,
                                            'Synthetic name-only allowances', temporary=True)
            manifest = recovery.save(self.store, destination, repos=[str(self.repo)], temporary=True,
                                     name_allowance=decision)
        self.assertTrue(calls)
        rows = manifest['name_allowance']['files']
        self.assertEqual([row['path'] for row in rows], names)
        for row in rows:
            expected = hashlib.sha256((self.repo / row['path']).read_bytes()).hexdigest()
            self.assertEqual((row['sha256'], row['index_sha256'], row['head_sha256']), (expected,) * 3)

    def test_shared_blob_keeps_head_symlink_validation_when_index_uses_it_as_a_regular_file(self):
        (self.repo / 'carrier.txt').write_bytes(b'../outside')
        (self.repo / 'link').symlink_to('../outside')
        self.git('add', 'carrier.txt', 'link')
        self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'Shared link and regular-file blob')
        self.git('rm', 'link')
        with self.assertRaisesRegex(registry.Error, 'HEAD symlink'):
            recovery.save(self.store, self.base / 'saved', repos=[str(self.repo)], temporary=True)
