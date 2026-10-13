"""Option D: real registry declarations, explicit selections and local fake-MCP effects, never real HOME/network."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import contract
import fake_mcp
import fx
from squadra_memories import actions, cli, connector, panel
from squadra_memories.source import Refusal


class SelectionFiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.key = 'a' * 64
        self.reg = type('Registry', (), {'event': lambda _, seq: {'hash': 'a' * 64}})()
        self.path = fx.request(self.reg, self.home, ['20:0'])
        self.good = json.loads(self.path.read_text())

    def opened(self):
        return actions.Selection(self.home, self.key)

    def refusal(self):
        with self.assertRaises(Refusal) as caught:
            with self.opened():
                pass
        self.assertEqual(caught.exception.code, 'E-ACTION')

    def test_exact_shape_and_valid_boundaries(self):
        for ids, date in [(['20:0'], '2024-02-29T23:59:59Z'),
                          ([f'{n}:0' for n in range(1, 501)], '2026-10-11T12:00:00Z')]:
            fx.request(self.reg, self.home, ids, date)
            with self.opened() as selection:
                self.assertEqual(selection.data['ids'], ids)
            self.assertIsNone(selection.original)
            self.assertIsNone(selection.lock)
        self.path.unlink()
        with self.opened() as selection:
            self.assertIsNone(selection.data)

    def test_strict_json_ids_timestamp_and_foreign_key(self):
        invalid = [{'schema': True}, {'schema': 2}, {'extra': 'secret'}, {'key': 'b' * 64}, {'key': 'A' * 64},
                   {'ids': []}, {'ids': ['20:0', '20:0']}, {'ids': [f'{n}:0' for n in range(1, 502)]},
                   {'ids': ['0:0']}, {'ids': ['01:1']}, {'ids': ['2:01']}, {'ids': ['2:' + '0' * 40]},
                   {'ids': [True]}, {'requested': '2026-02-30T00:00:00Z'}, {'requested': '2026-10-11T00:00:00+00:00'},
                   {'requested': '2026-10-11T00:00:00.0Z'}, {'requested': '2026-10-11T24:00:00Z'}]
        bodies = [json.dumps({**self.good, **change}).encode() for change in invalid]
        bodies += [b'', b'\xff', b'{"schema":1,"schema":1}', b'{"schema":NaN}', b'x' * (65536 + 1)]
        for body in bodies:
            with self.subTest(body=body[:80]):
                self.path.write_bytes(body)
                self.refusal()
                self.assertEqual(self.path.read_bytes(), body)

    def test_no_follow_each_component_private_hardlink_and_types(self):
        for relative in ('.squadra', '.squadra/plugins', '.squadra/plugins/memoranda',
                         '.squadra/plugins/memoranda/actions', '.squadra/plugins/memoranda/actions/send.json',
                         '.squadra/plugins/memoranda/actions/send.lock'):
            with self.subTest(relative=relative):
                path = self.home / relative
                held = path.with_name(path.name + '.held')
                path.rename(held)
                path.symlink_to(held, target_is_directory=held.is_dir())
                self.refusal()
                path.unlink()
                held.rename(path)
        for mode in (0o640, 0o644, 0o700):
            self.path.chmod(mode)
            self.refusal()
        self.path.chmod(0o600)
        twin = self.home / 'hardlink'
        os.link(self.path, twin)
        self.refusal()
        twin.unlink()
        lock = self.path.with_name('send.lock')
        os.link(lock, twin)
        self.refusal()
        twin.unlink()
        for name in ('send.json', 'send.lock'):
            target = self.path.with_name(name)
            saved = target.read_bytes()
            target.unlink()
            os.mkfifo(target, 0o600)
            self.refusal()
            target.unlink()
            target.write_bytes(saved)
            target.chmod(0o600)
        with patch.object(actions.os, 'getuid', return_value=os.getuid() + 1):
            self.refusal()
        folder = self.path.parent
        folder.chmod(0o770)
        self.refusal()
        folder.chmod(0o700)

    def test_shared_lock_is_bounded_and_fds_close(self):
        lock = os.open(self.path.with_name('send.lock'), os.O_RDWR)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            selection = self.opened()
            with patch.object(actions, 'LOCK_WAIT', 0):
                with self.assertRaises(Refusal) as caught:
                    selection.__enter__()
            self.assertEqual(caught.exception.code, 'E-ACTION-BUSY')
            self.assertEqual((selection.lock, selection.original, selection.folder, selection.plugin), (None,) * 4)
        finally:
            os.close(lock)

    def test_cleanup_never_deletes_replacement_or_recreates_cancel(self):
        with self.opened() as selection:
            original = self.path.stat().st_ino
            fx.request(self.reg, self.home, ['20:0'])  # Identical JSON and second; distinct inode.
            self.assertNotEqual(self.path.stat().st_ino, original)
            selection.clear()
            self.assertTrue(self.path.is_file())
        with self.opened() as selection:
            fx.request(self.reg, self.home, None)
            selection.clear()
            self.assertFalse(self.path.exists())
        fx.request(self.reg, self.home, ['20:0'])
        with self.opened() as selection:
            selection.clear()
            self.assertFalse(self.path.exists())

    def test_complete_uncertainty_wins_over_ineligibility(self):
        for status in ('sending', 'uncertain'):
            self.assertFalse(actions.complete(['20:0'], [], {'items': {'20:0': {'status': status}}}))
        self.assertTrue(actions.complete(['20:0'], [], {'items': {}}))


class ActionJourney(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.info = fx.info()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tmp = Path(self.temp.name)
        self.reg = fx.copy_registry(self.tmp)
        self.home = self.tmp / 'home'
        self.home.mkdir()
        self.out = self.tmp / 'out'
        self.out.mkdir(mode=0o700)
        self.fake = fake_mcp.Fake(fx.TOKEN)
        self.addCleanup(self.fake.close)

    def item(self, text='Only a recorded receipt proves completion.', **extra):
        return {'type': 'constraint', 'sentence': text, 'certainty': 'sure', 'private_content': 'no',
                'evidence': [{'event': self.info['review_seq']}], **extra}

    def declare(self, *items):
        seq = fx.declare(self.reg, 'b1', list(items or [self.item()]))
        run = fx.collect(self.reg, self.tmp / ('preview-' + str(seq)), home=self.home)
        if run.rc:
            raise fx.FixtureError(run.stderr)
        return seq

    def request(self, ids):
        return fx.request(self.reg, self.home, ids)

    def run_cli(self, command='send', *extra):
        return fx.run_connector(command, self.reg, self.out, self.home, *extra, endpoint=self.fake.url if command != 'resolve' else None)

    def ledger(self):
        return json.loads(next(self.out.glob('ledger-*.json')).read_text())

    def rows(self):
        return contract.check(self.home, 'memoranda', self.reg.event(1)['hash'])[1]['items']

    def test_absent_empty_foreign_never_send_all(self):
        seq = self.declare(self.item(), self.item('A second constraint.'))
        absent = self.run_cli()
        self.assertEqual(absent.returncode, 5, absent.stderr)
        self.assertEqual(json.loads(absent.stdout), {'ok': True, 'outcome': 'no_request', 'sent': 0})
        self.assertEqual(self.fake.requests, [])
        for body in (b'', json.dumps({'schema': 1, 'key': self.reg.event(1)['hash'], 'ids': [],
                                     'requested': '2026-10-11T12:00:00Z'}).encode()):
            path = self.request([f'{seq}:0'])
            path.write_bytes(body)
            result = self.run_cli()
            self.assertEqual(result.returncode, 3)
            self.assertTrue(result.stderr.startswith('E-ACTION:'), result.stderr)
            self.assertEqual(path.read_bytes(), body)
        path = fx.request(self.reg, self.home, [f'{seq}:0'], key='0' * 64)
        result = self.run_cli()
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(self.fake.requests, [])
        self.assertTrue(path.exists())

    def test_selected_one_only_other_remains_draft(self):
        seq = self.declare(self.item(), self.item('Keep the second local.'))
        path = self.request([f'{seq}:0', '99999:0'])
        done = self.run_cli()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([p['arguments']['content'] for p in self.fake.proposes], [self.item()['sentence']])
        rows = {r['id']: r for r in self.rows()}
        self.assertFalse(rows[f'{seq}:0']['selectable'])
        self.assertTrue(rows[f'{seq}:1']['selectable'])
        self.assertFalse(path.exists())
        again = self.run_cli()
        self.assertEqual(again.returncode, 5)
        self.assertEqual(len(self.fake.proposes), 1)
        self.assertNotIn(fx.SECRET, done.stdout + done.stderr + fx.files_text(self.out, self.home))

    def test_fresh_objection_and_private_or_contested_are_not_selectable(self):
        secret = self.item(private_content='yes')
        secret.pop('sentence')
        unanswered = self.item('No privacy answer.')
        unanswered.pop('private_content')
        seq = self.declare(self.item(), self.item('Contested.'), secret, unanswered)
        fx.reply(self.reg, 'r1', seq, 1, 'object', 'Not durable.')
        preview = fx.collect(self.reg, self.tmp / 'contested', home=self.home)
        self.assertEqual(preview.rc, 0, preview.stderr)
        rows = {r['id']: r for r in self.rows()}
        self.assertTrue(rows[f'{seq}:0']['selectable'])
        self.assertTrue(all(not rows[f'{seq}:{n}'].get('selectable', False) for n in (1, 2, 3)))
        path = self.request([f'{seq}:0', f'{seq}:1', f'{seq}:2', f'{seq}:3'])
        fx.reply(self.reg, 'r1', seq, 0, 'object', 'New objection after selection.')
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.fake.requests, [])
        self.assertFalse(path.exists())

    def test_panel_exclusion_and_durable_outcome_prevent_resend(self):
        seq = self.declare()
        self.request([f'{seq}:0'])
        panel_path = self.home / '.squadra/plugins/memoranda/panels' / (self.reg.event(1)['hash'] + '.json')
        doc = json.loads(panel_path.read_text())
        doc['items'][0]['selectable'] = False
        panel_path.write_text(json.dumps(doc))
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.fake.requests, [])
        self.assertTrue((self.home / '.squadra/plugins/memoranda/actions/send.json').exists())
        fx.choose_drafts(self.reg, self.home)
        with patch.object(actions.Selection, 'clear', side_effect=Refusal('E-ACTION')):
            args = SimpleNamespace(registry=self.reg.root, out_root=self.out,
                                   token_file=None, endpoint=self.fake.url, scope=None, timeout=3, accept_scope_policy=False)
            with patch.dict(os.environ, fx.connector_env(self.home), clear=True):
                with self.assertRaises(Refusal):
                    cli.run_send(args)
        self.assertEqual(len(self.fake.proposes), 1)
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.fake.proposes), 1)
        self.assertFalse((self.home / '.squadra/plugins/memoranda/actions/send.json').exists())

    def test_malformed_panel_selection_metadata_refuses_without_network(self):
        seq = self.declare()
        request = self.request([f'{seq}:0'])
        path = self.home / '.squadra/plugins/memoranda/panels' / (self.reg.event(1)['hash'] + '.json')
        document = json.loads(path.read_text())
        broken = []
        for value in (True, 2):
            broken.append({**document, 'schema': value})
        for update in ({'selectable': 1}, {'status': 'sent', 'selectable': True}, {'status': 'unknown'}):
            broken.append({**document, 'items': [{**document['items'][0], **update}]})
        broken.append({**document, 'items': document['items'] * 2})
        for invalid in broken:
            with self.subTest(invalid=invalid):
                path.write_text(json.dumps(invalid))
                result = self.run_cli()
                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertTrue(result.stderr.startswith('E-ACTION:'), result.stderr)
                self.assertTrue(request.exists())
        self.assertEqual(self.fake.requests, [])

    def test_request_is_snapshotted_only_after_ledger_admission(self):
        seq = self.declare(self.item(), self.item('Replacement won before admission.'))
        self.request([f'{seq}:0'])
        args = SimpleNamespace(registry=self.reg.root, out_root=self.out,
                                   token_file=None, endpoint=self.fake.url, scope=None, timeout=3, accept_scope_policy=False)
        enter = connector.Ledger.__enter__
        def admitted(ledger):
            result = enter(ledger)
            self.request([f'{seq}:1'])
            return result
        with patch.object(connector.Ledger, '__enter__', admitted), patch.dict(os.environ, fx.connector_env(self.home), clear=True):
            result = cli.run_send(args)
        self.assertTrue(result['ok'])
        self.assertEqual([p['arguments']['content'] for p in self.fake.proposes], ['Replacement won before admission.'])

    def test_uncertain_resolve_resend_then_clear(self):
        seq = self.declare()
        path = self.request([f'{seq}:0'])
        original = path.read_bytes()
        self.fake.script = ['lost']
        first = self.run_cli()
        self.assertEqual(first.returncode, 3)
        self.assertTrue(first.stderr.startswith('E-UNCERTAIN:'), first.stderr)
        again = self.run_cli()
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(len(self.fake.proposes), 1)
        self.assertEqual(path.read_bytes(), original)
        resolved = self.run_cli('resolve', '--item', f'{seq}:0', '--resend')
        self.assertEqual(resolved.returncode, 0, resolved.stderr)
        self.assertEqual(path.read_bytes(), original)
        sent = self.run_cli()
        self.assertEqual(sent.returncode, 0, sent.stderr)
        self.assertEqual(len(self.fake.proposes), 2)
        self.assertFalse(path.exists())

    def test_uncertain_adopt_or_abandon_no_new_proposal(self):
        for mode in ('adopt', 'abandon'):
            with self.subTest(mode=mode):
                seq = self.declare(self.item('Resolution ' + mode + '.'))
                path = self.request([f'{seq}:0'])
                before = len(self.fake.proposes)
                self.fake.script = ['lost']
                self.assertEqual(self.run_cli().returncode, 3)
                proposal = list(self.fake.proposals)[-1]
                flags = ('--proposal', proposal) if mode == 'adopt' else ('--abandon',)
                self.assertEqual(self.run_cli('resolve', '--item', f'{seq}:0', *flags).returncode, 0)
                self.assertTrue(path.exists())
                result = self.run_cli()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(self.fake.proposes), before + 1)
                self.assertFalse(path.exists())

    def test_partial_policy_stop_keeps_unsent_selection(self):
        self.fake.auto_scopes.add('squadra/fixture')
        seq = self.declare(self.item(), self.item('Second selected.'))
        path = self.request([f'{seq}:0', f'{seq}:1'])
        result = self.run_cli()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(len(self.fake.proposes), 1)
        self.assertTrue(path.exists())
        self.assertEqual(self.run_cli().returncode, 3)
        self.assertEqual(len(self.fake.proposes), 1)
        result = self.run_cli('send', '--accept-scope-policy')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.fake.proposes), 2)
        self.assertFalse(path.exists())

    def test_replace_or_cancel_during_rpc_finishes_admitted_batch(self):
        for mode in ('replace', 'cancel'):
            with self.subTest(mode=mode):
                seq = self.declare(self.item(mode + ' one.'), self.item(mode + ' two.'))
                path = self.request([f'{seq}:0', f'{seq}:1'])
                self.fake.held.clear()
                self.fake.release.clear()
                self.fake.script = ['hold']
                before = len(self.fake.proposes)
                child = subprocess.Popen(fx.connector_argv('send', self.reg, self.out, '--timeout', '60', endpoint=self.fake.url),
                                         cwd=fx.ROOT, env=fx.connector_env(self.home), stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, text=True)
                try:
                    if not self.fake.held.wait(30):
                        raise fx.FixtureError('client did not reach hold')
                    self.request([f'{seq}:1'] if mode == 'replace' else None)
                    saved = path.read_bytes() if mode == 'replace' else None
                finally:
                    self.fake.release.set()
                    try:
                        stdout, stderr = child.communicate(timeout=40)
                    finally:
                        if child.poll() is None:
                            child.kill()
                            child.wait(10)
                self.assertEqual(child.returncode, 0, stderr)
                self.assertEqual(len(self.fake.proposes), before + 2)
                if mode == 'replace':
                    self.assertTrue(path.exists(), 'replacement must survive the old batch cleanup')
                    self.assertEqual(path.read_bytes(), saved)
                else:
                    self.assertFalse(path.exists())

    def test_bilingual_copy_explains_running_batch_and_resend(self):
        self.declare()
        manifest, document = contract.check(self.home, 'memoranda', self.reg.event(1)['hash'])
        self.assertEqual(manifest['actions'], [{'id': 'send', 'label': {'it': 'Invia per revisione', 'en': 'Send for review'}}])
        instructions = {'it': (fx.BUNDLE / 'README.it.md').read_text(),
                        'en': (fx.BUNDLE / 'README.md').read_text()}
        for field in (instructions, document['note']):
            self.assertIn('2,4', field['it'])
            self.assertIn('2.4', field['en'])
            self.assertIn('already running', field['en'])
        self.assertIn('--resend', document['note']['en'])
        self.assertIn('next send', document['note']['en'])


if __name__ == '__main__':
    unittest.main()
