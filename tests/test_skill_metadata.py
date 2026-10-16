"""The skill declares its network compatibility in bounded frontmatter."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SkillMetadata(unittest.TestCase):
    def test_compatibility_declares_network_within_the_metadata_limit(self):
        text = (ROOT / 'SKILL.md').read_text(encoding='utf-8')
        frontmatter = re.match(r'\A---\n(.*?)\n---(?:\n|\Z)', text, re.S)
        self.assertIsNotNone(frontmatter, 'SKILL.md needs YAML frontmatter')
        field = re.search(r'^compatibility: ([^\n]+)$', frontmatter[1], re.M)
        self.assertIsNotNone(field, 'Declare network access in compatibility')
        value = field[1].strip()
        self.assertLessEqual(len(value), 500)
        self.assertRegex(value.casefold(), r'\bnetwork\b')


if __name__ == '__main__':
    unittest.main()
