"""A frozen delivery's handoff names the frozen copy the reader must read, never the builder's live tree."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_board as support
from registry import snapshot


class FrozenHandoffBody(unittest.TestCase):
    cli = support.Board.cli
    state = support.Board.state
    task = support.Board.task
    start = support.Board.start
    deliver = support.Board.deliver

    def setUp(self):
        home = tempfile.TemporaryDirectory(prefix='squadra-rf4b-home-'); self.addCleanup(home.cleanup)
        patcher = patch.dict(os.environ, {'HOME': home.name}); patcher.start(); self.addCleanup(patcher.stop)
        support.Board.setUp(self)

    def body(self, name='T'):
        task = self.state()['tasks'][name]
        return task, self.state()['messages'][task['delivery']['handoff']]['body']

    def check_frozen(self, task, body):
        sha = task['delivery']['frozen']['sha256']
        frozen = self.root / 'candidates' / sha / 'repo'
        self.assertEqual(task.get('delivery_policy'), 'frozen-v1')
        self.assertTrue(frozen.is_dir())
        first = body.splitlines()[0]
        self.assertIn(str(frozen), first)
        self.assertIn('candidates/' + sha + '/repo', first)
        self.assertIn('may differ', first)
        self.assertNotIn('the working tree of ' + str(self.repo) + ' matching', body)
        self.assertNotIn('Read exact candidate ' + task['candidate']['head'] + ' for', body)
        return first

    def test_clean_frozen_delivery_names_the_frozen_copy(self):
        self.task(); self.start(); self.deliver()
        task, body = self.body()
        first = self.check_frozen(task, body)
        self.assertIn(task['candidate']['head'], first)
        self.assertNotIn('uncommitted', first)

    def test_dirty_frozen_delivery_names_the_frozen_copy_and_the_paths_its_commit_lacks(self):
        self.task(); self.start()
        (self.repo / 'value.txt').write_text('Changed\n'); (self.repo / 'new.txt').write_text('Untracked\n')
        self.deliver()
        task, body = self.body()
        first = self.check_frozen(task, body)
        self.assertIn('NOT commit ' + task['candidate']['head'], first)
        self.assertIn('2 uncommitted or untracked paths: new.txt, value.txt', first)
        frozen = Path(self.root / 'candidates' / task['delivery']['frozen']['sha256'] / 'repo')
        self.assertEqual((frozen / 'new.txt').read_text(), 'Untracked\n')

    def test_builder_edits_after_delivery_do_not_change_what_the_body_names(self):
        self.task(); self.start(); self.deliver()
        task, body = self.body()
        (self.repo / 'value.txt').write_text('Builder continues\n')
        self.assertNotEqual(snapshot(self.repo)['sha256'], task['candidate']['sha256'])
        frozen = self.root / 'candidates' / task['delivery']['frozen']['sha256'] / 'repo'
        self.assertEqual((frozen / 'value.txt').read_text(), 'Synthetic fixture\n')
        self.check_frozen(task, body)

    def test_reassigned_frozen_read_names_the_frozen_copy(self):
        self.task(); self.start(); self.deliver()
        (self.repo / 'value.txt').write_text('Builder continues\n')
        self.cli('reassign', '--actor', 'C', '--task', 'T', '--reviewer', 'Other', '--reason', 'Reader occupied')
        task, body = self.body()
        self.check_frozen(task, body)

    def git(self, *args):
        import subprocess
        return subprocess.run(['git', *args], cwd=self.repo, check=True, capture_output=True).stdout.decode()

    def test_a_change_hidden_from_git_status_is_still_named(self):
        for flag, name in (('--skip-worktree', 'S'), ('--assume-unchanged', 'U')):
            with self.subTest(flag=flag):
                self.git('update-index', flag, 'value.txt')
                (self.repo / 'value.txt').write_text('Hidden ' + name + '\n')
                self.assertEqual(self.git('status', '--porcelain'), '')
                self.task(name); self.start(name); self.deliver(name)
                task, body = self.body(name)
                first = self.check_frozen(task, body)
                self.assertIn('NOT commit ' + task['candidate']['head'] + ', which lacks 1 uncommitted or untracked paths: value.txt', first)
                self.git('update-index', flag.replace('--', '--no-'), 'value.txt')
                self.git('checkout', '--', 'value.txt')

    def test_deleted_and_mode_changed_paths_are_named(self):
        (self.repo / 'other.txt').write_text('Committed\n')
        self.git('add', 'other.txt')
        self.git('-c', 'user.name=Synthetic', '-c', 'user.email=s@example.invalid', '-c', 'commit.gpgsign=false',
                 'commit', '-qm', 'second file')
        (self.repo / 'other.txt').unlink(); (self.repo / 'value.txt').chmod(0o755)
        self.task(); self.start(); self.deliver()
        task, body = self.body()
        self.assertIn('which lacks 2 uncommitted or untracked paths: other.txt, value.txt', self.check_frozen(task, body))

    def test_a_legacy_delivery_names_a_change_hidden_from_git_status(self):
        self.git('update-index', '--skip-worktree', 'value.txt')
        (self.repo / 'value.txt').write_text('Hidden change\n')
        body = self.legacy_body()
        head = snapshot(self.repo)['head']
        self.assertNotIn('Read exact candidate ' + head + ' for L.', body)
        self.assertIn('NOT commit ' + head + ', which lacks 1 uncommitted or untracked paths: value.txt', body)

    def legacy_body(self):
        from registry import Store
        from squadra import deliver
        import runtime, sys
        store = Store(self.root)
        try:
            data = {'id': 'L', 'title': 'Legacy work', 'owner': 'A', 'reviewer': 'R', 'repo': str(self.repo),
                    'base': snapshot(self.repo)['head'], 'scope': store.artifact(self.scope),
                    'proof': None, 'expected': None, 'depends': [], 'full_gate': False}
            store.batch([('task', 'C', data, None), ('message', 'C', {'id': 'assign-L', 'to': 'A', 'task': 'L',
                         'body': 'Scoped work'}, None)])
            store.append('ack', 'A', {'message': 'assign-L'})
            runtime.execute(store, 'A', 'L', argv=[sys.executable, '-c', 'pass'])
            handoff = deliver(store, 'A', 'L')['handoff']
            return store.state()['messages'][handoff]['body']
        finally:
            store.close()

    def test_legacy_delivery_keeps_the_candidate_line(self):
        body = self.legacy_body()
        self.assertTrue(body.startswith('Read exact candidate ' + snapshot(self.repo)['head'] + ' for L.\n'), body)
        self.assertNotIn('frozen copy', body)


if __name__ == '__main__':
    unittest.main()
