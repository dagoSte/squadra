"""Registry privacy, initialization and truthful CLI failures under synthetic homes."""
import contextlib
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import unittest
from unittest.mock import patch

import test_squadra as support
import squadra
from registry import Store


class RegistrySafety(unittest.TestCase):
    git = support.Workflow.git
    task = support.Workflow.task
    ack = support.Workflow.ack

    def setUp(self):
        support.Workflow.setUp(self)
        self.home_patch = patch.dict(os.environ, HOME=str(self.home),
                                    CODEX_HOME=str(self.home / 'codex'),
                                    CLAUDE_CONFIG_DIR=str(self.home / 'claude'))
        self.home_patch.start(); self.addCleanup(self.home_patch.stop)

    def invoke(self, *args, root=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = squadra.main(['--root', str(root or self.root), *args])
            except Exception as exc:
                rc = None
                err.write(type(exc).__name__ + ': ' + str(exc))
        return rc, out.getvalue(), err.getvalue()

    def inventory(self, root=None):
        root = root or self.root
        return {str(p.relative_to(root)): (hashlib.sha256(p.read_bytes()).hexdigest(),
                                          p.stat().st_mtime_ns, p.stat().st_mode & 0o777)
                for p in root.rglob('*') if p.is_file() and not p.name.endswith('-shm')}

    def init(self, root, *extra, project=None):
        return self.invoke('init', '--name', 'Synthetic', '--project', str(project or self.repo),
                           *extra, root=root)

    def send(self):
        body = self.base / 'body.txt'; body.write_text('Private synthetic message\n')
        return self.invoke('send', '--actor', 'A', '--to', 'R', '--body-file', str(body))

    def test_new_registry_stays_out_of_git_add_all(self):
        root = self.repo / '.squadra'
        rc, _, err = self.init(root, '--temporary')
        self.assertEqual(rc, 0, err)
        ignored = subprocess.run(['git', 'check-ignore', str(root / 'registry.sqlite3')],
                                 cwd=self.repo, env=self.env, capture_output=True)
        self.git('add', '-A')
        self.assertEqual(self.git('diff', '--cached', '--name-only'), b'')
        self.assertEqual(ignored.returncode, 0, ignored.stderr)
        self.assertEqual((root / '.gitignore').read_text(), '*\n')

    def test_store_creation_protects_external_roots_too(self):
        root = self.base / 'external'
        store = Store(root, create=True); store.close()
        self.assertTrue((root / '.gitignore').is_file())
        self.assertEqual((root / '.gitignore').read_text(), '*\n')

    def test_tracked_root_is_refused_without_changing_it(self):
        root = self.repo / 'tracked'; root.mkdir()
        (root / 'private.txt').write_text('already tracked\n')
        self.git('add', 'tracked/private.txt')
        before = self.inventory(root)
        rc, _, err = self.init(root, '--temporary')
        self.assertEqual(rc, 1)
        self.assertIn('tracked', err.lower())
        self.assertEqual(self.inventory(root), before)

    def test_invalid_init_creates_nothing(self):
        cases = [(['--temporary'], self.base / 'missing'), ([], self.repo),
                 (['--temporary', '--plan-rounds', '0'], self.repo),
                 (['--temporary'], self.repo / 'code.py')]
        for i, (args, project) in enumerate(cases):
            with self.subTest(case=i):
                root = self.base / ('invalid-' + str(i))
                rc, _, err = self.init(root, *args, project=project)
                self.assertEqual(rc, 1, err)
                self.assertFalse(root.exists(), 'a refused init must not create its root')

    def test_failed_init_preserves_existing_directory_bytes(self):
        root = self.base / 'existing'; root.mkdir()
        (root / 'note').write_text('keep me\n'); before = self.inventory(root)
        rc, _, _ = self.init(root, '--temporary', project=self.base / 'missing')
        self.assertEqual(rc, 1)
        self.assertEqual(self.inventory(root), before)

    def test_committed_message_survives_table_failure_without_diagnostic(self):
        before = len(self.store.events())
        with patch.object(squadra, 'table', side_effect=RuntimeError('synthetic render failure')):
            rc, out, err = self.send()
        self.assertEqual(rc, 0, err)
        events = self.store.events()[before:]
        self.assertEqual([e['kind'] for e in events], ['message'])
        first, _ = json.JSONDecoder().raw_decode(out)
        self.assertEqual(first['id'], events[0]['data']['id'])
        original_result = {'id': first['id'], 'to': 'R', 'body': 'Private synthetic message\n', 'reply_to': None}
        self.assertTrue(out.startswith(json.dumps(original_result, ensure_ascii=False, indent=2) + '\n'))
        self.assertIn('committed', err.lower())
        self.assertIn('do not retry', err.lower())

    def test_committed_message_survives_publication_failure(self):
        before = len(self.store.events())
        with patch.object(Store, 'publish', side_effect=OSError('synthetic projection failure')):
            rc, out, err = self.send()
        self.assertEqual(rc, 0, err)
        self.assertEqual([e['kind'] for e in self.store.events()[before:]], ['message'])
        self.assertIn('Private synthetic message', out)
        self.assertIn('committed', err.lower())

    def test_committed_start_survives_ancillary_forecast_failure(self):
        self.ack(); before = len(self.store.events())
        with patch('forecast.calculate', side_effect=RuntimeError('synthetic forecast failure')):
            rc, out, err = self.invoke('start', '--actor', 'A', '--task', 'T')
        self.assertEqual(rc, 0, err)
        kinds = [e['kind'] for e in self.store.events()[before:]]
        self.assertEqual(kinds.count('start'), 1)
        self.assertNotIn('diagnostic', kinds)
        self.assertIn('committed', err.lower())

    def test_unknown_ids_are_plain_and_do_not_write(self):
        for args, kind in [(['transport', '--actor', 'A', '--message', 'NOPE', '--via', 'manual'], 'message'),
                           (['check', '--actor', 'C', '--task', 'NOPE'], 'task'),
                           (['review', '--actor', 'R', '--task', 'NOPE', '--verdict', 'accepted',
                             '--report', str(self.report)], 'task'),
                           (['ack', '--actor', 'A', '--message', 'NOPE'], 'message')]:
            with self.subTest(command=args[0]):
                before = self.store.events()
                rc, _, err = self.invoke(*args)
                self.assertEqual(rc, 1)
                self.assertIn('Unknown ' + kind, err)
                self.assertIn('NOPE', err)
                self.assertNotIn("'NOPE'", err)
                self.assertEqual(len(err.splitlines()), 1)
                self.assertEqual(self.store.events(), before)

    def test_sqlite_open_failure_is_a_plain_folder_refusal(self):
        before = self.inventory()
        with patch.object(squadra, 'Store', side_effect=sqlite3.OperationalError('unable to open database file')):
            rc, _, err = self.invoke('status')
        self.assertEqual(rc, 1, err)
        self.assertIn(str(self.root), err)
        self.assertIn('writable roots', err.lower())
        self.assertEqual(len(err.splitlines()), 1)
        self.assertEqual(self.inventory(), before)

    def test_permission_error_names_the_folder_and_codex_remedy(self):
        before = self.inventory()
        with patch.object(squadra, 'Store', side_effect=PermissionError(errno.EACCES, 'denied', str(self.root))):
            rc, _, err = self.invoke('status')
        self.assertEqual(rc, 1, err)
        self.assertIn(str(self.root), err)
        self.assertIn('Codex', err)
        self.assertIn('writable roots', err.lower())
        self.assertEqual(len(err.splitlines()), 1)
        self.assertEqual(self.inventory(), before)

    def test_failing_dashboard_preserves_events_and_files(self):
        before, inventory = self.store.events(), self.inventory()
        with patch('dashboard.serve', side_effect=OSError('synthetic port busy')):
            rc, _, err = self.invoke('dash')
        self.assertEqual(rc, 1)
        self.assertEqual(len(err.splitlines()), 1)
        self.assertEqual(self.store.events(), before)
        self.assertEqual(self.inventory(), inventory)

    def test_read_only_render_failure_does_not_claim_success(self):
        before, inventory = self.store.events(), self.inventory()
        with patch.object(squadra, 'table', side_effect=RuntimeError('synthetic table failure')):
            rc, _, err = self.invoke('status')
        self.assertEqual(rc, 1)
        self.assertNotIn('committed', err.lower())
        self.assertEqual(self.store.events(), before)
        self.assertEqual(self.inventory(), inventory)

    def test_healthy_writes_reads_and_explicit_refusal_keep_their_status(self):
        rc, out, err = self.send(); self.assertEqual(rc, 0, err)
        first, _ = json.JSONDecoder().raw_decode(out)
        self.assertIn(first['id'], self.store.state()['messages'])
        before = self.store.events()
        for args in (['status'], ['doctor'], ['inbox', '--agent', 'R']):
            rc, _, err = self.invoke(*args); self.assertEqual(rc, 0, err)
        self.assertEqual(self.store.events(), before)
        rc, _, _ = self.invoke('start', '--actor', 'A', '--task', 'T')
        self.assertEqual(rc, 1, 'unacknowledged assignment still refuses')

    def test_existing_registry_gets_ignore_on_writable_open_without_git_call(self):
        ignore = self.root / '.gitignore'; ignore.unlink()
        with patch('registry.registry_git_root', side_effect=AssertionError('no Git on an existing writable open')):
            store = Store(self.root); store.close()
        self.assertEqual(ignore.read_text(), '*\n')
        before = self.inventory()
        store = Store(self.root); store.close()
        self.assertEqual(self.inventory(), before)

    def test_read_only_open_never_adds_an_ignore_file(self):
        ignore = self.root / '.gitignore'; ignore.unlink()
        before = self.inventory()
        store = Store(self.root, read_only=True); store.close()
        self.assertFalse(ignore.exists())
        self.assertEqual(self.inventory(), before)

    def test_doctor_reports_tracked_registry_without_untracking_it(self):
        root = self.repo / '.squadra'
        rc, _, err = self.init(root, '--temporary'); self.assertEqual(rc, 0, err)
        self.git('add', '-f', '.squadra/registry.sqlite3')
        before = self.git('ls-files', '-z')
        rc, out, err = self.invoke('doctor', root=root); self.assertEqual(rc, 0, err)
        data = json.loads(out)
        self.assertIn('git_warning', data)
        self.assertIn('git rm --cached -r', data['git_warning'])
        self.assertIn(str(root), data['git_warning'])
        self.assertEqual(self.git('ls-files', '-z'), before)

    def test_registry_at_worktree_root_is_refused(self):
        before = self.inventory(self.repo)
        rc, _, err = self.init(self.repo, '--temporary')
        self.assertEqual(rc, 1)
        self.assertIn('worktree root', err)
        self.assertEqual(self.inventory(self.repo), before)

    def test_existing_ignore_rules_are_preserved_and_negations_cannot_expose_registry(self):
        root = self.repo / '.squadra'; root.mkdir()
        (self.repo / '.gitignore').write_text('!.squadra/\n!.squadra/**\n')
        (root / '.gitignore').write_text('keep-this-rule\n!registry.sqlite3\n')
        rc, _, err = self.init(root, '--temporary'); self.assertEqual(rc, 0, err)
        self.assertEqual((root / '.gitignore').read_text(), 'keep-this-rule\n!registry.sqlite3\n*\n')
        self.git('add', '-A')
        self.assertEqual(self.git('diff', '--cached', '--name-only').decode().splitlines(), ['.gitignore'])

    def test_ignore_symlink_is_refused_without_touching_its_target(self):
        root = self.repo / '.squadra'; root.mkdir()
        target = self.base / 'ignore-target'; target.write_text('keep me\n')
        (root / '.gitignore').symlink_to(target)
        rc, _, err = self.init(root, '--temporary')
        self.assertEqual(rc, 1); self.assertIn('symlink', err)
        self.assertEqual(target.read_text(), 'keep me\n')
        self.assertFalse((root / 'registry.sqlite3').exists())

    def test_busy_refusal_never_attempts_a_second_diagnostic_write(self):
        before = self.store.events()
        with patch.object(Store, 'batch', side_effect=sqlite3.OperationalError('database is locked')) as batch:
            rc, _, err = self.send()
        self.assertEqual(rc, 1); self.assertEqual(err, 'Squadra: registry busy, retry\n')
        self.assertEqual(batch.call_count, 1)
        self.assertEqual(self.store.events(), before)

    def test_internal_key_error_stays_a_defect(self):
        before = len(self.store.events())
        with patch.object(squadra, 'dispatch', side_effect=KeyError('private-internal-field')):
            rc, _, err = self.send()
        self.assertEqual(rc, 1); self.assertEqual(err, 'Squadra: internal error\n')
        events = self.store.events()[before:]
        self.assertEqual([e['kind'] for e in events], ['diagnostic'])
        self.assertIn('private-internal-field', events[0]['data']['error'])

    def test_a_different_window_writing_does_not_make_this_command_committed(self):
        def foreign_write(*args):
            self.store.append('message', 'A', {'id': 'foreign', 'to': 'R', 'body': 'Other process'})
            return None
        with patch.object(squadra, 'dispatch', side_effect=foreign_write), \
                patch.object(squadra, 'table', side_effect=RuntimeError('summary unavailable')):
            rc, out, err = self.invoke('check', '--actor', 'C', '--task', 'T')
        self.assertEqual(rc, 1)
        self.assertNotIn('committed', out + err)
        self.assertNotIn('diagnostic', [e['kind'] for e in self.store.events()])

    def test_transport_failure_code_survives_a_render_failure(self):
        from types import SimpleNamespace
        self.store.append('agent', 'Q', {'id': 'Q', 'role': 'builder', 'identity': 'queue-target',
            'client': 'codex', 'repo': str(self.repo), 'skills': [], 'session': 'synthetic-session'})
        self.store.append('message', 'A', {'id': 'queue-message', 'to': 'Q', 'body': 'Synthetic queue message'})
        before = len(self.store.events())
        replies = [SimpleNamespace(returncode=0, stdout='--thread --message', stderr=''),
                   SimpleNamespace(returncode=1, stdout='', stderr='queue unavailable')]
        with patch('subprocess.run', side_effect=replies), \
                patch.object(squadra, 'table', side_effect=RuntimeError('render unavailable')):
            rc, out, err = self.invoke('transport', '--actor', 'A', '--message', 'queue-message', '--via', 'codex-queue')
        self.assertEqual(rc, 1, err)
        self.assertIn('delivery failed', err)
        self.assertNotIn('do not retry', err)
        self.assertEqual([e['kind'] for e in self.store.events()[before:]], ['transport'])
        first, _ = json.JSONDecoder().raw_decode(out); self.assertFalse(first['delivered'])

    def test_selftest_result_exit_code_is_preserved(self):
        with patch('selftest.run', return_value={'status': 'failed', 'tests': 1, 'folder': str(self.base)}):
            rc, _, _ = self.invoke('selftest', '--output', str(self.base / 'selftest'))
        self.assertEqual(rc, 1)

    def test_own_append_counter_excludes_idempotent_replays_and_rollbacks(self):
        count = self.store.commit_count
        data = {'id': 'counter-test', 'to': 'R', 'body': 'Synthetic'}
        self.store.append('message', 'A', data, 'counter-event')
        self.assertEqual(self.store.commit_count, count + 1)
        receipt = self.store.last_commit.copy()
        self.store.append('message', 'A', data, 'counter-event')
        self.assertEqual(self.store.commit_count, count + 1)
        self.assertEqual(self.store.last_commit, receipt)
        with self.assertRaises(Exception):
            self.store.batch([('message', 'A', {'id': 'rolled-back', 'to': 'R', 'body': 'Synthetic'}, None),
                              ('ack', 'A', {'message': 'missing'}, None)])
        self.assertEqual(self.store.commit_count, count + 1)
        self.assertEqual(self.store.last_commit, receipt)
        self.assertNotIn('rolled-back', self.store.state()['messages'])

    def test_private_ignore_survives_a_synthetic_checkpoint_and_is_checksummed(self):
        target = self.base / 'synthetic-checkpoint'
        self.store.checkpoint(target)
        self.assertEqual((target / '.gitignore').read_text(), '*\n')
        hashes = json.loads((target / 'CHECKSUMS.json').read_text())
        self.assertEqual(hashes['.gitignore'], hashlib.sha256(b'*\n').hexdigest())

    def test_restore_check_receipt_survives_projection_failure(self):
        target = self.base / 'synthetic-restore'; target.mkdir()
        (target / 'RESTORE-RESULT.json').write_text('{"status":"passed"}\n')
        before = len(self.store.events())
        with patch('recovery.restore_check', return_value={'status': 'passed'}), \
                patch.object(Store, 'project', side_effect=RuntimeError('synthetic projection failure')):
            rc, out, err = self.invoke('restore-check', '--actor', 'C', '--from', str(self.base / 'unused-package'),
                                        '--to', str(target))
        self.assertEqual(rc, 0, err)
        self.assertEqual([e['kind'] for e in self.store.events()[before:]], ['restore.check'])
        self.assertTrue(json.loads(out)['recorded'])
        self.assertIn('do not retry', err)


if __name__ == '__main__':
    unittest.main()
