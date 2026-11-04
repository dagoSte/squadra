"""Protections recorded while a purge runs (counterexamples from the S313-B2 read, probe 0640818d)."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1] / 'scripts')]
import test_storage_policy as support
import storage_policy as policy
from registry import Error
from squadra import deliver

EXPIRE_ALL = {'checkpoints': 0, 'saves': 0, 'hours': 0}


class PurgeRaces(unittest.TestCase):
    setUp = support.StoragePolicy.setUp
    git = support.StoragePolicy.git
    task = support.StoragePolicy.task
    proof = support.StoragePolicy.proof
    checkpoint = support.StoragePolicy.checkpoint
    cli = support.StoragePolicy.cli

    def purge_with(self, hook):
        record = policy.record

        self.refused = []

        def during(store, actor, action, **data):
            result = record(store, actor, action, **data)
            if action == 'purge' and data.get('phase') == 'started':
                try:
                    hook(store)
                except Error as exc:
                    self.refused.append(str(exc))
            return result

        with patch.object(policy, 'record', side_effect=during):
            return policy.purge(self.store, 'C', 'Remove expired synthetic data', apply=True, policy=EXPIRE_ALL)

    def test_preexisting_keep_is_respected(self):
        checkpoint = self.checkpoint('retain-control')
        policy.keep(self.store, 'A', checkpoint, 'Preexisting keep')
        policy.purge(self.store, 'C', 'Remove expired synthetic data', apply=True, policy=EXPIRE_ALL)
        self.assertTrue(checkpoint.is_dir())

    def test_keep_recorded_after_the_locked_replan_is_never_recorded_and_lost(self):
        checkpoint = self.checkpoint('must-retain')
        result = self.purge_with(lambda store: policy.record(store, 'A', 'keep', path=str(checkpoint),
                                                             reason='Keep received during purge'))
        keeps = [o for o in self.store.state()['outputs'] if o.get('storage_action', {}).get('action') == 'keep']
        self.assertFalse(keeps and not checkpoint.is_dir(), 'Purge deleted a recovery point after a recorded keep')
        self.assertTrue(self.refused and 'being purged' in self.refused[0])
        self.assertIn(str(checkpoint), result['removed'])
        self.assertFalse(any(p.name.startswith('.purging-') for p in checkpoint.parent.iterdir()))

    def test_task_opened_on_a_source_inside_the_target_is_never_recorded_and_lost(self):
        data = {'id': 'F', 'title': 'Frozen input', 'owner': 'A', 'repo': str(self.repo),
                'base': self.git('rev-parse', 'HEAD').decode().strip(), 'scope': self.store.artifact(self.scope),
                'proof': None, 'expected': None, 'depends': [], 'full_gate': False,
                'delivery_policy': 'frozen-v1'}
        self.store.append('task', 'C', data)
        self.store.append('message', 'C', {'id': 'assign-F', 'to': 'A', 'task': 'F', 'body': 'Synthetic input'})
        self.store.append('ack', 'A', {'message': 'assign-F'})
        self.proof(task='F'); deliver(self.store, 'A', 'F')
        sha = self.store.state()['tasks']['F']['delivery']['frozen']['sha256']
        checkpoint = self.checkpoint('source-for-new-task')
        source = checkpoint / 'candidates' / sha / 'repo'
        self.purge_with(lambda store: store.append('task', 'C', {**data, 'id': 'new-active', 'repo': str(source)}))
        self.assertFalse('new-active' in self.store.state()['tasks'] and not source.is_dir(),
                         'Purge deleted the repository of a newly active task')
        self.assertTrue(self.refused and 'being purged' in self.refused[0])

    def commit_after_last_check(self, write):
        """Run write right after purge's last protection read, as a concurrent client would."""
        record, protections, started, outcome = policy.record, policy.protections, [], []

        def mark(store, actor, action, **data):
            result = record(store, actor, action, **data)
            if action == 'purge' and data.get('phase') == 'started':
                started.append(True)
            return result

        def late(store, **kwargs):
            guard = protections(store, **kwargs)
            if started and not outcome:
                try:
                    outcome.append(write(store))
                except Error as exc:
                    outcome.append(exc)
            return guard

        with patch.object(policy, 'record', side_effect=mark), patch.object(policy, 'protections', side_effect=late):
            result = policy.purge(self.store, 'C', 'Remove expired synthetic data', apply=True, policy=EXPIRE_ALL)
        return result, outcome[0]

    def test_keep_validated_earlier_and_committed_after_the_last_check_is_refused(self):
        checkpoint = self.checkpoint('late-keep')
        result, outcome = self.commit_after_last_check(
            lambda store: policy.record(store, 'A', 'keep', path=str(checkpoint), reason='Validated before the purge'))
        self.assertIsInstance(outcome, Error)
        self.assertIn('being purged', str(outcome))
        self.assertIn(str(checkpoint), result['removed'])
        self.assertFalse(any(o.get('storage_action', {}).get('action') == 'keep'
                             for o in self.store.state()['outputs']), 'A keep was recorded for deleted data')

    def test_prepared_task_committed_after_the_last_check_is_refused(self):
        checkpoint = self.checkpoint('late-task')
        data = {'id': 'late', 'title': 'Prepared earlier', 'owner': 'A', 'repo': str(checkpoint / 'inside'),
                'base': self.git('rev-parse', 'HEAD').decode().strip(), 'scope': self.store.artifact(self.scope),
                'proof': None, 'expected': None, 'depends': [], 'full_gate': False, 'delivery_policy': 'frozen-v1'}
        result, outcome = self.commit_after_last_check(lambda store: store.append('task', 'C', data))
        self.assertIsInstance(outcome, Error)
        self.assertNotIn('late', self.store.state()['tasks'])
        self.store.append('task', 'C', {**data, 'repo': str(self.repo)})

    def test_keep_and_task_are_accepted_again_once_the_purge_has_ended(self):
        checkpoint = self.checkpoint('after-purge')
        policy.purge(self.store, 'C', 'Remove expired synthetic data', apply=True, policy=EXPIRE_ALL)
        other = self.checkpoint('next-one')
        policy.keep(self.store, 'A', other, 'Recorded after the purge ended')


    def test_keep_validated_before_purge_and_resumed_later_never_confirms_deleted_data(self):
        import threading
        from registry import Store
        checkpoint = self.checkpoint('delayed-keep')
        validated, proceed, outcome = threading.Event(), threading.Event(), []
        real_record = policy.record

        def record(store, actor, action, **fields):
            if action == 'keep' and threading.current_thread().name == 'late-keep':
                validated.set()
                proceed.wait(10)
            return real_record(store, actor, action, **fields)

        def keep():
            writer = Store(self.root)
            try:
                outcome.append(policy.keep(writer, 'A', checkpoint, 'Validated before the purge'))
            except Error as exc:
                outcome.append(exc)
            finally:
                writer.close()

        purged = []

        def purge():
            writer = Store(self.root)
            try:
                purged.append(policy.purge(writer, 'C', 'Remove expired synthetic data', apply=True, policy=EXPIRE_ALL))
            except Error as exc:
                purged.append(exc)
            finally:
                writer.close()

        with patch.object(policy, 'record', side_effect=record):
            late = threading.Thread(target=keep, name='late-keep'); late.start()
            self.assertTrue(validated.wait(10))
            cleaner = threading.Thread(target=purge); cleaner.start()
            cleaner.join(2)
            self.assertTrue(cleaner.is_alive(), 'purge must wait for the keep that holds the candidate lock')
            proceed.set(); late.join(20); cleaner.join(60)
        self.assertEqual(outcome[0]['kept'], str(checkpoint))
        self.assertTrue(checkpoint.is_dir(), 'A confirmed keep must still name existing data')
        self.assertIsInstance(purged[0], Error)

    def test_keep_arriving_while_purge_removes_is_refused_after_it(self):
        import threading
        from registry import Store
        checkpoint = self.checkpoint('removed-first')
        removing, outcome = threading.Event(), []
        original = policy.remove_tree

        def slow_remove(path):
            removing.set()
            waiter.join(2)
            return original(path)

        def keep():
            removing.wait(10)
            writer = Store(self.root)
            try:
                outcome.append(policy.keep(writer, 'A', checkpoint, 'Too late'))
            except Error as exc:
                outcome.append(exc)
            finally:
                writer.close()

        waiter = threading.Thread(target=keep); waiter.start()
        with patch.object(policy, 'remove_tree', side_effect=slow_remove):
            result = policy.purge(self.store, 'C', 'Remove expired synthetic data', apply=True, policy=EXPIRE_ALL)
        waiter.join(20)
        self.assertIn(str(checkpoint), result['removed'])
        self.assertIsInstance(outcome[0], Error)
        self.assertFalse(any(o.get('storage_action', {}).get('action') == 'keep'
                             for o in self.store.state()['outputs']))


if __name__ == '__main__':
    unittest.main()
