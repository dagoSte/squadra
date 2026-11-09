"""The README ends with its footer and explains window replacement inside the workflow chapter."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CASES = (('README.md', 'Developed by', '### Replacing a window', '## Squadra commands'),
         ('README.it.md', 'Sviluppato da', '### Sostituire una finestra', '## Comandi di squadra'))


class ReadmeLayout(unittest.TestCase):
    def test_nothing_follows_the_footer(self):
        for name, footer, _, _ in CASES:
            with self.subTest(readme=name):
                lines = (ROOT / name).read_text().rstrip('\n').splitlines()
                self.assertTrue(lines[-1].startswith(footer), name + ' must end with its footer line')

    def test_window_replacement_lives_in_the_workflow_chapter(self):
        for name, _, section, after in CASES:
            with self.subTest(readme=name):
                text = (ROOT / name).read_text()
                self.assertEqual(text.count(section), 1)
                self.assertLess(text.index(section), text.index(after))
                body = text[text.index(section):text.index(after)]
                for command in ('handover prepare', 'replace', 'access probe', 'context --task', 'inbox --agent'):
                    self.assertIn(command, body)


if __name__ == '__main__':
    unittest.main()
