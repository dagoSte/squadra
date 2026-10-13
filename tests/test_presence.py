"""Check presence replies and process observations without live clients."""
import contextlib
import copy
import datetime as dt
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import test_squadra as support
from registry import Error, Store, apply, initial, now, presence, standby, latest_ping, table, waits
from squadra import main, deliver, parser
from census import collect, parse_processes, parse_lsof


class Presence(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task
    ack = support.Workflow.ack
    cli = support.Workflow.cli
    proof = support.Workflow.proof

    def ping(self, agent='A', actor='C'):
        self.assertEqual(self.cli('ping', '--actor', actor, '--to', agent), 0)
        return latest_ping(self.store.state(), agent)

    def clock(self, ping, seconds):
        return (dt.datetime.fromisoformat(ping['utc']) + dt.timedelta(seconds=seconds)).isoformat()

    def sweep(self, at):
        with patch('squadra.now', return_value=at), patch('registry.now', return_value=at):
            return self.cli('ping', '--actor', 'C', '--sweep')

    def hold(self, agent='A'):
        self.assertEqual(self.cli('standby', '--actor', 'C', '--agent', agent, '--reason', 'Explicit hold'), 0)

    def test_ping_retry_reuses_deadline_and_deduplicates_identity(self):
        first = self.ping(); before = self.store.events()
        self.assertEqual(self.ping('A-alias')['id'], first['id'])
        self.assertEqual(self.store.events(), before)
        self.assertEqual(self.cli('ping', '--actor', 'C', '--all'), 0)
        messages = [m for m in self.store.state()['messages'].values() if m.get('ping')]
        self.assertEqual({m['to'] for m in messages}, {'A', 'R'})
        self.assertEqual(len(messages), 2)
        self.assertIsNone(first['ack'])

    def test_actual_recipient_ack_only_and_queue_is_not_an_answer(self):
        ping = self.ping()
        self.store.append('transport', 'C', {'message': ping['id'], 'delivered': True})
        self.assertEqual(self.cli('ack', '--actor', 'R', '--message', ping['id']), 1)
        self.assertIsNone(latest_ping(self.store.state(), 'A')['ack'])
        self.assertEqual(self.cli('ack', '--actor', 'A', '--message', ping['id']), 0)
        self.assertEqual(self.sweep(self.clock(ping, 301)), 0)
        self.assertFalse(standby(self.store.state(), 'A'))
        observed = next(r for r in presence(self.store.state(), self.clock(ping, 301)) if r['agent'] == 'A')
        self.assertEqual(observed['last_answer'], latest_ping(self.store.state(), 'A')['ack']['utc'])
        self.assertFalse(observed['due'])

    def test_ages_change_without_events_or_projection_rewrites(self):
        ping = self.ping(); self.store.project()
        before = {p.name: p.read_bytes() for p in self.root.iterdir() if p.suffix in {'.json', '.jsonl', '.md'}}
        state = self.store.state(); events = self.store.events()
        early = presence(state, self.clock(ping, 299))[1]
        later = presence(state, self.clock(ping, 300))[1]
        self.assertEqual((early['seconds'], early['due']), (299, False))
        self.assertEqual((later['seconds'], later['due']), (300, True))
        self.assertFalse(standby(self.store.state(), 'A'))
        self.assertEqual(events, self.store.events())
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir() if p.suffix in {'.json', '.jsonl', '.md'}})
        self.assertFalse(any(x['kind'] == 'alive' for x in waits(state, self.clock(ping, 300), {'alive': 400})))

    def test_boundary_sweep_and_changed_alive_setting(self):
        ping = self.ping()
        self.assertEqual(self.sweep(self.clock(ping, 299)), 0)
        self.assertFalse(standby(self.store.state(), 'A'))
        self.store.append('settings', 'C', {'wait_seconds': {'alive': 400}})
        self.assertFalse(presence(self.store.state(), self.clock(ping, 300))[1]['due'])
        self.assertEqual(self.sweep(self.clock(ping, 300)), 0)
        self.assertFalse(standby(self.store.state(), 'A'))
        self.assertEqual(self.sweep(self.clock(ping, 400)), 0)
        self.assertTrue(standby(self.store.state(), 'A'))
        self.assertTrue(standby(self.store.state(), 'A-alias'))
        before = self.store.events()
        self.assertEqual(self.sweep(self.clock(ping, 500)), 0)
        self.assertEqual(before, self.store.events())

    def test_stale_sweep_decision_is_rechecked_inside_transaction(self):
        ping = self.ping()
        stale = {'agent': 'A', 'status': 'stand-by', 'ping': ping['id'], 'reason': 'Precomputed'}
        self.store.append('settings', 'C', {'wait_seconds': {'alive': 1000}})
        with patch('registry.now', return_value=self.clock(ping, 500)):
            with self.assertRaisesRegex(Error, 'no longer overdue'):
                self.store.append('availability', 'C', stale)
        self.store.append('settings', 'C', {'wait_seconds': {'alive': 0}})
        self.store.append('ack', 'A', {'message': ping['id']})
        with self.assertRaisesRegex(Error, 'no longer overdue'):
            self.store.append('availability', 'C', stale)
        newer = self.ping()
        with self.assertRaisesRegex(Error, 'challenge changed'):
            self.store.append('availability', 'C', stale)
        self.assertEqual(self.sweep(self.clock(newer, 1)), 0)
        self.assertTrue(standby(self.store.state(), 'A'))

    def test_only_coordinator_changes_availability_entrants_can_ping_coordinator(self):
        self.assertEqual(self.cli('ping', '--actor', 'A', '--all'), 1)
        self.assertEqual(self.cli('ping', '--actor', 'A', '--to', 'R'), 1)
        ping = self.ping('C', 'A')
        self.assertEqual(self.cli('ack', '--actor', 'C', '--message', ping['id']), 0)
        for actor in ('A', 'R'):
            self.assertEqual(self.cli('ping', '--actor', actor, '--sweep'), 1)
            self.assertEqual(self.cli('standby', '--actor', actor, '--agent', 'A', '--reason', 'No'), 1)
        self.assertFalse(standby(self.store.state(), 'A'))

    def test_stale_ack_does_not_return_and_fresh_ack_needs_coordinator_sweep(self):
        old = self.ping(); self.hold()
        self.assertEqual(self.cli('ack', '--actor', 'A', '--message', old['id']), 0)
        self.assertEqual(self.sweep(now()), 0)
        self.assertTrue(standby(self.store.state(), 'A'))
        fresh = self.ping('A-alias')
        self.assertNotEqual(old['id'], fresh['id'])
        self.assertEqual(self.cli('ack', '--actor', 'A-alias', '--message', fresh['id']), 0)
        self.assertTrue(standby(self.store.state(), 'A'))
        self.assertEqual(self.sweep(now()), 0)
        self.assertFalse(standby(self.store.state(), 'A'))
        event = self.store.events()[-1]
        self.assertEqual((event['kind'], event['actor'], event['data']['status']), ('availability', 'C', 'active'))
        self.assertEqual(event['data']['ping'], fresh['id'])

    def test_explicit_return_discards_old_deadline_and_records_new_challenge(self):
        old = self.ping(); self.hold()
        self.assertEqual(self.cli('standby', '--actor', 'C', '--agent', 'A', '--resume', '--reason', 'Window answered directly'), 0)
        self.assertEqual(self.sweep(self.clock(old, 900)), 0)
        self.assertFalse(standby(self.store.state(), 'A'))
        self.assertNotEqual(self.ping()['id'], old['id'])

    def test_registration_and_new_alias_cannot_reset_standby(self):
        self.hold()
        data = dict(self.store.state()['agents']['A'])
        data.pop('updated'); data.pop('availability')
        self.store.append('agent', 'A', data)
        self.store.append('agent', 'A-new', data | {'id': 'A-new'})
        for name in ('A', 'A-alias', 'A-new'):
            self.assertTrue(standby(self.store.state(), name))
        with self.assertRaisesRegex(Error, 'recorded coordinator'):
            self.store.append('agent', 'A', data | {'availability': {'status': 'active'}})

    def test_new_owner_and_reviewer_assignments_refused_atomically(self):
        self.hold(); before = self.store.events()
        for owner in ('A', 'A-alias'):
            with self.assertRaisesRegex(Error, 'stand-by'):
                self.task('new-'+owner, owner=owner)
        self.assertEqual(before, self.store.events())
        self.assertEqual(self.cli('standby', '--actor', 'C', '--agent', 'A', '--resume', '--reason', 'Ready'), 0)
        self.task('active-control')
        self.hold('R'); before = self.store.events()
        with self.assertRaisesRegex(Error, 'stand-by'):
            self.store.append('task', 'C', {'id': 'read', 'owner': 'A', 'reviewer': 'R'})
        self.assertEqual(before, self.store.events())

    def test_new_review_handoff_refused_then_delivered_after_return(self):
        data = dict(self.store.state()['tasks']['T']) | {'id': 'RTask', 'reviewer': 'R'}
        self.store.batch([('task', 'C', data, None), ('message', 'C', {'id': 'assign-RTask', 'task': 'RTask', 'to': 'A', 'body': 'Assignment'}, None)])
        self.ack('RTask'); self.proof(task='RTask'); self.hold('R')
        before = self.store.events()
        with self.assertRaisesRegex(Error, 'stand-by'):
            deliver(self.store, 'A', 'RTask')
        self.assertEqual(before, self.store.events())
        self.assertNotIn('candidate', self.store.state()['tasks']['RTask'])
        self.assertEqual(self.cli('standby', '--actor', 'C', '--agent', 'R', '--resume', '--reason', 'Ready'), 0)
        self.assertIn(deliver(self.store, 'A', 'RTask')['handoff'], self.store.state()['messages'])

    def test_sweep_preserves_work_resource_and_process_records(self):
        self.ack()
        self.store.append('start', 'A', {'task': 'T'})
        self.store.append('run.start', 'A', {'id': 'run', 'task': 'T', 'mode': 'proof'})
        self.store.append('resource.acquire', 'A', {'name': 'gate', 'reason': 'Owned'})
        before = self.store.state(); ping = self.ping()
        self.assertEqual(self.sweep(self.clock(ping, 300)), 0)
        for key in ('tasks', 'runs', 'resources'):
            self.assertEqual(before[key], self.store.state()[key])
        self.assertEqual(self.cli('ping', '--actor', 'C', '--all'), 0)
        self.assertEqual(latest_ping(self.store.state(), 'A')['id'], ping['id'])

    def test_new_histories_replay_and_project_without_derived_ages(self):
        ping = self.ping(); self.sweep(self.clock(ping, 300))
        state = initial()
        for event in self.store.events(): apply(state, event)
        self.store.project()
        self.assertEqual(state, json.loads((self.root/'state.json').read_text()))
        self.assertIn('STAND-BY', (self.root/'stato.md').read_text())
        self.assertIn(ping['id'], (self.root/'squadra-posta.md').read_text())
        self.assertNotIn('seconds', state['agents']['A']['availability'])
        self.assertNotIn('due', state['messages'][ping['id']])
        reopened = Store(self.root, read_only=True)
        try: self.assertEqual(reopened.state(), self.store.state())
        finally: reopened.close()

    def test_old_history_does_not_gain_availability_or_presence_projection_fields(self):
        events = copy.deepcopy(self.store.events()); self.store.project()
        expected = (self.root/'state.json').read_bytes()
        self.assertTrue(all('availability' not in a for a in self.store.state()['agents'].values()))
        self.assertNotIn('presence', self.store.state())
        presence(self.store.state(), now())
        self.store.project()
        self.assertEqual(expected, (self.root/'state.json').read_bytes())
        self.assertEqual(events, self.store.events())

    def test_census_cli_is_read_only_even_if_ps_is_denied(self):
        self.store.project(); before = self.store.events()
        paths = [self.root/'state.json', self.root/'stato.md', self.root/'squadra-posta.md']
        stamps = [(p.stat().st_mtime_ns, p.read_bytes()) for p in paths]
        out = io.StringIO()
        with patch('census.subprocess.run', side_effect=PermissionError('denied')), contextlib.redirect_stdout(out):
            self.assertEqual(main(['--root', str(self.root), 'census']), 0)
        report = json.loads(out.getvalue())
        self.assertEqual(report['scan'], 'partial')
        self.assertEqual(len(report['agents']), 4)
        self.assertEqual(stamps, [(p.stat().st_mtime_ns, p.read_bytes()) for p in paths])
        self.assertEqual(before, self.store.events())

    def test_invalid_alive_values_refused(self):
        for value in (-1, True, 1.2, '30'):
            with self.subTest(value=value), self.assertRaises(Error):
                self.store.append('settings', 'C', {'wait_seconds': {'alive': value}})
        ping = self.ping()
        self.store.append('settings', 'C', {'wait_seconds': {'alive': 0}})
        self.assertEqual(self.sweep(ping['utc']), 0)
        self.assertTrue(standby(self.store.state(), 'A'))

    def test_dashboard_reads_real_replayed_standby_and_return_histories(self):
        self.assertIsNotNone(shutil.which('node'), 'Node is required for the dashboard contract')
        old = self.ping()
        waiting = self.store.state(); waiting['presence'] = presence(waiting, self.clock(old, 299))
        overdue = self.store.state(); overdue['presence'] = presence(overdue, self.clock(old, 300))
        self.sweep(self.clock(old, 300))
        fresh = self.ping()
        self.cli('ack', '--actor', 'A', '--message', fresh['id'])
        held = self.store.state(); held['presence'] = presence(held, self.clock(fresh, 7))
        self.sweep(self.clock(fresh, 8))
        active = self.store.state(); active['presence'] = presence(active, self.clock(fresh, 8))
        self.hold('R')
        manual = self.store.state(); manual['presence'] = presence(manual, self.clock(fresh, 9))
        fixture = self.base/'histories.json'; fixture.write_text(json.dumps([held, active, waiting, overdue, manual]))
        source = Path(__file__).resolve().parents[1]
        p = subprocess.run(['node', str(source/'tests/presence_dashboard.js'),
                            str(source/'assets/dashboard.html'), str(fixture)], capture_output=True, text=True, timeout=10)
        self.assertEqual(p.returncode, 0, p.stdout+p.stderr)
        self.assertIn('Presence dashboard:', p.stdout)

    def test_readme_command_tables_match_actual_cli(self):
        commands = set(next(a for a in parser()._actions if a.dest == 'command').choices)
        source = Path(__file__).resolve().parents[1]
        for name in ('README.md', 'README.it.md'):
            text = (source/name).read_text().split('<a id="commands"></a>')[1].split('## 🛡️')[0]
            listed = set()
            for line in text.splitlines():
                if line.startswith('| `'):
                    listed.update(re.findall(r'`([^`]+)`', line.split('|')[1]))
            self.assertEqual(listed-{'--version'}, commands, name)
            self.assertIn('**'+str(len(commands))+' ', text)


