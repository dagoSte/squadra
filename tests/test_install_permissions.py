"""Install and the plugin Store under a group-writable umask, an unsupported Python and a linked HOME."""
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import install as installer  # noqa: E402

FIXTURE = ROOT / 'tests/fixtures/plugin-store/example'
GIT = {'GIT_CONFIG_NOSYSTEM': '1', 'GIT_AUTHOR_NAME': 'Synthetic', 'GIT_AUTHOR_EMAIL': 's@example.invalid',
       'GIT_COMMITTER_NAME': 'Synthetic', 'GIT_COMMITTER_EMAIL': 's@example.invalid'}
# Runs an entry point as __main__ under an older sys.version_info, which is what that interpreter reaches first.
AS_VERSION = ("import os, runpy, sys; sys.version_info = tuple(int(n) for n in sys.argv[1].split('.')) + ('final', 0); "
              "path = sys.argv[2]; sys.argv = sys.argv[2:]; sys.path.insert(0, os.path.dirname(path)); "
              "runpy.run_path(path, run_name='__main__')")


def shared_write(top):
    """Folders and files below top, links excluded, that group or others may write."""
    found = []
    for folder, _, names in os.walk(top):
        for path in [folder, *(os.path.join(folder, name) for name in names)]:
            if not os.path.islink(path) and stat.S_IMODE(os.lstat(path).st_mode) & 0o022:
                found.append(os.path.relpath(path, top))
    return found


class Fixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='sq-install-perms-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.home = self.base / 'home'; self.home.mkdir(mode=0o755)
        self.env = {**os.environ, **GIT, 'HOME': str(self.home), 'PYTHONDONTWRITEBYTECODE': '1', 'PATH': '/usr/bin:/bin'}

    def release(self, umask=0o022):
        """A committed candidate with the real scripts and manifest, moded as a clone under umask leaves it."""
        repo = self.base / 'release'
        for folder in ('scripts', '.codex-plugin'):
            shutil.copytree(ROOT / folder, repo / folder, ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(FIXTURE, repo / 'plugins/example')
        shutil.copy(ROOT / 'SKILL.md', repo / 'SKILL.md')
        for folder, _, names in os.walk(repo):
            os.chmod(folder, 0o777 & ~umask)
            for name in names:
                os.chmod(os.path.join(folder, name), 0o666 & ~umask)
        for args in (('init', '-q'), ('add', '.'), ('-c', 'commit.gpgsign=false', 'commit', '-qm', 'candidate')):
            subprocess.run(['git', *args], cwd=repo, env=self.env, check=True, capture_output=True)
        return repo

    def cli(self, repo, *args, home=None):
        env = self.env if home is None else {**self.env, 'HOME': str(home)}
        return subprocess.run([sys.executable, '-B', str(repo / 'scripts/squadra.py'), 'plugin', *args],
                              env=env, cwd=self.base, capture_output=True, text=True, timeout=30)


class GroupWritableUmask(Fixture):
    def test_install_under_umask_002_yields_a_tree_the_store_accepts(self):
        repo = self.release(0o002)
        previous = os.umask(0o002)
        try:
            result = installer.install(repo, self.home, self.base / 'backup')
            self.assertEqual(os.umask(0o002), 0o002, 'install restores the caller umask')
        finally:
            os.umask(previous)
        run = subprocess.run([str(self.home / '.local/bin/squadra'), 'plugin', 'list'], env=self.env, cwd=self.base,
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual([row['name'] for row in json.loads(run.stdout)['plugins']], ['example'])
        self.assertEqual(shared_write(result['version']), [])
        self.assertEqual(shared_write(self.home), [])

    def test_reinstalling_a_version_written_under_umask_002_repairs_it(self):
        repo = self.release(0o002)
        result = installer.install(repo, self.home, self.base / 'backup-1')
        version = Path(result['version'])
        for folder in (version / 'scripts', version / 'plugins/example'):
            folder.chmod(0o775)
        (version / 'scripts/squadra.py').chmod(0o664)
        installer.install(repo, self.home, self.base / 'backup-2')
        self.assertEqual(shared_write(version), [])

    def test_unsafe_folders_are_named_with_the_chmod_that_repairs_them(self):
        repo = self.release()
        meta, target = repo / '.codex-plugin', self.home / '.squadra/plugins/example'
        self.assertEqual(self.cli(repo, 'install', 'example').returncode, 0)
        cases = [(meta, 0o775, ('list',), ['chmod', '-R', 'go-w', str(repo)]),
                 (self.home, 0o777, ('install', 'example'), ['chmod', 'go-w', str(self.home)]),
                 (target, 0o755, ('install', 'example'), ['chmod', '700', str(target)])]
        for folder, mode, args, repair in cases:
            with self.subTest(folder=folder.name):
                folder.chmod(mode)
                run = self.cli(repo, *args)
                self.assertEqual((run.returncode, run.stdout), (1, ''))
                self.assertEqual(run.stderr, 'Squadra: unsafe_permissions: ' + str(folder) +
                                 ' is open to other users; repair: ' + shlex.join(repair) + '\n')
                printed = shlex.split(run.stderr.strip().split('; repair: ', 1)[1])
                self.assertEqual(subprocess.run(printed, capture_output=True).returncode, 0)
                run = self.cli(repo, *args)
                self.assertEqual(run.returncode, 0, run.stderr)

    def test_a_foreign_folder_is_named_without_a_chmod(self):
        import plugin_store
        import plugins
        from unittest.mock import patch
        repo = self.release()
        with patch.object(plugins.os, 'getuid', return_value=os.getuid() + 1):
            with self.assertRaises(plugins.Refused) as caught:
                plugin_store.catalogue(self.home, repo)
        self.assertEqual(str(caught.exception), 'unsafe_permissions')
        self.assertEqual(plugin_store.explain(caught.exception, repo),
                         'unsafe_permissions: ' + str(repo) + ' belongs to another user')


class UnsupportedPython(Fixture):
    def entry(self, version, script, *args):
        return subprocess.run([sys.executable, '-B', '-c', AS_VERSION, version, str(ROOT / 'scripts' / script), *args],
                              env=self.env, cwd=self.base, capture_output=True, text=True, timeout=30)

    def test_an_older_python_gets_one_plain_refusal_and_nothing_is_written(self):
        repo = self.release()
        project = self.base / 'project'; project.mkdir()
        for version in ('3.9.6', '3.11.9'):
            calls = [('squadra.py', ('init', '--name', 'S', '--project', str(project), '--temporary', '--root', str(self.base / 'record'))),
                     ('install.py', ('--source', str(repo), '--home', str(self.home), '--backup', str(self.base / 'backup')))]
            for script, args in calls:
                with self.subTest(version=version, script=script):
                    run = self.entry(version, script, *args)
                    expected = 'Squadra: needs Python 3.12 or newer; this is Python ' + version.rsplit('.', 1)[0] + '.\n'
                    self.assertEqual((run.returncode, run.stdout, run.stderr), (1, '', expected))
        self.assertEqual(os.listdir(self.home), [])
        self.assertFalse((self.base / 'backup').exists())
        self.assertFalse((self.base / 'record').exists())

    def test_the_documented_minimum_runs_and_matches_the_documents(self):
        import squadra
        run = self.entry('3.12.0', 'squadra.py', '--version')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(getattr(squadra, 'PYTHON', None), (3, 12))
        for name in ('README.md', 'README.it.md', 'SKILL.md'):
            self.assertIn('Python 3.12+', (ROOT / name).read_text(encoding='utf-8'), name)


class LinkedHome(Fixture):
    def test_a_symlinked_home_installs_a_plugin_and_links_below_it_stay_refused(self):
        repo = self.release()
        alias = self.base / 'alias'; alias.symlink_to(self.home, target_is_directory=True)
        run = self.cli(repo, 'install', 'example', home=alias)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue((self.home / '.squadra/plugins/example/.store-receipt.json').is_file())
        run = self.cli(repo, 'list', home=alias)
        self.assertEqual(run.returncode, 0, run.stderr)
        row = json.loads(run.stdout)['plugins'][0]
        self.assertEqual((row['installed'], row['managed']), (True, True))
        other, elsewhere = self.base / 'other-home', self.base / 'elsewhere'
        other.mkdir(mode=0o755); elsewhere.mkdir()
        (other / '.squadra').symlink_to(elsewhere, target_is_directory=True)
        run = self.cli(repo, 'install', 'example', home=other)
        self.assertEqual((run.returncode, run.stderr), (1, 'Squadra: symlink: ' + str(other / '.squadra') +
                                                         ' is a symbolic link, which the Store does not follow\n'))
        self.assertEqual(list(elsewhere.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
