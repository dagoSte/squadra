"""MA1–MA3: automatic proposals use the existing ledger path and a loopback MCP fake."""
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import contract
import fake_mcp
import fx
from squadra_memories import panel


class Automatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.info = fx.info()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tmp = Path(self.temp.name)
        self.reg = fx.copy_registry(self.tmp)
        self.home, self.out = self.tmp / 'home', self.tmp / 'out'
        self.home.mkdir()
        fx.install(self.home)
        self.out.mkdir(mode=0o700)
        self.fake = fake_mcp.Fake(fx.TOKEN)
        self.addCleanup(self.fake.close)

    def item(self, sentence='Keep durable receipts before reporting success.', **extra):
        return {'type': 'constraint', 'sentence': sentence, 'certainty': 'sure', 'private_content': 'no',
                'evidence': [{'event': self.info['review_seq']}], **extra}

    def run_cli(self, command='send', *args, automatic=True):
        flags = ['--auto'] if command == 'send' and automatic else []
        return fx.run_connector(command, self.reg, self.out, self.home, *flags, *args,
                                endpoint=self.fake.url if command != 'resolve' else None)

    def ledger_path(self):
        return self.out / ('ledger-' + self.reg.event(1)['hash'] + '.json')

    def ledger(self):
        return json.loads(self.ledger_path().read_text())

    def test_eligibility_matrix_and_terminal_states_send_once(self):
        empty = self.run_cli()
        self.assertEqual(empty.returncode, 0, empty.stderr)
        self.assertEqual(self.fake.requests, [])
        private = self.item(private_content='yes'); private.pop('sentence')
        unanswered = self.item(); unanswered.pop('private_content')
        statuses = ('sent', 'approved', 'rejected', 'uncertain', 'sending', 'abandoned', 'error', 'merged')
        seq = fx.declare(self.reg, 'b1', [self.item(), self.item('Contested.'), private, unanswered,
                                        self.item(certainty='invalid'), self.item(evidence=[{'event': 999999}]),
                                        *[self.item('Recorded ' + s) for s in statuses]])
        fx.reply(self.reg, 'r1', seq, 1, 'object', 'Not durable.')
        ledger = self.ledger()
        ledger['items'] = {f'{seq}:{i}': {'status': s, 'attempts': 7} for i, s in enumerate(statuses, 6)}
        self.ledger_path().write_text(json.dumps(ledger))
        first = self.run_cli()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual([p['arguments']['content'] for p in self.fake.proposes], [self.item()['sentence']])
        self.assertEqual(set(self.ledger()['items']), set(ledger['items']) | {f'{seq}:0'})
        for i, status in enumerate(statuses, 6):
            entry = self.ledger()['items'][f'{seq}:{i}']
            self.assertEqual(entry['status'], 'uncertain' if status == 'sending' else status)
            self.assertEqual(entry['attempts'], 7)
        count = len(self.fake.requests)
        again = self.run_cli()
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(len(self.fake.requests), count)
        self.assertNotIn(fx.SECRET, first.stdout + first.stderr + fx.files_text(self.out, self.home))

    def test_auto_leaves_manual_and_foreign_requests_untouched(self):
        seq = fx.declare(self.reg, 'b1', [self.item(), self.item('A second durable fact.')])
        preview = fx.collect(self.reg, self.tmp / 'preview', home=self.home)
        self.assertEqual(preview.rc, 0, preview.stderr)
        path = fx.request(self.reg, self.home, [f'{seq}:0'])
        before = (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
        done = self.run_cli()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(self.fake.proposes), 2)
        self.assertEqual((path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns), before)
        manual = self.run_cli(automatic=False)
        self.assertEqual(manual.returncode, 0, manual.stderr)
        self.assertFalse(path.exists())
        self.assertEqual(len(self.fake.proposes), 2)
        fx.request(self.reg, self.home, ['999:0'], key='0' * 64)
        body = path.read_bytes()
        fx.declare(self.reg, 'b1', [self.item('Another eligible fact.')])
        done = self.run_cli()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(self.fake.proposes), 3)
        self.assertEqual(path.read_bytes(), body)
        manual = self.run_cli(automatic=False)
        self.assertEqual(manual.returncode, 3)
        self.assertTrue(manual.stderr.startswith('E-ACTION:'), manual.stderr)

    def test_binding_and_ledger_lock_refuse_before_network(self):
        fx.declare(self.reg, 'b1', [self.item()])
        first = self.run_cli()
        self.assertEqual(first.returncode, 0, first.stderr)
        fx.declare(self.reg, 'b1', [self.item('A later fact.')])
        before = len(self.fake.requests)
        refused = self.run_cli('send', '--scope', 'squadra/other')
        self.assertEqual(refused.returncode, 3)
        self.assertTrue(refused.stderr.startswith('E-BINDING:'), refused.stderr)
        fd = os.open(self.ledger_path().with_suffix('.lock'), os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            refused = self.run_cli()
            self.assertEqual(refused.returncode, 3)
            self.assertTrue(refused.stderr.startswith('E-BUSY:'), refused.stderr)
        finally:
            os.close(fd)
        self.assertEqual(len(self.fake.requests), before)

    def test_lost_reply_is_not_retried_and_status_reads_owner_decision(self):
        seq = fx.declare(self.reg, 'b1', [self.item()])
        self.fake.script = ['lost']
        lost = self.run_cli()
        self.assertEqual(lost.returncode, 3)
        self.assertTrue(lost.stderr.startswith('E-UNCERTAIN:'), lost.stderr)
        again = self.run_cli()
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(len(self.fake.proposes), 1)
        self.assertEqual(self.ledger()['items'][f'{seq}:0']['status'], 'uncertain')
        seq2 = fx.declare(self.reg, 'b1', [self.item('A separate durable fact.')])
        done = self.run_cli()
        self.assertEqual(done.returncode, 0, done.stderr)
        proposal = self.ledger()['items'][f'{seq2}:0']['proposal_id']
        self.fake.decide(proposal, 'approved', memory_id='mem_' + '1' * 26)
        status = self.run_cli('status')
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(self.ledger()['items'][f'{seq2}:0']['status'], 'approved')
        again = self.run_cli()
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(len(self.fake.proposes), 2)

    def test_crash_keeps_intent_and_never_repeats_the_request(self):
        seq = fx.declare(self.reg, 'b1', [self.item()])
        self.fake.script = ['hold']
        child = subprocess.Popen(fx.connector_argv('send', self.reg, self.out, '--auto', '--timeout', '30',
                                                   endpoint=self.fake.url), cwd=fx.ROOT,
                                 env=fx.connector_env(self.home), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            if not self.fake.held.wait(30):
                raise fx.FixtureError('automatic send did not reach the local fake')
            self.assertEqual(self.ledger()['items'].get(f'{seq}:0', {}).get('status'), 'sending')
        finally:
            if child.poll() is None:
                child.send_signal(signal.SIGKILL)
            child.communicate(timeout=30)
            self.fake.release.set()
        again = self.run_cli()
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(len(self.fake.proposes), 1)
        self.assertEqual(self.ledger()['items'][f'{seq}:0']['status'], 'uncertain')

    def test_automatic_scope_stops_without_silent_acceptance(self):
        self.fake.auto_scopes.add('squadra/fixture')
        seq = fx.declare(self.reg, 'b1', [self.item(), self.item('Wait for the scope decision.')])
        done = self.run_cli()
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertTrue(done.stderr.startswith('E-SCOPE-POLICY:'), done.stderr)
        self.assertEqual(len(self.fake.proposes), 1)
        self.assertIsNone(self.ledger()['accepted_scope_policy'])
        self.assertEqual(self.ledger()['items'][f'{seq}:0']['status'], 'approved')
        self.assertNotIn(f'{seq}:1', self.ledger()['items'])
        again = self.run_cli()
        self.assertEqual(again.returncode, 3)
        self.assertEqual(len(self.fake.proposes), 1)



class AutomaticInstructions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tmp = Path(self.temp.name)
        self.reg = fx.copy_registry(self.tmp)
        self.home, self.out = self.tmp / 'home', self.tmp / 'out'
        self.home.mkdir()
        fx.install(self.home)
        self.out.mkdir(mode=0o700)

    def test_instructions_and_bilingual_copy_describe_automatic_proposals(self):
        done = fx.collect(self.reg, self.out, home=self.home)
        self.assertEqual(done.rc, 0, done.stderr)
        manifest, view = contract.check(self.home, 'memoranda', self.reg.event(1)['hash'])
        for lang, markers in {'it': ('Invii automatici', "l'approvazione resta in memoranda", 'non disattiva'),
                              'en': ('Automatic sends', 'approval stays in memoranda', 'does not disable')}.items():
            for marker in markers:
                self.assertIn(marker, view['note'][lang])
            how = next(s for s in manifest['about']['sections'] if s['id'] == 'how')
            self.assertIn('invia automaticamente' if lang == 'it' else 'automatically sends',
                          how['items'][2][lang])
        text = (self.home / '.squadra/plugins/memoranda/AGENTS.md').read_text()
        commands = [line for line in text.splitlines() if line.startswith('python3 ')]
        self.assertEqual(commands[:3], [f'python3 -I -B \"$HOME/.squadra/plugins/memoranda/bridge/memoranda_plugin.py\" {cmd} --registry <registry>' for cmd in ('collect', 'send --auto', 'status')])
        for marker in ('every assignment', 'closing seat answered', 'Answer it for your own decisions too',
                       'Never use `--resend`, `--accept-scope-policy` or `resolve` without',
                       'privacy answer', 'Never invent another seat', 'No selection or command is required'):
            self.assertIn(marker, text)
        readme = (fx.BUNDLE / 'README.md').read_text()
        self.assertIn('free trial, then is paid', ' '.join(readme.split()))
        self.assertIn('**0**', readme)
        self.assertIn('**5**', readme)
        self.assertNotIn('No real memoranda.dev send has been', readme)
        self.assertNotIn('**Not run yet.**', readme)


class AutomaticRemedyText(unittest.TestCase):
    def test_pending_request_copy_requires_cancel_before_choosing_again(self):
        empty = {name: [] for name in ('candidates', 'contested', 'withheld', 'evidence_not_found', 'malformed')}
        view = json.loads(panel.build(empty, None))
        for lang, old, new in (
                ('it', 'Puoi sostituire o annullare la richiesta',
                 "Puoi annullare la richiesta e sceglierne un'altra"),
                ('en', 'You can replace or cancel the request',
                 'You can cancel the request and choose again')):
            with self.subTest(language=lang):
                self.assertNotIn(old, view['note'][lang])
                self.assertIn(new, view['note'][lang])
        with self.subTest(document='README.md'):
            readme = (fx.BUNDLE / 'README.md').read_text()
            self.assertNotIn('You can replace a pending choice', readme)
            self.assertIn('You can cancel a pending choice and choose again', readme)

    def test_status_still_runs_after_a_stopped_automatic_send(self):
        for name, text in (('AGENTS.md', panel.agents_bytes().decode('utf-8')),
                           ('README.md', (fx.BUNDLE / 'README.md').read_text())):
            with self.subTest(document=name):
                self.assertIn('Run `status` even when `send --auto` stops, including an uncertain outcome or a scope\n'
                              'that approves automatically. Capture and report its exit code and outcomes too.', text)
