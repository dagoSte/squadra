"""Bundles cannot carry other clients' discovery files, and a refused file-level link is named."""
from pathlib import Path
import unittest

import test_plugin_store as support
import plugin_store as store

ROOT = Path(__file__).resolve().parents[1]


class Discovery(unittest.TestCase):
    setUp = support.Fixture.setUp
    run_cli = support.Fixture.run_cli
    install = support.Fixture.install
    catalogue = support.Fixture.catalogue

    def test_client_folders_and_instruction_files_are_refused_at_every_depth(self):
        cases = [('.claude', 'settings.json'), ('.CLAUDE', 'settings.json'), ('.cursor', 'mcp.json'),
                 ('.codex', 'config.toml'), ('CLAUDE.md', None), ('claude.MD', None)]
        for name, child in cases:
            for prefix in ('', 'programs/'):
                with self.subTest(name=name, prefix=prefix):
                    path = self.source / (prefix + name)
                    if child:
                        path.mkdir(); (path / child).write_text('{"hooks":{}}')
                    else:
                        path.write_text('# instructions another client would load\n')
                    try:
                        self.assertEqual(self.catalogue()['plugins'], [])
                    finally:
                        if child:
                            (path / child).unlink(); path.rmdir()
                        else:
                            path.unlink()
        self.assertEqual(len(self.catalogue()['plugins']), 1)

    def test_only_the_root_agents_md_is_accepted(self):
        for nested in ('programs/AGENTS.md', 'programs/agents.MD'):
            with self.subTest(nested=nested):
                path = self.source / nested
                path.write_text('# nested instructions\n')
                try:
                    self.assertEqual(self.catalogue()['plugins'], [])
                finally:
                    path.unlink()
        self.assertEqual(len(self.catalogue()['plugins']), 1)

    def test_documented_list_matches_the_refusal(self):
        text = ' '.join((ROOT / 'references/plugins.md').read_text().split())
        for name in ('`.claude`', '`.cursor`', '`.codex`', '`CLAUDE.md`', 'an `AGENTS.md` below the plugin root'):
            self.assertTrue(name in text, name)

    def test_refused_source_file_symlink_is_named(self):
        link = self.source / 'programs/link.txt'
        link.symlink_to(self.source / 'README.md')
        run = self.run_cli('install', 'example')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn(str(link), run.stderr)
        self.assertIn('symbolic link', run.stderr)

    def test_refused_source_directory_symlink_is_named(self):
        link = self.source / 'linkdir'
        link.symlink_to(self.source / 'programs', target_is_directory=True)
        run = self.run_cli('install', 'example')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('linkdir', run.stderr)
        self.assertIn('symbolic link', run.stderr)

    def test_refused_installed_file_symlink_is_named(self):
        self.install()
        managed = self.target / 'README.md'
        managed.unlink(); managed.symlink_to(self.source / 'README.md')
        run = self.run_cli('install', 'example')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('README.md', run.stderr)
        self.assertIn('symbolic link', run.stderr)

    def test_refusal_code_stays_the_code(self):
        (self.source / 'programs/link.txt').symlink_to(self.source / 'README.md')
        with self.assertRaises(store.p.Refused) as caught:
            self.install()
        self.assertEqual(str(caught.exception), 'symlink')
        self.assertNotIn(str(self.home), str(caught.exception))


if __name__ == '__main__':
    unittest.main()
