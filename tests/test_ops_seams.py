"""Short answers, closable orphan runs, fair presence and the forwarding rule, through the real CLI."""
import contextlib
import datetime as dt
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import test_squadra as support
import registry
from registry import Error, attention, latest_ping, presence, standby, waits
from squadra import deliver, main
from light_route import POLICY, contract

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'scripts/squadra.py'


def later(stamp, seconds):
    return (dt.datetime.fromisoformat(stamp) + dt.timedelta(seconds=seconds)).isoformat()


class Squad(unittest.TestCase):
    """The shared Workflow squad plus X, a Codex reader with a registered session."""
    git = support.Workflow.git
    task = support.Workflow.task
    ack = support.Workflow.ack
    proof = support.Workflow.proof

    def setUp(self):
        support.Workflow.setUp(self)
        self.store.append('agent', 'X', {'id': 'X', 'role': 'reviewer', 'identity': 'codex-reader', 'client': 'codex',
                                         'repo': str(self.repo), 'skills': [], 'session': 'thread-x'})

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = main(['--root', str(self.root), *args])
            except SystemExit as exc:
                rc = exc.code
        return rc, out.getvalue(), err.getvalue()

    def run_cli(self, *args, timeout=60):
        return subprocess.run([sys.executable, '-B', str(CLI), '--root', str(self.root), *args],
                              env=self.env | {'PYTHONDONTWRITEBYTECODE': '1'},
                              capture_output=True, text=True, timeout=timeout)

    def reader_task(self, name, reviewer=None, pool=None):
        data = {'id': name, 'title': 'Synthetic ' + name, 'owner': 'A', 'repo': str(self.repo),
                'base': registry.snapshot(self.repo)['head'], 'scope': self.store.artifact(self.scope),
                'proof': None, 'expected': None, 'depends': [], 'full_gate': False, 'reviewer': reviewer}
        if pool:
            data['pool'] = pool
        self.store.batch([('task', 'C', data, None), ('message', 'C', {'id': 'assign-' + name, 'to': 'A',
                          'task': name, 'body': 'Scoped work'}, None)])
        self.store.append('ack', 'A', {'message': 'assign-' + name})


class ShortAnswers(Squad):
    def test_task_brief_and_start_print_about_two_kilobytes_with_ten_windows(self):
        for i in range(1, 7):
            role, client = ('builder', 'codex' if i % 2 else 'claude') if i < 5 else ('reviewer', 'claude')
            self.store.append('agent', 'W' + str(i), {'id': 'W' + str(i), 'role': role, 'identity': 'window-' + str(i),
                              'client': client, 'repo': str(self.repo), 'skills': [], 'session': ''})
        self.assertEqual(len({a['identity'] for a in self.store.state()['agents'].values()}), 10)
        brief = self.base / 'brief.md'; brief.write_text('Task one\nwhy\n')
        printed = {}
        rc, printed['task'], err = self.cli('task', '--actor', 'C', '--id', 'T1', '--title', 'One', '--owner', 'W1',
                                            '--reviewer', 'R', '--repo', str(self.repo), '--scope-file', str(self.scope))
        self.assertEqual(rc, 0, err)
        rc, printed['brief'], err = self.cli('brief', '--actor', 'C', '--task', 'T1', '--file', str(brief))
        self.assertEqual(rc, 0, err)
        self.store.append('ack', 'W1', {'message': 'assign-T1'})
        rc, printed['start'], err = self.cli('start', '--actor', 'W1', '--task', 'T1')
        self.assertEqual(rc, 0, err)
        for command, text in printed.items():
            with self.subTest(command=command):
                self.assertLessEqual(len(text.encode()), 2500, 'a short answer, as README and SKILL.md say')
                short = json.loads(text[text.index('{\n  "balance"'):])['balance']
                self.assertNotIn('windows', short, 'the whole ranking is not reprinted')
                self.assertEqual((short['status'], short['full']), ('ranked', 'squadra balance --task T1'))
                self.assertIn(short['read']['window'], {'author', 'outside', 'coord', 'codex-reader', 'window-5', 'window-6'})
                self.assertEqual((short['build']['candidates'], short['read']['candidates']), (5, 6))
                self.assertTrue(short['build']['reason'] and short['read']['reason'])
        rc, out, err = self.cli('balance', '--task', 'T1')
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(json.loads(out)['windows']), 10, 'squadra balance --task keeps the whole ranking')


