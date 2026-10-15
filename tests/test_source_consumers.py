"""Referenced source bindings preserve forecast history and fail closed on corruption."""
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest

import test_system_skills as skills_support
import forecast
import registry_current
from registry import canonical, snapshot
from source_manifests import compact


class SourceConsumers(unittest.TestCase):
    setUp = skills_support.Forecast.setUp
    git = skills_support.Forecast.git
    task = skills_support.Forecast.task
    corpus = skills_support.Forecast.corpus

    def referenced(self):
        state, events = self.corpus()
        state['tasks']['T']['candidate'] = snapshot(self.repo)
        expected = forecast.calculate(state, events, 'T')
        state['tasks']['T']['candidate'] = compact(state['tasks']['T']['candidate'], self.store.artifact_bytes)
        for old in state['tasks'].values():
            if old.get('delivery'):
                old['delivery']['snapshot'] = compact(old['delivery']['snapshot'], self.store.artifact_bytes)
        return state, events, expected

    def test_referenced_current_and_historical_scope_equals_inline(self):
        state, events, expected = self.referenced()
        before = canonical(state)
        self.assertEqual(forecast.calculate(state, events, 'T'), expected)
        self.assertEqual(canonical(state), before)
        (self.repo / 'code.py').write_text('Changed after the recorded delivery\n')
        self.assertEqual(forecast.calculate(state, events, 'T'), expected)

    def test_mixed_old_inline_and_new_referenced_history_equals_inline(self):
        state, events, expected = self.referenced()
        state['tasks']['H0']['delivery']['snapshot'] = self.store.resolve_snapshot(
            state['tasks']['H0']['delivery']['snapshot'])
        self.assertEqual(forecast.calculate(state, events, 'T'), expected)

    def test_missing_or_tampered_source_manifest_cannot_supply_a_forecast(self):
        state, events, expected = self.referenced()
        pointer = state['tasks']['T']['candidate']['manifest']
        artifact = self.root / 'artifacts' / pointer['sha256']
        body = artifact.read_bytes()
        artifact.unlink()
        self.assertEqual(forecast.calculate(state, events, 'T')['status'], 'scope_unavailable')
        artifact.write_bytes(body + b' ')
        self.assertEqual(forecast.calculate(state, events, 'T')['status'], 'scope_unavailable')
        artifact.write_bytes(body)
        self.assertEqual(forecast.calculate(state, events, 'T'), expected)

    def test_automatic_empty_event_argument_uses_authenticated_minimal_timeline(self):
        state, events, expected = self.referenced()
        class Indexed(dict):
            pass
        state = Indexed(state)
        indexes = registry_current.empty()
        # Only the index builder consumes history; calculate receives no journal payloads.
        metadata_state = {'messages': {}}
        for event in events:
            if event['kind'] == 'message':
                metadata_state['messages'][event['data']['id']] = event['data']
            registry_current._forecast(indexes, metadata_state, event)
        state.current = indexes
        before = canonical(indexes)
        self.assertEqual(forecast.calculate(state, (), 'T'), expected)
        self.assertEqual(canonical(indexes), before)

    def test_authenticated_reference_view_equals_full_inline_history(self):
        from registry_cache import Tree
        state, events, expected = self.referenced()
        indexes = registry_current.empty()
        metadata_state = {'messages': {}}
        for event in events:
            if event['kind'] == 'message':
                metadata_state['messages'][event['data']['id']] = event['data']
            registry_current._forecast(indexes, metadata_state, event)
        connection = sqlite3.connect(':memory:', isolation_level=None)
        connection.row_factory = sqlite3.Row
        self.addCleanup(connection.close)
        tree = Tree.build(connection, state)
        current = Tree.build(connection, indexes, domain='current')
        tree.root.current = current.root
        self.assertEqual(forecast.calculate(tree.root, (), 'T'), expected)

    def proof(self, task='T'):
        from runtime import execute
        self.store.append('ack', 'A', {'message': 'assign-' + task})
        return execute(self.store, 'A', task, argv=[sys.executable, '-c', 'pass'])

    def test_public_full_state_restores_files_and_packet_without_changing_event_or_artifact(self):
        from squadra import deliver
        original = self.store.state()['tasks']['T']
        keys = ('title', 'owner', 'repo', 'base', 'scope', 'proof', 'expected', 'depends', 'full_gate')
        task = {key: original[key] for key in keys}
        task.update(id='U', reviewer='R')
        self.store.batch([('task', 'C', task, None), ('message', 'C', {
            'id': 'assign-U', 'task': 'U', 'to': 'A', 'body': 'Synthetic assignment'}, None)])
        self.assertEqual(self.proof('U')['status'], 'passed')
        delivered = deliver(self.store, 'A', 'U')
        events = self.store.events()
        raw = self.store.state(events, resolve=False)['tasks']['U']
        self.assertIn('manifest', raw['candidate'])
        self.assertNotIn('files', raw['candidate'])
        packet = self.root / 'artifacts' / delivered['packet']['sha256']
        packet_before = packet.read_bytes()
        full = self.store.state(events)['tasks']['U']
        self.assertEqual(full['candidate'], snapshot(self.repo))
        self.assertEqual(full['delivery']['packet_data']['candidate'], full['candidate'])
        self.assertEqual(full['proofs'][0]['after'], full['candidate'])
        self.assertEqual(events, self.store.events())
        self.assertEqual(packet.read_bytes(), packet_before)

    def test_isolated_bridges_read_reference_metadata_without_manifest_helper(self):
        self.assertEqual(self.proof()['status'], 'passed')
        root = Path(__file__).resolve().parents[1]
        probe = """import importlib,json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
bridge=importlib.import_module(sys.argv[2]+'_plugin')
identity,state=bridge.registry(sys.argv[3])
assert identity
proof=state['tasks']['T']['proofs'][0]
assert 'manifest' in proof['after'] and 'files' not in proof['after']
assert proof['task']=='T' and proof['status']=='passed'
assert 'source_manifests' not in sys.modules
for name in ('registry','plugins','plugin_actions','plugin_settings'):
 assert Path(sys.modules[name].__file__).parent==Path(sys.argv[1])
print('reference metadata preserved without the manifest helper')
"""
        for name in ('playwright', 'sentry'):
            with self.subTest(bridge=name):
                folder = root / 'plugins' / name / 'bridge'
                self.assertFalse((folder / 'source_manifests.py').exists())
                result = subprocess.run([sys.executable, '-I', '-B', '-c', probe, str(folder), name, str(self.root)],
                                        cwd=self.base, env=self.env, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
