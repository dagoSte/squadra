"""Mail to a window in stand-by, and old heartbeats of its runs, leave the waits as one counted line per window."""
import datetime as dt
import http.client
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import registry
from registry import attention, waits, table, pending_ack_ids
import test_squadra as support
import test_dashboard_transfer as transfer
from test_msg_waits import codex_seats, STAMP
from test_usage_page import state_fixture

ROOT = Path(__file__).resolve().parents[1]
REASON = 'Window closed on 10-11; the census finds no process'


def later():
    return dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)


def mail_rows(rows, window):
    return [w for w in rows if w['kind'] == 'to-forward' and w.get('to') == window
            or w['kind'] == 'ack' and w.get('who') == [window] and not w.get('handoffs')]


class StandbyWaits(unittest.TestCase):
    git = support.Workflow.git
    task = support.Workflow.task
    cli = support.Workflow.cli

    def setUp(self):
        with codex_seats('R'):
            support.Workflow.setUp(self)
        for name in ('M1', 'M2', 'M3'):
            with patch('registry.now', return_value=STAMP):
                self.store.append('message', 'C', {'id': name, 'to': 'R', 'body': 'Please read'})
        self.store.append('transport', 'C', {'message': 'M1', 'via': 'manual', 'delivered': False})

    def standby(self, agent='R', resume=False):
        args = ['standby', '--actor', 'C', '--agent', agent, '--reason', REASON] + (['--resume'] if resume else [])
        self.assertEqual(self.cli(*args), 0, 'the coordinator records stand-by for a window silent for days')

    def test_mail_to_a_window_in_standby_folds_into_one_counted_row(self):
        clock = later()
        before = waits(self.store.state(), clock)
        self.assertEqual(sorted({w['id'] for w in mail_rows(before, 'R')}), ['M1', 'M2', 'M3'], 'positive control')
        self.standby()
        state = self.store.state()
        rows = waits(state, clock)
        self.assertEqual(mail_rows(rows, 'R'), [], 'mail to a window in stand-by leaves the waits')
        held = [w for w in rows if w['kind'] == 'stand-by']
        self.assertEqual([(w['id'], sorted(w['messages']), w['runs'], w['count'], w['reason']) for w in held],
                         [('R', ['M1', 'M2', 'M3'], [], 3, REASON)])
        self.assertEqual(held[0]['text'], '3 messaggi a R in stand-by (' + REASON + ')')
        self.assertEqual(held[0]['text_en'], '3 messages to R on stand-by (' + REASON + ')')
        self.assertEqual(held[0]['utc'], STAMP, 'the row ages from its oldest message')
        self.assertIn('assign-T', [w['id'] for w in mail_rows(rows, 'A')], 'an active window keeps its waits')
        for name in ('M1', 'M2', 'M3'):
            self.assertIsNone(state['messages'][name]['ack'], 'the message stays without ACK')
        self.assertLessEqual({'M1', 'M2', 'M3'}, set(pending_ack_ids(state)), 'the messages stay pending')

    def test_an_ack_closes_a_message_and_a_return_brings_the_waits_back(self):
        self.standby()
        self.store.append('ack', 'R', {'message': 'M2'})
        held = [w for w in waits(self.store.state(), later()) if w['kind'] == 'stand-by']
        self.assertEqual([(w['id'], sorted(w['messages']), w['text']) for w in held],
                         [('R', ['M1', 'M3'], '2 messaggi a R in stand-by (' + REASON + ')')])
        self.store.append('ack', 'R', {'message': 'M3'})
        held = [w for w in waits(self.store.state(), later()) if w['kind'] == 'stand-by']
        self.assertEqual([w['text_en'] for w in held], ['1 message to R on stand-by (' + REASON + ')'])
        self.standby(resume=True)
        rows = waits(self.store.state(), later())
        self.assertFalse([w for w in rows if w['kind'] == 'stand-by'])
        self.assertEqual(sorted((w['kind'], w['id']) for w in mail_rows(rows, 'R')), [('ack', 'M1'), ('to-forward', 'M1')])

    def test_status_and_saved_summary_show_the_counted_line_in_both_languages(self):
        self.standby()
        state = self.store.state()
        clock = later()
        line = '- 3 messaggi a R in stand-by (' + REASON + ')'
        compact = table(state, read_at=clock, compact=True)
        self.assertIn('In stand-by, fuori dalle attese', compact)
        self.assertIn(line, compact)
        listed = [w for w in waits(state, clock) if w['kind'] != 'stand-by']
        self.assertIn(str(len(listed)) + ' totali · ', compact, 'the count leaves the stand-by mail out')
        full = table(state, read_at=clock)
        self.assertEqual(full.count(line), 1)
        for name in ('M1', 'M2', 'M3'):
            self.assertNotIn(' · ' + name + ' · ', full)
            self.assertNotIn('--message ' + name + ' ', full)
        self.store.publish()
        for saved in ((self.root / 'stato.md').read_text(),):
            self.assertIn(line, saved)
            self.assertNotIn('--message M1 ', saved)
            self.assertIn('--message assign-T ', saved, 'forwarding to an active window stays')
        self.store.project()
        saved = (self.root / 'stato.md').read_text()
        self.assertIn(line, saved)
        self.assertNotIn('--message M1 ', saved)
        state['workspace']['lang'] = 'en'
        english = table(state, read_at=clock, compact=True)
        self.assertIn('On stand-by, not counted among the waits', english)
        self.assertIn('- 3 messages to R on stand-by (' + REASON + ')', english)

    def test_old_heartbeats_of_a_standby_windows_runs_fold_and_the_runs_stay_uncertain(self):
        self.store.append('ack', 'A', {'message': 'assign-T'})
        self.store.append('run.start', 'A', {'id': 'orphan', 'task': 'T', 'mode': 'proof', 'covers': []})
        clock = later()
        self.assertIn('orphan', [w['id'] for w in waits(self.store.state(), clock) if w['kind'] == 'heartbeat'],
                      'positive control')
        self.standby('A')
        state = self.store.state()
        rows = waits(state, clock)
        self.assertNotIn('orphan', [w.get('id') for w in rows if w['kind'] == 'heartbeat'])
        held = {w['id']: w for w in rows if w['kind'] == 'stand-by'}
        self.assertEqual((held['A']['runs'], held['A']['messages']), (['orphan'], []))
        self.assertEqual(held['A']['text'], '1 esecuzione di A in stand-by (' + REASON + '), stato incerto')
        self.assertEqual(held['A']['text_en'], '1 run of A on stand-by (' + REASON + '), state uncertain')
        self.assertEqual(state['runs']['orphan']['status'], 'running', 'a run without a receipt is never stopped')
        self.assertIn('orphan', [w.get('id') for w in attention(state)['waiting'] if w['kind'] == 'running'],
                      'the run stays listed with its uncertain state')
        self.store.append('message', 'C', {'id': 'M4', 'to': 'A', 'body': 'Also this'})
        both = {w['id']: w for w in waits(self.store.state(), clock) if w['kind'] == 'stand-by'}['A']
        self.assertEqual(both['text'], '1 messaggio a A in stand-by (' + REASON + ') · 1 esecuzione di A, stato incerto')
        self.assertEqual(both['text_en'], '1 message to A on stand-by (' + REASON + ') · 1 run of A, state uncertain')


