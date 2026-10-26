"""Every command count the READMEs state equals the parser's subcommands, in both languages."""
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from squadra import parser  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
COUNT = re.compile(r'(\d+) (?:commands|subcommands|comandi|sottocomandi)\b')


class CommandCounts(unittest.TestCase):
    def test_every_stated_command_count_matches_the_parser(self):
        commands = len(next(a for a in parser()._actions if a.dest == 'command').choices)
        for name in ('README.md', 'README.it.md'):
            with self.subTest(readme=name):
                found = COUNT.findall((ROOT / name).read_text())
                self.assertGreaterEqual(len(found), 3, found)
                self.assertEqual(set(found), {str(commands)}, found)


if __name__ == '__main__':
    unittest.main()
