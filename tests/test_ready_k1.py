"""R3151-READY: reader.ready opens no K1 gap (deferral 6 of 3.15.0).

The question: can an alias of a lane's builder declare itself ready as the reader of the lane's parent,
or of a successor of the parent, and then take the read? It cannot, at three independent layers:
1. reader.ready accepts only the task's configured reviewer or a pool member;
2. becoming that reader goes through K1 (task --reviewer, check_pool, reassign; at the door with the
   declaration's ancestry for a successor);
3. reader.ready itself calls replacement.independent, which applies require_not_lane_builder to every
   ancestor of the task: even a state where the alias were already the configured reader is refused.
These tests are a guard: green on v3.15.0 and on any later version that keeps the three layers.
"""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_lane_parent_reader as k1  # also puts scripts/ on sys.path
import lanes_apply
import registry
from registry import Error, plain


class ReadyK1(k1.LaneParentReader):
    """The real lanes_apply fixture: parent P (owner C, reader V-read), lanes built by A and B."""


for _name in [n for n in dir(k1.LaneParentReader) if n.startswith('test_')]:
    setattr(ReadyK1, _name, None)


class ReadyK1(ReadyK1):
    def setUp(self):
        super().setUp()
        self.lanes()
        state = plain(self.store.view())
        self.lane = next(lane for lane in ('P.L1', 'P.L2') if state['tasks'][lane]['owner'] == 'A')

    def ready(self, actor, task):
        return self.cli('ready', '--actor', actor, '--task', task)

    def successor(self, reviewer):
        data = {**self.task, 'id': 'P2', 'supersedes': 'P', 'reviewer': reviewer, 'title': 'Parent successor'}
        self.store.batch([('task', 'C', data, None), ('message', 'C', lanes_apply.assignment(data), None)])

    def test_layer1_a_lane_builders_alias_cannot_declare_itself_ready_on_the_parent(self):
        code, _, err = self.ready('A-read', 'P')
        self.assertNotEqual(code, 0)
        self.assertIn('Configured reader required', err)
        self.assertNotIn('A-read', plain(self.store.view())['tasks']['P'].get('reader_ready', {}))

    def test_layer2_it_cannot_become_the_reader_of_the_parent_or_of_a_successor(self):
        with self.assertRaisesRegex(Error, 'lane_builder_reader'):
            self.successor('A-read')
        self.deliver_parent()
        code, _, err = self.reassign('A-read')
        self.assertNotEqual(code, 0)
        self.assertIn('lane_builder_reader', err)

    def test_layer3_reader_ready_itself_refuses_the_alias_on_the_parent_and_on_a_successor(self):
        def refused(task):
            forged = copy.deepcopy(plain(self.store.view()))
            forged['root'] = str(self.store.root)
            forged['tasks'][task]['reviewer'] = 'A-read'  # as if an earlier layer had let it through
            with self.assertRaisesRegex(Error, 'lane_builder_reader: A-read built lane ' + self.lane):
                registry.validate(forged, 'reader.ready', 'A-read', {'task': task, 'ready': True})
        with self.subTest(task='P'):
            refused('P')
        self.successor('V-read')  # P2 supersedes P: its ancestry carries P's lanes
        with self.subTest(task='P2'):
            refused('P2')

    def test_control_the_configured_independent_reader_is_still_ready(self):
        code, _, err = self.ready('V-read', 'P')
        self.assertEqual(code, 0, err[-300:])
        self.assertIn('V-read', plain(self.store.view())['tasks']['P']['reader_ready'])


if __name__ == '__main__':
    unittest.main()