class StandbyRuns(unittest.TestCase):
    """A run of a window in stand-by counts only once its heartbeat is old, and the dashboard shows that count."""
    git = support.Workflow.git
    task = support.Workflow.task

    def setUp(self):
        with patch('registry.now', return_value=STAMP):
            support.Workflow.setUp(self)
            self.store.append('ack', 'A', {'message': 'assign-T'})
            self.store.append('run.start', 'A', {'id': 'orphan', 'task': 'T', 'mode': 'proof', 'covers': []})
            self.store.append('availability', 'C', {'agent': 'A', 'status': 'stand-by', 'reason': REASON})

    def at(self, seconds):
        return dt.datetime.fromisoformat(STAMP) + dt.timedelta(seconds=seconds)

    def held(self, seconds):
        return [(w['id'], w['messages'], w['runs']) for w in waits(self.store.state(), self.at(seconds))
                if w['kind'] == 'stand-by']

    def test_a_fresh_heartbeat_is_not_counted_until_it_is_old(self):
        self.assertEqual(self.held(1), [], 'one second after its start the run is not an old heartbeat')
        self.assertEqual(self.held(1799), [])
        self.assertEqual(self.held(1800), [('A', [], ['orphan'])], 'at the heartbeat threshold it counts')
        with patch('registry.now', return_value=STAMP):
            self.store.append('message', 'C', {'id': 'mail', 'to': 'A', 'body': 'Please read'})
        self.assertEqual(self.held(1), [('A', ['mail'], [])], 'mail to a window in stand-by counts at any age')

    def landing(self):
        import dashboard
        with patch.object(dashboard, 'now', return_value=self.at(7200).isoformat()), \
                patch.object(Path, 'home', return_value=self.home):
            server, token = dashboard.make_server(self.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(dashboard, 'now', return_value=self.at(7200).isoformat()):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                try:
                    connection.request('GET', '/api/state', headers={'X-Squadra-Token': token,
                                       'X-Squadra-View': 'compact', 'X-Squadra-State-Token': ''})
                    response = connection.getresponse()
                    raw = response.read()
                finally:
                    connection.close()
        finally:
            server.shutdown(); server.server_close(); thread.join(5)
        self.assertEqual(response.status, 200, raw)
        return json.loads(raw)

    def page(self, landing, mode):
        page = state_fixture()
        page.update(registry.plain(self.store.view()))
        page.update(attention=landing['attention'], waits=landing['waits'])
        fixture = self.base / (mode + '.json')
        fixture.write_text(json.dumps(page))
        return subprocess.run(['node', str(ROOT / 'tests/waits_standby_runs_contract.js'), str(ROOT / 'assets/dashboard.html'),
                               str(fixture), REASON, mode], capture_output=True, text=True, timeout=30)

    def test_the_dashboard_counts_old_runs_alone_and_beside_mail(self):
        self.assertIsNotNone(shutil.which('node'), 'Node is required; the page contract cannot be skipped')
        landing = self.landing()
        held = [(w['id'], w['messages'], w['runs']) for w in landing['attention']['waiting'] if w['kind'] == 'stand-by']
        self.assertEqual(held, [('A', [], ['orphan'])], 'the recorded row counts a run-only window')
        self.assertIn('orphan', [w.get('id') for w in landing['attention']['waiting'] if w['kind'] == 'running'],
                      'the run stays listed as uncertain')
        result = self.page(landing, 'runs')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('runs contract passed: runs', result.stdout)
        with patch('registry.now', return_value=STAMP):
            self.store.append('message', 'C', {'id': 'mail', 'to': 'A', 'body': 'Please read'})
        landing = self.landing()
        held = [(w['id'], w['messages'], w['runs']) for w in landing['attention']['waiting'] if w['kind'] == 'stand-by']
        self.assertEqual(held, [('A', ['mail'], ['orphan'])], 'mail and runs of one window share its row')
        result = self.page(landing, 'mixed')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('runs contract passed: mixed', result.stdout)


class StandbyConditional(unittest.TestCase):
    """An open page learns that a stand-by window's run became old, though no event was recorded."""
    git = support.Workflow.git
    task = support.Workflow.task
    # The run starts a minute after STAMP, so its 1800-second crossing is not a 15-minute activity boundary.
    started = (dt.datetime.fromisoformat(STAMP) + dt.timedelta(seconds=60)).isoformat()

    def setUp(self):
        with patch('registry.now', return_value=STAMP):
            support.Workflow.setUp(self)
            self.store.append('ack', 'A', {'message': 'assign-T'})
            self.store.append('availability', 'C', {'agent': 'A', 'status': 'stand-by', 'reason': REASON})
        with patch('registry.now', return_value=self.started):
            self.store.append('run.start', 'A', {'id': 'orphan', 'task': 'T', 'mode': 'proof', 'covers': []})

    def replies(self, mail):
        import dashboard
        if mail:
            with patch('registry.now', return_value=STAMP):
                self.store.append('message', 'C', {'id': 'mail', 'to': 'A', 'body': 'Please read'})
        self.clock = (dt.datetime.fromisoformat(self.started) + dt.timedelta(seconds=1799)).isoformat()
        with patch.object(dashboard, 'now', side_effect=lambda: self.clock), \
                patch.object(Path, 'home', return_value=self.home):
            server, token = dashboard.make_server(self.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def get(held=''):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            try:
                connection.request('GET', '/api/state', headers={'X-Squadra-Token': token,
                                   'X-Squadra-View': 'compact', 'X-Squadra-State-Token': held})
                response = connection.getresponse()
                raw = response.read()
            finally:
                connection.close()
            self.assertEqual(response.status, 200, raw)
            return json.loads(raw)
        try:
            with patch.object(dashboard, 'now', side_effect=lambda: self.clock):
                deadline = time.monotonic() + 15
                while True:
                    before = get()
                    if before.get('audit', {}).get('status') == 'complete':
                        break
                    self.assertLess(time.monotonic(), deadline, 'fixture background audit completes')
                    time.sleep(0.01)
                same = get(before['change_token'])
                self.clock = (dt.datetime.fromisoformat(self.started) + dt.timedelta(seconds=1800)).isoformat()
                after = get(before['change_token'])
        finally:
            server.shutdown(); server.server_close(); thread.join(5)
        return before, same, after

    def check(self, mail):
        before, same, after = self.replies(mail)
        self.assertTrue(same.get('not_modified'), 'at the same clock the page keeps its copy')
        held = lambda reply: [(w['messages'], w['runs']) for w in reply['attention']['waiting'] if w['kind'] == 'stand-by']
        self.assertEqual(held(before), [(['mail'], [])] if mail else [])
        self.assertFalse(after.get('not_modified'), 'the heartbeat became old: the page needs the new count')
        self.assertEqual(held(after), [(['mail'] if mail else [], ['orphan'])])
        self.assertEqual(after['registry_head'], before['registry_head'], 'no event was recorded')
        self.assertEqual(after['activity']['since'], before['activity']['since'], 'the activity bucket did not change')

    def test_a_run_becoming_old_refreshes_a_run_only_row(self):
        self.check(mail=False)

    def test_a_run_becoming_old_refreshes_a_row_with_mail(self):
        self.check(mail=True)

    def test_the_reply_token_binds_the_members_of_a_standby_row(self):
        import dashboard
        row = {'kind': 'stand-by', 'id': 'A', 'who': ['A'], 'threshold': 0, 'messages': ['mail'], 'runs': []}
        token = lambda rows: dashboard.transfer_signature('head', '', {}, rows, [], {})
        self.assertNotEqual(token([row]), token([{**row, 'runs': ['orphan']}]))
        self.assertNotEqual(token([row]), token([{**row, 'messages': ['mail', 'more']}]))
        self.assertEqual(token([row]), token([{**row, 'seconds': 5}]), 'ages stay local to the page')


class StandbyDashboard(unittest.TestCase):
    setUp = transfer.DashboardTransfer.setUp
    stop_server = transfer.DashboardTransfer.stop_server
    get = transfer.DashboardTransfer.get
    state = transfer.DashboardTransfer.state
    stamp = transfer.DashboardTransfer.stamp
    task_id = transfer.DashboardTransfer.task_id
    run_id = transfer.DashboardTransfer.run_id
    source_path = transfer.DashboardTransfer.source_path
    source_hash = transfer.DashboardTransfer.source_hash
    compact = transfer.DashboardTransfer.compact

    def landing(self):
        import dashboard
        # Newer than every other wait: the page must still list the counted row last.
        newer = (dt.datetime.fromisoformat(self.stamp) + dt.timedelta(hours=1)).isoformat()
        with patch('registry.now', return_value=newer):
            for name in ('M1', 'M2'):
                self.store.append('message', 'C', {'id': name, 'to': 'R', 'body': 'Please read'})
            self.store.append('availability', 'C', {'agent': 'R', 'status': 'stand-by', 'reason': REASON})
        clock = (dt.datetime.fromisoformat(self.stamp) + dt.timedelta(hours=2)).isoformat()
        with patch.object(dashboard, 'now', return_value=clock):
            state, _ = self.compact()
        return state

    def test_the_landing_lists_one_counted_row_and_keeps_the_mail_pending(self):
        state = self.landing()
        recorded = state['attention']['waiting']
        self.assertEqual(mail_rows(recorded, 'R'), [], 'the recorded waits leave the stand-by mail out')
        self.assertEqual([(w['id'], sorted(w['messages']), w['reason']) for w in recorded if w['kind'] == 'stand-by'],
                         [('R', ['M1', 'M2'], REASON)])
        self.assertLessEqual({'M1', 'M2'}, set(state['attention'].get('pending_ack', [])), 'pending ACKs keep that mail')
        self.assertEqual(mail_rows(state['waits'], 'R'), [])
        self.assertEqual([w['id'] for w in state['waits'] if w['kind'] == 'stand-by'], ['R'])

    def test_page_shows_the_counted_row_in_both_languages(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required; the page contract cannot be skipped')
        landing = self.landing()
        page = state_fixture()
        page.update(registry.plain(self.store.view()))
        page.update(attention=landing['attention'], waits=landing['waits'])
        fixture = self.base / 'state.json'
        fixture.write_text(json.dumps(page))
        result = subprocess.run([node, str(ROOT / 'tests/waits_standby_contract.js'), str(ROOT / 'assets/dashboard.html'),
                                 str(fixture), REASON], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(re.findall(r'waits stand-by contract passed: \d+ combinations', result.stdout),
                         ['waits stand-by contract passed: 8 combinations'])


if __name__ == '__main__':
    unittest.main()
