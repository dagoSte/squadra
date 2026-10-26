"""Snapshot paths represent the checkout without following parent links or aliases."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from registry import Error, digest, snapshot


class SnapshotPaths(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='squadra-rf4a-snapshot-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / 'repo'; self.repo.mkdir()
        home = self.base / 'home'; home.mkdir()
        env = dict(os.environ, HOME=str(home), GIT_CONFIG_NOSYSTEM='1',
                   GIT_CONFIG_GLOBAL='/dev/null', GIT_TERMINAL_PROMPT='0')
        for name in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_CONFIG_COUNT'):
            env.pop(name, None)
        context = patch.dict(os.environ, env, clear=True)
        context.start(); self.addCleanup(context.stop)
        self.git('init', '--quiet')
        self.git('config', 'user.name', 'Synthetic')
        self.git('config', 'user.email', 'synthetic@example.invalid')
        self.git('config', 'core.precomposeunicode', 'false')
        (self.repo / 'dir').mkdir()
        (self.repo / 'dir/file.txt').write_text('committed\n')
        self.git('add', 'dir/file.txt')
        self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'Synthetic fixture')

    def git(self, *args, body=None):
        return subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', *args],
                              cwd=self.repo, input=body, capture_output=True,
                              timeout=15, check=True).stdout

    def test_tracked_paths_cannot_traverse_an_external_symlinked_parent(self):
        outside = self.base / 'outside'; outside.mkdir()
        (outside / 'file.txt').write_text('outside bytes\n')
        (self.repo / 'dir/file.txt').unlink(); (self.repo / 'dir').rmdir()
        (self.repo / 'dir').symlink_to(outside, target_is_directory=True)
        (self.repo / '.git/info/exclude').write_text('dir\n')
        with self.assertRaisesRegex(Error, r'[Ss]ymlink.*dir/file\.txt'):
            snapshot(self.repo)

    def test_tracked_paths_cannot_traverse_an_internal_symlinked_parent(self):
        original = self.repo / 'original'
        (self.repo / 'dir').rename(original)
        (self.repo / 'dir').symlink_to('original', target_is_directory=True)
        with self.assertRaisesRegex(Error, r'[Ss]ymlink.*dir/file\.txt'):
            snapshot(self.repo)

    def test_symlink_into_registry_keeps_its_link_bytes_in_the_fingerprint(self):
        registry = self.repo / '.squadra'; registry.mkdir()
        (registry / 'state.json').write_text('registry evidence\n')
        (self.repo / 'registry-link').symlink_to('.squadra/state.json')
        rows = {row['path']: row for row in snapshot(self.repo, registry)['files']}
        self.assertNotIn('.squadra/state.json', rows)
        self.assertEqual(rows['registry-link'], {'path': 'registry-link', 'kind': 'symlink',
                                                'sha256': digest(b'.squadra/state.json')})

    def test_owner_execute_bit_matches_git(self):
        path = self.repo / 'mode.txt'; path.write_text('mode\n')
        self.git('config', 'core.filemode', 'true')
        for mode, expected in [(0o654, False), (0o744, True)]:
            with self.subTest(mode=oct(mode)):
                path.chmod(mode); self.git('add', 'mode.txt')
                staged_mode = self.git('ls-files', '--stage', '--', 'mode.txt').split()[0]
                self.assertEqual(staged_mode, b'100755' if expected else b'100644')
                rows = {row['path']: row for row in snapshot(self.repo)['files']}
                self.assertEqual(rows['mode.txt']['executable'], expected)

    def test_case_colliding_index_paths_are_refused(self):
        blob = self.git('hash-object', '-w', '--stdin', body=b'other indexed blob\n').decode().strip()
        (self.repo / 'Config.py').write_text('one worktree file\n')
        self.git('add', 'Config.py')
        self.git('update-index', '--add', '--cacheinfo', '100644', blob, 'config.py')
        entries = self.git('ls-files', '-z').split(b'\0')
        self.assertIn(b'Config.py', entries); self.assertIn(b'config.py', entries)
        with self.assertRaisesRegex(Error, r'[Cc]ase|[Aa]lias'):
            snapshot(self.repo)

    def test_unicode_normalization_colliding_index_paths_are_refused(self):
        first, second = 'caf\u00e9.txt', 'cafe\u0301.txt'
        blob = self.git('hash-object', '-w', '--stdin', body=b'index-only\n').decode().strip()
        self.git('update-index', '--add', '--cacheinfo', '100644', blob, first)
        self.git('update-index', '--add', '--cacheinfo', '100644', blob, second)
        entries = self.git('ls-files', '-z').split(b'\0')
        self.assertIn(os.fsencode(first), entries); self.assertIn(os.fsencode(second), entries)
        with self.assertRaisesRegex(Error, r'[Cc]ase|[Aa]lias'):
            snapshot(self.repo)

    def test_missing_parent_keeps_the_tracked_deletion(self):
        (self.repo / 'dir/file.txt').unlink(); (self.repo / 'dir').rmdir()
        self.assertEqual(snapshot(self.repo)['files'], [{'path': 'dir/file.txt', 'kind': 'deleted'}])


if __name__ == '__main__':
    unittest.main()
