"""The printed lesson command is shell-quoted like every other command Squadra prints."""
import shlex
import unittest

import test_errata  # noqa: F401  Puts scripts/ on the import path.
import errata


class HintQuoting(unittest.TestCase):
    def test_registry_words_stay_single_arguments(self):
        for actor, task in (('A', 'T'), ('seat with space', 'T-1'), ('A', "T; touch pwned"), ("o'neil", '$(id)')):
            with self.subTest(actor=actor, task=task):
                words = shlex.split(errata.hint(task, 12, actor, 'proof failed')['command'])
                self.assertEqual(words[:8], ['squadra', 'lesson', '--actor', actor, '--task', task, '--source-event', '12'])
                self.assertEqual(words[8:], ['--text', '<what to do differently next time>', '--path', '<glob>',
                                             '--word', '<word>'])

    def test_plain_ids_print_as_before(self):
        self.assertEqual(errata.hint('T', 12, 'A', 'proof failed')['command'],
                         'squadra lesson --actor A --task T --source-event 12 '
                         '--text "<what to do differently next time>" --path "<glob>" --word "<word>"')


if __name__ == '__main__':
    unittest.main()
