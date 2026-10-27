"""New event envelopes refuse fixed-hash readers without rewriting legacy history."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import registry

_spec = importlib.util.spec_from_file_location('frozen_squadra_360_version_guard',
    Path(__file__).parent / 'fixtures/squadra-3.6.0/registry.py')
legacy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(legacy)


class RegistryVersionGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='squadra-version-guard-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'record'
        old = legacy.Store(self.root, create=True)
        try:
            old.append('init', 'system', {'name': 'Version fixture', 'project': str(self.root.parent),
                                         'version': '3.6.0', 'limits': {'plan': 3, 'code': 3, 'build': 3}})
            for name, role in [('C', 'coordinator'), ('A', 'builder')]:
                old.append('agent', name, {'id': name, 'identity': name, 'role': role,
                                         'client': 'manual', 'repo': str(self.root.parent), 'skills': []})
            old.append('message', 'C', {'id': 'old-message', 'to': 'A', 'body': 'Legacy event'})
            self.legacy_events = old.events()
        finally:
            old.close()

    def test_doctor_names_minimum_version_only_after_new_format(self):
        import contextlib
        import io
        import squadra
        def doctor():
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = squadra.main(['--root', str(self.root), 'doctor'])
            self.assertEqual(result, 0)
            return json.loads(output.getvalue())
        self.assertNotIn('compatibility', doctor())
        self.append_new()
        before = self.rows()
        self.assertEqual(doctor()['compatibility'], 'This registry requires Squadra 3.10.1 or later.')
        self.assertEqual(self.rows(), before)

    def rows(self):
        db = sqlite3.connect(self.root / 'registry.sqlite3')
        try:
            return db.execute('SELECT seq,id,utc,kind,actor,data,previous,hash FROM events ORDER BY seq').fetchall()
        finally:
            db.close()

    def append_new(self):
        store = registry.Store(self.root)
        self.addCleanup(store.close)
        store.append('ack', 'A', {'message': 'old-message'})
        return store

    def raw_event(self, store, *, version=2, include_format=True, seq_gap=0, kind='diagnostic'):
        last = store.events()[-1]
        event = {'id': 'raw-event', 'utc': registry.now(), 'kind': kind, 'actor': 'system',
                 'data': {'reason': 'Synthetic event'}, 'previous': last['hash']}
        if include_format:
            event['format'] = version
        store.db.execute('INSERT INTO events(seq,id,utc,kind,actor,data,previous,hash,hash_format) VALUES(?,?,?,?,?,?,?,?,?)',
                         (last['seq'] + 1 + seq_gap, event['id'], event['utc'], kind, event['actor'],
                          registry.canonical(event['data']), event['previous'],
                          registry.digest(registry.canonical(event).encode()), version))
        return event

    def test_current_reader_accepts_legacy_only_history_without_schema_write(self):
        before = self.rows()
        reader = registry.Store(self.root, read_only=True)
        try:
            self.assertEqual(reader.events(), self.legacy_events)
            columns = [row[1] for row in reader.db.execute('PRAGMA table_info(events)')]
            self.assertNotIn('hash_format', columns)
        finally:
            reader.close()
        self.assertEqual(self.rows(), before)

    def test_new_envelope_preserves_every_legacy_row_and_refuses_old_reader(self):
        before = self.rows(); store = self.append_new()
        self.assertEqual(self.rows()[:len(before)], before)
        events = store.events()
        self.assertEqual(events[:len(before)], self.legacy_events)
        self.assertNotIn('format', events[0])
        self.assertEqual(events[-1].get('format'), 2)
        self.assertEqual(store.state()['messages']['old-message']['ack']['agent'], 'A')
        old = legacy.Store(self.root, read_only=True)
        try:
            with self.assertRaisesRegex(legacy.Error, 'integrity'):
                old.events()
        finally:
            old.close()
        self.assertEqual(len(self.rows()), len(before) + 1)

    def test_fixed_hash_old_writer_cannot_append_after_new_envelope(self):
        self.append_new(); before = self.rows(); old = legacy.Store(self.root)
        try:
            with self.assertRaisesRegex(legacy.Error, 'integrity'):
                old.append('message', 'C', {'id': 'unsafe-old-write', 'to': 'A', 'body': 'Must not append'})
        finally:
            old.close()
        self.assertEqual(self.rows(), before)

    def test_envelope_format_is_covered_by_hash_not_a_mutable_hint(self):
        store = self.append_new(); store.db.execute('DROP TRIGGER no_update')
        store.db.execute('UPDATE events SET hash_format=NULL WHERE seq=(SELECT max(seq) FROM events)')
        with self.assertRaisesRegex(registry.Error, 'integrity'):
            store.events()

    def test_old_hash_cannot_be_relabelled_as_new_format(self):
        store = registry.Store(self.root); self.addCleanup(store.close)
        store.db.execute('DROP TRIGGER no_update')
        store.db.execute('UPDATE events SET hash_format=2 WHERE seq=1')
        with self.assertRaisesRegex(registry.Error, 'integrity'):
            store.events()

    def test_unknown_hash_format_is_refused_even_if_its_hash_matches(self):
        store = self.append_new(); self.raw_event(store, version=5)
        with self.assertRaisesRegex(registry.Error, 'Unsupported event hash format'):
            store.events()

    def test_new_format_requires_its_discriminator_in_the_digest(self):
        store = self.append_new(); self.raw_event(store, include_format=False)
        with self.assertRaisesRegex(registry.Error, 'integrity'):
            store.events()

    def test_hash_valid_sequence_gap_is_refused(self):
        store = self.append_new(); self.raw_event(store, seq_gap=1)
        with self.assertRaisesRegex(registry.Error, 'chain'):
            store.events()

    def test_contiguous_hash_valid_format_two_positive_control(self):
        store = self.append_new(); event = self.raw_event(store)
        self.assertEqual(store.events()[-1]['id'], event['id'])
        self.assertEqual(store.events()[-1]['format'], 2)

    def test_new_kind_cannot_be_silently_ignored_by_fixed_hash_reader(self):
        # The frozen reducer demonstrates why an unknown kind alone is not a guard.
        state = legacy.initial(); state['runs']['orphan'] = {'status': 'running'}
        legacy.apply(state, {'kind': 'run.abandon', 'data': {'id': 'orphan'}, 'actor': 'A',
                             'utc': registry.now(), 'seq': 1, 'id': 'new-kind'})
        self.assertEqual(state['runs']['orphan']['status'], 'running')
        store = registry.Store(self.root); self.addCleanup(store.close)
        self.raw_event(store, kind='run.abandon')
        self.assertEqual(store.events()[-1]['kind'], 'run.abandon')
        old = legacy.Store(self.root, read_only=True)
        try:
            with self.assertRaisesRegex(legacy.Error, 'integrity'):
                old.events()
        finally:
            old.close()


if __name__ == '__main__':
    unittest.main()
