"""Conditional transfers retain complete evidence and invalidate on real change."""
import json
from copy import deepcopy
from unittest.mock import patch
import unittest
from pathlib import Path
import shutil
import subprocess
import tempfile

import test_dashboard_performance as fixtures
import dashboard
import test_panel as panel


class DashboardTransfer(unittest.TestCase):
    setUp = fixtures.DashboardPerformance.setUp
    stop_server = fixtures.DashboardPerformance.stop_server
    get = fixtures.DashboardPerformance.get
    state = fixtures.DashboardPerformance.state
    stamp = fixtures.DashboardPerformance.stamp
    task_id = fixtures.DashboardPerformance.task_id
    run_id = fixtures.DashboardPerformance.run_id
    source_path = fixtures.DashboardPerformance.source_path
    source_hash = fixtures.DashboardPerformance.source_hash

    def compact(self, token='', **headers):
        status, _, raw = self.get(headers={'X-Squadra-View': 'compact',
                                           'X-Squadra-State-Token': token, **headers})
        self.assertEqual(status, 200, raw)
        return json.loads(raw), len(raw)

    def detail(self, state, section, record=''):
        status, _, raw = self.get('/api/detail/view', headers={
            'X-Squadra-State-Head': state['registry_head']['hash'],
            'X-Squadra-Section': section, 'X-Squadra-Record': record})
        return status, json.loads(raw)

    def test_idle_response_is_small_and_state_is_retained(self):
        self.state(**{'X-Squadra-View': 'compact', 'X-Squadra-State-Token': ''})
        first, size = self.compact()
        self.assertIn('change_token', first)
        again, idle_size = self.compact(first['change_token'])
        self.assertEqual(again['not_modified'], True)
        self.assertLess(idle_size, 300)
        self.assertGreater(size, idle_size * 10)
        self.assertNotIn('tasks', again)

    def test_second_writer_invalidates_the_token(self):
        first, _ = self.compact()
        other = dashboard.Store(self.root)
        try:
            other.append('message', 'C', {'id': 'NEW', 'to': 'A', 'body': 'Fresh event'})
        finally:
            other.close()
        changed, _ = self.compact(first['change_token'])
        self.assertIn('NEW', changed['messages'])
        self.assertNotEqual(first['change_token'], changed['change_token'])
        self.assertEqual(self.detail(first, 'mail')[0], 409)

    def test_omitted_bodies_and_history_are_recoverable(self):
        body = 'Short title\n' + 'Deep evidence canary. ' * 300
        self.store.append('message', 'C', {'id': 'LONG', 'to': 'A', 'body': body,
                                          'related_task': self.task_id})
        state, _ = self.compact()
        self.assertLess(len(state['messages']['LONG']['body']), len(body))
        self.assertTrue(state['messages']['LONG']['detail_pending'])
        self.assertNotIn('proofs', state['tasks'][self.task_id]['delivery'])
        self.assertTrue(all(not event['data'] for event in state['events']))
        code, detail = self.detail(state, 'mail')
        self.assertEqual(code, 200)
        self.assertEqual(detail['patch']['messages']['LONG']['body'], body)
        code, detail = self.detail(state, 'history')
        self.assertEqual(code, 200)
        self.assertEqual(detail['patch']['events'][-1]['data']['body'], body)

    def test_outputs_and_story_recover_text_and_artifact_links(self):
        body = 'Output title\n' + 'Recorded output evidence. ' * 100
        self.store.append('output', 'A', {'task': self.task_id, 'text': body,
                                         'artifacts': [self.artifact], 'next': 'Review'})
        state, _ = self.compact()
        self.assertNotEqual(state['outputs'][-1]['text'], body)
        self.assertEqual(state['outputs'][-1]['artifacts'], [])
        for section, record in [('outputs', ''), ('story', self.task_id)]:
            code, detail = self.detail(state, section, record)
            self.assertEqual(code, 200)
            self.assertEqual(detail['patch']['outputs'][-1]['text'], body)
            self.assertEqual(detail['patch']['outputs'][-1]['artifacts'], [self.artifact])

    def test_search_finds_text_not_transferred_in_preview(self):
        self.store.append('message', 'C', {'id': 'LONG', 'to': 'A',
                                          'body': 'Title\n' + 'x' * 500 + ' invisibleNeedle'})
        first, _ = self.compact()
        found, _ = self.compact(first['change_token'], **{'X-Squadra-Search': 'invisibleneedle'})
        self.assertFalse(found.get('not_modified', False))
        self.assertEqual(found['search']['messages'], ['LONG'])

    def test_wait_threshold_changes_without_an_event(self):
        with patch('registry.now', return_value='2026-10-12T12:00:03+00:00'):
            self.store.append('message', 'C', {'id': 'WAIT', 'to': 'A', 'body': 'Waiting'})
        with patch.object(dashboard, 'now', return_value='2026-10-12T12:15:02+00:00'):
            before, _ = self.compact()
        with patch.object(dashboard, 'now', return_value='2026-10-12T12:15:03+00:00'):
            after, _ = self.compact(before['change_token'])
        self.assertNotEqual(before['change_token'], after['change_token'])
        self.assertTrue(any(w['id'] == 'WAIT' for w in after['waits']))

    def test_presence_threshold_and_activity_bucket_invalidate(self):
        self.store.append('message', 'C', {'id': 'PING', 'to': 'A', 'body': 'Ping',
                                          'ping': True, 'availability_event': None})
        self.store.append('transport', 'C', {'message': 'PING', 'via': 'codex-queue', 'delivered': True})
        with patch.object(dashboard, 'now', return_value='2026-10-12T12:04:59+00:00'):
            before, _ = self.compact()
        with patch.object(dashboard, 'now', return_value='2026-10-12T12:05:00+00:00'):
            after, _ = self.compact(before['change_token'])
            self.assertTrue(next(p for p in after['presence'] if p['agent'] == 'A')['due'])
        with patch.object(dashboard, 'now', return_value='2026-10-12T12:15:00+00:00'):
            later, _ = self.compact(after['change_token'])
        self.assertNotEqual(after['activity']['since'], later['activity']['since'])

    def test_overdue_delivery_keeps_conditional_http_available(self):
        with patch.object(dashboard, 'now', return_value='2026-10-12T14:00:00+00:00'):
            self.state(**{'X-Squadra-View': 'compact', 'X-Squadra-State-Token': ''})
            first, _ = self.compact()
            self.assertTrue(any(w['kind'] == 'delivered' and w['task'] == self.task_id
                                for w in first['waits']))
            again, _ = self.compact(first['change_token'])
        self.assertTrue(again['not_modified'])

    def test_signature_covers_identity_of_every_real_wait_kind(self):
        self.store.append('message', 'C', {'id': 'PING', 'to': 'A', 'body': 'Ping',
                                          'ping': True, 'availability_event': None})
        self.store.append('transport', 'C', {'message': 'PING', 'via': 'codex-queue', 'delivered': True})
        state = self.store.state()
        reviewed = deepcopy(state['tasks'][self.task_id])
        reviewed.update(id='REVIEWED', status='reviewed', coordinator='C')
        state['tasks']['REVIEWED'] = reviewed
        for key, status in [('RUNNING', 'running'), ('CLEANUP', 'cleanup-unknown')]:
            state['runs'][key] = {'id': key, 'task': self.task_id, 'agent': 'A',
                                  'mode': 'proof', 'status': status, 'utc': self.stamp}
        waiting = dashboard.waits(state, '2026-10-12T14:00:00+00:00')
        self.assertEqual({w['kind'] for w in waiting},
                         {'ack', 'delivered', 'reviewed', 'cleanup-unknown', 'heartbeat', 'alive'})
        signature = lambda rows: dashboard.transfer_signature('head', '', {}, rows, [], {})
        token = signature(waiting)
        self.assertEqual(token, signature(dashboard.waits(state, '2026-10-12T14:00:01+00:00')))
        for index, row in enumerate(waiting):
            for field in ('id', 'task', 'who'):
                if field not in row:
                    continue
                with self.subTest(kind=row['kind'], identity=field):
                    changed = deepcopy(waiting)
                    changed[index][field] = ['OTHER'] if field == 'who' else 'OTHER'
                    self.assertNotEqual(token, signature(changed))

    def test_plugin_change_invalidates_without_a_registry_event(self):
        with patch.object(dashboard.plugin_actions, 'discover', return_value={'installed': []}):
            before = self.state(**{'X-Squadra-View': 'compact', 'X-Squadra-State-Token': ''})
            self.assertEqual(before['audit'], {'status': 'complete'})
            same, _ = self.compact(before['change_token'])
            self.assertTrue(same['not_modified'])
        with patch.object(dashboard.plugin_actions, 'discover', return_value={'installed': ['new']}):
            after, _ = self.compact(before['change_token'])
        self.assertNotEqual(before['change_token'], after['change_token'])
        self.assertEqual(before['registry_head'], after['registry_head'])
        self.assertEqual(after['plugins']['installed'], ['new'])

    def test_corrupt_registry_does_not_return_cached_success(self):
        before, _ = self.compact()
        self.store.db.execute('DROP TRIGGER no_update')
        self.store.db.execute("UPDATE events SET hash=? WHERE seq=1", ('0' * 64,))
        self.store.db.commit()
        status, _, _ = self.get(headers={'X-Squadra-View': 'compact',
                                         'X-Squadra-State-Token': before['change_token']})
        self.assertEqual(status, 409)

    def test_detail_allowlist_and_authentication(self):
        first, _ = self.compact()
        self.assertEqual(self.detail(first, 'unknown')[0], 400)
        code, _, _ = self.get('/api/detail/view', authenticated=False)
        self.assertEqual(code, 401)


class DashboardTransferPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='sq-transfer-page-')
        cls.fixture = Path(cls.temp.name) / 'state.json'
        cls.fixture.write_text(json.dumps(panel.PanelJavaScript.build(Path(cls.temp.name))))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def contract(self, scenario):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node required')
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([node, str(root / 'tests/dashboard_transfer_contract.js'),
                                 str(root / 'assets/dashboard.html'), str(self.fixture), scenario],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('transfer contract passed: ' + scenario, result.stdout)

    def test_unchanged_keeps_state_and_external_polling(self):
        self.contract('unchanged')

    def test_pages_and_story_recover_full_details(self):
        self.contract('details')

    def test_stale_detail_reply_is_discarded(self):
        self.contract('stale')

    def test_detail_failure_and_retry(self):
        self.contract('retry')

    def test_story_failure_offers_scoped_retry(self):
        self.contract('story_retry')

    def test_story_response_does_not_reopen_after_navigation(self):
        self.contract('story_navigation')

    def test_latest_story_selection_wins(self):
        self.contract('story_latest')

    def test_closing_pending_story_restores_focus_and_stays_closed(self):
        self.contract('story_close')


if __name__ == '__main__':
    unittest.main()
