"""The positive handover identity check uses a positive, localized message."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from checklist import handover_checks


class ActorText(unittest.TestCase):
    def test_registered_actor_is_positive_in_the_workspace_language(self):
        for language, expected in [('it', 'Sede registrata: A'), ('en', 'Seat registered: A')]:
            with self.subTest(language=language):
                state = {'workspace': {'lang': language}, 'agents': {'A': {'identity': 'seat-A'}},
                         'tasks': {}, 'runs': {}, 'resources': {}, 'messages': {}, 'handovers': {}}
                before = deepcopy(state)
                result = handover_checks(state, 'A', '2026-11-09T12:00:00+00:00')
                actor = next(item for item in result['items'] if item['id'] == 'actor')
                self.assertEqual(actor, {'id': 'actor', 'status': 'ok', 'detail': expected})
                self.assertEqual(state, before)


if __name__ == '__main__':
    unittest.main()