class OrphanRuns(Squad):
    def declare(self, checks):
        (self.repo / '.squadra-precheck.json').write_text(json.dumps({'schema': 1, 'total_seconds': 90, 'checks': checks}))
        self.git('add', '.squadra-precheck.json')
        self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'declare checks')

    def wait_for(self, condition, what, seconds=20):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(0.05)
        self.fail('timed out waiting for ' + what)

    def running(self, mode='precheck'):
        return {r['check']: r for r in self.store.state()['runs'].values()
                if r['mode'] == mode and r['status'] == 'running'}

    def test_sigterm_during_the_second_precheck_then_reconcile_supersede_and_close(self):
        marker = self.base / 'slow-marker'
        slow = ("import os, time\nprint('slow started', flush=True)\n"
                "if os.path.exists(" + repr(str(marker)) + "): time.sleep(30)\n")
        self.declare([{'name': 'quick', 'argv': [sys.executable, '-c', "print('quick')"], 'timeout_seconds': 5},
                      {'name': 'slow', 'argv': [sys.executable, '-c', slow], 'timeout_seconds': 60}])
        self.ack()
        proof = ['--', sys.executable, '-c', "print('proof')"]
        p = self.run_cli('prove', '--actor', 'A', '--task', 'T', *proof)
        self.assertEqual(p.returncode, 0, p.stderr)
        marker.write_text('hold')
        killed = subprocess.Popen([sys.executable, '-B', str(CLI), '--root', str(self.root), 'deliver', '--actor', 'A',
                                   '--task', 'T'], env=self.env | {'PYTHONDONTWRITEBYTECODE': '1'},
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: killed.poll() is None and killed.kill())
        orphan = self.wait_for(lambda: self.running().get('slow') if len(self.running()) == 2 else None, 'two prechecks')
        log = Path(orphan['folder']) / 'stdout.log'
        self.wait_for(lambda: log.is_file() and 'slow started' in log.read_text(), 'the second precheck output')
        self.assertTrue(registry.run_lease_paths(self.root, self.running()['quick']['id'])[3].is_file())
        killed.send_signal(signal.SIGTERM)
        killed.communicate(timeout=20)
        self.assertEqual(killed.returncode, -signal.SIGTERM)
        group = json.loads(registry.run_lease_paths(self.root, orphan['id'])[2].read_text())['pgid']
        os.killpg(group, signal.SIGKILL)

        def gone():
            try:
                os.killpg(group, 0)
            except ProcessLookupError:
                return True
            except PermissionError:
                return False
            return False
        self.wait_for(gone, 'the orphaned check to end')
        marker.unlink()
        runs = self.running()
        self.assertEqual(set(runs), {'quick', 'slow'}, 'SIGTERM left both prechecks running')
        refused = self.run_cli('task', '--actor', 'C', '--id', 'T2', '--title', 'Replacement', '--owner', 'A',
                               '--repo', str(self.repo), '--scope-file', str(self.scope), '--reviewer', 'R',
                               '--supersedes', 'T')
        self.assertEqual(refused.returncode, 1)
        self.assertIn('Reconcile first', refused.stderr)

        p = self.run_cli('cancel', '--actor', 'C', '--run', runs['slow']['id'], '--reconcile')
        self.assertEqual(p.returncode, 1, 'only the starter reconciles')
        self.assertIn('original run starter', p.stderr)
        p = self.run_cli('cancel', '--actor', 'A', '--run', runs['quick']['id'], '--reconcile')
        self.assertEqual(p.returncode, 0, p.stderr)
        quick = json.JSONDecoder().raw_decode(p.stdout)[0]
        self.assertEqual((quick['status'], quick['native_rc'], quick['source']), ('passed', 0, 'receipt'))
        p = self.run_cli('cancel', '--actor', 'A', '--run', runs['slow']['id'], '--reconcile')
        self.assertEqual(p.returncode, 0, p.stderr)
        ended = json.JSONDecoder().raw_decode(p.stdout)[0]
        self.assertEqual((ended['status'], ended['native_rc'], ended['source']), ('interrupted', None, 'logs'))
        self.assertIn('stdout.log', ended['artifacts'])
        record = self.store.state()['runs'][runs['slow']['id']]
        kept = next(a for a in record['artifacts'] if a['name'] == 'stdout.log')
        self.assertIn(b'slow started', (self.root / 'artifacts' / kept['sha256']).read_bytes())
        doctor = self.run_cli('doctor')
        self.assertEqual(json.loads(doctor.stdout)['unknown_running'], [])

        for args in (('task', '--actor', 'C', '--id', 'T2', '--title', 'Replacement', '--owner', 'A', '--repo',
                      str(self.repo), '--scope-file', str(self.scope), '--reviewer', 'R', '--supersedes', 'T'),
                     ('ack', '--actor', 'A', '--message', 'assign-T2'),
                     ('prove', '--actor', 'A', '--task', 'T2', *proof)):
            p = self.run_cli(*args)
            self.assertEqual(p.returncode, 0, (args[0], p.stderr))
        p = self.run_cli('deliver', '--actor', 'A', '--task', 'T2')
        self.assertEqual(p.returncode, 0, p.stderr)
        handoff = json.JSONDecoder().raw_decode(p.stdout)[0]['handoff']
        for args in (('ack', '--actor', 'R', '--message', handoff),
                     ('review', '--actor', 'R', '--task', 'T2', '--verdict', 'accepted', '--report', str(self.report)),
                     ('close', '--actor', 'C', '--task', 'T2')):
            p = self.run_cli(*args)
            self.assertEqual(p.returncode, 0, (args[0], p.stderr))
        state = self.store.state()
        self.assertEqual((state['tasks']['T']['superseded_by'], state['tasks']['T2']['status']), ('T2', 'done'))

    def leased(self, name, task='T', mode='precheck', pgid=None, receipt=None, output=None, **extra):
        """A run whose starter died: lease metadata, and optionally its receipt and its logs."""
        if self.store.state()['tasks'][task]['status'] == 'waiting-ack':
            self.ack(task)
        self.store.append('run.start', 'A', {'id': name, 'task': task, 'mode': mode, 'check': 'quick',
                          'folder': str(self.root / 'runs' / name), 'lease_version': 1, 'covers': [], **extra})
        with registry.run_lease(self.root, name):
            pass
        _, _, metadata, terminal = registry.run_lease_paths(self.root, name)
        registry.atomic(metadata, registry.canonical({'run': name, 'phase': 'spawned', 'pgid': pgid}).encode())
        if receipt:
            registry.atomic(terminal, registry.canonical({'run': name, 'ended': registry.now(), **receipt}).encode())
        if output is not None:
            folder = self.root / 'runs' / name
            folder.mkdir(parents=True, mode=0o700)
            (folder / 'stdout.log').write_text(output)
            (folder / 'stderr.log').write_text('')
        return name

    def test_reconcile_is_the_starters_under_the_lease_and_never_while_a_group_lives(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
        self.addCleanup(lambda: child.poll() is None and child.kill())
        name = self.leased('orphan', pgid=child.pid, output='partial output\n')
        rc, _, err = self.cli('cancel', '--actor', 'C', '--run', name, '--reconcile')
        self.assertEqual((rc, 'original run starter' in err), (1, True), err)
        with registry.run_lease(self.root, name):
            rc, _, err = self.cli('cancel', '--actor', 'A', '--run', name, '--reconcile')
        self.assertEqual((rc, 'lease is held' in err), (1, True), err)
        rc, _, err = self.cli('cancel', '--actor', 'A', '--run', name, '--reconcile')
        self.assertEqual((rc, 'still live' in err), (1, True), err)
        child.kill(); child.wait(timeout=5)
        rc, out, err = self.cli('cancel', '--actor', 'A', '--run', name, '--reconcile')
        self.assertEqual(rc, 0, err)
        run = self.store.state()['runs'][name]
        self.assertEqual((run['status'], run['native_rc'], run['reconciled']), ('interrupted', None, {'source': 'logs'}))
        self.assertEqual([a['name'] for a in run['artifacts']], ['stdout.log', 'stderr.log'])
        empty = self.leased('empty')
        rc, _, err = self.cli('cancel', '--actor', 'A', '--run', empty, '--reconcile')
        self.assertEqual((rc, 'abandoned instead' in err), (1, True), err)

    def test_a_passed_proof_receipt_is_kept_but_never_rebuilt_into_a_passed_proof(self):
        name = self.leased('proof-orphan', mode='proof', receipt={'status': 'passed', 'native_rc': 0}, output='ok\n')
        rc, out, err = self.cli('cancel', '--actor', 'A', '--run', name, '--reconcile')
        self.assertEqual(rc, 0, err)
        run = self.store.state()['runs'][name]
        self.assertEqual((run['status'], run['native_rc']), ('interrupted', 0))
        self.assertEqual((run['reconciled']['source'], run['reconciled']['receipt_status']), ('receipt', 'passed'))
        receipt = registry.run_lease_paths(self.root, name)[3].read_bytes()
        self.assertEqual(run['reconciled']['receipt']['sha256'], registry.digest(receipt))
        self.assertFalse([p for p in self.store.state()['tasks']['T']['proofs'] if p['status'] == 'passed'])

    def test_the_validator_refuses_a_record_that_hides_a_log_or_differs_from_the_receipt(self):
        name = self.leased('forged', receipt={'status': 'failed', 'native_rc': 1}, output='trace\n')
        honest = registry.reconcile_end(self.store, self.store.state()['runs'][name]) \
            if hasattr(registry, 'reconcile_end') else None
        forged = {'id': name, 'status': 'passed', 'native_rc': 0, 'ended': registry.now(), 'artifacts': [],
                  'reconciled': {'source': 'receipt', 'receipt_status': 'passed'}}
        with self.assertRaises(Error):
            self.store.append('run.end', 'A', forged)
        self.assertIsNotNone(honest, 'the reconciliation record is built by the registry')
        with self.assertRaisesRegex(Error, 'every file the run left'):
            self.store.append('run.end', 'A', {**honest, 'artifacts': honest['artifacts'][1:]})
        with self.assertRaisesRegex(Error, 'differs from the terminal result receipt'):
            self.store.append('run.end', 'A', {**honest, 'status': 'passed', 'native_rc': 0})
        with self.assertRaisesRegex(Error, 'original run starter'):
            self.store.append('run.end', 'C', honest)
        self.store.append('run.end', 'A', honest)
        self.assertEqual(self.store.state()['runs'][name]['status'], 'failed')

    def test_a_light_task_can_be_promoted_once_its_orphaned_precheck_is_reconciled(self):
        proof = [sys.executable, '-c', "print('proof')"]
        data = {'id': 'L', 'title': 'Small change', 'owner': 'A', 'reviewer': 'R', 'repo': str(self.repo),
                'base': registry.snapshot(self.repo)['head'], 'scope': self.store.artifact(self.scope), 'proof': proof,
                'requirements': [{'id': 'R1', 'text': 'Keep it'}], 'depends': [], 'full_gate': False,
                'expected': None, 'route': 'light', 'light_policy': dict(POLICY)}
        data['contract'] = contract(data)
        self.store.batch([('task', 'C', data, None), ('message', 'C', {'id': 'assign-L', 'to': 'A', 'task': 'L',
                          'body': 'Small change'}, None)])
        self.store.append('ack', 'A', {'message': 'assign-L'})
        name = self.leased('light-orphan', task='L', receipt={'status': 'failed', 'native_rc': 2}, output='',
                           contract=data['contract'])
        proof_file = self.base / 'proof.json'; proof_file.write_text(json.dumps(proof))
        promote = ('route', '--actor', 'C', '--task', 'L', '--full', '--scope-file', str(self.scope),
                   '--proof-file', str(proof_file), '--reason', 'A second read needs the full route')
        rc, _, err = self.cli(*promote)
        self.assertEqual((rc, 'active run' in err), (1, True), err)
        rc, _, err = self.cli('cancel', '--actor', 'A', '--run', name, '--reconcile')
        self.assertEqual(rc, 0, err)
        rc, _, err = self.cli(*promote)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.store.state()['tasks']['L']['route'], 'full')


