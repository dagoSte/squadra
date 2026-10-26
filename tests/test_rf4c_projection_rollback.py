"""A restored or lost journal tail is refused before the projections that still hold it are rewritten."""
import json
import shutil
import unittest

import test_registry_projections as base
from test_registry_projections import StoreFixture, projections
import test_squadra as support
from registry import Store

REFUSAL = getattr(projections, 'JournalRollback', projections.ProjectionError)


def refusal(call):
    """The refusal message, or None when the call went through."""
    try:
        call()
    except REFUSAL as exc:
        return str(exc)
    return None


class FixtureRollback(unittest.TestCase):
    setUp = base.ProjectionTests.setUp

    def lost(self):
        return (self.root / 'events.jsonl').read_bytes().count(b'LOST')

    def roll_back(self, keep, lost):
        """Publish `keep` events, copy the database, publish `lost` more, then restore the copy."""
        for i in range(keep):
            self.store.append('decision', {'reason': 'early ' + str(i)})
        projections.publish(self.store)
        self.store.db.close()
        shutil.copy(self.root / 'registry.sqlite3', self.root / 'older.sqlite3')
        self.reopen()
        for i in range(lost):
            self.store.append('decision', {'reason': 'LOST ' + str(i)})
        projections.publish(self.store)
        self.store.db.close()
        shutil.copy(self.root / 'older.sqlite3', self.root / 'registry.sqlite3')
        self.reopen()
        return (self.root / 'events.jsonl').read_bytes()

    def reopen(self):
        self.store = StoreFixture(self.root)
        self.addCleanup(self.store.db.close)

    def test_publish_without_a_new_event_refuses_instead_of_truncating(self):
        before = self.roll_back(2, 3)
        message = refusal(lambda: projections.publish(self.store))
        self.assertEqual((self.lost(), message is not None), (3, True))
        self.assertRegex(message, r'#3\b.*journal')
        self.assertEqual((self.root / 'events.jsonl').read_bytes(), before)

    def test_new_event_reusing_a_lost_seq_is_refused_and_the_projections_keep_their_bytes(self):
        before = self.roll_back(2, 3)
        posta = {p: p.read_bytes() for p in self.root.rglob('*.md')}
        self.store.append('decision', {'reason': 'after rollback'})
        message = refusal(lambda: projections.publish(self.store))
        self.assertEqual((self.lost(), message is not None), (3, True))
        self.assertEqual((self.root / 'events.jsonl').read_bytes(), before)
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob('*.md')}, posta)

    def test_regeneration_after_a_rollback_refuses_too(self):
        before = self.roll_back(2, 3)
        self.store.db.execute("UPDATE derived_projection_meta SET data='{}'")
        message = refusal(lambda: projections.publish(self.store))
        self.assertEqual((self.lost(), message is not None), (3, True))
        self.assertEqual((self.root / 'events.jsonl').read_bytes(), before)

    def test_an_interrupted_append_of_journal_events_still_recovers(self):
        first = self.store.append('decision', {'reason': 'first'})
        projections.publish(self.store)
        second = self.store.append('decision', {'reason': 'second'})
        with (self.root / 'events.jsonl').open('ab') as out:
            out.write((projections._canonical(second) + '\n').encode() + b'{"partial":')
        result = projections.publish(self.store)
        self.assertFalse(result['rebuilt'])
        exported = [json.loads(line) for line in (self.root / 'events.jsonl').read_text().splitlines()]
        self.assertEqual(exported, [first, second])


class StoreRollback(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task

    def test_restored_older_registry_refuses_the_next_publish(self):
        self.store.publish()
        self.store.close()
        older = self.base / 'older'; older.mkdir()
        for name in ('registry.sqlite3', 'registry.sqlite3-wal'):
            if (self.root / name).exists():
                shutil.copy(self.root / name, older / name)
        self.store = Store(self.root); self.addCleanup(self.store.close)
        for i in range(3):
            self.store.append('diagnostic', 'system', {'note': 'LOST ' + str(i)})
        self.store.publish()
        self.store.close()
        before = (self.root / 'events.jsonl').read_bytes()
        self.assertEqual(before.count(b'LOST'), 3)
        for name in ('registry.sqlite3', 'registry.sqlite3-wal', 'registry.sqlite3-shm'):
            (self.root / name).unlink(missing_ok=True)
            if (older / name).exists():
                shutil.copy(older / name, self.root / name)
        self.store = Store(self.root); self.addCleanup(self.store.close)
        self.store.append('diagnostic', 'system', {'note': 'after rollback'})
        message = refusal(self.store.publish)
        self.assertEqual(((self.root / 'events.jsonl').read_bytes().count(b'LOST'), message is not None), (3, True))
        self.assertEqual((self.root / 'events.jsonl').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
