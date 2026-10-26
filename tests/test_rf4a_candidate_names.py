"""Retained HEAD names are historical; content and changed-name guards remain enforced."""
import json
import os
from pathlib import Path
import subprocess
import unittest

import test_board as support
import test_rf4a_frozen as policy_support
import frozen_candidate as frozen
import recovery
from registry import Error, Store


KEY = ('-' * 5 + 'BEGIN PRIVATE KEY' + '-' * 5 + '\nSYNTHETIC-ONLY\n').encode()
NAME = 'deploy/credentials.py'
BENIGN = b'APP_MODE = "synthetic"\n'


class CandidateNamePolicy(unittest.TestCase):
    cli = support.Board.cli
    state = support.Board.state
    task = support.Board.task
    start = support.Board.start
    deliver = support.Board.deliver
    legacy_secret_delivery = policy_support.FrozenPolicy.legacy_secret_delivery

    def setUp(self):
        support.Board.setUp(self)
        self.addCleanup(self.make_writable)

    def make_writable(self):
        for path in [self.base, *self.base.rglob('*')]:
            if not path.is_symlink():
                path.chmod(path.stat().st_mode | 0o700)

    def raw(self, *args):
        env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
                   GIT_AUTHOR_NAME='Synthetic', GIT_AUTHOR_EMAIL='s@example.invalid',
                   GIT_COMMITTER_NAME='Synthetic', GIT_COMMITTER_EMAIL='s@example.invalid')
        return subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', *args], cwd=self.repo,
                              env=env, capture_output=True, timeout=15, check=True).stdout

    def commit_file(self, name=NAME, body=BENIGN):
        path = self.repo / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        self.raw('add', '--', name)
        self.raw('-c', 'commit.gpgsign=false', 'commit', '-qm', 'Synthetic name-policy fixture')

    def delivered_file(self, name=NAME, body=BENIGN):
        self.commit_file(name, body)
        self.task(); self.start(); self.deliver()
        return self.state()['tasks']['T']['delivery']['frozen']['sha256']

    def allowance(self, store, target):
        return recovery.allow_names(store, 'C', target, [str(self.repo)], [NAME],
                                    'Synthetic source module; one-save name allowance', temporary=True)

    def assert_not_saved(self, target):
        manifest = target / 'MANIFEST.json'
        self.assertFalse(manifest.exists() and json.loads(manifest.read_text())['status'] == 'saved')

    def test_committed_benign_name_checkpoints_and_saves_with_live_allowance(self):
        sha = self.delivered_file(); store = Store(self.root)
        try:
            # The separate live-repository policy still requires an explicit allowance.
            with self.assertRaisesRegex(Error, r'Tracked secret.*deploy/credentials\.py'):
                recovery.save(store, self.base / 'no-live-allowance', temporary=True)
            report = {}; checkpoint = self.base / 'checkpoint'
            store.checkpoint(checkpoint, report=report)
            self.assertEqual(report['candidates'][0]['status'], 'available')
            self.assertEqual((checkpoint / 'candidates' / sha / 'repo' / NAME).read_bytes(), BENIGN)
            target = self.base / 'save'; allowance = self.allowance(store, target)
            manifest = recovery.save(store, target, repos=[str(self.repo)], temporary=True,
                                     name_allowance=allowance)
            self.assertEqual(manifest['name_allowance']['decision'], allowance)
            self.assertEqual(manifest['candidates'][0]['status'], 'available')
            self.assertEqual(manifest['candidates'][0]['sha256'], sha)
            self.assertEqual((target / 'registry/candidates' / sha / 'repo' / NAME).read_bytes(), BENIGN)
        finally:
            store.close()

    def test_cached_committed_key_is_history_and_checkpoints(self):
        # dec-3110-candidate-history: content committed at the delivered HEAD is Git history, as in the all-ref bundle.
        sha = self.delivered_file(body=KEY)
        self.commit_file(body=BENIGN)
        store = Store(self.root)
        try:
            report = {}; checkpoint = self.base / 'checkpoint-key'
            store.checkpoint(checkpoint, report=report)
            self.assertEqual(report['candidates'][0]['status'], 'available')
            self.assertTrue((checkpoint / 'candidates' / sha / 'repo' / NAME).exists())
        finally:
            store.close()

    def test_index_only_key_in_committed_name_is_rejected_by_index_content_check(self):
        self.commit_file()
        path = self.repo / NAME; path.write_bytes(KEY); self.raw('add', '--', NAME)
        path.write_bytes(BENIGN)
        self.assertEqual(self.raw('show', 'HEAD:' + NAME), BENIGN)
        self.assertEqual(path.read_bytes(), BENIGN)
        self.assertEqual(self.raw('show', ':' + NAME), KEY)
        with self.assertRaisesRegex(Error, r'private-key index blob refused: deploy/credentials\.py'):
            frozen.validate_candidate(self.repo)

    def test_changed_worktree_name_stays_refused_for_bytes_and_owner_mode(self):
        self.commit_file(); path = self.repo / NAME
        for body, mode in [(b'APP_MODE = "changed"\n', 0o644), (BENIGN, 0o755)]:
            with self.subTest(mode=oct(mode), body_changed=body != BENIGN):
                path.write_bytes(body); path.chmod(mode)
                with self.assertRaisesRegex(Error, r'secret file refused: deploy/credentials\.py'):
                    frozen.validate_candidate(self.repo)

    def test_added_index_and_worktree_key_content_stays_refused_committed_key_is_history(self):
        path = self.repo / 'value.txt'; benign = path.read_bytes()
        path.write_bytes(KEY)
        with self.assertRaisesRegex(Error, r'value\.txt'):
            frozen.validate_candidate(self.repo)
        self.raw('add', 'value.txt'); path.write_bytes(benign)
        with self.assertRaisesRegex(Error, r'private-key index blob refused: value\.txt'):
            frozen.validate_candidate(self.repo)
        path.write_bytes(KEY)
        self.raw('-c', 'commit.gpgsign=false', 'commit', '-qm', 'Synthetic HEAD key control')
        path.write_bytes(benign)
        frozen.validate_candidate(self.repo)  # the committed key is history; the changed benign row passes

    def test_legacy_untracked_name_still_refuses_checkpoint_and_save(self):
        sha = self.legacy_secret_delivery(); store = Store(self.root)
        try:
            checkpoint = self.base / 'legacy-checkpoint'
            with self.assertRaisesRegex(Error, sha + r'.*\.env'):
                store.checkpoint(checkpoint)
            self.assertFalse((checkpoint / 'candidates' / sha / 'repo/.env').exists())
            target = self.base / 'legacy-save'
            with self.assertRaisesRegex(Error, sha + r'.*\.env'):
                recovery.save(store, target, temporary=True)
            self.assertFalse((target / 'registry/candidates' / sha / 'repo/.env').exists())
            self.assert_not_saved(target)
        finally:
            store.close()


if __name__ == '__main__':
    unittest.main()
