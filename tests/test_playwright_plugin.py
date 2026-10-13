"""Exercise the agent-operated bridge with synthetic requests and registered reports."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'plugins/playwright/bridge'))
import playwright_plugin as bridge
import plugin_store
import plugin_actions as actions
import plugins
from registry import Store


class FixedClock(datetime):
    @staticmethod
    def now(_):
        return datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc)


class PlaywrightBridge(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='squadra-playwright-')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.home = self.folder / 'home'
        self.home.mkdir()
        self.root = self.folder / 'registry'
        self.store = Store(self.root, create=True)
        self.addCleanup(self.store.close)
        self.store.append('init', 'system', {'name': 'Browser checks', 'project': str(self.folder), 'limits': {'plan': 3, 'code': 2, 'build': 3}})
        for name, role in [('C', 'coordinator'), ('A', 'builder')]:
            self.store.append('agent', name, {'id': name, 'role': role, 'identity': name, 'client': 'manual',
                                            'repo': str(self.folder), 'skills': [], 'session': ''})
        for name in ('T', 'OTHER'):
            self.store.append('task', 'C', {'id': name, 'owner': 'A', 'title': 'Check visible heading',
                                           'repo': str(self.folder), 'base': 'a' * 40, 'scope': {},
                                           'depends': [], 'proof': None, 'expected': None})
        self.key = self.store.events()[0]['hash']
        self.plugin = self.home / '.squadra/plugins/playwright'
        plugin_store.install(self.home, 'playwright', ROOT)
        self.spec = {'schema': 1, 'task': 'T', 'checks': [
            {'title': 'Heading', 'url': 'http://127.0.0.1:8765/', 'expect': 'Squadra browser check'},
            {'title': 'Footer', 'url': 'https://example.invalid/', 'expect': 'Synthetic footer'}]}

    def refuse(self, function, code):
        with self.assertRaises(plugins.Refused) as failure:
            function()
        self.assertEqual(failure.exception.code, code)

    def prepared(self):
        return bridge.prepare(self.home, self.root, self.spec)['ids']

    def select(self, ids):
        actions.save(self.home, 'playwright', 'check', self.key, ids)
        return bridge.pending(self.home, self.root)

    def receipt(self, task='T', artifacts=True):
        artifact = self.store.artifact_bytes(b'Synthetic tool reply; no browser executed by this test.', 'reply.txt', 'synthetic')
        return self.store.append('output', 'A', {'task': task, 'text': 'Synthetic browser report', 'next': 'Review',
                                                'artifacts': [artifact] if artifacts else []})

    def result(self, identity, status='passed', receipt=None):
        return {'id': identity, 'status': status, 'observed': 'Synthetic reported visible text',
                'evidence': [receipt or self.receipt()]}

    def record(self, snapshot, *results):
        return bridge.record(self.home, self.root, snapshot, {'schema': 1, 'results': list(results)})

    @property
    def request(self):
        return self.plugin / 'actions/check.json'

    @property
    def journal(self):
        return self.plugin / ('checks/' + self.key + '.json')

    def panel(self):
        return {'panel': plugins.load_panel(self.home, 'playwright', self.key)}

    def test_panel_summary_has_singular_and_plural_in_both_languages(self):
        for count, expected in (
            (1, {'it': '1 controllo del browser', 'en': '1 browser check'}),
            (2, {'it': '2 controlli del browser', 'en': '2 browser checks'}),
        ):
            with self.subTest(count=count):
                spec = dict(self.spec, checks=self.spec['checks'][:count])
                bridge.prepare(self.home, self.root, spec)
                self.assertEqual(self.panel()['panel']['summary'], expected)

    def test_install_is_explicit_self_contained_private_and_refuses_overwrite(self):
        self.assertEqual(plugins.discover(self.home)['plugins'][0]['results']['label']['en'], 'Browser checks')
        self.assertFalse((self.plugin / 'actions').exists())
        self.prepared()
        settings = self.plugin / 'settings.json'
        settings.write_text(json.dumps({'schema': 1, 'updated': '2026-10-12T12:00:00Z', 'values': {}, 'secrets': []}))
        settings.chmod(0o600)
        def inventory():
            return {str(p.relative_to(self.plugin)): p.read_bytes() for p in self.plugin.rglob('*') if p.is_file()}
        before = inventory()
        reply = plugin_store.install(self.home, 'playwright', ROOT)
        self.assertEqual(reply['state'], 'complete')
        self.assertFalse(reply['automatic_execution'])
        self.assertEqual(before, inventory())
        managed = self.plugin / 'README.md'
        managed.write_text(managed.read_text() + '\nLocal edit\n')
        edited = inventory()
        self.refuse(lambda: plugin_store.install(self.home, 'playwright', ROOT), 'modified_installation')
        self.assertEqual(edited, inventory())
        managed.write_bytes(before['README.md'])
        receipt = self.plugin / '.store-receipt.json'
        receipt.unlink()
        unmanaged = inventory()
        self.refuse(lambda: plugin_store.install(self.home, 'playwright', ROOT), 'unmanaged_installation')
        self.assertEqual(unmanaged, inventory())
        receipt.write_bytes(before['.store-receipt.json']); receipt.chmod(0o600)
        for path in self.plugin.rglob('*'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)
        proc = subprocess.run([sys.executable, '-I', '-B', str(self.plugin / 'bridge/playwright_plugin.py'), 'pending',
                               '--root', str(self.root), '--home', str(self.home)],
                              cwd=self.folder, env={k: v for k, v in (os.environ | {'HOME': str(self.home)}).items() if k != 'PYTHONPATH'}, capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), None)

    def test_prepare_immutable_ids_preserves_reports_and_revision_requests_new_check(self):
        first, second = self.prepared()
        self.assertEqual(self.prepared(), [first, second])
        snapshot = self.select([first])
        self.record(snapshot, self.result(first))
        self.prepared()
        self.assertEqual(self.panel()['panel']['items'][0]['status'], 'approved')
        self.spec['checks'][0]['revision'] = 'after-fix'
        third = self.prepared()[0]
        self.assertNotEqual(third, first)
        items = {x['id']: x for x in self.panel()['panel']['items']}
        self.assertEqual(set(items), {first, second, third})
        self.assertTrue(items[third]['selectable'])
        self.assertFalse(items[first]['selectable'])

    def test_prepare_checks_task_schema_urls_bounds_and_duplicates_before_writing(self):
        original = deepcopy(self.spec)
        for url in ('file:///etc/passwd', 'javascript:alert(1)', 'http://example.invalid/',
                    'https://user:secret@example.invalid/', 'https://example.invalid/#token', 'https://exa\\mple.invalid/'):
            value = deepcopy(original)
            value['checks'][0]['url'] = url
            self.refuse(lambda: bridge.prepare(self.home, self.root, value), 'bad_value')
        value = deepcopy(original); value['task'] = 'MISSING'
        self.refuse(lambda: bridge.prepare(self.home, self.root, value), 'unknown_task')
        value = deepcopy(original); value['checks'] *= 26
        self.refuse(lambda: bridge.prepare(self.home, self.root, value), 'bad_value')
        value = deepcopy(original); value['checks'] = [original['checks'][0]] * 2
        self.refuse(lambda: bridge.prepare(self.home, self.root, value), 'duplicate_check')
        value = deepcopy(original); value['checks'][0]['code'] = 'execute()'
        self.refuse(lambda: bridge.prepare(self.home, self.root, value), 'unknown_key')
        self.assertFalse(self.journal.exists())

    def test_pending_snapshots_selected_immutable_checks_and_exact_private_file_identity(self):
        ids = self.prepared(); snapshot = self.select(list(reversed(ids)))
        self.assertEqual(snapshot['request']['ids'], list(reversed(ids)))
        self.assertEqual(snapshot['identity'], bridge.identity(self.request.stat()))
        self.assertEqual(set(snapshot['checks']), set(ids))
        self.assertEqual(snapshot['checks'][ids[0]]['check']['expect'], self.spec['checks'][0]['expect'])
        self.assertEqual(snapshot['key'], self.key)

    def test_registered_agent_report_consumes_request_without_fabricating_a_run_or_approval(self):
        first = self.prepared()[0]; snapshot = self.select([first])
        result = self.result(first)
        before = self.store.events()
        reply = self.record(snapshot, result)
        self.assertEqual(reply, {'saved': True, 'complete': True, 'consumed': True, 'reported': 1, 'claim': 'agent-reported'})
        self.assertFalse(self.request.exists())
        item = self.panel()['panel']['items'][0]
        self.assertEqual(item['refs'], [{'task': 'T'}])
        self.assertEqual(item['status'], 'approved')
        self.assertIn('Agent-reported', self.panel()['panel']['note']['en'])
        self.assertEqual(self.store.events(), before)
        self.assertEqual(self.store.state()['runs'], {})
        self.assertEqual(self.store.state()['tasks']['T']['status'], 'waiting-ack')

    def test_partial_batch_and_failed_check_keep_outcomes_until_every_selection_is_terminal(self):
        first, second = self.prepared(); snapshot = self.select([first, second])
        first_result = self.result(first, 'failed')
        partial = self.record(snapshot, first_result)
        self.assertFalse(partial['complete']); self.assertTrue(self.request.exists())
        second_result = self.result(second, 'error')
        final = self.record(snapshot, second_result)
        self.assertTrue(final['consumed']); self.assertFalse(self.request.exists())
        records = json.loads(self.journal.read_text())['checks']
        self.assertEqual(records[first]['outcome']['status'], 'failed')
        self.assertEqual(records[second]['outcome']['status'], 'error')
        self.assertFalse(self.record(snapshot, first_result)['consumed'])
        self.assertEqual(json.loads(self.journal.read_text())['checks'], records)

    def test_missing_mcp_does_not_consume_and_evidence_must_be_same_task_with_artifacts(self):
        first = self.prepared()[0]; snapshot = self.select([first])
        before = self.journal.read_bytes()
        self.assertFalse(self.record(snapshot)['complete'])
        self.assertEqual(self.journal.read_bytes(), before)
        for receipt in ('invented', self.receipt('OTHER'), self.receipt(artifacts=False)):
            result = self.result(first, receipt=receipt)
            self.refuse(lambda: self.record(snapshot, result), 'evidence_required')
        self.assertTrue(self.request.exists())
        self.assertEqual(self.journal.read_bytes(), before)

    def test_replacement_is_preserved_while_old_outcomes_are_retained(self):
        first, second = self.prepared(); snapshot = self.select([first])
        newer = self.select([second])
        reply = self.record(snapshot, self.result(first))
        self.assertFalse(reply['consumed']); self.assertTrue(reply['complete'])
        self.assertEqual(bridge.pending(self.home, self.root)['request'], newer['request'])
        self.assertEqual(json.loads(self.journal.read_text())['checks'][first]['outcome']['status'], 'passed')

    def test_same_second_a_b_a_generation_survives_old_consumer(self):
        first, second = self.prepared()
        with patch.object(actions, 'datetime', FixedClock):
            old = self.select([first])
            self.select([second]); newer = self.select([first])
        self.assertEqual(old['request'], newer['request'])
        self.assertNotEqual(old['identity'], newer['identity'])
        self.assertFalse(self.record(old, self.result(first))['consumed'])
        self.assertEqual(bridge.pending(self.home, self.root)['identity'], newer['identity'])

    def test_same_second_cancel_reselect_generation_survives_old_consumer(self):
        first = self.prepared()[0]
        with patch.object(actions, 'datetime', FixedClock):
            old = self.select([first])
            actions.save(self.home, 'playwright', 'check', self.key, [])
            newer = self.select([first])
        self.assertEqual(old['request'], newer['request'])
        self.assertNotEqual(old['identity'], newer['identity'])
        self.assertFalse(self.record(old, self.result(first))['consumed'])
        self.assertEqual(bridge.pending(self.home, self.root)['identity'], newer['identity'])

    def test_journal_failure_does_not_delete_request_or_publish_success(self):
        first = self.prepared()[0]; snapshot = self.select([first]); result = self.result(first)
        before = self.journal.read_bytes()
        with patch.object(bridge.files, 'atomic', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError): self.record(snapshot, result)
        self.assertEqual(self.journal.read_bytes(), before)
        self.assertTrue(self.request.exists())
        self.assertEqual(self.panel()['panel']['items'][0]['status'], 'draft')

    def test_panel_failure_keeps_durable_report_and_request_for_safe_recovery(self):
        first = self.prepared()[0]; snapshot = self.select([first]); result = self.result(first)
        atomic = bridge.files.atomic
        calls = []
        def failing(parent, name, body):
            calls.append(name)
            if len(calls) == 2: raise OSError('panel write interrupted')
            return atomic(parent, name, body)
        with patch.object(bridge.files, 'atomic', side_effect=failing):
            with self.assertRaises(OSError): self.record(snapshot, result)
        self.assertTrue(self.request.exists())
        self.assertEqual(json.loads(self.journal.read_text())['checks'][first]['outcome']['status'], 'passed')
        self.assertEqual(self.panel()['panel']['items'][0]['status'], 'draft')
        self.assertTrue(self.record(snapshot)['consumed'])
        self.assertEqual(self.panel()['panel']['items'][0]['status'], 'approved')

    def test_same_lock_protects_producer_and_consumer_against_action_writers(self):
        first = self.prepared()[0]
        with bridge.locked(self.home):
            self.refuse(lambda: self.select([first]), 'busy')
            self.refuse(lambda: self.prepared(), 'busy')
            self.refuse(lambda: bridge.pending(self.home, self.root), 'busy')
        self.assertIsNotNone(self.select([first]))

    def test_terminal_reports_and_snapshot_content_cannot_be_rewritten(self):
        first, second = self.prepared(); snapshot = self.select([first, second]); result = self.result(first)
        bad = deepcopy(snapshot); bad['checks'][first]['check']['expect'] = 'Changed selection'
        self.refuse(lambda: self.record(bad, result), 'unknown_check')
        self.record(snapshot, result)
        changed = {**result, 'status': 'failed'}
        self.refuse(lambda: self.record(snapshot, changed), 'outcome_conflict')
        self.assertTrue(self.request.exists())
        self.assertEqual(json.loads(self.journal.read_text())['checks'][first]['outcome']['status'], 'passed')

    def test_symlink_request_and_journal_refused_without_outside_mutation(self):
        first = self.prepared()[0]; snapshot = self.select([first]); result = self.result(first)
        for path in (self.request, self.journal):
            saved = path.with_suffix('.saved'); path.rename(saved)
            before = saved.read_bytes(); path.symlink_to(saved)
            try:
                self.refuse(lambda: self.record(snapshot, result), 'symlink')
                self.assertEqual(saved.read_bytes(), before)
            finally:
                path.unlink(); saved.rename(path)

    def test_foreign_registry_and_tampered_chain_cannot_import_results(self):
        first = self.prepared()[0]; snapshot = self.select([first])
        bad = deepcopy(snapshot); bad['key'] = '0' * 64
        self.refuse(lambda: self.record(bad, self.result(first)), 'registry_mismatch')
        self.store.db.execute('DROP TRIGGER no_update')
        self.store.db.execute("UPDATE events SET hash = ? WHERE seq = 1", ('0' * 64,))
        self.refuse(lambda: bridge.pending(self.home, self.root), 'registry_invalid')
        self.assertTrue(self.request.exists())


if __name__ == '__main__':
    unittest.main()
