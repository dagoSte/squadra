"""Save survives a slow Git, the way back reads with the saving version, a looked-at package restores, one audit, the stash."""
import contextlib
import importlib.util
import io
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import test_squadra as support
from recovery import restore_check, save
import registry
from registry import Error, Store, git
from squadra import main

_spec = importlib.util.spec_from_file_location('frozen_squadra_360_recovery',
    Path(__file__).parent / 'fixtures/squadra-3.6.0/registry.py')
legacy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(legacy)


def outcome(call):
    try:
        return call()
    except Exception as exc:
        return type(exc).__name__ + ': ' + str(exc)


class RecoverySeams(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task

    def pack(self):
        path = self.base / 'saved'
        return path, save(self.store, path, temporary=True)

    def test_a_bundle_slower_than_thirty_seconds_still_saves(self):
        real = subprocess.run
        def slow(argv, *args, **kwargs):
            if list(argv[:3]) == ['git', 'bundle', 'create'] and (kwargs.get('timeout') or 10**9) < 3600:
                raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
            return real(argv, *args, **kwargs)
        with patch.object(registry.subprocess, 'run', side_effect=slow):
            result = outcome(lambda: self.pack()[1]['status'])
        self.assertEqual(result, 'saved')

    def test_a_git_timeout_is_a_plain_refusal(self):
        with patch.object(registry.subprocess, 'run', side_effect=subprocess.TimeoutExpired(['git', 'status'], 30)):
            result = outcome(lambda: git(self.repo, 'status'))
        self.assertTrue(result.startswith('Error: git status timed out'), result)

    def test_a_package_read_by_status_still_restores_and_a_written_one_is_refused(self):
        path, _ = self.pack()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            main(['--root', str(path / 'registry'), 'status'])
        for name, body in (('registry.sqlite3-shm', b'\0' * 32768), ('registry.sqlite3-wal', b'')):
            if not (path / 'registry' / name).exists():
                (path / 'registry' / name).write_bytes(body)
        self.assertEqual(outcome(lambda: restore_check(path, self.base / 'restored')['status']), 'passed')
        with (path / 'registry' / 'registry.sqlite3').open('ab') as stream:
            stream.write(b'x')
        self.assertIn('tampered', outcome(lambda: restore_check(path, self.base / 'restored-again')['status']))

    def test_a_save_runs_one_full_audit(self):
        calls, real = [], Store.audit
        def counting(store, *args, **kwargs):
            calls.append(store.root)
            return real(store, *args, **kwargs)
        with patch.object(Store, 'audit', counting):
            self.pack()
        self.assertEqual(len(calls), 1)

    def test_restore_keeps_the_stash_and_names_other_refs(self):
        (self.repo / 'stashed.txt').write_text('first\n'); self.git('add', 'stashed.txt'); self.git('commit', '-qm', 'stash base')
        (self.repo / 'stashed.txt').write_text('older work\n'); self.git('stash')
        (self.repo / 'stashed.txt').write_text('work in progress\n'); self.git('stash')
        stash = self.git('rev-parse', 'refs/stash').strip()
        self.git('update-ref', 'refs/notes/x', 'HEAD')
        path, _ = self.pack()
        result = restore_check(path, self.base / 'restored')
        root = Path(result['repositories'][0]['restored'])
        listed = subprocess.run(['git', 'stash', 'list', '--format=%H'], cwd=root, capture_output=True).stdout.split()
        self.assertEqual(listed, [stash])
        self.assertEqual(result['repositories'][0].get('refs_not_restored'), ['refs/notes/x'])
        self.assertEqual(result['repositories'][0].get('stash_entries_not_restored'), 1)

    def test_a_package_with_new_events_keeps_a_new_format_freeze(self):
        path, _ = self.pack()
        restore_check(path, self.base / 'restored')
        db = sqlite3.connect(self.base / 'restored/registry/registry.sqlite3')
        try:
            self.assertEqual(db.execute('SELECT kind, hash_format FROM events ORDER BY seq DESC LIMIT 1').fetchone(),
                             ('restore.freeze', 2))
        finally:
            db.close()


class LegacyWayBack(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='squadra-way-back-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.repo = self.base / 'repo'; self.repo.mkdir()
        for args in (['init', '-q'], ['config', 'user.name', 'Synthetic'], ['config', 'user.email', 'test@example.invalid']):
            subprocess.run(['git', *args], cwd=self.repo, check=True, capture_output=True)
        (self.repo / 'code.py').write_text('VALUE=1\n')
        subprocess.run(['git', 'add', '.'], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(['git', 'commit', '-qm', 'base'], cwd=self.repo, check=True, capture_output=True)
        self.root = self.base / 'record'
        old = legacy.Store(self.root, create=True)
        try:
            old.append('init', 'system', {'name': 'Way back', 'project': str(self.repo), 'version': '3.6.0',
                                         'limits': {'plan': 3, 'code': 3, 'build': 3}})
            for name, role in [('C', 'coordinator'), ('A', 'builder')]:
                old.append('agent', name, {'id': name, 'identity': name, 'role': role,
                                         'client': 'manual', 'repo': str(self.repo), 'skills': []})
        finally:
            old.close()

    def test_a_package_saved_before_the_update_restores_into_a_copy_the_old_version_reads(self):
        store = Store(self.root)
        try:
            save(store, self.base / 'saved', temporary=True)
        finally:
            store.close()
        restore_check(self.base / 'saved', self.base / 'restored')
        reader = legacy.Store(self.base / 'restored/registry', read_only=True)
        try:
            kinds = outcome(lambda: [event['kind'] for event in reader.events()])
        finally:
            reader.close()
        self.assertIsInstance(kinds, list, kinds)
        self.assertEqual(kinds[-1], 'restore.freeze')


if __name__ == '__main__':
    unittest.main()
