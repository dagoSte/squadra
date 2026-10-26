"""Frozen sources enforce secret and symlink policy, including retained legacy caches."""
import json
import subprocess
from pathlib import Path
import unittest
from unittest.mock import patch

import test_workflow_improvements as support
import frozen_candidate as frozen
from registry import Error, Store, plain, snapshot
from recovery import save


class FrozenPolicy(unittest.TestCase):
    cli = support.Improvements.cli
    refuse = support.Improvements.refuse
    state = support.Improvements.state
    task = support.Improvements.task
    start = support.Improvements.start
    deliver = support.Improvements.deliver

    def setUp(self):
        support.Improvements.setUp(self)
        def writable():
            for p in [self.base, *self.base.rglob('*')]:
                if not p.is_symlink(): p.chmod(p.stat().st_mode | 0o700)
        self.addCleanup(writable)

    def test_freeze_refuses_worktree_secret_names_or_content(self):
        self.task()
        store = Store(self.root)
        try:
            task = plain(store.task('T'))
            cases = [('.env', b'SYNTHETIC=never-export\n'), ('client.pfx', b'synthetic'),
                     ('service.json', json.dumps({'key': '-' * 5 + 'BEGIN PRIVATE KEY' + '-' * 5 + '\nfake'}).encode())]
            for name, body in cases:
                with self.subTest(name=name):
                    path = self.repo / name; path.write_bytes(body)
                    try:
                        with self.assertRaisesRegex(Error, name.replace('.', r'\.')):
                            frozen.freeze(store, task, snapshot(self.repo, self.root))
                    finally: path.unlink()
        finally: store.close()

    def test_freeze_refuses_absolute_or_escaping_symlinks(self):
        self.task(); store = Store(self.root)
        try:
            task = plain(store.task('T')); outside = self.base / 'outside'; outside.write_text('Synthetic outside')
            for target in (str(outside), '../outside'):
                with self.subTest(target=target):
                    path = self.repo / 'link'; path.symlink_to(target)
                    try:
                        with self.assertRaisesRegex(Error, 'link'):
                            frozen.freeze(store, task, snapshot(self.repo, self.root))
                    finally: path.unlink()
        finally: store.close()

    def test_template_exemption_needs_delivered_head_membership(self):
        self.task(); store = Store(self.root); path = self.repo / '.env.example'
        try:
            task = plain(store.task('T')); path.write_text('PLACEHOLDER=example\n')
            for staged in (False, True):
                if staged: subprocess.run(['git', '-C', str(self.repo), 'add', '.env.example'], check=True)
                with self.assertRaisesRegex(Error, 'commit.*ignore'):
                    frozen.freeze(store, task, snapshot(self.repo, self.root))
            subprocess.run(['git', '-C', str(self.repo), '-c', 'user.name=Synthetic', '-c',
                            'user.email=s@example.invalid', '-c', 'commit.gpgsign=false', 'commit', '-qm', 'Template'], check=True)
            result = frozen.freeze(store, task, snapshot(self.repo, self.root))
            frozen.validate_candidate(Path(result['repo']))
        finally: store.close()

    def test_committed_key_is_allowed_at_freeze_but_refused_at_checkpoint_boundary(self):
        self.task(); store = Store(self.root); path = self.repo / 'service.json'
        path.write_text(json.dumps({'key': '-' * 5 + 'BEGIN PRIVATE KEY' + '-' * 5 + '\nfake'}))
        subprocess.run(['git', '-C', str(self.repo), 'add', 'service.json'], check=True)
        subprocess.run(['git', '-C', str(self.repo), '-c', 'user.name=Synthetic', '-c',
                        'user.email=s@example.invalid', '-c', 'commit.gpgsign=false', 'commit', '-qm', 'Synthetic committed key'], check=True)
        try:
            task = plain(store.task('T')); candidate = snapshot(self.repo, self.root)
            result = frozen.freeze(store, task, candidate)
            with self.assertRaisesRegex(Error, 'service.json'):
                frozen.validate_candidate(Path(result['repo']), label='Frozen candidate ' + candidate['sha256'])
        finally: store.close()

    def legacy_secret_delivery(self):
        self.task(); self.start()
        secret = self.repo / '.env'; secret.write_text('SYNTHETIC-LEGACY-CANDIDATE\n')
        # Simulate a retained pre-filter delivery while preserving its true fingerprint.
        with patch('frozen_candidate.validate_candidate', return_value=None, create=True):
            self.deliver()
        secret.unlink()
        return self.state()['tasks']['T']['delivery']['frozen']['sha256']

    def test_checkpoint_refuses_a_flagged_legacy_candidate_before_copying_it(self):
        sha = self.legacy_secret_delivery(); target = self.base / 'checkpoint'; store = Store(self.root)
        try:
            with self.assertRaisesRegex(Error, sha + r'.*\.env'):
                store.checkpoint(target)
            self.assertFalse((target / 'candidates' / sha / 'repo/.env').exists())
        finally: store.close()

    def test_save_never_ships_a_candidate_secret_behind_an_exclusion_receipt(self):
        sha = self.legacy_secret_delivery(); target = self.base / 'saved'; store = Store(self.root)
        try:
            with self.assertRaisesRegex(Error, sha + r'.*\.env'):
                save(store, target, temporary=True)
            self.assertFalse((target / 'registry/candidates' / sha / 'repo/.env').exists())
            manifest = target / 'MANIFEST.json'
            self.assertFalse(manifest.exists() and json.loads(manifest.read_text())['status'] == 'saved')
        finally: store.close()
