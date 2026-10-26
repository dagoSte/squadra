"""Internal Git commands cannot execute programs supplied by repository configuration."""
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import registry
import test_board as board_support
from frozen_candidate import current, remove
from recovery import inventory, restore_check, save


def isolated_environment(home):
    values = dict(os.environ, HOME=str(home), GIT_CONFIG_NOSYSTEM='1',
                  GIT_CONFIG_GLOBAL='/dev/null', GIT_TERMINAL_PROMPT='0',
                  PYTHONDONTWRITEBYTECODE='1')
    for name in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_CONFIG_COUNT',
                 'GIT_EXTERNAL_DIFF', 'GIT_SSH', 'GIT_SSH_COMMAND'):
        values.pop(name, None)
    return values


def marker_program(folder, name, tail):
    marker, program = folder / (name + '.marker'), folder / (name + '.sh')
    program.write_text('#!/bin/sh\nprintf x >> ' + shlex.quote(str(marker)) + '\n' + tail + '\n')
    program.chmod(0o700)
    return marker, program


class GitPrograms(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='squadra-rf4a-git-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / 'home'; self.home.mkdir()
        self.env = isolated_environment(self.home)
        context = patch.dict(os.environ, self.env, clear=True)
        context.start(); self.addCleanup(context.stop)
        self.repo = self.base / 'repo'; self.repo.mkdir()
        self.raw('init', '--quiet')
        self.raw('config', 'user.name', 'Synthetic')
        self.raw('config', 'user.email', 'synthetic@example.invalid')
        (self.repo / 'payload.txt').write_text('initial\n')
        (self.repo / '.gitattributes').write_text('*.txt diff=probe\n')
        self.raw('add', 'payload.txt', '.gitattributes')
        self.raw('-c', 'commit.gpgsign=false', 'commit', '-qm', 'Synthetic fixture')

    def raw(self, *args, repo=None):
        return subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', *args],
                              cwd=repo or self.repo, env=self.env, capture_output=True,
                              timeout=15, check=True).stdout

    def test_snapshot_and_probe_do_not_run_repository_fsmonitor(self):
        marker, program = marker_program(self.base, 'fsmonitor', "printf 'probe-token\\000'")
        self.raw('config', 'core.fsmonitor', str(program))
        expected = self.raw('rev-parse', 'HEAD').decode().strip()
        self.assertEqual(registry.snapshot(self.repo)['head'], expected)
        self.assertFalse(marker.exists(), 'snapshot executed repository-local core.fsmonitor')
        checked = registry.git_probe('-C', str(self.repo), '--literal-pathspecs',
                                     'ls-files', '--error-unmatch', '--', 'payload.txt')
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertFalse(marker.exists(), 'git_probe executed repository-local core.fsmonitor')

    def test_diff_keeps_raw_staged_and_worktree_bytes_without_textconv(self):
        marker, program = marker_program(self.base, 'textconv', 'cat "$1"')
        self.raw('config', 'diff.probe.textconv', str(program))
        (self.repo / 'payload.txt').write_text('staged\n')
        self.raw('add', 'payload.txt')
        (self.repo / 'payload.txt').write_text('working\n')
        staged = registry.git(self.repo, 'diff', '--cached', '--binary', '--no-ext-diff')
        working = registry.git(self.repo, 'diff', '--binary', '--no-ext-diff')
        self.assertIn(b'+staged\n', staged)
        self.assertIn(b'+working\n', working)
        self.assertFalse(marker.exists(), 'binary patches executed a repository textconv driver')

    def test_diff_does_not_run_repository_external_diff(self):
        marker, program = marker_program(self.base, 'external-diff', 'exit 0')
        self.raw('config', 'diff.external', str(program))
        (self.repo / 'payload.txt').write_text('working\n')
        result = registry.git(self.repo, 'diff', '--binary')
        self.assertFalse(marker.exists(), 'diff executed repository-local diff.external')
        self.assertIn(b'+working\n', result)

    def test_git_process_accepts_global_options_and_byte_input(self):
        marker, program = marker_program(self.base, 'global-textconv', 'cat "$1"')
        self.raw('config', 'diff.probe.textconv', str(program))
        (self.repo / 'payload.txt').write_text('working\n')
        result = registry.git_process(None, '-C', str(self.repo), '-c', 'diff.algorithm=myers',
                                      '--literal-pathspecs', 'diff', '--binary')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'+working\n', result.stdout)
        self.assertFalse(marker.exists())
        body = b'Input bytes, including a NUL: \0\n'
        hashed = registry.git_process(self.repo, 'hash-object', '--stdin', input=body)
        self.assertEqual(hashed.returncode, 0, hashed.stderr)
        body_file = self.base / 'input'; body_file.write_bytes(body)
        self.assertEqual(hashed.stdout, self.raw('hash-object', str(body_file)))

    def test_checkout_does_not_run_repository_hooks(self):
        marker, program = marker_program(self.base, 'post-checkout', 'exit 0')
        hooks = self.base / 'hooks'; hooks.mkdir()
        (hooks / 'post-checkout').write_bytes(program.read_bytes())
        (hooks / 'post-checkout').chmod(0o700)
        self.raw('config', 'core.hooksPath', str(hooks))
        registry.git(self.repo, 'checkout', '--quiet', '--detach', 'HEAD')
        self.assertFalse(marker.exists(), 'checkout executed the configured post-checkout hook')

    def test_local_fetch_refuses_url_rewrite_even_when_ssh_is_allowed(self):
        marker, program = marker_program(self.base, 'ssh-rewrite', 'exit 1')
        target = self.base / 'target'; target.mkdir()
        self.raw('init', '--quiet', repo=target)
        self.raw('config', 'core.sshCommand', str(program), repo=target)
        self.raw('config', 'protocol.ssh.allow', 'always', repo=target)
        self.raw('config', 'url.ssh://synthetic.invalid/repo.insteadOf', self.repo.as_uri(), repo=target)
        head = self.raw('rev-parse', 'HEAD').decode().strip()
        with patch.dict(os.environ, {'GIT_ALLOW_PROTOCOL': 'file:ssh'}):
            result = registry.git_process(target, 'fetch', '--quiet', '--depth=1',
                                          '--', self.repo.as_uri(), head, local_only=True)
            self.assertEqual(os.environ['GIT_ALLOW_PROTOCOL'], 'file:ssh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"transport 'ssh' not allowed", result.stderr)
        self.assertFalse(marker.exists(), 'a local-only fetch invoked the configured SSH program')

    def test_local_file_fetch_and_bundle_clone_keep_working(self):
        target = self.base / 'local-target'; target.mkdir()
        self.raw('init', '--quiet', repo=target)
        head = self.raw('rev-parse', 'HEAD').decode().strip()
        result = registry.git_process(target, 'fetch', '--quiet', '--depth=1',
                                      '--', self.repo.as_uri(), head, local_only=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(registry.git(target, 'rev-parse', 'FETCH_HEAD').decode().strip(), head)
        bundle, copied = self.base / 'history.bundle', self.base / 'cloned'
        self.raw('bundle', 'create', str(bundle), 'HEAD')
        cloned = registry.git_process(self.base, 'clone', '--quiet', '--no-hardlinks',
                                      str(bundle), str(copied), local_only=True)
        self.assertEqual(cloned.returncode, 0, cloned.stderr)
        self.assertEqual(registry.git(copied, 'rev-parse', 'HEAD').decode().strip(), head)

    def test_local_fetch_ignores_a_remote_uploadpack_program_named_for_its_url(self):
        marker, program = marker_program(self.base, 'remote-uploadpack', 'exit 1')
        target = self.base / 'uploadpack-target'; target.mkdir()
        self.raw('init', '--quiet', repo=target)
        uri = self.repo.as_uri()
        self.raw('config', 'remote.' + uri + '.url', uri, repo=target)
        self.raw('config', 'remote.' + uri + '.uploadpack', str(program), repo=target)
        head = self.raw('rev-parse', 'HEAD').decode().strip()
        result = registry.git_process(target, 'fetch', '--quiet', '--depth=1',
                                      '--', uri, head, local_only=True)
        self.assertFalse(marker.exists(), 'a file fetch invoked repository-local remote.uploadpack')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_light_and_forecast_git_calls_have_program_suppression(self):
        import forecast
        import light_route
        head = self.raw('rev-parse', 'HEAD').decode().strip()
        forecast.base_manifest.cache_clear()
        original = subprocess.run

        def guarded(argv, *args, **kwargs):
            if argv[0] == 'git':
                self.assertIn('core.fsmonitor=false', argv)
                self.assertIn('core.hooksPath=/dev/null', argv)
            return original(argv, *args, **kwargs)

        operations = {
            'light base': lambda: light_route.base_files(self.repo, head),
            'light lines': lambda: light_route.line_counts(b'old\n', b'new\n', self.base),
            'forecast base': lambda: forecast.base_manifest(str(self.repo), head),
        }
        for name, operation in operations.items():
            with self.subTest(operation=name), patch.object(subprocess, 'run', side_effect=guarded):
                result = operation()
                self.assertTrue(result)

    def test_required_clean_smudge_and_process_filters_never_run(self):
        for kind in ('clean', 'smudge', 'process'):
            with self.subTest(filter=kind):
                driver = 'probe.' + kind + '=literal'
                marker, program = marker_program(self.base, kind, 'exit 1' if kind == 'process' else 'cat')
                (self.repo / '.gitattributes').write_text('*.txt filter=' + driver + '\n')
                self.raw('add', '.gitattributes')
                self.raw('-c', 'commit.gpgsign=false', 'commit', '-qm', 'Synthetic filter attributes')
                self.raw('config', 'filter.' + driver + '.' + kind, str(program))
                self.raw('config', 'filter.' + driver + '.required', 'true')
                if kind == 'smudge':
                    (self.repo / 'payload.txt').unlink()
                    result = registry.git_process(self.repo, 'checkout', '--', 'payload.txt')
                else:
                    (self.repo / 'payload.txt').write_text('Raw working bytes for ' + kind + '\n')
                    result = registry.git_process(self.repo, 'diff', '--binary')
                self.assertFalse(marker.exists(), 'configured ' + kind + ' filter executed')
                self.assertEqual(result.returncode, 0, result.stderr)
                if kind == 'smudge':
                    self.assertEqual((self.repo / 'payload.txt').read_text(), 'initial\n')
                else:
                    self.assertIn(('Raw working bytes for ' + kind).encode(), result.stdout)
                self.raw('config', '--remove-section', 'filter.' + driver)
                (self.repo / 'payload.txt').write_text('initial\n')

    def test_global_system_and_worktree_filters_are_disabled(self):
        marker, program = marker_program(self.base, 'scoped-clean', 'cat')
        (self.repo / '.gitattributes').write_text('*.txt filter=scoped\n')
        (self.repo / 'payload.txt').write_text('Raw scoped bytes\n')
        for scope in ('global', 'system', 'worktree'):
            with self.subTest(scope=scope):
                marker.unlink(missing_ok=True)
                if scope == 'worktree':
                    self.raw('config', 'extensions.worktreeConfig', 'true')
                    self.raw('config', '--worktree', 'filter.scoped.clean', str(program))
                    self.raw('config', '--worktree', 'filter.scoped.required', 'true')
                    settings = {}
                else:
                    config = self.base / (scope + '.gitconfig')
                    self.raw('config', '--file', str(config), 'filter.scoped.clean', str(program))
                    self.raw('config', '--file', str(config), 'filter.scoped.required', 'true')
                    settings = ({'GIT_CONFIG_GLOBAL': str(config)} if scope == 'global' else
                                {'GIT_CONFIG_NOSYSTEM': '0', 'GIT_CONFIG_SYSTEM': str(config)})
                with patch.dict(os.environ, settings):
                    result = registry.git_process(self.repo, 'diff', '--binary')
                self.assertFalse(marker.exists(), scope + ' filter program executed')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(b'Raw scoped bytes', result.stdout)

    def test_filter_config_inspection_failure_refuses_before_running_git(self):
        with (self.repo / '.git/config').open('a') as stream:
            stream.write('\n[invalid\n')
        with self.assertRaisesRegex(registry.Error, 'Cannot inspect Git filter configuration'):
            registry.git(self.repo, 'status', '--porcelain')


class RestoredCandidatePrograms(unittest.TestCase):
    cli = board_support.Board.cli
    state = board_support.Board.state
    task = board_support.Board.task
    start = board_support.Board.start
    deliver = board_support.Board.deliver

    def setUp(self):
        self.outer = tempfile.TemporaryDirectory(prefix='squadra-rf4a-restored-')
        self.addCleanup(self.outer.cleanup)
        home = Path(self.outer.name) / 'home'; home.mkdir()
        context = patch.dict(os.environ, isolated_environment(home), clear=True)
        context.start(); self.addCleanup(context.stop)
        board_support.Board.setUp(self)
        self.addCleanup(remove, self.base)

    def test_restored_candidate_cannot_execute_self_attested_fsmonitor(self):
        self.task(); self.start(); self.deliver()
        package, restored = self.base / 'package', self.base / 'restored'
        store = registry.Store(self.root)
        try:
            expected = store.task('T')['candidate']
            save(store, package, temporary=True)
        finally:
            store.close()
        marker, program = marker_program(self.base, 'restored-fsmonitor', "printf 'probe-token\\000'")
        config = package / 'registry/candidates' / expected['sha256'] / 'repo/.git/config'
        config.chmod(0o644)
        with config.open('a') as stream:
            stream.write('\n[core]\n\tfsmonitor = ' + str(program) + '\n')
        (package / 'CHECKSUMS.json').write_text(registry.canonical(inventory(package)) + '\n')
        self.assertEqual(restore_check(package, restored)['status'], 'passed')
        reader = registry.Store(restored / 'registry', read_only=True)
        try:
            self.assertEqual(current(reader.task('T'), restored / 'registry'), expected)
        finally:
            reader.close()
        self.assertFalse(marker.exists(), 'reading a restored candidate executed packaged core.fsmonitor')

    def test_task_advance_save_and_forecast_do_not_execute_repository_programs(self):
        import forecast
        fsmonitor, monitor_program = marker_program(self.base, 'workflow-fsmonitor', "printf 'probe-token\\000'")
        textconv, text_program = marker_program(self.base, 'workflow-textconv', 'cat "$1"')
        registry.git(self.repo, 'config', 'core.fsmonitor', str(monitor_program))
        registry.git(self.repo, 'config', 'diff.probe.textconv', str(text_program))
        (self.repo / '.gitattributes').write_text('*.txt diff=probe\n')
        registry.git(self.repo, 'add', '.gitattributes')
        registry.git(self.repo, '-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid',
                     '-c', 'commit.gpgsign=false', 'commit', '-qm', 'Synthetic attributes')
        self.task(); self.start()
        (self.repo / 'value.txt').write_text('Changed task source\n')
        calculated = forecast.calculate(self.state(), (), 'T')
        self.assertEqual(calculated['scope']['paths'], ['value.txt'])
        self.deliver()
        self.cli('advance', '--actor', 'A', '--task', 'T')
        store = registry.Store(self.root)
        try:
            save(store, self.base / 'workflow-package', temporary=True)
        finally:
            store.close()
        self.assertFalse(fsmonitor.exists(), 'a workflow command executed repository-local core.fsmonitor')
        self.assertFalse(textconv.exists(), 'a workflow command executed repository-local textconv')

    def test_save_roundtrip_ignores_repository_diff_prefix_configuration(self):
        for name, value in (('diff.noprefix', 'true'), ('diff.mnemonicPrefix', 'true'),
                            ('diff.srcPrefix', 'wrong-source/'), ('diff.dstPrefix', 'wrong-target/')):
            registry.git(self.repo, 'config', name, value)
        (self.repo / 'value.txt').write_text('Staged source\n')
        registry.git(self.repo, 'add', 'value.txt')
        (self.repo / 'value.txt').write_text('Working source\n')
        package, restored = self.base / 'prefix-package', self.base / 'prefix-restored'
        store = registry.Store(self.root)
        try:
            save(store, package, temporary=True)
        finally:
            store.close()
        self.assertEqual(restore_check(package, restored)['status'], 'passed')
        self.assertEqual((restored / 'repo-0/value.txt').read_text(), 'Working source\n')
        self.assertEqual(registry.git(restored / 'repo-0', 'show', ':value.txt'), b'Staged source\n')


if __name__ == '__main__':
    unittest.main()