class FairPresence(Squad):
    def sweep(self, at):
        with patch('squadra.now', return_value=at), patch('registry.now', return_value=at):
            return self.cli('ping', '--actor', 'C', '--sweep')[0]

    def test_an_undelivered_ping_never_qualifies_the_sweep(self):
        self.assertEqual(self.cli('ping', '--actor', 'C', '--to', 'A')[0], 0)
        ping = latest_ping(self.store.state(), 'A')
        self.store.append('transport', 'C', {'message': ping['id'], 'via': 'codex-queue', 'delivered': False})
        self.store.append('transport', 'C', {'message': ping['id'], 'via': 'manual', 'delivered': None})
        self.assertEqual(self.sweep(later(ping['utc'], 3600)), 0)
        self.assertFalse(standby(self.store.state(), 'A'), 'a ping that never arrived cannot be unanswered')
        row = next(r for r in presence(self.store.view(), later(ping['utc'], 3600)) if r['agent'] == 'A')
        self.assertEqual((row['delivered'], row['due']), (False, False))
        with patch('registry.now', return_value=later(ping['utc'], 3600)), self.assertRaisesRegex(Error, 'no longer overdue'):
            self.store.append('availability', 'C', {'agent': 'A', 'status': 'stand-by', 'ping': ping['id'],
                                                    'reason': 'Precomputed'})
        self.store.append('transport', 'C', {'message': ping['id'], 'via': 'codex-queue', 'delivered': True})
        self.assertEqual(self.sweep(later(ping['utc'], 3600)), 0)
        self.assertTrue(standby(self.store.state(), 'A'))

    def test_delivery_skips_a_pool_member_in_stand_by(self):
        self.reader_task('P', pool=['R', 'X'])
        self.assertEqual(self.proof(task='P')['status'], 'passed')
        self.store.append('availability', 'C', {'agent': 'X', 'status': 'stand-by', 'reason': 'Window away'})
        result = deliver(self.store, 'A', 'P')
        self.assertEqual([h.rsplit('-', 1)[1] for h in result['handoffs']], ['R'])
        self.assertEqual(result['skipped_standby'], ['X'])
        self.store.append('ack', 'R', {'message': result['handoffs'][0]})
        self.assertEqual(self.store.state()['tasks']['P']['claim']['agent'], 'R')


