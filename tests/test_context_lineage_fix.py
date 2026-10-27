"""Regressions for predecessor history and task-scoped resource provenance."""
import tempfile
import unittest
from pathlib import Path

import test_context_brief
from context_brief import reentry
from test_supersede import new_store, add_task, spend_code_budget


class ContextLineageFix(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sq-context-lineage-')
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name).resolve()
        self.store = new_store(self.folder / 'registry', self.folder)
        self.addCleanup(self.store.close)

    def packet(self, task):
        event = self.store.events()[-1]
        return reentry(self.store, task, recipient='A',
                       since=str(event['seq']) + ':' + event['hash'], max_chars=32000)

    def history(self):
        add_task(self.store, self.folder, 'T')
        self.store.append('decision', 'C', {'id': 'old-contract', 'tasks': ['T'],
                                           'reason': 'Old task scope only'})
        spend_code_budget(self.store, self.folder, 'T')
        add_task(self.store, self.folder, 'UNRELATED', owner='B')
        self.store.append('decision', 'C', {'id': 'unrelated-contract', 'tasks': ['UNRELATED'],
                                           'reason': 'Unrelated scope'})

    def assert_history(self, packet):
        self.assertEqual([e['id'] for e in packet['errors']], ['T-red'])
        self.assertTrue(packet['errors'][0].get('historical'))
        self.assertEqual(packet['errors'][0]['task'], 'T')
        self.assertIsNotNone(packet['errors'][0]['later_passing_seq'])
        self.assertEqual({p['id'] for p in packet['references']['proofs']}, {'T-red', 'T-green'})
        self.assertTrue(all(p['historical'] for p in packet['references']['proofs']))
        self.assertFalse(any(p['recorded_contract_match'] for p in packet['references']['proofs']))
        self.assertEqual([d['id'] for d in packet['constraints']['decisions']], ['old-contract'])
        self.assertTrue(packet['constraints']['decisions'][0]['historical'])
        self.assertTrue(packet['constraints']['decisions'][0]['task_superseded'])
        assignment = next(s for s in packet['supersessions'] if s.get('id') == 'assign-T')
        self.assertEqual(assignment['replaced_by'], 'U')
        self.assertTrue(assignment['historical'])
        self.assertEqual(assignment['task'], 'T')
        self.assertFalse(any(s.get('current') for s in packet['supersessions']))
        self.assertEqual(packet['references']['task']['owner'], 'A')
        self.assertEqual(packet['next_step']['phase'], 'waiting-ack')
        self.assertTrue(packet['delta_empty'])
        self.assertEqual(packet['omitted']['changes'], 0)

    def test_explicit_supersede_keeps_predecessor_history(self):
        self.history()
        add_task(self.store, self.folder, 'U')
        self.store.append('supersede', 'C', {'task': 'T', 'by': 'U', 'reason': 'New scope'})
        before = self.store.events()
        self.assert_history(self.packet('U'))
        self.assertEqual(self.store.events(), before)
        self.assertNotIn('supersedes', self.store.state()['tasks']['U'])

    def test_task_supersedes_keeps_the_same_historical_labels(self):
        self.history()
        add_task(self.store, self.folder, 'U', supersedes='T')
        self.assert_history(self.packet('U'))

    def test_mixed_transitive_lineage_excludes_successors_and_unrelated_tasks(self):
        self.history()
        add_task(self.store, self.folder, 'U')
        self.store.append('supersede', 'C', {'task': 'T', 'by': 'U', 'reason': 'New scope'})
        add_task(self.store, self.folder, 'V', supersedes='U')
        add_task(self.store, self.folder, 'W', supersedes='V')
        self.store.append('decision', 'C', {'id': 'future-contract', 'tasks': ['W'],
                                           'reason': 'Successor scope'})
        self.assert_history(self.packet('V'))
        self.assertEqual(self.packet('V')['blocks']['superseded_by'], 'W')
        self.assertNotIn('assign-W', [s.get('id') for s in self.packet('V')['supersessions']])

    def test_task_scoped_resource_keeps_actual_acquisition_seq(self):
        add_task(self.store, self.folder, 'T')
        self.store.append('resource.acquire', 'A', {'name': 'database', 'task': 'T',
                                                   'reason': 'Exclusive task resource'})
        acquisition = self.store.events()[-1]['seq']
        self.store.append('resource.acquire', 'B', {'name': 'other', 'reason': 'Unrelated resource'})
        resources = {r['name']: r for r in self.packet('T')['blocks']['resources']}
        self.assertEqual(resources['database']['seq'], acquisition)
        self.assertEqual(resources['database']['owner'], 'A')
        self.assertEqual(resources['database']['task'], 'T')
        self.store.append('resource.release', 'A', {'name': 'database', 'reason': 'Finished'})
        self.assertNotIn('database', [r['name'] for r in self.packet('T')['blocks']['resources']])
        self.store.append('resource.acquire', 'A', {'name': 'database', 'task': 'T', 'reason': 'Next use'})
        reacquisition = self.store.events()[-1]['seq']
        resource = next(r for r in self.packet('T')['blocks']['resources'] if r['name'] == 'database')
        self.assertEqual(resource['seq'], reacquisition)


if __name__ == '__main__':
    unittest.main()
