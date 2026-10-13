"""Protected OS aliases work; user-controlled links still refuse before copying."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_memoranda_bundle import ROOT, migration_module


class MigrationPaths(unittest.TestCase):
    def test_default_temporary_directory_opens_the_exact_private_leaf(self):
        module = migration_module()
        with tempfile.TemporaryDirectory() as tmp:
            fd = module.directory(tmp)
            try:
                actual, expected = os.fstat(fd), Path(tmp).stat()
                self.assertEqual((actual.st_dev, actual.st_ino), (expected.st_dev, expected.st_ino))
            finally:
                os.close(fd)

    def test_fresh_process_under_system_symlinked_tmpdir(self):
        # macOS /tmp is the root-owned link that previously made ordinary migration fail.
        if sys.platform == 'darwin':
            self.assertTrue(Path('/tmp').is_symlink())
            self.assertEqual(Path('/tmp').lstat().st_uid, 0)
        with tempfile.TemporaryDirectory() as home:
            env = {k: v for k, v in os.environ.items() if k in ('PATH', 'LANG', 'LC_ALL')}
            env.update(HOME=home, TMPDIR='/tmp', PYTHONDONTWRITEBYTECODE='1')
            code = '''import importlib.util, os, sys, tempfile
from pathlib import Path
spec = importlib.util.spec_from_file_location('migration', sys.argv[1])
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
assert tempfile.gettempdir() == '/tmp'
with tempfile.TemporaryDirectory() as tmp:
    fd = module.directory(tmp)
    try:
        assert os.fstat(fd).st_ino == Path(tmp).stat().st_ino
    finally:
        os.close(fd)
print('system TMPDIR accepted')
'''
            run = subprocess.run([sys.executable, '-I', '-B', '-c', code,
                                  str(ROOT / 'plugins/memoranda/bridge/migrate.py')],
                                 env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(run.stdout.strip(), 'system TMPDIR accepted')

    def test_user_link_refusal_names_component_and_copies_nothing(self):
        module = migration_module()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            leaf = base / 'leaf'; leaf.mkdir(mode=0o700)
            child = leaf / 'child'; child.mkdir(mode=0o700)
            alias = base / 'linked-source'; alias.symlink_to(leaf)
            for path in (alias, alias / 'child'):
                with self.assertRaises(module.LinkedComponent) as caught:
                    module.directory(path)
                self.assertEqual(caught.exception.component, 'linked-source')
            env = {k: v for k, v in os.environ.items() if k in ('PATH', 'LANG', 'LC_ALL')}
            env['HOME'] = str(base)
            run = subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'plugins/memoranda/bridge/migrate.py'),
                                  '--from-plugin', str(alias), '--from-data', str(child)],
                                 env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(run.returncode, 3)
            self.assertIn("linked component 'linked-source'", run.stderr)
            self.assertEqual(run.stdout, '')
            self.assertFalse((base / '.squadra').exists())

    def test_root_owned_link_in_user_tree_is_still_refused(self):
        module = migration_module()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            target = base / 'target'; target.mkdir(mode=0o700)
            child = target / 'child'; child.mkdir(mode=0o700)
            alias = base / 'alias'; alias.symlink_to(target)
            original = module.os.stat
            def root_link(path, *args, **kwargs):
                result = original(path, *args, **kwargs)
                if path == 'alias' and kwargs.get('follow_symlinks') is False:
                    fields = list(result); fields[4] = 0
                    return os.stat_result(fields)
                return result
            with patch.object(module.os, 'stat', side_effect=root_link):
                with self.assertRaises(module.LinkedComponent) as caught:
                    module.directory(alias / 'child')
            self.assertEqual(caught.exception.component, 'alias')

    def test_leaf_must_remain_private(self):
        module = migration_module()
        with tempfile.TemporaryDirectory() as tmp:
            leaf = Path(tmp) / 'readable'; leaf.mkdir(mode=0o755); leaf.chmod(0o755)
            with self.assertRaises(module.p.Refused):
                module.directory(leaf)

    def test_short_card_keeps_price_and_moves_operational_detail_to_readme(self):
        bundle = ROOT / 'plugins/memoranda'
        manifest = json.loads((bundle / 'plugin.json').read_text())
        expected = {
            'it': 'Plugin gratuito; servizio memoranda.dev con prova gratuita, poi a pagamento. Le memorie che gli agenti della squadra hanno dichiarato, proposte a memoranda.dev.',
            'en': "Free plugin; memoranda.dev service with a free trial, then paid. The memories the squad's agents declared, proposed to memoranda.dev.",
        }
        self.assertEqual(manifest['description'], expected)
        for lang, filename, timing in [('it', 'README.it.md', '2,4'), ('en', 'README.md', '2.4')]:
            self.assertNotIn(timing, manifest['description'][lang])
            self.assertIn(timing, (bundle / filename).read_text())


if __name__ == '__main__':
    unittest.main()