class Forwarding(Squad):
    def forward(self, at):
        return [w for w in waits(self.store.state(), at) if w['kind'] == 'to-forward']

    def test_a_followed_claude_recipient_is_never_to_forward(self):
        from inbox_follow import Follower
        self.ack()
        self.store.append('message', 'A', {'id': 'M', 'to': 'R', 'body': 'Read the plan'})
        rc, out, err = self.cli('transport', '--actor', 'A', '--message', 'M', '--via', 'manual')
        self.assertEqual(rc, 0, err)
        reader = registry.Store(self.root, read_only=True)
        self.addCleanup(reader.close)
        with contextlib.redirect_stdout(io.StringIO()):
            follower = Follower(reader, ['R'])
            shown = follower.poll()[2]
        self.assertIn('M', [row.get('id') for row in shown], 'the follower shows the mail')
        utc = self.store.state()['messages']['M']['utc']
        self.assertEqual(self.forward(later(utc, 600)), [], 'no forwarder could end this wait')
        self.assertEqual([w['id'] for w in waits(self.store.state(), later(utc, 900)) if w['kind'] == 'ack'], ['M'])

    def test_an_untransported_handoff_to_a_codex_reader_is_to_forward(self):
        self.ack()
        self.reader_task('V', reviewer='X')
        self.assertEqual(self.proof(task='V')['status'], 'passed')
        handoff = deliver(self.store, 'A', 'V')['handoff']
        message = self.store.state()['messages'][handoff]
        self.assertEqual((message['from'], message.get('transport')), ('C', None))
        self.assertEqual(self.forward(later(message['utc'], 119)), [])
        rows = self.forward(later(message['utc'], 120))
        self.assertEqual([(w['id'], w['who'], w['utc']) for w in rows], [(handoff, ['C'], message['utc'])])
        self.assertIn('--actor X --message ' + handoff, rows[0]['ack_command'])
        self.store.append('transport', 'C', {'message': handoff, 'via': 'codex-queue', 'delivered': True})
        self.assertEqual(self.forward(later(message['utc'], 120)), [])

    def test_manual_transport_is_unconfirmed_and_keeps_a_codex_wait_open(self):
        self.ack()
        self.store.append('message', 'C', {'id': 'N', 'to': 'X', 'body': 'Note for the Codex reader'})
        rc, out, err = self.cli('transport', '--actor', 'C', '--message', 'N', '--via', 'manual')
        self.assertEqual(rc, 0, err)
        record = [e['data'] for e in self.store.events() if e['kind'] == 'transport'][-1]
        self.assertIsNone(record['delivered'], 'a hand-off by hand is unconfirmed, not failed')
        utc = self.store.state()['messages']['N']['utc']
        self.assertEqual([w['id'] for w in self.forward(later(utc, 3600))], ['N'])
        self.store.append('ack', 'X', {'message': 'N'})
        self.assertEqual(self.forward(later(utc, 3600)), [])


