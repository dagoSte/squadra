"""Recovery completion is published after checksums; frozen fixture cleanup is repeatable."""
import errno
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_ops_seams as ops
import recovery
from registry import Error


class PackagePublication(ops.Squad):
    def test_include_refuses_a_symlinked_parent_and_git_alias(self):
        outside = self.base / 'outside'; outside.mkdir(); (outside / 'value').write_text('outside')
        link = self.repo / 'linked'; link.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(Error, 'parent'):
            recovery.record_file(link / 'value', self.repo)
        self.assertEqual(recovery.included_files(self.repo, [str(link)]), {link})
        alias = self.repo / '.GiT' / 'synthetic'
        with self.assertRaisesRegex(Error, 'Git internals'):
            recovery.record_file(alias, self.repo)

    def test_saved_status_is_not_published_before_checksums(self):
        target = self.base / 'saved'; atomic = recovery.atomic; observed = []
        def fail(path, body):
            if Path(path).name == 'CHECKSUMS.json':
                observed.append(json.loads((target / 'MANIFEST.json').read_text())['status'])
                raise OSError(errno.ENOSPC, 'Synthetic checksum failure')
            return atomic(path, body)
        with patch('recovery.atomic', side_effect=fail):
            with self.assertRaises(OSError):
                recovery.save(self.store, target, temporary=True)
        self.assertEqual(observed, ['building'])
        self.assertNotEqual(json.loads((target / 'MANIFEST.json').read_text())['status'], 'saved')
        self.assertFalse((target / 'CHECKSUMS.json').exists())

    def test_missing_checksums_is_a_plain_package_refusal(self):
        target = self.base / 'saved'; recovery.save(self.store, target, temporary=True)
        (target / 'CHECKSUMS.json').unlink()
        with self.assertRaisesRegex(Error, 'CHECKSUMS.json'):
            recovery.verify_package(target)


class FixtureCleanup(unittest.TestCase):
    def test_an_interrupted_fixture_with_readonly_candidates_can_be_rebuilt(self):
        from memoranda_cases import fx
        with tempfile.TemporaryDirectory(prefix='rf4a-fixture-') as temp:
            base = Path(temp); calls = []
            def build(home):
                calls.append(home)
                if len(calls) == 1:
                    folder = home / 'reg/candidates/frozen/repo'; folder.mkdir(parents=True)
                    leaf = folder / 'value'; leaf.write_text('synthetic'); leaf.chmod(0o444); folder.chmod(0o555)
                    raise RuntimeError('Synthetic interrupted fixture')
            try:
                with patch.object(fx.tempfile, 'gettempdir', return_value=str(base)), patch.object(fx, '_build', build):
                    with self.assertRaisesRegex(RuntimeError, 'interrupted fixture'):
                        fx.base()
                    home = fx.base()
                    self.assertTrue((home / 'READY').is_file())
                    self.assertEqual(len(calls), 2)
            finally:
                for p in base.rglob('*'):
                    if not p.is_symlink(): p.chmod(p.stat().st_mode | 0o700)
