"""Mixed 3.10.1 windows share canonical cache bytes and one operational ACK list."""
import contextlib
import io
import json
from pathlib import Path
import unittest
from collections.abc import Mapping
from unittest.mock import patch

import test_msg_waits as support
import registry
import registry_cache
from comms import communications
from squadra import main


class Compatibility(unittest.TestCase):
    setUp = support.MessageWaits.setUp
    git = support.MessageWaits.git
    task = support.MessageWaits.task
    ack = support.MessageWaits.ack
    cli = support.MessageWaits.cli
    proof = support.MessageWaits.proof
    delivered = support.MessageWaits.delivered
    message = support.MessageWaits.message
    transport = support.MessageWaits.transport
    plan = support.MessageWaits.plan

    def test_closed_message_body_is_not_read_before_eligibility_filter(self):
        self.message(related_task='T')
        state = self.store.state(); state['tasks']['T']['status']='done'
        class Guard(Mapping):
            def __init__(self, row): self.row=row
            def __iter__(self): return iter(self.row)
            def __len__(self): return len(self.row)
            def __getitem__(self, key):
                if key=='body': raise AssertionError('closed message body was read')
                return self.row[key]
        state['messages']={key:Guard(row) for key,row in state['messages'].items()}
        self.assertEqual(registry.attention(state)['waiting'], [])
        view=registry.wait_view(state,registry.wait_facts(self.store.events()))
        self.assertEqual(registry.attention(view)['waiting'], [])

    def test_only_null_transport_stays_open_until_true_or_original_ack(self):
        self.ack(); self.message('NULL'); self.transport('NULL', delivered=None)
        rows = lambda: [w['id'] for w in registry.attention(self.store.view())['waiting'] if w['kind']=='to-forward']
        self.assertEqual(rows(), ['NULL'])
        self.transport('NULL', minute=2)
        self.assertEqual(rows(), ['NULL'])
        self.transport('NULL', delivered=True, minute=3)
        self.transport('NULL', delivered=None, minute=4)
        self.assertEqual(rows(), [])
        self.message('ACK'); self.transport('ACK', delivered=None)
        self.assertEqual(rows(), ['ACK'])
        self.store.append('ack', 'R', {'message':'ACK'})
        self.assertEqual(rows(), [])

    def test_optional_settings_keep_legacy_keys_and_ignore_unknown_read_keys(self):
        self.ack(); self.message(); self.transport(); self.plan()
        config = self.base / 'thresholds.json'
        config.write_text(json.dumps({'ack': 15, 'to-forward': 0, 'plan': 5}))
        self.assertEqual(self.cli('settings', '--actor', 'C', '--file', str(config)), 0)
        event = self.store.events()[-1]
        self.assertEqual(event['data']['wait_seconds'], {'ack': 15})
        self.assertEqual(event['data']['message_wait_seconds'], {'to-forward': 0, 'plan': 5})
        observed = registry.waits(self.store.state(), '2026-10-16T15:00:00+00:00')
        self.assertEqual(next(w['threshold'] for w in observed if w['kind'] == 'to-forward'), 0)
        self.assertEqual(next(w['threshold'] for w in observed if w['kind'] == 'plan'), 5)
        state = self.store.state(); state['workspace']['wait_seconds']['future-ignored'] = True
        self.assertTrue(registry.waits(state, '2026-10-16T15:00:00+00:00'))

    def test_independent_writers_keep_schema5_and_matching_canonical_roots(self):
        self.ack(); self.message(); self.transport(); self.plan()
        writer = registry.Store(self.root); self.addCleanup(writer.close)
        for index in range(3):
            with patch.object(writer, '_rebuild_cache', side_effect=AssertionError('No cross-connection rebuild')):
                writer.append('message', 'A', {'to': 'R', 'body': 'Synthetic independent writer', 'id': 'OTHER-' + str(index)})
            with patch.object(self.store, '_rebuild_cache', side_effect=AssertionError('No cross-version rebuild')):
                self.store.append('diagnostic', 'system', {'reason': 'new writer ' + str(index)})
            meta = json.loads(self.store.db.execute('SELECT body FROM derived_state_meta').fetchone()[0])
            self.assertEqual(meta['schema'], 5)
            self.assertEqual(registry_cache.SCHEMA, 5)
            self.assertEqual(self.store.audit()['cache'], 'matched')
            reader = registry.Store(self.root, read_only=True); self.addCleanup(reader.close)
            with patch.object(reader, 'state', side_effect=AssertionError('No reader replay')):
                rows = registry.attention(reader.view())['waiting']
            self.assertEqual(next(w['utc'] for w in rows if w['kind'] == 'to-forward'),
                             '2026-10-16T12:01:00+00:00')

    def test_overlay_is_optional_and_never_changes_legacy_reducer_or_cache(self):
        self.ack(); self.message(); self.transport(); self.transport(minute=2); self.plan()
        canonical = self.store.normalized_state(self.store.events())
        self.assertNotIn('transport_first_utc', canonical['messages']['ORIGINAL'])
        self.assertNotIn('plan_utc', canonical['tasks']['T'])
        self.assertNotIn('message_wait_seconds', canonical['workspace'])
        self.assertEqual(self.store.audit()['cache'], 'matched')
        before = self.store.db.total_changes
        with patch.object(self.store, 'state', side_effect=AssertionError('No full-history replay')):
            value = self.store.view()
        self.assertEqual(self.store.db.total_changes, before)
        self.assertEqual(value['messages']['ORIGINAL']['transport_first_utc'], '2026-10-16T12:01:00+00:00')
        self.assertEqual(value['tasks']['T']['plan_utc'], '2026-10-16T12:00:00+00:00')
        with self.assertRaises(registry.Error):
            self.store.append('settings', 'C', {'wait_seconds': {'to-forward': 0}})
        for invalid in [True, -1, 0.5]:
            with self.assertRaises(registry.Error):
                self.store.append('settings', 'C', {'wait_seconds': {}, 'message_wait_seconds': {'plan': invalid}})

    def test_doctor_status_and_comms_use_attention_without_fabricating_ack(self):
        self.message(related_task='T'); self.message('UNLINKED')
        self.delivered()
        self.assertEqual(self.cli('review', '--actor', 'R', '--task', 'T', '--verdict', 'accepted', '--report', str(self.report)), 0)
        self.assertEqual(self.cli('close', '--actor', 'C', '--task', 'T'), 0)
        state = self.store.state()
        self.assertEqual([w['id'] for w in registry.attention(state)['waiting'] if w['kind']=='ack'], ['UNLINKED'])
        self.assertIsNone(state['messages']['ORIGINAL']['ack'])
        text = registry.table(state, '2026-10-17T00:00:00+00:00', compact=True)
        self.assertIn('Messaggi senza ACK: 1.', registry.table(state, '2026-10-17T00:00:00+00:00'))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(['--root', str(self.root), 'doctor']), 0)
        self.assertIn('"pending_ack": [\n    "UNLINKED"\n  ]', output.getvalue())
        at = '2026-10-17T00:00:00+00:00'
        view = communications(state, self.store.events(), registry.waits(state, at), at)
        pending = [edge['message'] for edge in view['edges'] if edge.get('needs_ack')]
        self.assertEqual(pending, ['UNLINKED'])
        self.assertEqual(sum(pair['pending'] for pair in view['pairs']), 1)
        original = next(e for e in view['edges'] if e.get('message') == 'ORIGINAL' and e['kind'] == 'message')
        self.assertEqual(original['state'], 'sent')
