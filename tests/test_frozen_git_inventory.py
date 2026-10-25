"""Committed light changes and staged Git inventories must survive freezing."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_workflow_improvements as workflow_support
from registry import Store, snapshot
from frozen_candidate import current, size
from light_route import measure


class FrozenGitInventory(unittest.TestCase):
    cli = workflow_support.Improvements.cli
    refuse = workflow_support.Improvements.refuse
    state = workflow_support.Improvements.state
    task = workflow_support.Improvements.task
    start = workflow_support.Improvements.start
    deliver = workflow_support.Improvements.deliver
    review = workflow_support.Improvements.review

    def setUp(self):
        workflow_support.Improvements.setUp(self)
        for key, value in [('user.name', 'Synthetic'), ('user.email', 's@example.invalid'), ('commit.gpgsign', 'false')]:
            self.git('config', key, value)

    def git(self, *argv, cwd=None):
        return subprocess.check_output(['git', *argv], cwd=cwd or self.repo).decode().strip()

    def light(self):
        requirements = self.base / 'requirements.json'
        requirements.write_text(json.dumps([{'id': 'R1', 'text': 'Preserve behavior'}]))
        self.cli('task', '--actor', 'C', '--id', 'L', '--title', 'Small descriptive change', '--owner', 'A',
                 '--repo', str(self.repo), '--scope-file', str(self.scope), '--proof-file', str(self.proof),
                 '--reviewer', 'R', '--route', 'light', '--requirements-file', str(requirements))
        self.start('L')

    def commit_change(self):
        (self.repo / 'README.md').write_text('One descriptive line\n')
        self.git('add', 'README.md'); self.git('commit', '-qm', 'Small change')

    def deliver_light(self):
        self.cli('prove', '--actor', 'A', '--task', 'L', '--covers', 'R1', '--', sys.executable, '-c', 'pass')
        self.cli('deliver', '--actor', 'A', '--task', 'L')
        self.cli('ack', '--actor', 'R', '--message', self.state()['tasks']['L']['delivery']['handoff'])

    def finish_light(self):
        self.cli('review', '--actor', 'R', '--task', 'L', '--verdict', 'accepted', '--covers', 'R1', '--report', str(self.report))
        self.cli('check', '--actor', 'C', '--task', 'L')
        self.cli('close', '--actor', 'C', '--task', 'L')
        self.assertEqual(self.state()['tasks']['L']['status'], 'done')

    def test_committed_light_change_can_be_reviewed_checked_and_closed_after_builder_moves(self):
        self.light(); self.commit_change(); self.deliver_light()
        task = self.state()['tasks']['L']; frozen = Path(task['delivery']['frozen']['repo'])
        self.assertEqual(self.git('rev-parse', task['base'] + '^{tree}', cwd=frozen), self.git('rev-parse', task['base'] + '^{tree}'))
        (self.repo / 'README.md').write_text('Builder continues\n' * 100)
        self.git('commit', '-qam', 'Next builder change')
        self.finish_light()
        self.assertEqual(frozen.stat().st_mode & 0o222, 0)

    def test_checkpoint_retains_light_base_without_the_original_checkout(self):
        self.light(); self.commit_change(); self.deliver_light()
        store = Store(self.root)
        try:
            copied = self.base / 'checkpoint'; store.checkpoint(copied)
        finally:
            store.close()
        self.repo.rename(self.base / 'source-unavailable')
        reader = Store(copied, read_only=True)
        try:
            task = reader.task('L'); result = measure(task, current(task, copied), root=copied)
            self.assertEqual((result['files'], result['lines']), (1, 1))
        finally:
            reader.close()

    def test_existing_full_candidate_can_retain_a_later_light_delivery_base(self):
        self.task(); self.start(); self.light(); self.commit_change(); self.deliver()
        before = self.state()['tasks']['T']; frozen = Path(before['delivery']['frozen']['repo'])
        self.deliver_light(); self.finish_light()
        store = Store(self.root)
        try:
            self.assertEqual(current(store.task('T'), self.root), before['candidate'])
        finally:
            store.close()
        self.assertEqual((frozen / '.git').stat().st_mode & 0o222, 0)

    def test_additional_light_base_respects_existing_candidate_storage_limit(self):
        self.task(); self.start(); self.light(); self.commit_change(); self.deliver()
        task = self.state()['tasks']['T']; frozen = Path(task['delivery']['frozen']['repo'])
        self.cli('prove', '--actor', 'A', '--task', 'L', '--covers', 'R1', '--', sys.executable, '-c', 'pass')
        with patch('frozen_candidate.MAX_COPY_BYTES', size(frozen.parent) + 1):
            self.assertIn('storage limit', self.refuse('deliver', '--actor', 'A', '--task', 'L'))
        self.assertEqual((frozen / '.git').stat().st_mode & 0o222, 0)

    def finish_full(self):
        self.deliver()
        task = self.state()['tasks']['T']; frozen = Path(task['delivery']['frozen']['repo'])
        store = Store(self.root)
        try:
            self.assertEqual(current(store.task('T'), self.root), task['candidate'])
        finally:
            store.close()
        self.review(); self.cli('check', '--actor', 'C', '--task', 'T'); self.cli('close', '--actor', 'C', '--task', 'T')
        return frozen

    def test_staged_git_rm_matches_the_proved_snapshot(self):
        self.task(); self.start(); self.git('rm', '-q', 'value.txt')
        frozen = self.finish_full(); self.assertFalse((frozen / 'value.txt').exists())

    def test_staged_git_mv_matches_the_proved_snapshot(self):
        self.task(); self.start(); self.git('mv', 'value.txt', 'renamed.txt')
        frozen = self.finish_full()
        self.assertFalse((frozen / 'value.txt').exists()); self.assertTrue((frozen / 'renamed.txt').is_file())

    def test_staged_rename_to_an_ignored_path_still_belongs_to_the_snapshot(self):
        (self.repo / '.gitignore').write_text('ignored.txt\n')
        self.git('add', '.gitignore'); self.git('commit', '-qm', 'Ignore generated name')
        self.task(); self.start(); self.git('mv', 'value.txt', 'ignored.txt')
        frozen = self.finish_full(); self.assertTrue((frozen / 'ignored.txt').is_file())

    def test_ignored_rename_with_git_pathspec_characters_is_literal(self):
        name = ':(literal)ignored.txt'
        (self.repo / '.gitignore').write_text(name + '\n')
        self.git('add', '.gitignore'); self.git('commit', '-qm', 'Ignore literal name')
        self.task(); self.start(); self.git('--literal-pathspecs', 'mv', 'value.txt', name)
        frozen = self.finish_full(); self.assertTrue((frozen / name).is_file())

    def test_new_staged_path_deleted_from_worktree_keeps_its_deleted_manifest_row(self):
        self.task(); self.start(); new = self.repo / 'staged.txt'; new.write_text('Staged bytes are not in the proved working tree\n')
        self.git('add', 'staged.txt'); new.unlink()
        frozen = self.finish_full(); self.assertFalse((frozen / 'staged.txt').exists())
