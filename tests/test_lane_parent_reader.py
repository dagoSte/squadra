"""K1 (3.14.0 deferral): a lane's builder never reads its parent's integration, whatever the entry point."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_lanes_apply as lanes_support  # also puts scripts/ on sys.path
import lanes_apply
import registry
from registry import Error, plain
from runtime import execute


class LaneParentReader(lanes_support.LaneApplication):
    """Reuses the synthetic parent P (owner C, reader V-read) and builders A and B of the lane tests."""


for _name in [n for n in dir(lanes_support.LaneApplication) if n.startswith('test_')]:
    setattr(LaneParentReader, _name, None)


class LaneParentReader(LaneParentReader):
    def lanes(self):
        result = lanes_apply.apply(self.store, 'C', 'P', lanes_apply.plan(self.store, 'P', self.partition()))
        state = plain(self.store.view())
        builders = {state['tasks'][lane]['owner'] for lane in result['created'] if lane != 'P.L0'}
        self.assertEqual(builders, {'A', 'B'})
        return result

    def deliver_parent(self):
        code, _, err = self.cli('ack', '--actor', 'C', '--message', 'assign-P')
        self.assertEqual(code, 0, err[-400:])
        self.assertEqual(execute(self.store, 'C', 'P', argv=self.task['proof'])['status'], 'passed')
        code, _, err = self.cli('deliver', '--actor', 'C', '--task', 'P')
        self.assertEqual(code, 0, err[-400:])

    def reassign(self, reader):
        return self.cli('reassign', '--actor', 'C', '--task', 'P', '--reviewer', reader,
                        '--reason', 'K1 regression: who may read the parent')

    def test_K1_reassign_refuses_a_lane_builders_alias_as_the_parents_reader(self):
        self.lanes()
        self.deliver_parent()
        before = len(self.store.events())
        code, _, err = self.reassign('A-read')
        self.assertNotEqual(code, 0, 'a lane builder was accepted as reader of the parent integration')
        self.assertIn('lane_builder_reader', err)
        self.assertIn('P.L', err)
        # Only the CLI's refusal diagnostic is recorded; no reassignment or handoff.
        self.assertEqual({e['kind'] for e in self.store.events()[before:]}, {'diagnostic'})
        self.assertEqual(plain(self.store.view())['tasks']['P']['reviewer'], 'V-read')

    def test_K1_reassign_still_accepts_an_independent_reader(self):
        self.agent('W-read', 'reviewer', 'W', 'codex', self.repo)
        self.lanes()
        self.deliver_parent()
        code, _, err = self.reassign('W-read')
        self.assertEqual(code, 0, err[-400:])
        self.assertEqual(plain(self.store.view())['tasks']['P']['reviewer'], 'W-read')

    def test_K1_a_dot_L_name_without_the_recorded_lane_relation_is_not_a_lane(self):
        # A task merely named P.L9, created outside lanes_apply, builds nothing for P.
        self.agent('W', 'builder', 'W', 'codex', self.repo)
        self.agent('W-read', 'reviewer', 'W', 'codex', self.repo)
        manual = {**self.task, 'id': 'P.L9', 'owner': 'W', 'reviewer': 'R', 'title': 'Unrelated name'}
        self.store.batch([('task', 'C', manual, None), ('message', 'C', lanes_apply.assignment(manual), None)])
        self.deliver_parent()
        self.assertEqual(registry.lane_builder_of(plain(self.store.view()), 'P', 'W-read'), None)
        code, _, err = self.reassign('W-read')
        self.assertEqual(code, 0, err[-400:])

    def test_K1_one_rule_for_reviewer_pool_and_reassign(self):
        self.lanes()
        state = plain(self.store.view())
        built = {state['tasks'][lane]['owner']: lane for lane in ('P.L1', 'P.L2')}
        self.assertEqual(registry.lane_builder_of(state, 'P', 'A-read'), built['A'])
        self.assertEqual(registry.lane_builder_of(state, 'P', 'B'), built['B'])
        self.assertIsNone(registry.lane_builder_of(state, 'P', 'V-read'))
        self.assertIsNone(registry.lane_builder_of(state, 'P.L1', 'A-read'))
        # The task and pool checks call the same rule; a parent with recorded lanes refuses either.
        with self.assertRaisesRegex(Error, 'lane_builder_reader: A-read'):
            registry.require_not_lane_builder(state, 'P', 'A-read')
        with self.assertRaisesRegex(Error, 'review_pool_member: B-read: lane_builder_reader'):
            registry.check_pool(state, {'id': 'P', 'owner': 'C', 'pool': ['V-read', 'B-read'], 'reviewer': None})


ROOT = Path(__file__).resolve().parents[1]


class MinorCleanups(unittest.TestCase):
    """The two small 3.14.0 deferrals that travel with K1."""

    def test_compact_refuses_a_missing_storage_reuse_even_with_storage_compact_loaded(self):
        # The ReuseError import in storage_policy.compact is this guard, not dead code.
        import storage_compact  # noqa: F401  loaded first, as after an earlier compact in the same process
        import storage_policy
        from unittest.mock import patch
        with patch.dict(sys.modules, {'storage_reuse': None}):
            try:
                storage_policy.compact(None)
            except ModuleNotFoundError as exc:
                missing = exc.name
            except Exception as exc:  # went past the guard and reached the (absent) store
                missing = 'none: ' + type(exc).__name__
        self.assertEqual(missing, 'storage_reuse', 'compact went past a missing storage_reuse')

    def test_pin_3_13_1_label_names_only_the_tagged_release(self):
        # A candidate's entry carries "squadra <VERSION>" while it is the bundled build (test_memoranda_bundle);
        # once a release replaces it, the candidate is renamed so the label names the tagged build only.
        import json
        accepted = json.loads((ROOT / 'plugins/memoranda/bridge/squadra_memories/pin.json').read_text())['accepted']
        commits = [entry['source_commit'] for entry in accepted if entry['label'] == 'squadra 3.13.1']
        self.assertEqual(commits, ['a0c4abadc8f8f01a0ab5437fa0f4a161051d0a7f'])


if __name__ == '__main__':
    unittest.main()
