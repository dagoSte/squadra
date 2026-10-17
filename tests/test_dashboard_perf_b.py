"""The dashboard reads only new events, answers idle polls cheaply, sends deltas, and keeps its guards."""
import http.client
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from test_review_pool_gate_refusals import Fixture  # first: its support import puts scripts/ on the path
import dashboard
from registry import Error, Store, canonical, digest, now
from squadra import deliver
import test_dashboard_performance as performance
import test_panel as panel


def exact(value):
    return json.dumps(value, ensure_ascii=False, default=str)


class Oracle(Fixture, unittest.TestCase):
    """After every committed batch the incremental view must equal a full read, key order included."""

    def setUp(self):
        Fixture.setUp(self)
        self.view = dashboard.VerifiedView(self.root)
        self.addCleanup(self.view.close)
        self.view.read()
        self.checked, self.partial = [], []
        original, refreshed = Store._batch_owned, dashboard.refreshed

        def counted(*args, **kwargs):
            self.partial.append(1)
            return refreshed(*args, **kwargs)

        def watched(store, operations, recovering=False):
            result = original(store, operations, recovering)
            if Path(store.root) == Path(self.root).resolve():
                self.compare([kind for kind, *_ in operations])
            return result
        for target, replacement in ((Store, ('_batch_owned', watched)), (dashboard, ('refreshed', counted))):
            patcher = patch.object(target, *replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def compare(self, kinds):
        events, state, compact = self.view.read()
        fresh = dashboard.VerifiedView(self.root)
        try:
            full_events, full_state, full_compact = fresh.read()
        finally:
            fresh.close()
        self.assertEqual([e['hash'] for e in events], [e['hash'] for e in full_events], kinds)
        self.assertEqual(exact(state), exact(full_state), kinds)
        self.assertEqual(exact(compact), exact(full_compact), kinds)
        self.checked.extend(kinds)

    def cli(self, *args):
        rc, err = self.run_cli(*args)
        self.assertEqual(rc, 0, (args, err))

    def test_every_event_kind_keeps_the_incremental_view_equal_to_a_full_read(self):
        self.ack()
        self.full_task('W', reviewer='R')
        self.store.append('start', 'A', {'task': 'W'})
        self.cli('plan', '--actor', 'A', '--task', 'W', '--file', str(self.scope))
        self.cli('review', '--actor', 'R', '--task', 'W', '--stage', 'plan', '--verdict', 'accepted', '--report', str(self.report))
        self.proved('W')
        handoff = deliver(self.store, 'A', 'W')['handoff']
        self.store.append('ack', 'R', {'message': handoff})
        self.cli('review', '--actor', 'R', '--task', 'W', '--stage', 'code', '--verdict', 'accepted', '--report', str(self.report))
        self.cli('close', '--actor', 'C', '--task', 'W')
        handoffs = self.pool_delivered('P')
        self.store.append('ack', 'R2', {'message': handoffs[1]})
        self.closed_at_gate('L1')
        self.gate_delivered('G')
        self.gate_read('G')
        self.cli('decision', '--actor', 'C', '--defer-read', 'L1', '--gate', 'G', '--reason', 'Docs only; next gate')
        self.full_task('S1')
        self.full_task('S2')
        self.cli('supersede', '--actor', 'C', '--task', 'S1', '--by', 'S2', '--reason', 'Replaced')
        options = Path(self.base) / 'options.json'
        options.write_text(json.dumps([{'id': 'A', 'label': 'Keep', 'say': 'Keep it'}, {'id': 'B', 'label': 'Drop', 'say': 'Drop it'}]))
        for ask in ('Q1', 'Q2'):
            self.cli('ask', '--actor', 'C', '--id', ask, '--question', ask + '?', '--options-file', str(options))
        self.cli('decision', '--actor', 'C', '--answers', 'Q1', '--choice', 'A', '--channel', 'typed-here',
                 '--owner-words', 'Keep it', '--reason', 'Owner chose')
        self.cli('decision', '--actor', 'C', '--withdraws', 'Q2', '--reason', 'No longer needed')
        body = Path(self.base) / 'body.txt'
        body.write_text('Synthetic note\n')
        self.cli('send', '--actor', 'C', '--to', 'R', '--id', 'M1', '--body-file', str(body))
        self.cli('transport', '--actor', 'C', '--message', 'M1', '--via', 'manual')
        self.cli('send', '--actor', 'C', '--to', 'R', '--info', '--body-file', str(body))
        settings = Path(self.base) / 'settings.json'
        settings.write_text(json.dumps({'to-forward': 600, 'plan': 1200}))
        self.cli('settings', '--actor', 'C', '--file', str(settings))
        self.cli('standby', '--actor', 'C', '--agent', 'R3', '--reason', 'Away')
        self.cli('standby', '--actor', 'C', '--agent', 'R3', '--resume', '--reason', 'Back')
        for kind in ('ack', 'start', 'plan', 'review', 'run.start', 'run.end', 'deliver', 'close', 'decision',
                     'supersede', 'ask', 'message', 'transport', 'settings', 'availability', 'task'):
            self.assertIn(kind, self.checked)
        self.assertTrue(self.partial, 'the incremental path must actually run')


class Reads(Fixture, unittest.TestCase):
    def view(self):
        view = dashboard.VerifiedView(self.root)
        self.addCleanup(view.close)
        return view

    def full(self):
        fresh = dashboard.VerifiedView(self.root)
        try:
            return fresh.read()
        finally:
            fresh.close()

    def test_a_returned_snapshot_is_never_changed_by_a_later_read(self):
        view = self.view()
        view.read()
        self.ack()
        _, first, first_compact = view.read()
        before = exact(first), exact(first_compact)
        for name in ('X1', 'X2'):
            self.full_task(name)
        self.store.append('start', 'A', {'task': 'X1'})
        view.read()
        self.assertEqual((exact(first), exact(first_compact)), before)

    def test_a_changed_read_never_materializes_the_whole_view(self):
        view = self.view()
        view.read()
        self.ack()
        view.read()
        whole = []
        original = dashboard.plain

        def tracked(value):
            if hasattr(value, 'keys') and 'event_count' in value:
                whole.append(1)
            return original(value)
        self.store.append('start', 'A', {'task': 'T'})
        with patch.object(dashboard, 'plain', tracked):
            view.read()
        self.assertEqual(whole, [])

    def test_a_changed_read_derives_again_only_the_records_it_changed(self):
        view = self.view()
        view.read()
        self.ack()
        for name in ('X1', 'X2'):
            self.full_task(name)
        view.read()
        derived = []

        def counting(name):
            original = getattr(dashboard, name)
            def call(value, *args, **kwargs):
                if isinstance(value, dict) and value.get('id') in ('X1', 'X2'):
                    derived.append((name, value['id']))
                return original(value, *args, **kwargs)
            return call
        with patch.multiple(dashboard, **{name: counting(name) for name in ('requirement_matrix', 'compact_records')}):
            self.store.append('message', 'C', {'id': 'NOTE', 'to': 'A', 'body': 'Unrelated'})
            view.read()
            self.assertEqual(derived, [], 'a message derives no task again')
            self.store.append('start', 'A', {'task': 'X1'})
            view.read()
        self.assertEqual(sorted(set(derived)), [('compact_records', 'X1'), ('requirement_matrix', 'X1')])
        self.assertEqual(exact(view.read()[1:]), exact(self.full()[1:]))

    def test_a_non_format_two_suffix_and_a_count_mismatch_take_the_full_read(self):
        view = self.view()
        view.read()
        self.ack()
        view.read()
        last = self.store.events()[-1]
        event = {'id': 'legacy-row', 'utc': now(), 'kind': 'message', 'actor': 'C', 'previous': last['hash'],
                 'data': {'id': 'LEGACY', 'to': 'A', 'body': 'Old writer'}}
        self.store.db.execute('INSERT INTO events(seq,id,utc,kind,actor,data,previous,hash,hash_format) VALUES(?,?,?,?,?,?,?,?,?)',
                              (last['seq'] + 1, event['id'], event['utc'], 'message', 'C', canonical(event['data']),
                               last['hash'], digest(canonical(event).encode()), None))
        with patch.object(dashboard, 'refreshed', side_effect=AssertionError('legacy suffix read incrementally')):
            _, state, _ = view.read()
        self.assertEqual(exact(state), exact(self.full()[1]))
        self.store.close()
        self.store = Store(self.root)
        self.store.append('message', 'C', {'id': 'NEXT', 'to': 'A', 'body': 'New writer'})
        original = dashboard.refreshed
        with patch.object(dashboard, 'refreshed', lambda *a: {**original(*a), 'event_count': -1}):
            _, state, _ = view.read()
        self.assertEqual(exact(state), exact(self.full()[1]))

    def test_an_unchanged_tip_never_restarts_the_audit(self):
        view = self.view()
        view.read()
        queued = []
        with patch.object(dashboard.VerifiedView, '_queue_audit', lambda self, identity: queued.append(identity)):
            other = sqlite3.connect(self.root / 'registry.sqlite3')
            other.execute('PRAGMA user_version = 7')
            other.close()
            first = view.read()
            self.assertIs(view.read(), first)
        self.assertEqual(queued, [])


class Transfer(unittest.TestCase):
    setUp = performance.DashboardPerformance.setUp
    stop_server = performance.DashboardPerformance.stop_server
    get = performance.DashboardPerformance.get
    state = performance.DashboardPerformance.state
    stamp = performance.DashboardPerformance.stamp
    task_id = performance.DashboardPerformance.task_id
    run_id = performance.DashboardPerformance.run_id
    source_path = performance.DashboardPerformance.source_path
    source_hash = performance.DashboardPerformance.source_hash

    def compact(self, token, **headers):
        status, _, raw = self.get(headers={'X-Squadra-View': 'compact', 'X-Squadra-State-Token': token, **headers})
        self.assertEqual(status, 200, raw)
        return json.loads(raw), len(raw)

    def test_an_idle_poll_reuses_its_membership_until_something_can_cross(self):
        first = self.state(**{'X-Squadra-View': 'compact', 'X-Squadra-State-Token': ''})
        counts = {'waits': 0, 'presence': 0, 'activity': 0}

        def counting(name):
            original = getattr(dashboard, name)
            def call(*args, **kwargs):
                counts[name] += 1
                return original(*args, **kwargs)
            return call
        with patch.multiple(dashboard, **{name: counting(name) for name in counts}):
            for _ in range(3):
                self.assertTrue(self.compact(first['change_token'])[0]['not_modified'])
            self.assertEqual(counts, {'waits': 0, 'presence': 0, 'activity': 0})
            with patch.object(dashboard, 'now', return_value='2026-10-12T12:15:00+00:00'):
                self.compact(first['change_token'])
            self.assertGreater(counts['activity'], 0, 'a 15-minute bucket boundary recomputes')

    def test_a_delta_reply_carries_only_the_change_and_a_search_only_its_results(self):
        first = self.state(**{'X-Squadra-View': 'compact', 'X-Squadra-State-Token': ''})
        full_size = len(json.dumps(first))
        self.store.append('message', 'C', {'id': 'NEW', 'to': 'A', 'body': 'A new message'})
        reply, size = self.compact(first['change_token'], **{'X-Squadra-Delta': '1'})
        self.assertIn('delta', reply)
        self.assertEqual(reply['base_token'], first['change_token'])
        self.assertEqual(reply['registry_head']['seq'], first['registry_head']['seq'] + 1)
        self.assertIn('NEW', reply['delta']['maps']['messages']['set'])
        self.assertLess(size, full_size)
        searched, _ = self.compact(reply['change_token'], **{'X-Squadra-Delta': '1', 'X-Squadra-Search': 'inspect'})
        self.assertEqual(set(searched['delta']['set']), {'search'})
        self.assertEqual((searched['delta']['maps'], searched['delta']['lists'], searched['delta']['remove']), ({}, {}, []))

    def settled(self):
        for _ in range(200):
            value = self.state(**{'X-Squadra-View': 'compact', 'X-Squadra-State-Token': ''})
            if value.get('audit', {}).get('status') == 'complete':
                return value
            time.sleep(.01)
        self.fail('the synthetic audit did not complete')

    def search(self, token, query, at, **headers):
        with patch.object(dashboard, 'now', return_value=at):
            return self.compact(token, **{'X-Squadra-Delta': '1', 'X-Squadra-Search': query, **headers})[0]

    def test_a_query_change_reuses_the_landing_until_a_threshold_or_a_bucket(self):
        self.store.append('message', 'C', {'id': 'PING', 'to': 'A', 'body': 'Ping', 'ping': True, 'availability_event': None})
        self.store.append('transport', 'C', {'message': 'PING', 'via': 'codex-queue', 'delivered': True})
        first = self.settled()
        reused = self.search(first['change_token'], 'inspect', '2026-10-12T12:00:02+00:00')
        self.assertEqual(set(reused['delta']['set']), {'search'}, 'only the results travel two seconds later')
        self.assertEqual(reused['server_time'], '2026-10-12T12:00:02+00:00', 'the reply carries the current time')
        crossed = self.search(reused['change_token'], 'ping', '2026-10-12T12:05:00+00:00')
        self.assertLessEqual({'presence', 'waits', 'server_time', 'search'}, set(crossed['delta']['set']),
                             'an unanswered ping crossing its threshold recomputes')
        self.assertEqual(crossed['delta']['set']['server_time'], '2026-10-12T12:05:00+00:00')
        bucket = self.search(crossed['change_token'], 'inspect', '2026-10-12T12:15:00+00:00')
        self.assertIn('activity', bucket['delta']['set'], 'a 15-minute bucket recomputes')
        self.assertEqual(bucket['delta']['set']['server_time'], '2026-10-12T12:15:00+00:00')

    def test_a_held_landing_is_reused_only_with_the_same_head_audit_plugins_and_membership(self):
        first = self.settled()
        view = dashboard.VerifiedView(self.root)
        self.addCleanup(view.close)
        view.replies = {'held': first}
        head, audit, plugins_value = first['registry_head']['hash'], first['audit'], first['plugins']
        held = (first['waits'], first['presence'], first['activity'])
        self.assertIs(view.search_base('held', head, audit, plugins_value, held), first)
        moved = [{**row, 'due': not row['due']} for row in first['presence']]
        for args in (('other', head, audit, plugins_value, held), (None, head, audit, plugins_value, held),
                     ('held', 'f' * 64, audit, plugins_value, held), ('held', head, {**audit, 'status': 'running'}, plugins_value, held),
                     ('held', head, audit, {**plugins_value, 'key': 'other'}, held), ('held', head, audit, plugins_value, None),
                     ('held', head, audit, plugins_value, (first['waits'], moved, first['activity']))):
            with self.subTest(args=[type(a).__name__ for a in args]):
                self.assertIsNone(view.search_base(*args))

    def test_a_whole_reply_is_computed_when_it_is_sent(self):
        first = self.settled()
        reused = self.search(first['change_token'], 'inspect', '2026-10-12T12:00:02+00:00')
        self.assertEqual(set(reused['delta']['set']), {'search'})
        for token, headers in ((reused['change_token'], {}), ('f' * 64, {'X-Squadra-Delta': '1'})):
            with patch.object(dashboard, 'now', return_value='2026-10-12T12:00:04+00:00'):
                whole, _ = self.compact(token, **{'X-Squadra-Search': 'other', **headers})
            self.assertNotIn('delta', whole)
            self.assertEqual((whole['server_time'], whole['comms']['as_of']),
                             ('2026-10-12T12:00:04+00:00', '2026-10-12T12:00:04+00:00'))

    def test_without_the_delta_header_or_with_an_unknown_token_the_reply_is_whole(self):
        first = self.state(**{'X-Squadra-View': 'compact', 'X-Squadra-State-Token': ''})
        self.store.append('message', 'C', {'id': 'NEW', 'to': 'A', 'body': 'A new message'})
        for headers in ({}, {'X-Squadra-Delta': '1'}):
            token = first['change_token'] if not headers else 'f' * 64
            reply, _ = self.compact(token, **headers)
            self.assertNotIn('delta', reply)
            self.assertIn('NEW', reply['messages'])

    def test_loopback_hosts_are_served_and_others_and_a_non_ascii_token_are_refused(self):
        port = str(self.server.server_port)
        for host, expected in (('localhost:' + port, 200), ('[::1]:' + port, 200), ('evil.example:' + port, 403)):
            status, _, _ = self.get(headers={'Host': host, 'X-Squadra-View': 'compact'})
            self.assertEqual(status, expected, host)
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.putrequest('GET', '/api/state', skip_host=True)
            connection.putheader('Host', '127.0.0.1:' + port)
            connection.putheader('X-Squadra-Token', 'caffè'.encode('latin-1'))
            connection.endheaders()
            self.assertEqual(connection.getresponse().status, 401)
        finally:
            connection.close()
        self.assertEqual(self.get(headers={'X-Squadra-View': 'compact'})[0], 200, 'the server still answers')

    def test_the_complete_view_is_bounded(self):
        self.state()
        with patch.object(dashboard, 'FULL_FILES_LIMIT', 1):
            status, _, body = self.get()
        self.assertEqual((status, json.loads(body)), (409, {'error': 'full_state_too_large'}))
        self.assertEqual(self.get(headers={'X-Squadra-View': 'compact'})[0], 200)


class Page(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='sq-dash-perf-page-')
        cls.fixture = Path(cls.temp.name) / 'state.json'
        cls.fixture.write_text(json.dumps(panel.PanelJavaScript.build(Path(cls.temp.name))))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def contract(self, scenario):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required; JS contracts cannot be skipped')
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([node, str(root / 'tests/dashboard_perf_b_contract.js'), str(root / 'assets/dashboard.html'),
                                 str(self.fixture), scenario], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('dashboard perf contract passed: ' + scenario, result.stdout)

    def test_a_delta_rebuilds_the_new_landing(self):
        self.contract('delta')

    def test_a_delta_that_fails_is_replaced_by_a_whole_reply(self):
        self.contract('fallback')

    def test_only_a_registry_failure_stops_polling(self):
        self.contract('conflict')

    def test_history_search_and_pairs_name_records_as_the_server_does(self):
        self.contract('names')

    def test_every_record_id_stays_an_own_key_through_deltas_and_detail_patches(self):
        self.contract('ownkeys')

    def test_a_reused_landing_ages_from_the_time_it_was_computed(self):
        self.contract('aging')


if __name__ == '__main__':
    unittest.main()
