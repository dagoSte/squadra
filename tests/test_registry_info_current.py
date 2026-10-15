"""Information keeps its receipt contract on current and legacy registry views."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import registry
import registry_cache
import registry_current
from squadra import dispatch, parser
import test_squadra as workflows


class NoHistoryScan(dict):
    def __iter__(self):
        raise AssertionError('message history enumerated')
    def values(self):
        raise AssertionError('message history values enumerated')
    def items(self):
        raise AssertionError('message history items enumerated')


class NoBody(dict):
    def __getitem__(self, key):
        if key == 'body':
            raise AssertionError('message body read for status/count')
        return super().__getitem__(key)
    def get(self, key, default=None):
        if key == 'body':
            raise AssertionError('message body read for status/count')
        return super().get(key, default)


class RegistryInformationSeams(unittest.TestCase):
    def setUp(self):
        self.f = workflows.Workflow()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store = self.f.store
        self.f.ack()

    def send(self, ident, **extra):
        self.store.append('message', 'C', dict(id=ident, to='A', body='BODY-COUNT-CANARY', **extra))

    def fixture(self):
        self.send('z-normal')
        self.send('a-false', info=False)
        self.send('m-info', info=True, related_task='T')
        self.send('b-info-ack', info=True)
        self.store.append('ack', 'A', {'message': 'b-info-ack'})

    def test_indexed_legacy_replay_and_voluntary_answer_agree(self):
        self.fixture()
        indexed, legacy = self.store.view(), self.store.state()
        self.assertEqual(registry.plain(indexed.current),
                         registry_current.build(self.store.events(), registry.apply, registry.initial))
        expected = ['z-normal', 'a-false']
        for value in (indexed, legacy):
            self.assertEqual([m['id'] for m in registry.pending_messages(value)], expected)
            self.assertEqual([m['id'] for m in registry.pending_messages(value, 'A')], expected)
            self.assertEqual(registry.information_count(value), 2)
            self.assertEqual(next(p for p in registry.presence(value, registry.now()) if p['agent'] == 'A')['last_answer'],
                             legacy['messages']['b-info-ack']['ack']['utc'])
        ids = list(registry_current.message_ids(indexed.current, recipient='A'))
        self.assertIn('m-info', ids)
        self.assertIn('b-info-ack', ids)
        self.assertIn('m-info', list(registry_current.message_ids(indexed.current, task='T')))
        self.assertEqual(registry.attention(indexed), registry.attention(legacy))
        for compact in (False, True):
            self.assertEqual(registry.table(indexed, compact=compact), registry.table(legacy, compact=compact))

    def test_information_count_and_compact_status_never_scan_message_history_or_bodies(self):
        self.fixture()
        source = self.store.view()
        class WithCurrent(dict):
            pass
        state = WithCurrent(source.items())
        state.current = source.current
        state['messages'] = NoHistoryScan({k: NoBody(registry.plain(v)) for k, v in source['messages'].items()})
        self.assertEqual(registry.information_count(state), 2)
        for compact in (True, False):
            text = registry.table(state, read_at=registry.now(), compact=compact)
            self.assertIn('Informazioni senza conferma richiesta: 2', text)
            self.assertNotIn('BODY-COUNT-CANARY', text)

    def test_old_derived_cache_is_not_reused_and_next_write_rebuilds(self):
        self.send('old-info', info=True)
        old_empty = registry_current.empty
        old_message = registry_current._message
        def before_info_empty():
            value = old_empty()
            value.pop('information_count')
            return value
        def before_info_message(indexes, state, name, order, *, created):
            proxy = dict(state)
            proxy['messages'] = dict(state['messages'])
            proxy['messages'][name] = {**proxy['messages'][name], 'info': False}
            return old_message(indexes, proxy, name, order, created=created)
        self.store.release_views()
        with patch.object(registry_cache, 'SCHEMA', 4), \
             patch.object(registry_current, 'empty', side_effect=before_info_empty), \
             patch.object(registry_current, '_message', side_effect=before_info_message):
            self.store._rebuild_cache()
        self.assertGreater(registry_cache.SCHEMA, 4)
        before = self.store.db.execute('SELECT body,digest FROM derived_state_meta').fetchone()
        value = self.store.view()
        self.assertIsNone(registry.current_index(value), 'old cache must use read-only verified fallback')
        self.assertEqual(registry.information_count(value), 1)
        self.assertEqual(list(registry.pending_messages(value)), [])
        self.assertEqual(tuple(self.store.db.execute('SELECT body,digest FROM derived_state_meta').fetchone()), tuple(before))
        self.store.append('message', 'C', dict(id='new-info', to='A', body='new', info=True))
        current = self.store.view()
        self.assertIsNotNone(registry.current_index(current))
        self.assertEqual(registry.information_count(current), 2)
        self.assertEqual(list(registry.pending_messages(current)), [])
        self.assertEqual(registry.plain(current.current),
                         registry_current.build(self.store.events(), registry.apply, registry.initial))

    def test_information_transport_and_both_exports_keep_the_same_receipt_contract(self):
        self.store.project()
        old_state = (self.store.root / 'state.json').read_bytes()
        self.send('notice', info=True)
        receipt = dispatch(parser().parse_args(['transport', '--actor', 'C', '--message', 'notice', '--via', 'manual']), self.store)
        self.assertIn('inbox', receipt['instruction'])
        self.assertIn('no ACK required', receipt['instruction'])
        self.assertNotIn('squadra-posta.md', receipt['instruction'])
        self.store.publish()
        incremental = (self.store.root / 'squadra-posta.md').read_text()
        self.assertIn('INFO', incremental)
        self.assertIn('No ACK required', incremental)
        self.assertEqual((self.store.root / 'state.json').read_bytes(), old_state)
        inbox = dispatch(parser().parse_args(['inbox', '--agent', 'A']), self.store)
        self.assertIs(next(m for m in inbox if m['id'] == 'notice')['info'], True)
        self.store.project()
        complete = (self.store.root / 'squadra-posta.md').read_text()
        self.assertIn('INFO', complete)
        self.assertIn('NOT REQUIRED', complete)
        self.assertEqual(registry.information_count(self.store.view()), 1)
