"""Operational waits keep forwarding visible without inventing delivery or ACKs."""
from copy import deepcopy
import datetime as dt
import io
import contextlib
import json
from pathlib import Path
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import registry
import registry_cache
from registry import attention, waits, table, Store, Error
from squadra import main
import test_squadra as support
import test_dashboard_transfer as transfer
from test_usage_page import state_fixture

STAMP = '2026-10-16T12:00:00+00:00'


@contextlib.contextmanager
def codex_seats(*seats):
    """Register these seats as Codex windows from their first registration: only a Codex recipient's mail
    waits on forwarding, and a seat's client cannot be rewritten later."""
    original = Store.append

    def append(store, kind, actor, data, event_id=None):
        if kind == 'agent' and data.get('id') in seats:
            data = {**data, 'client': 'codex', 'session': 'thread-' + data['id']}
        return original(store, kind, actor, data, event_id)

    with patch.object(Store, 'append', append):
        yield


class MessageWaits(unittest.TestCase):
    git = support.Workflow.git

    def setUp(self):
        with codex_seats('R', 'A-alias'):
            support.Workflow.setUp(self)

    task = support.Workflow.task
    ack = support.Workflow.ack
    cli = support.Workflow.cli
    proof = support.Workflow.proof
    delivered = support.Workflow.delivered

    def message(self, name='ORIGINAL', to='R', **kwargs):
        with patch('registry.now', return_value=STAMP):
            self.store.append('message', 'A', {'id': name, 'to': to, 'body': 'Read the submitted plan', **kwargs})

    def transport(self, name='ORIGINAL', delivered=False, minute=1):
        stamp = (dt.datetime.fromisoformat(STAMP) + dt.timedelta(minutes=minute)).isoformat()
        with patch('registry.now', return_value=stamp):
            self.store.append('transport', 'A', {'message': name, 'via': 'manual', 'delivered': delivered})
        return stamp

    def forward(self, state=None):
        return [w for w in attention(state or self.store.state())['waiting'] if w['kind'] == 'to-forward']

    def plan(self, minute=0, text='Plan one'):
        path = self.base / 'plan.md'; path.write_text(text)
        stamp = (dt.datetime.fromisoformat(STAMP) + dt.timedelta(minutes=minute)).isoformat()
        with patch('registry.now', return_value=stamp):
            self.store.append('plan', 'A', {'task': 'T', 'artifact': self.store.artifact(path)})
        return stamp

    def test_manual_false_plan_read_is_first_and_ends_at_original_ack(self):
        self.ack(); self.plan()
        self.store.append('agent', 'B', {'id': 'B', 'role': 'reviewer', 'identity': 'second-reader', 'client': 'codex', 'repo': str(self.repo)})
        for recipient in ('R', 'B'):
            with self.subTest(recipient=recipient):
                name = 'ORIGINAL-' + recipient
                self.message(name, recipient, related_task='T')
                first = self.transport(name)
                state = self.store.state(); rows = self.forward(state)
                self.assertEqual([w['id'] for w in rows], [name], 'a manual-false plan request is to forward')
                self.assertEqual(rows[0]['who'], ['C'])
                self.assertEqual(rows[0]['to'], recipient)
                self.assertEqual(rows[0]['utc'], first)
                command = rows[0].get('ack_command', '')
                self.assertEqual(shlex.split(command), ['squadra', 'ack', '--actor', recipient, '--message', name, '--root', str(self.root)])
                clock = dt.datetime.fromisoformat(first) + dt.timedelta(seconds=120)
                text = table(state, read_at=clock, compact=True)
                shown = [line for line in text.splitlines() if line.startswith('- ')]
                self.assertTrue(shown[0].startswith('- to-forward · ' + name + ' ·'), text)
                self.assertIn(command, text)
                self.assertNotIn('- ack · ' + name + ' ·', text)
                self.assertIsNone(state['messages'][name]['ack'])
                self.store.append('ack', recipient, {'message': name})
                self.assertFalse(self.forward())

    def test_related_task_close_removes_ack_but_keeps_unlinked_and_history(self):
        self.message(related_task='T'); self.message('UNLINKED')
        self.delivered()
        self.assertEqual(self.cli('review', '--actor', 'R', '--task', 'T', '--verdict', 'accepted', '--report', str(self.report)), 0)
        self.assertEqual(self.cli('close', '--actor', 'C', '--task', 'T'), 0)
        before = len(self.store.events()); state = self.store.state()
        names = [w['id'] for w in attention(state)['waiting'] if w['kind'] == 'ack']
        self.assertNotIn('ORIGINAL', names, 'closed related_task is no longer an operational ACK wait')
        self.assertIn('UNLINKED', names)
        self.assertIsNone(state['messages']['ORIGINAL']['ack'])
        self.assertEqual(len(self.store.events()), before)

    def test_current_plan_ages_from_submission_and_resubmission_resets_it(self):
        self.ack(); first = self.plan(minute=10)
        start = dt.datetime.fromisoformat(first)
        self.assertFalse([w for w in waits(self.store.state(), start + dt.timedelta(seconds=3599)) if w['kind'] == 'plan'])
        rows = [w for w in waits(self.store.state(), start + dt.timedelta(seconds=3600)) if w['kind'] == 'plan']
        self.assertEqual(len(rows), 1, 'the submitted plan has its own aged wait')
        self.assertEqual((rows[0]['seconds'], rows[0]['threshold'], rows[0]['utc']), (3600, 3600, first))
        self.plan(minute=70, text='Replacement')
        self.assertFalse([w for w in waits(self.store.state(), start + dt.timedelta(seconds=3600)) if w['kind'] == 'plan'])

    def test_all_failed_history_and_first_transport_age_not_last_attempt(self):
        self.ack(); self.message(); first = self.transport()
        self.transport(minute=2)
        start = dt.datetime.fromisoformat(first)
        self.assertFalse([w for w in waits(self.store.state(), start + dt.timedelta(seconds=119)) if w['kind'] == 'to-forward'])
        row = next(w for w in waits(self.store.state(), start + dt.timedelta(seconds=120)) if w['kind'] == 'to-forward')
        self.assertEqual((row['seconds'], row['utc'], row['threshold']), (120, first, 120))
        self.transport(delivered=True, minute=3)
        self.assertFalse(self.forward())
        self.transport(minute=4)
        self.assertFalse(self.forward(), 'a later failure cannot erase an earlier true receipt')
        self.assertIsNone(self.store.state()['messages']['ORIGINAL']['ack'])
        self.message('UNKNOWN'); self.transport('UNKNOWN', delivered=None)
        self.transport('UNKNOWN', minute=3)
        self.assertEqual([w['id'] for w in self.forward()], ['UNKNOWN'], 'unknown delivery keeps forwarding open')

    def test_info_and_closed_or_superseded_messages_do_not_forward(self):
        self.ack(); self.message(); self.message('INFO', info=True); self.transport('INFO')
        rows = self.forward()
        self.assertEqual([w['id'] for w in rows], ['ORIGINAL'],
                         'an untransported message to a Codex window waits on the coordinator; information never does')
        self.assertEqual(rows[0]['utc'], STAMP, 'without a transport attempt it ages from its creation')
        self.transport()
        state = self.store.state()
        for link in ('task', 'related_task'):
            for terminal in ('done', 'superseded'):
                with self.subTest(link=link, terminal=terminal):
                    current = deepcopy(state)
                    current['messages']['ORIGINAL'][link] = 'T'
                    if terminal == 'done': current['tasks']['T']['status'] = 'done'
                    else: current['tasks']['T']['superseded_by'] = 'NEXT'
                    self.assertFalse(self.forward(current))
                    self.assertNotIn('ORIGINAL', [w.get('id') for w in attention(current)['waiting'] if w['kind'] == 'ack'])

    def test_exact_plan_verdict_is_not_an_unread_plan(self):
        self.ack(); self.plan()
        state = self.store.state()
        for verdict in ('accepted', 'changes-required', 'blocked'):
            state['tasks']['T']['reviews'] = [{'stage': 'plan', 'binding': state['tasks']['T']['plan']['sha256'], 'verdict': verdict}]
            self.assertFalse([w for w in waits(state, '2026-10-16T15:00:00+00:00') if w['kind'] == 'plan'])
        state['tasks']['T']['reviews'][-1]['binding'] = 'stale'
        self.assertEqual(len([w for w in waits(state, '2026-10-16T15:00:00+00:00') if w['kind'] == 'plan']), 1)

    def test_old_thresholds_new_overrides_and_invalid_values(self):
        self.ack(); self.message(); first = self.transport(); self.plan()
        old = {'ack': 5, 'delivered': 6, 'reviewed': 7, 'heartbeat': 8, 'alive': 9}
        self.store.append('settings', 'C', {'wait_seconds': old})
        state = self.store.state()
        self.assertTrue(waits(state, '2026-10-16T15:00:00+00:00'))
        self.assertEqual(next(w for w in waits(state, '2026-10-16T15:00:00+00:00') if w['kind'] == 'ack')['threshold'], old['ack'])
        self.assertEqual(next(w for w in waits(state, first, {'to-forward': 0}) if w['kind'] == 'to-forward')['seconds'], 0)
        self.assertEqual(next(w for w in waits(state, first, {'plan': 0}) if w['kind'] == 'plan')['threshold'], 0)
        for key in ('to-forward', 'plan'):
            for value in (True, -1, 0.5):
                with self.assertRaises(Error): self.store.append('settings', 'C', {'wait_seconds': {key: value}})
        with self.assertRaises(Error): self.store.append('settings', 'C', {'wait_seconds': {'unknown': 0}})
        self.store.append('settings', 'C', registry.wait_settings({**old, 'to-forward': 0, 'plan': 0}))
        self.assertEqual(self.store.state()['workspace']['wait_seconds']['ack'], old['ack'])

    def test_reopen_and_schema5_readonly_replay_keep_history_and_never_write(self):
        self.ack(); self.message(); self.transport(); self.transport(minute=2); self.plan()
        self.assertNotIn('transport_first_utc', self.store.normalized_state(self.store.events())['messages']['ORIGINAL'])
        expected = self.store.state(); normal = attention(expected)
        reader = Store(self.root, read_only=True)
        self.addCleanup(reader.close)
        files = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.iterdir()
                 if p.is_file() and p.suffix in ('.md', '.json', '.jsonl')}
        before = reader.db.total_changes
        with patch.object(reader, 'state', side_effect=AssertionError('Schema-5 cache must not replay')):
            rebuilt = reader.view()
        self.assertEqual(attention(rebuilt), normal)
        self.assertEqual(reader.db.total_changes, before)
        self.assertEqual({p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.iterdir()
                          if p.is_file() and p.suffix in ('.md', '.json', '.jsonl')}, files)
        self.assertEqual(registry_cache.SCHEMA, 5)
        self.assertEqual(next(w['utc'] for w in normal['waiting'] if w['kind']=='to-forward'), '2026-10-16T12:01:00+00:00')
        with patch.object(self.store, '_rebuild_cache', side_effect=AssertionError('No schema flip-flop')):
            self.store.append('diagnostic', 'system', {'reason': 'Current writer keeps the shared schema'})
        self.assertEqual(attention(self.store.view()), attention(self.store.state()))

    def test_saved_summary_and_export_keep_root_bound_forward_command(self):
        self.ack(); self.message(related_task='T'); self.transport()
        command = self.forward()[0]['ack_command']
        self.store.publish()
        self.assertIn(command, (self.root / 'stato.md').read_text())
        self.store.project()
        saved = (self.root / 'stato.md').read_text()
        self.assertIn(command, saved)
        self.assertIn('2026-10-16T12:01:00+00:00', saved)
        self.assertNotIn('120s', saved)
        state = self.store.state(); state['workspace']['lang'] = 'en'
        self.assertIn('To forward', table(state, '2026-10-16T13:00:00+00:00', compact=True))

    def test_pool_sibling_claim_and_old_packet_do_not_request_forwarding(self):
        self.ack(); self.message(); first = self.transport()
        state = registry.plain(self.store.view()); task = state['tasks']['T']
        task.update(status='delivered', pool=['R', 'A-alias'], delivery={'handoffs': ['H1', 'H2']})
        for name, to in [('H1', 'R'), ('H2', 'A-alias')]:
            state['messages'][name] = {**state['messages']['ORIGINAL'], 'id': name, 'related_task': 'T',
                                       'to': to, 'pool': ['H1', 'H2'], 'utc': first}
        self.assertEqual({w['id'] for w in self.forward(state)}, {'H1', 'H2', 'ORIGINAL'})
        task['claim'] = {'agent': 'R', 'handoff': 'H1', 'utc': first}
        self.assertEqual([w['id'] for w in self.forward(state)], ['ORIGINAL'])
        task.pop('claim'); task['delivery']['handoffs'] = ['NEW-H1', 'NEW-H2']
        self.assertEqual([w['id'] for w in self.forward(state)], ['ORIGINAL'])

    def test_root_command_quotes_shell_metacharacters(self):
        self.ack(); self.message(); self.transport()
        state = self.store.state(); state['root'] = str(self.root) + "/space ' and | dollar$"
        command = self.forward(state)[0]['ack_command']
        self.assertEqual(shlex.split(command)[-2:], ['--root', state['root']])

    def test_dom_forwarding_is_first_bilingual_folded_and_original_ack_command_is_literal(self):
        node = shutil.which('node'); self.assertIsNotNone(node)
        self.ack(); self.message(related_task='T'); self.transport()
        state = state_fixture(); state.update(registry.plain(self.store.view()))
        state['attention'] = attention(state)
        state['waits'] = waits(state, '2026-10-16T15:00:00+00:00')
        path = self.base / 'state.json'; path.write_text(json.dumps(state))
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([node, str(root/'tests/msg_waits_contract.js'),
                                 str(root/'assets/dashboard.html'), str(path)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('message waits contract passed: 8 combinations', result.stdout)


class MessageWaitsHTTP(unittest.TestCase):
    def setUp(self):
        with codex_seats('R'):
            transfer.DashboardTransfer.setUp(self)

    stop_server = transfer.DashboardTransfer.stop_server
    get = transfer.DashboardTransfer.get
    state = transfer.DashboardTransfer.state
    stamp = transfer.DashboardTransfer.stamp
    task_id = transfer.DashboardTransfer.task_id
    run_id = transfer.DashboardTransfer.run_id
    source_path = transfer.DashboardTransfer.source_path
    source_hash = transfer.DashboardTransfer.source_hash
    compact = transfer.DashboardTransfer.compact

    def test_event_count_race_fallback_retains_both_transient_waits(self):
        import dashboard
        with patch('registry.now', return_value=self.stamp):
            self.store.append('plan', 'A', {'task':self.task_id, 'artifact':self.artifact})
            self.store.append('message', 'A', {'id':'FORWARD', 'to':'R', 'related_task':self.task_id, 'body':'Read plan'})
            self.store.append('transport', 'A', {'message':'FORWARD', 'delivered':None, 'via':'manual'})
        original=dashboard.Store.view; forced=[]
        def raced(store):
            value=dict(original(store)); value['event_count']-=1; forced.append(True)
            return value
        clock=(dt.datetime.fromisoformat(self.stamp)+dt.timedelta(seconds=3600)).isoformat()
        with patch.object(dashboard.Store, 'view', raced), patch.object(dashboard, 'now', return_value=clock):
            state,_=self.compact()
        self.assertTrue(forced)
        self.assertTrue({'plan','to-forward'} <= {row['kind'] for row in state['waits']})
        self.assertEqual(next(row['utc'] for row in state['waits'] if row['kind']=='to-forward'), self.stamp)

    def test_both_new_kinds_keep_conditional_http_and_invalidate_at_their_thresholds(self):
        import dashboard
        with patch('registry.now', return_value=self.stamp):
            self.store.append('plan', 'A', {'task': self.task_id, 'artifact': self.artifact})
            self.store.append('message', 'A', {'id': 'FORWARD', 'to': 'R', 'related_task': self.task_id, 'body': 'Read plan'})
            self.store.append('transport', 'A', {'message': 'FORWARD', 'delivered': False, 'via': 'manual'})
        deadline = time.monotonic() + 10
        while True:
            state, _ = self.compact()
            if state['audit']['status'] == 'complete': break
            self.assertLess(time.monotonic(), deadline, 'fixture background audit completes')
            time.sleep(0.01)
        clock = dt.datetime.fromisoformat(self.stamp)
        with patch.object(dashboard, 'now', return_value=(clock+dt.timedelta(seconds=119)).isoformat()):
            before, _ = self.compact()
            self.assertFalse([w for w in before['waits'] if w['kind']=='to-forward'])
        with patch.object(dashboard, 'now', return_value=(clock+dt.timedelta(seconds=120)).isoformat()):
            after, _ = self.compact(before['change_token'])
            self.assertNotEqual(after['change_token'], before['change_token'])
            self.assertEqual(after['waits'][0]['kind'], 'to-forward')
            self.assertIn(str(self.root), after['waits'][0]['ack_command'])
            self.assertTrue(self.compact(after['change_token'])[0]['not_modified'])
        with patch.object(dashboard, 'now', return_value=(clock+dt.timedelta(seconds=3600)).isoformat()):
            plan, _ = self.compact(after['change_token'])
            self.assertTrue(any(w['kind']=='plan' for w in plan['waits']))
            self.assertTrue(self.compact(plan['change_token'])[0]['not_modified'])
            self.store.append('ack', 'R', {'message': 'FORWARD'})
            acked, _ = self.compact(plan['change_token'])
            self.assertFalse([w for w in acked['attention']['waiting'] if w['kind']=='to-forward'])


if __name__ == '__main__':
    unittest.main()
