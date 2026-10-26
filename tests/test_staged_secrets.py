"""Refuse staged private keys before a recovery package can export index patches."""
import json
from pathlib import Path
import shutil
import subprocess

import test_ops_seams as ops

KEY = b'----\x2dBEGIN OPENSSH PRIVATE KEY-----\nsynthetic-only\n-----END OPENSSH PRIVATE KEY-----\n'
BENIGN = b'Synthetic benign source\n'


class StagedSecrets(ops.Squad):
    def staged_key(self, name='notes.txt', body=KEY):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(BENIGN)
        self.git('add', name)
        self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'Synthetic benign baseline')
        path.write_bytes(body)
        self.git('add', name)
        path.write_bytes(BENIGN)
        return path

    def save_args(self, destination='saved'):
        return ['save', '--actor', 'C', '--to', str(self.base / destination),
                '--repo', str(self.repo), '--temporary']

    def assert_refused_without_package(self, args, path):
        rc, _, err = self.cli(*args)
        self.assertEqual(rc, 1, err)
        self.assertIn(path, err)
        self.assertIn('staged', err.lower())
        self.assertFalse(Path(args[args.index('--to') + 1]).exists())

    def test_staged_only_key_in_ordinary_or_binary_tracked_file_leaves_no_package(self):
        for name, body in [('notes.txt', KEY), ('binary.dat', b'\x00synthetic\n' + KEY)]:
            with self.subTest(name=name):
                path = self.staged_key(name, body)
                try:
                    self.assert_refused_without_package(self.save_args(name + '-save'), name)
                    self.assertEqual(path.read_bytes(), BENIGN)
                    self.assertEqual(self.git('show', 'HEAD:' + name), BENIGN)
                    self.assertEqual(self.git('show', ':' + name), body)
                finally:
                    path.write_bytes(BENIGN)
                    self.git('add', name)

    def test_allowing_another_name_never_allows_a_staged_key_in_an_ordinary_file(self):
        module = self.repo / 'deploy/credentials.py'; module.parent.mkdir()
        module.write_text('VALUE = "synthetic source module"\n')
        self.git('add', 'deploy/credentials.py')
        self.staged_key()
        self.assert_refused_without_package(self.save_args() + ['--allow-name', 'deploy/credentials.py',
                                            '--reason', 'Synthetic source module'], 'notes.txt')

    def test_name_allowed_file_with_staged_only_key_is_refused_without_package(self):
        self.staged_key('deploy/credentials.py')
        self.assert_refused_without_package(self.save_args() + ['--allow-name', 'deploy/credentials.py',
                                            '--reason', 'Must not override key detection'], 'deploy/credentials.py')

    def test_later_repository_is_checked_before_any_repository_is_packaged(self):
        second = self.base / 'second'; shutil.copytree(self.repo, second)
        path = second / 'code.py'; benign = path.read_bytes()
        path.write_bytes(KEY)
        subprocess.run(['git', 'add', 'code.py'], cwd=second, capture_output=True, check=True)
        path.write_bytes(benign)
        self.assert_refused_without_package(self.save_args() + ['--repo', str(second)], 'code.py')

    def test_new_staged_file_without_head_or_worktree_is_checked(self):
        path = self.repo / 'new.txt'; path.write_bytes(KEY)
        self.git('add', 'new.txt'); path.unlink()
        self.assert_refused_without_package(self.save_args(), 'new.txt')

    def test_staged_symlink_blob_is_checked_even_when_head_and_worktree_links_are_benign(self):
        path = self.repo / 'link'
        path.symlink_to('code.py')
        self.git('add', 'link')
        self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'Synthetic benign link')
        path.unlink(); path.symlink_to(KEY.decode('ascii'))
        self.git('add', 'link')
        path.unlink(); path.symlink_to('code.py')
        self.assert_refused_without_package(self.save_args(), 'link')

    def test_successful_manifest_declares_the_three_tracked_versions(self):
        rc, _, err = self.cli(*self.save_args())
        self.assertEqual(rc, 0, err)
        limitation = json.loads((self.base / 'saved/MANIFEST.json').read_text())['limitations']
        for text in ('HEAD', 'index', 'working tree', 'untracked', 'history'):
            self.assertIn(text, limitation)