class Census(unittest.TestCase):
    def test_exact_executables_and_nul_cwd_paths_with_spaces(self):
        self.assertEqual(parse_processes(' 12 /opt/Codex\n13 /opt/claude\n14 node\n15 codex-helper\n16 /path with spaces/codex\nbad\n'), {12:'codex',13:'claude',16:'codex'})
        self.assertEqual(parse_lsof('p12\0\nfcwd\0n/project with spaces\0\np13\0\nn/another\0'), {12:'/project with spaces',13:'/another'})

    def test_macos_census_filters_siblings_and_includes_registered_worktrees(self):
        with support.tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve(); project=root/'project';project.mkdir()
            sibling=root/'project-other';sibling.mkdir(); external=root/'worktree';external.mkdir()
            state=initial();state['workspace']={'project':str(project)}
            state['agents']['A']={'id':'A','identity':'window','role':'builder','client':'codex','repo':str(external)}
            fake=[SimpleNamespace(returncode=0,stdout='1 codex\n2 claude\n3 codex\n4 claude\n'),
                  SimpleNamespace(returncode=1,stdout=f'p1\0n{project}/sub\0p2\0n{sibling}\0p3\0n{external}\0')]
            with patch('census.sys.platform','darwin'),patch('census.subprocess.run',side_effect=fake) as run:
                report=collect(project,state)
            self.assertEqual([p['pid'] for p in report['processes']],[1,3])
            self.assertEqual(report['scan'],'partial')
            self.assertTrue(any('PID 4' in e for e in report['errors']))
            self.assertTrue(all(c.kwargs['timeout']==5 for c in run.call_args_list))
            self.assertIsNone(report['agents'][0]['last_answer'])

    def test_linux_census_cwd_failure_is_explicit(self):
        with support.tempfile.TemporaryDirectory() as name:
            root=Path(name).resolve()
            with patch('census.sys.platform','linux'),patch('census.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='1 codex\n2 claude\n')),patch.object(Path,'readlink',side_effect=[root,PermissionError()]):
                report=collect(root)
            self.assertEqual([p['pid'] for p in report['processes']],[1])
            self.assertEqual(report['scan'],'partial')
            self.assertTrue(any('PID 2' in e for e in report['errors']))

    def test_missing_ps_timeout_and_unsupported_platform_are_not_empty_success(self):
        with support.tempfile.TemporaryDirectory() as name:
            for error in (FileNotFoundError(),PermissionError(),subprocess.TimeoutExpired('ps',5)):
                with self.subTest(error=error),patch('census.sys.platform','linux'),patch('census.subprocess.run',side_effect=error):
                    self.assertEqual(collect(name)['scan'],'partial')
            with patch('census.sys.platform','win32'),patch('census.subprocess.run') as run:
                self.assertEqual(collect(name)['scan'],'partial');run.assert_not_called()

    def test_census_before_init_creates_no_registry(self):
        with support.tempfile.TemporaryDirectory() as name:
            root=Path(name)/'absent'
            with patch('census.subprocess.run',side_effect=PermissionError()),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['--root',str(root),'census','--project',name]),0)
            self.assertFalse(root.exists())
