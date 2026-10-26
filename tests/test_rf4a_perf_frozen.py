"""Freezing and checkpoint validation use a bounded number of Git processes."""
from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import test_board as support
import frozen_candidate as frozen
import recovery
import registry


class FrozenGitProcessBounds(unittest.TestCase):
    cli = support.Board.cli
    state = support.Board.state
    task = support.Board.task
    start = support.Board.start
    deliver = support.Board.deliver
    FILES = 2000
    GIT_PROCESS_LIMIT = 20

    def setUp(self):
        support.Board.setUp(self)
        self.addCleanup(self.make_writable)
        self.task(); self.start()
        folder = self.repo / 'many'; folder.mkdir()
        for number in range(self.FILES):
            (folder / f'{number:04d}.txt').write_text(f'Unique synthetic payload {number:04d}\n')
        self.raw('add', 'many')
        self.raw('-c', 'commit.gpgsign=false', 'commit', '-qm', 'Many distinct synthetic blobs')
        blobs = self.raw('ls-files', '--stage', '-z').split(b'\0')
        identities = {row.split(b' ', 2)[1] for row in blobs if row}
        self.assertGreaterEqual(len(identities), self.FILES)

    def raw(self, *args):
        env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
                   GIT_AUTHOR_NAME='Synthetic', GIT_AUTHOR_EMAIL='s@example.invalid',
                   GIT_COMMITTER_NAME='Synthetic', GIT_COMMITTER_EMAIL='s@example.invalid')
        return subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', *args], cwd=self.repo,
                              env=env, capture_output=True, check=True, timeout=30).stdout

    def make_writable(self):
        for path in [self.base, *self.base.rglob('*')]:
            if not path.is_symlink():
                path.chmod(path.stat().st_mode | 0o700)

    @contextmanager
    def bounded_git(self, operation):
        calls = []
        original = registry.git_process

        def counted(repo, *args, **kwargs):
            calls.append(args)
            self.assertLessEqual(len(calls), self.GIT_PROCESS_LIMIT,
                                 f'{operation} launches Git per file: call {len(calls)} is {args!r}')
            return original(repo, *args, **kwargs)

        with ExitStack() as stack:
            for module in (registry, frozen, recovery):
                if hasattr(module, 'git_process'):
                    stack.enter_context(patch.object(module, 'git_process', counted))
            yield calls
        print(f'{operation}: files={self.FILES + 1}, git_process_calls={len(calls)}')

    def test_freeze_many_distinct_blobs_has_bounded_git_processes(self):
        store = registry.Store(self.root)
        try:
            task = registry.plain(store.task('T'))
            candidate = registry.snapshot(self.repo, self.root)
            with self.bounded_git('freeze'):
                result = frozen.freeze(store, task, candidate)
            self.assertEqual(result['sha256'], candidate['sha256'])
            self.assertEqual((Path(result['repo']) / 'many/1999.txt').read_text(),
                             'Unique synthetic payload 1999\n')
        finally:
            store.close()

    def test_checkpoint_many_distinct_blobs_has_bounded_git_processes(self):
        # Prepare a genuine delivery; the measured checkpoint must perform all checks.
        with patch.object(frozen, 'validate_candidate', return_value=None):
            self.deliver()
        store = registry.Store(self.root)
        try:
            target = self.base / 'checkpoint'; report = {}
            with self.bounded_git('checkpoint'):
                store.checkpoint(target, report=report)
            sha = self.state()['tasks']['T']['delivery']['frozen']['sha256']
            self.assertEqual(report['candidates'][0]['status'], 'available')
            self.assertEqual((target / 'candidates' / sha / 'repo/many/1999.txt').read_text(),
                             'Unique synthetic payload 1999\n')
        finally:
            store.close()


class DeletedIndexGitProcessBounds(unittest.TestCase):
    cli = support.Board.cli
    state = support.Board.state
    task = support.Board.task
    start = support.Board.start
    raw = FrozenGitProcessBounds.raw
    make_writable = FrozenGitProcessBounds.make_writable
    bounded_git = FrozenGitProcessBounds.bounded_git
    FILES = 30
    GIT_PROCESS_LIMIT = 20

    def setUp(self):
        support.Board.setUp(self)
        self.addCleanup(self.make_writable)
        self.task(); self.start()

    def test_many_staged_additions_missing_from_worktree_use_one_index_update(self):
        names = [f'staged-{number:02d}.txt' for number in range(self.FILES - 1)]
        names.append(':(literal)deleted\tfile.txt')
        for number, name in enumerate(names):
            (self.repo / name).write_text(f'Synthetic staged-only payload {number}\n')
        self.raw('--literal-pathspecs', 'add', '--', *names)
        for name in names:
            (self.repo / name).unlink()
        candidate = registry.snapshot(self.repo, self.root)
        self.assertEqual(sum(row['kind'] == 'deleted' for row in candidate['files']), self.FILES)
        store = registry.Store(self.root)
        try:
            task = registry.plain(store.task('T'))
            with self.bounded_git('freeze-deleted-staged'):
                result = frozen.freeze(store, task, candidate)
            self.assertEqual(result['sha256'], candidate['sha256'])
            self.assertEqual(frozen.measured(task, Path(result['repo'])), candidate)
            self.assertFalse(any((Path(result['repo']) / name).exists() for name in names))
        finally:
            store.close()


if __name__ == '__main__':
    unittest.main()