class CensusInSandbox(unittest.TestCase):
    def test_census_inside_a_codex_sandbox_says_to_run_it_from_a_claude_window(self):
        from census import collect
        with support.tempfile.TemporaryDirectory() as name:
            project = Path(name).resolve()
            own = [SimpleNamespace(returncode=0, stdout='7 codex\n'), SimpleNamespace(returncode=0, stdout=f'p7\0n{project}\0')]
            with patch.dict(os.environ, {'CODEX_SANDBOX': 'seatbelt'}), patch('census.sys.platform', 'darwin'), \
                    patch('census.subprocess.run', side_effect=own):
                report = collect(project)
            self.assertEqual(report['scan'], 'partial', 'a sandboxed process list is never a complete census')
            self.assertTrue(any('Run census from a Claude window' in e for e in report['errors']), report['errors'])
            with patch.dict(os.environ, {}, clear=False), patch('census.sys.platform', 'linux'), \
                    patch('census.subprocess.run', side_effect=PermissionError()):
                os.environ.pop('CODEX_SANDBOX', None)
                report = collect(project)
            self.assertTrue(any('Run census from a Claude window' in e for e in report['errors']), report['errors'])


class UntilFirst(Squad):
    def test_a_background_follower_exits_after_the_first_mail_with_its_cursor(self):
        self.ack()
        head = self.store.events()[-1]['seq']
        p = subprocess.Popen([sys.executable, '-B', str(CLI), '--root', str(self.root), 'inbox', '--agent', 'R',
                              '--follow', '--until-first', '--since', str(head), '--timeout', '30'],
                             env=self.env | {'PYTHONDONTWRITEBYTECODE': '1'}, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        time.sleep(1)
        self.assertIsNone(p.poll(), 'no mail yet: the follower keeps waiting')
        started = time.monotonic()
        self.store.append('message', 'C', {'id': 'WAKE', 'to': 'R', 'body': 'Wake up'})
        out, err = p.communicate(timeout=20)
        self.assertEqual(p.returncode, 0, err)
        self.assertLess(time.monotonic() - started, 10, 'it ends on the mail, not on its timeout')
        rows = [json.loads(line) for line in out.splitlines()]
        self.assertEqual([r['kind'] for r in rows], ['message', 'cursor'])
        self.assertEqual(rows[0]['id'], 'WAKE')
        self.assertEqual(rows[1]['event'], self.store.events()[-1]['seq'])


class Documentation(unittest.TestCase):
    def test_docs_state_the_new_rules_where_they_are_used(self):
        work = (ROOT / 'references/work.md').read_text()
        for text in ('run_in_background', '--until-first', 'status cannot tell', 'The sweep acts only on a',
                     'Delivery skips a member in stand-by', 'records `delivered: null`', 'To forward.'):
            self.assertIn(text, work, text)
        self.assertIn('--reconcile', (ROOT / 'references/runtime.md').read_text())
        self.assertIn('Run the census from a Claude window', (ROOT / 'references/start.md').read_text())
        skill = (ROOT / 'SKILL.md').read_text()
        self.assertIn('read the short balance printed after the', skill)
        for name, cancel, size in (('README.md', 'with `--reconcile`', 'about 2.0 KB with 10 windows'),
                                   ('README.it.md', 'con `--reconcile`', 'circa 2,0 KB con 10 finestre')):
            readme = (ROOT / name).read_text()
            self.assertIn(cancel, readme)
            self.assertIn(size, readme)
            self.assertIn('--until-first', readme)


if __name__ == '__main__':
    unittest.main()
