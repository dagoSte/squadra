"""Cuciture dell'integrazione 3.15: K1 in replace/handover e decisione del proprietario nel grafo."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_lane_parent_reader as k1  # also puts scripts/ on sys.path
import registry
import replacement
from registry import Error, plain


class Seams(k1.LaneParentReader):
    """Reuses parent P (owner C, reader V-read) and lane builders A and B."""


for _name in [n for n in dir(k1.LaneParentReader) if n.startswith('test_')]:
    setattr(Seams, _name, None)


class Seams(Seams):
    def test_replacement_reader_cannot_be_a_lane_builder_of_the_parent(self):
        self.lanes()
        state = plain(self.store.view())
        with self.assertRaisesRegex(Error, 'lane_builder_reader: A-read'):
            replacement.independent(state, state['tasks']['P'], 'A-read')
        replacement.independent(state, state['tasks']['P'], 'V-read')

    def test_a_successor_keeps_the_lanes_of_the_task_it_takes_over(self):
        self.lanes()
        state = plain(self.store.view())
        successor = {**state['tasks']['P'], 'id': 'P-takeover-0001', 'supersedes': 'P', 'taken_over_from': 'P'}
        with self.assertRaisesRegex(Error, 'lane_builder_reader: A-read'):
            replacement.independent(state, successor, 'A-read')

    def test_k1_follows_the_whole_chain_of_takeovers(self):
        # Two takeovers P -> P-1 -> P-2: the lanes decision stays on P, the oldest ancestor.
        self.lanes()
        state = plain(self.store.view())
        first = {**state['tasks']['P'], 'id': 'P-1', 'supersedes': 'P', 'taken_over_from': 'P'}
        state['tasks']['P-1'] = first
        second = {**first, 'id': 'P-2', 'supersedes': 'P-1', 'taken_over_from': 'P-1'}
        for reader in ('A-read', 'B-read'):
            with self.subTest(reader=reader), self.assertRaisesRegex(Error, 'lane_builder_reader: ' + reader):
                replacement.independent(state, second, reader)
        replacement.independent(state, second, 'V-read')

    def test_lineage_lists_every_ancestor_nearest_first(self):
        self.lanes()
        state = plain(self.store.view())
        state['tasks']['P-1'] = {**state['tasks']['P'], 'id': 'P-1', 'supersedes': 'P', 'taken_over_from': 'P'}
        second = {**state['tasks']['P-1'], 'id': 'P-2', 'supersedes': 'P-1', 'taken_over_from': 'P-1'}
        self.assertEqual(registry.task_lineage(state, second), ['P-2', 'P-1', 'P'])
        self.assertEqual(replacement.lineage(state, second), ['P-2', 'P-1', 'P'])

    def test_a_new_successor_task_refuses_a_lane_builder_reader_at_creation(self):
        # The task being validated is not in state yet: its declaration must carry the ancestry.
        self.lanes()
        state = plain(self.store.view())
        declared = {k: v for k, v in state['tasks']['P'].items() if k in registry.TASK_FIELDS}
        declared.update(id='P-next', supersedes='P', reviewer='A-read', pool=None)
        with self.assertRaisesRegex(Error, 'lane_builder_reader: A-read'):
            registry.validate(state, 'task', 'C', declared)
        pooled = {**declared, 'reviewer': None, 'pool': ['V-read', 'A-read']}
        with self.assertRaisesRegex(Error, 'lane_builder_reader: A-read'):
            registry.validate(state, 'task', 'C', pooled)

    def test_shared_k1_check_covers_a_successor_after_one_takeover(self):
        # reassign, task --reviewer and pools call the shared helper with the successor's own id.
        self.lanes()
        state = plain(self.store.view())
        state['tasks']['P-1'] = {**state['tasks']['P'], 'id': 'P-1', 'supersedes': 'P', 'taken_over_from': 'P'}
        with self.assertRaisesRegex(Error, 'lane_builder_reader: A-read'):
            registry.require_not_lane_builder(state, 'P-1', 'A-read')
        with self.assertRaisesRegex(Error, 'lane_builder_reader: B-read'):
            registry.check_pool(state, {'id': 'P-1', 'owner': 'C', 'pool': ['V-read', 'B-read'], 'reviewer': None})
        self.assertIsNone(registry.lane_builder_of(state, 'P-1', 'V-read'))

    def test_lineage_stops_on_a_cycle(self):
        state = {'tasks': {'X': {'id': 'X', 'supersedes': 'Y'}, 'Y': {'id': 'Y', 'supersedes': 'X'}}}
        self.assertEqual(replacement.lineage(state, state['tasks']['X']), ['X', 'Y'])


class HandoverDecisionGraph(unittest.TestCase):
    def test_owner_words_inside_a_handover_prepare_are_a_decision_node(self):
        events = [
            {'kind': 'ask', 'seq': 1, 'hash': 'h1', 'actor': 'C', 'utc': '2026-11-09T10:00:00+00:00',
             'data': {'id': 'q-ho', 'question': 'Passiamo la sede?', 'options': [{'id': 'si', 'label': 'Sì'}]}},
            {'kind': 'handover.prepare', 'seq': 2, 'hash': 'h2', 'actor': 'C', 'utc': '2026-11-09T10:01:00+00:00',
             'data': {'id': 'handover-x', 'from': 'C', 'to': 'C2',
                      'decision': {'id': 'ho-x', 'answers': 'q-ho', 'choice': 'si', 'channel': 'typed-here',
                                   'owner_words': 'riprendi la sede C2', 'reason': 'Passaggio approvato'}}},
        ]
        graph = registry.decision_graph(events)
        node = next(n for n in graph['nodes'] if n['id'] == 'decision:ho-x')
        self.assertEqual((node['kind'], node['seq'], node['hash'], node['words']),
                         ('owner-answer', 2, 'h2', 'riprendi la sede C2'))
        self.assertIn({'from': 'ask:q-ho', 'to': 'decision:ho-x', 'kind': 'answers', 'recorded': True}, graph['edges'])
        self.assertEqual(graph['unresolved'], [])


if __name__ == '__main__':
    unittest.main()
