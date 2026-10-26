"""A Claude read of a delivered copy loads no project-level Claude settings from that copy."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from runtime import cli_command  # noqa: E402


class ClaudeReadSettings(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='squadra-rf4b-runtime-'); self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)

    def test_a_claude_read_loads_user_settings_only(self):
        argv = cli_command('claude', 'read', self.folder)
        self.assertIn('--setting-sources', argv)
        self.assertEqual(argv[argv.index('--setting-sources') + 1], 'user')
        self.assertEqual(argv.count('--setting-sources'), 1)
        for kept in ('--safe-mode', '--strict-mcp-config', '--no-chrome'):
            self.assertIn(kept, argv)
        model = cli_command('claude', 'read', self.folder, 'requested-model')
        self.assertEqual(model[model.index('--setting-sources') + 1], 'user')

    def test_a_claude_build_in_its_own_tree_keeps_its_project_settings(self):
        self.assertNotIn('--setting-sources', cli_command('claude', 'build', self.folder))


if __name__ == '__main__':
    unittest.main()
