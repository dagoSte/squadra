"""Caller Git environment overlays preserve private indexes and process suppression."""
import os
import shutil
import unittest

import test_rf4a_git as support


class GitEnvironment(unittest.TestCase):
    setUp = support.GitPrograms.setUp
    raw = support.GitPrograms.raw

    def test_only_the_supplied_private_index_changes(self):
        private = self.base / 'private.index'
        shutil.copyfile(self.repo / '.git/index', private)
        real_before = (self.repo / '.git/index').read_bytes()
        private_before = private.read_bytes()
        original_environment = dict(os.environ)
        (self.repo / 'payload.txt').write_text('Private staged bytes\n')
        extra = {'GIT_INDEX_FILE': str(private)}
        result = support.registry.git_process(self.repo, 'add', '--', 'payload.txt', env=extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.repo / '.git/index').read_bytes(), real_before)
        self.assertNotEqual(private.read_bytes(), private_before)
        self.assertEqual(support.registry.git(self.repo, 'show', ':payload.txt'), b'initial\n')
        staged = support.registry.git_process(self.repo, 'show', ':payload.txt', env=extra)
        self.assertEqual(staged.stdout, b'Private staged bytes\n')
        self.assertEqual(dict(os.environ), original_environment)
        self.assertEqual(extra, {'GIT_INDEX_FILE': str(private)})

    def test_overlay_config_is_discovered_and_suppressed_without_losing_the_private_index(self):
        marker, program = support.marker_program(self.base, 'overlay-filter', 'cat')
        monitor, monitor_program = support.marker_program(self.base, 'overlay-monitor', "printf 'probe-token\\000'")
        private = self.base / 'filtered-private.index'
        shutil.copyfile(self.repo / '.git/index', private)
        real_before = (self.repo / '.git/index').read_bytes()
        (self.repo / '.gitattributes').write_text('*.txt filter=overlay\n')
        (self.repo / 'payload.txt').write_text('Raw private bytes\n')
        extra = {'GIT_INDEX_FILE': str(private), 'GIT_CONFIG_COUNT': '3',
                 'GIT_CONFIG_KEY_0': 'filter.overlay.clean', 'GIT_CONFIG_VALUE_0': str(program),
                 'GIT_CONFIG_KEY_1': 'filter.overlay.required', 'GIT_CONFIG_VALUE_1': 'true',
                 'GIT_CONFIG_KEY_2': 'core.fsmonitor', 'GIT_CONFIG_VALUE_2': str(monitor_program)}
        original_environment, original_extra = dict(os.environ), dict(extra)
        result = support.registry.git_process(self.repo, 'add', '--', 'payload.txt', env=extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        staged = support.registry.git_process(self.repo, 'show', ':payload.txt', env=extra)
        self.assertEqual(staged.stdout, b'Raw private bytes\n')
        self.assertEqual((self.repo / '.git/index').read_bytes(), real_before)
        self.assertFalse(marker.exists(), 'overlay-configured clean filter executed')
        self.assertFalse(monitor.exists(), 'overlay-configured fsmonitor executed')
        self.assertEqual(dict(os.environ), original_environment)
        self.assertEqual(extra, original_extra)

    def test_local_only_overrides_the_overlay_protocol_allowlist(self):
        marker, program = support.marker_program(self.base, 'overlay-ssh', 'exit 1')
        target = self.base / 'target'; target.mkdir()
        self.raw('init', '--quiet', repo=target)
        self.raw('config', 'core.sshCommand', str(program), repo=target)
        self.raw('config', 'url.ssh://synthetic.invalid/repo.insteadOf', self.repo.as_uri(), repo=target)
        head = self.raw('rev-parse', 'HEAD').decode().strip()
        extra = {'GIT_ALLOW_PROTOCOL': 'ssh'}
        original_environment = dict(os.environ)
        result = support.registry.git_process(target, 'fetch', '--quiet', '--', self.repo.as_uri(), head,
                                              env=extra, local_only=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"transport 'ssh' not allowed", result.stderr)
        self.assertFalse(marker.exists())
        self.assertEqual(dict(os.environ), original_environment)
        self.assertEqual(extra, {'GIT_ALLOW_PROTOCOL': 'ssh'})


if __name__ == '__main__':
    unittest.main()
