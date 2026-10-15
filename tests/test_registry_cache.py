"""Compare incremental registry views with authoritative replay and corrupt their trust boundary."""
import contextlib
import hashlib
import json
from pathlib import Path
import random
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import registry
import registry_cache as cache
from registry_radix import Index


class RegistryCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='squadra-cache-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'registry'
        self.store = registry.Store(self.root, create=True)
        self.addCleanup(self.store.close)
        self.store.append('init', 'system', {'name': 'Cache fixture', 'project': str(self.root.parent),
                                           'version': 'synthetic', 'limits': {'plan': 3, 'code': 3, 'build': 3}})
        for name, role in [('C', 'coordinator'), ('A', 'builder'), ('R', 'reviewer')]:
            self.store.append('agent', name, {'id': name, 'identity': name, 'role': role, 'client': 'manual',
                                             'repo': str(self.root.parent), 'session': '', 'skills': []})

    def test_challenge_snapshot_full_normalization_matches_incremental_view(self):
        files = []
        body = {'repo': '/synthetic', 'head': 'a' * 40, 'files': files}
        fingerprint = {**body, 'sha256': registry.digest(registry.canonical(body).encode())}
        self.store.append('challenge', 'C', {'candidate': fingerprint, 'summary': 'synthetic'})
        expected = self.store.normalized_state(self.store.events())
        self.assertEqual(cache.plain(self.store.view()), expected)
        self.assertIn('manifest', expected['challenges'][0]['candidate'])
        self.assertEqual(self.store.audit()['cache'], 'matched')

    def message(self, name='m1', body='original'):
        self.store.append('message', 'C', {'id': name, 'to': 'A', 'body': body})

    def metadata(self):
        row = self.store.db.execute('SELECT body,digest FROM derived_state_meta WHERE id=1').fetchone()
        return json.loads(row['body'])

    def set_metadata(self, value, *, recompute=True):
        self.store.db.execute('UPDATE derived_state_meta SET body=?,digest=? WHERE id=1',
                              (cache.canonical(value), cache.metadata_digest(value) if recompute else 'bad'))

    def rebuild_root(self):
        db = sqlite3.connect(':memory:', isolation_level=None)
        try:
            tree = cache.Tree.build(db, self.store.state())
            return tree.flush()
        finally:
            db.close()

    def assert_same(self):
        self.assertEqual(cache.plain(self.store.view()), self.store.state())
        self.assertEqual(self.metadata()['state'], self.rebuild_root())

    def snapshot(self):
        return {str(path.relative_to(self.root)): (hashlib.sha256(path.read_bytes()).hexdigest(),
                                                   path.stat().st_mtime_ns, path.stat().st_mode)
                for path in self.root.rglob('*') if path.is_file() and not path.name.endswith('-shm')}

    def mutate_leaf(self, path, key):
        namespace = cache.namespace(path)
        for row in self.store.db.execute('SELECT digest,data FROM derived_radix_nodes WHERE namespace=?', (namespace,)):
            body = json.loads(row['data'])
            if body.get('kind') == 'leaf' and body.get('key') == key:
                body['value'] = {'order': body['value']['order'], 'value': {'scalar': 'forged'}}
                self.store.db.execute('UPDATE derived_radix_nodes SET data=? WHERE namespace=? AND digest=?',
                                     (cache.canonical(body), namespace, row['digest']))
                return
        self.fail('The positive fixture must contain the targeted authenticated leaf')

    def append_external(self, kind, actor, data, *, gap=0):
        last = self.store.events()[-1]
        seq = last['seq'] + 1 + gap
        event = {'id': 'external-' + str(seq), 'utc': registry.now(), 'kind': kind, 'actor': actor,
                 'data': data, 'previous': last['hash']}
        self.store.db.execute('INSERT INTO events(seq,id,utc,kind,actor,data,previous,hash) VALUES(?,?,?,?,?,?,?,?)',
                              (seq, event['id'], event['utc'], kind, actor, registry.canonical(data),
                               event['previous'], registry.digest(registry.canonical(event).encode())))

    def test_random_valid_reducer_journey_matches_full_replay_at_every_step(self):
        rng = random.Random(734092)
        pending = []; sequence = 0
        for _ in range(80):
            action = rng.choice(['message', 'ack', 'settings', 'output', 'decision'])
            if action == 'ack' and pending:
                name = pending.pop(rng.randrange(len(pending)))
                self.store.append('ack', 'A', {'message': name})
            elif action in {'ack', 'message'}:
                sequence += 1; name = 'm' + str(sequence)
                self.message(name, 'Body ' + str(rng.randrange(10000))); pending.append(name)
            elif action == 'settings':
                self.store.append('settings', 'C', {'wait_seconds': {'ack': rng.randrange(1, 5000)}})
            elif action == 'output':
                self.store.append('output', 'A', {'text': 'Step ' + str(sequence), 'next': 'Continue', 'task': None})
            else:
                self.store.append('decision', 'C', {'reason': 'Retained choice ' + str(sequence)})
            self.assert_same()

    def test_remove_and_reinsert_preserves_full_replay_commitment(self):
        self.store.append('resource.acquire', 'A', {'name': 'temporary', 'reason': 'fixture'})
        self.assert_same()
        self.store.append('resource.release', 'A', {'name': 'temporary'})
        self.assert_same()
        self.store.append('resource.acquire', 'A', {'name': 'new', 'reason': 'fixture'})
        self.assert_same()

    def test_readonly_current_view_preserves_all_persistent_bytes_and_mtimes(self):
        self.message(); expected = self.store.state(); before = self.snapshot()
        reader = registry.Store(self.root, read_only=True)
        try:
            self.assertEqual(cache.plain(reader.view()), expected)
        finally:
            reader.close()
        self.assertEqual(self.snapshot(), before)

    def test_missing_cache_readonly_falls_back_without_creating_projection(self):
        self.message(); expected = self.store.state()
        self.store.db.execute('DROP TABLE derived_state_meta')
        before = self.snapshot(); reader = registry.Store(self.root, read_only=True)
        try:
            self.assertEqual(cache.plain(reader.view()), expected)
        finally:
            reader.close()
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.root / 'state.json').exists())

    def test_foreign_invalid_and_wrong_head_metadata_fall_back_to_full_replay(self):
        self.message(); original = self.metadata(); expected = self.store.state()
        variants = [original | {'registry': '/another-registry'}, original | {'schema': -1},
                    original | {'head': self.store.events()[0]['hash']}, original | {'state': {'bad': True}}]
        for value in variants:
            with self.subTest(value=value):
                self.set_metadata(value)
                self.assertEqual(cache.plain(self.store.view()), expected)
        self.set_metadata(original, recompute=False)
        self.assertEqual(cache.plain(self.store.view()), expected)

    def test_corrupt_anchor_data_preserving_hash_is_not_accepted(self):
        self.message()
        self.store.db.execute('DROP TRIGGER no_update')
        self.store.db.execute("UPDATE events SET data='{}' WHERE seq=?", (self.metadata()['seq'],))
        with self.assertRaisesRegex(registry.Error, 'integrity'):
            cache.plain(self.store.view())
        with self.assertRaisesRegex(registry.Error, 'integrity'):
            self.store.append('settings', 'C', {'wait_seconds': {'ack': 10}})

    def test_verified_external_suffix_is_applied_and_readonly_does_not_save(self):
        self.message(); old = self.metadata()
        self.append_external('ack', 'A', {'message': 'm1'})
        expected = self.store.state(); before = self.snapshot()
        self.assertEqual(cache.plain(self.store.view()), expected)
        self.assertEqual(self.metadata(), old)
        self.assertEqual(self.snapshot(), before)
        self.store.append('settings', 'C', {'wait_seconds': {'ack': 10}})
        self.assert_same()

    def test_gap_in_external_suffix_is_refused_even_with_correct_hash(self):
        self.message()
        self.append_external('ack', 'A', {'message': 'm1'}, gap=1)
        with self.assertRaisesRegex(registry.Error, 'chain'):
            cache.plain(self.store.view())
        with self.assertRaisesRegex(registry.Error, 'chain'):
            self.store.append('settings', 'C', {'wait_seconds': {'ack': 10}})

    def test_tampered_demanded_leaf_falls_back_to_full_replay(self):
        self.message(); self.assertEqual(self.store.view()['messages']['m1']['body'], 'original')
        self.mutate_leaf(('messages', 'm1'), 'body')
        before = self.snapshot()
        try:
            body = self.store.view()['messages']['m1']['body']
        except Exception as exc:
            self.fail('A corrupt derived leaf must trigger authoritative fallback, not escape: ' + repr(exc))
        self.assertEqual(body, 'original')
        self.assertEqual(self.snapshot(), before)

    def test_tampered_write_dependency_falls_back_then_acknowledges_real_message(self):
        self.message(); self.mutate_leaf(('messages', 'm1'), 'to')
        try:
            self.store.append('ack', 'A', {'message': 'm1'})
        except Exception as exc:
            self.fail('A corrupt write dependency must rebuild then validate against authority: ' + repr(exc))
        self.assert_same()
        self.assertEqual(self.store.state()['messages']['m1']['ack']['agent'], 'A')

    def test_omitted_demanded_leaf_cannot_become_forged_absence(self):
        self.message(); namespace = cache.namespace(('messages', 'm1'))
        for row in self.store.db.execute('SELECT digest,data FROM derived_radix_nodes WHERE namespace=?', (namespace,)):
            body = json.loads(row['data'])
            if body.get('kind') == 'leaf' and body.get('key') == 'body':
                self.store.db.execute('DELETE FROM derived_radix_nodes WHERE namespace=? AND digest=?', (namespace, row['digest']))
                break
        else:
            self.fail('Missing positive leaf fixture')
        try:
            value = self.store.view()['messages']['m1'].get('body', 'false absence')
        except Exception as exc:
            self.fail('Omitted cached data must fall back: ' + repr(exc))
        self.assertEqual(value, 'original')

    def test_corrupt_enumeration_falls_back_with_every_real_member(self):
        self.message('m1', 'first'); self.message('m2', 'second')
        self.mutate_leaf(('messages', 'm1'), 'body')
        expected = self.store.state()['messages']
        try:
            actual = cache.plain(self.store.view()['messages'])
        except Exception as exc:
            self.fail('An enumeration cannot expose incomplete or corrupt derived values: ' + repr(exc))
        self.assertEqual(actual, expected)
        self.assertEqual(list(actual), ['m1', 'm2'])

    def test_full_replay_detects_old_row_tampering_outside_anchor(self):
        self.message(); self.store.append('ack', 'A', {'message': 'm1'})
        self.store.db.execute('DROP TRIGGER no_update')
        self.store.db.execute("UPDATE events SET data='{}' WHERE seq=1")
        with self.assertRaisesRegex(registry.Error, 'integrity'):
            self.store.events()
        with self.assertRaisesRegex(registry.Error, 'integrity'):
            self.store.state()

    def test_cache_fallback_never_accepts_a_corrupt_authoritative_old_row(self):
        self.message(); self.store.append('settings', 'C', {'wait_seconds': {'ack': 12}})
        self.mutate_leaf(('messages', 'm1'), 'to')
        self.store.db.execute('DROP TRIGGER no_update')
        self.store.db.execute("UPDATE events SET data='{}' WHERE seq=1")
        count = self.store.db.execute('SELECT count(*) FROM events').fetchone()[0]
        with self.assertRaisesRegex(registry.Error, 'integrity'):
            self.store.view()['messages']['m1']['to']
        with self.assertRaisesRegex(registry.Error, 'integrity'):
            self.store.append('ack', 'A', {'message': 'm1'})
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM events').fetchone()[0], count)

    def test_view_retains_one_snapshot_while_writer_appends(self):
        self.message(); old = self.store.state(); view = self.store.view()
        self.store.append('ack', 'A', {'message': 'm1'})
        self.assertEqual(cache.plain(view), old)
        self.assertEqual(cache.plain(self.store.view()), self.store.state())
        self.assertIsNone(view['messages']['m1']['ack'])

    def test_ack_reads_bounded_nodes_with_long_message_history(self):
        results = []
        for count in (10, 1000):
            root = self.root.parent / ('scale-' + str(count)); store = registry.Store(root, create=True)
            try:
                for event in self.store.events():
                    store.append(event['kind'], event['actor'], event['data'], event['id'])
                store.batch([('message', 'C', {'id': 'm'+str(i), 'to': 'A', 'body': 'x'}, None) for i in range(count)])
                calls = []; original = Index._load
                def counted(index, *args, **kwargs):
                    calls.append(index.namespace)
                    return original(index, *args, **kwargs)
                with patch.object(Index, '_load', counted):
                    store.append('ack', 'A', {'message': 'm0'})
                self.assertEqual(store.state()['messages']['m0']['ack']['agent'], 'A')
                results.append(len(calls))
            finally:
                store.close()
        print('ACK radix reads 10/1000 messages:', results)
        self.assertLess(results[1], 500, 'An ACK must not enumerate message history')
        self.assertLess(results[1], results[0] * 3, '100-fold historical growth must not drive ACK IO')


if __name__ == '__main__':
    unittest.main()
