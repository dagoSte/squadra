"""Owner-authorized handovers: atomic transitions, complete notes and refusals.

L2 owns the real safe-moment gate and condition evaluator. These tests inject
their frozen interfaces; the integrated suite also exercises L2's real checks.
"""
import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_squadra as support
from registry import Error, Store, canonical, now, pending_ack_ids, standby
import context_brief
import handover
from squadra import main


OUTSIDE = '\n'.join(h + '\nnessuno\n' for h in handover.HEADINGS)


def condition_stub(state, record, stamp):
    def met(row):
        if row['kind'] == 'confirm':
            return bool(row.get('confirmed'))
        if row['kind'] == 'task':
            return state['tasks'][row['value']]['status'] == 'done'
        return dt.datetime.fromisoformat(stamp) >= dt.datetime.fromisoformat(row['value'])
    return [{**{k: row[k] for k in ('n', 'kind', 'value')}, 'met': met(row), 'detail': 'Synthetic L2 interface'}
            for row in record['conditions']]


class Handover(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task
    ack = support.Workflow.ack
    cli = support.Workflow.cli

    def setUp(self):
        support.Workflow.setUp(self)
        self.outside = self.base / 'outside.md'
        self.outside.write_text(OUTSIDE)
        self.gate = self.enterContext(patch('checklist.run', return_value={'ready': True, 'items': []}))
        self.enterContext(patch.dict(sys.modules, {'handover_conditions': SimpleNamespace(evaluate=condition_stub)}))
        self.enterContext(patch('squadra.codex_queue', return_value={'delivered': True, 'native_rc': 0}))

    def prepare(self, origin='C', target='New', **values):
        return handover.prepare(self.store, origin, origin, target, outside_file=self.outside,
                                owner_words='Apri una sessione nuova per questa sede', channel='typed-here', **values)['id']

    def accept(self, ident, actor='New', session='actual-new-session', client='claude', dry_run=False):
        return handover.accept(self.store, actor, ident, session, client, dry_run)

    def test_coordinator_with_work_mail_decisions_and_aliases_transfers_atomically(self):
        self.task('OWN', owner='C')
        self.store.append('agent', 'C-read', {'id': 'C-read', 'identity': 'coord', 'session': '',
                          'role': 'reviewer', 'client': 'manual', 'repo': str(self.repo), 'skills': []})
        self.store.append('output', 'C', {'task': 'OWN', 'text': 'Prepared', 'next': 'Read the terminal receipt'})
        self.store.append('decision', 'C', {'id': 'contract', 'tasks': ['OWN'], 'reason': 'Keep the API stable'})
        self.store.append('message', 'A', {'id': 'incoming', 'to': 'C', 'body': 'Preserve this pending question'})
        self.store.append('message', 'C', {'id': 'outgoing', 'to': 'A', 'body': 'Reply with the proof'})
        self.store.append('resource.acquire', 'C', {'name': 'cpu-heavy'})
        self.store.append('resource.release', 'C', {'name': 'cpu-heavy'})
        ident = self.prepare()
        before = self.store.events()
        original = self.store.state()['tasks']['OWN']
        result = self.accept(ident)
        state = self.store.state()
        accepted = [e for e in self.store.events()[len(before):] if e['kind'] == 'handover']
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]['format'], 5)
        self.assertEqual([name for name, a in state['agents'].items() if a['role'] == 'coordinator' and not standby(state, name)], ['New'])
        self.assertTrue(standby(state, 'C-read'))
        successor = state['tasks'][result['successors']['OWN']]
        self.assertEqual(successor['authors'], ['C', 'New'])
        self.assertEqual(successor['proof'], original['proof'])
        self.assertEqual(state['tasks']['OWN']['superseded_by'], successor['id'])
        note = json.loads(state['handovers'][ident]['accepted']['seat_note'])
        self.assertEqual(note['tasks'][0]['id'], 'OWN')
        self.assertEqual(note['tasks'][0]['next'], 'Read the terminal receipt')
        self.assertEqual({m['id'] for m in note['pending_messages']}, {'assign-T', 'assign-OWN', 'incoming', 'outgoing'})
        self.assertIn('contract', {d['id'] for d in note['decisions']})
        onboarding = next(state['messages'][m] for m in result['outbox'] if '-onboard-' in m)
        self.assertIn('Read the terminal receipt', onboarding['body'])
        for target in ('A', 'A-alias', 'R'):
            introduction = next(state['messages'][m] for m in result['outbox'] if state['messages'][m]['to'] == target)
            self.assertIsNone(introduction['ack'])
            self.assertIn('role coordinator', introduction['body'])
        self.assertNotIn('incoming', pending_ack_ids(self.store.view()))
        self.assertIn('outgoing', pending_ack_ids(self.store.view()))
        self.assertEqual(self.store.view()['tasks'][successor['id']]['owner'], 'New')
        audit = self.store.audit()
        self.assertNotEqual(audit['cache'], 'mismatch')

    def test_prepare_changes_no_roles_and_is_idempotent(self):
        before = self.store.state()['agents']
        ident = self.prepare()
        self.assertEqual(before, self.store.state()['agents'])
        events = self.store.events()
        self.assertEqual(self.prepare(), ident)
        self.assertEqual(events, self.store.events())
        self.assertEqual(self.store.state()['handovers'][ident]['status'], 'pending')

    def test_builder_handover_keeps_coordinator_and_demands_fresh_assignment_ack(self):
        self.ack()
        ident = self.prepare('A')
        result = self.accept(ident, client='codex')
        state = self.store.state()
        successor = result['successors']['T']
        self.assertFalse(standby(state, 'C'))
        self.assertEqual(state['agents']['New']['role'], 'builder')
        self.assertEqual(state['tasks'][successor]['status'], 'waiting-ack')
        with self.assertRaisesRegex(Error, 'ACK'):
            self.store.append('start', 'New', {'task': successor})

    def test_live_run_gate_refuses_without_a_partial_event(self):
        ident = self.prepare('A')
        self.ack()
        self.store.append('run.start', 'A', {'id': 'run-live', 'task': 'T', 'mode': 'proof'})
        self.gate.return_value = {'ready': False, 'items': [{'id': 'runs', 'status': 'fail', 'detail': 'run-live'}]}
        before = self.store.events()
        with self.assertRaisesRegex(Error, 'run-live'):
            self.accept(ident, client='codex')
        self.assertEqual(before, self.store.events())
        self.assertNotIn('New', self.store.state()['agents'])

    def test_conditions_are_read_time_facts_and_only_confirm_can_be_confirmed(self):
        ident = self.prepare(when=['confirm:owner has checked the server', 'task:T'])
        before = self.store.events()
        self.assertFalse(handover.status(self.store)['handovers'][0]['conditions_evaluated'][0]['met'])
        self.assertEqual(before, self.store.events())
        with self.assertRaisesRegex(Error, 'not met'):
            self.accept(ident)
        self.store.append('handover.confirm', 'C', {'id': ident, 'condition': 1, 'reason': 'Owner checked'})
        with self.assertRaisesRegex(Error, 'confirm:'):
            self.store.append('handover.confirm', 'C', {'id': ident, 'condition': 2, 'reason': 'Pretend the task closed'})
        with self.assertRaisesRegex(Error, 'not met'):
            self.accept(ident)

    def test_full_accept_dry_run_has_no_files_or_events_and_retry_has_no_transport_replay(self):
        ident = self.prepare()
        self.store.project()
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertTrue(self.accept(ident, dry_run=True)['dry_run'])
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.accept(ident)
        events = self.store.events()
        self.assertTrue(self.accept(ident)['already_accepted'])
        self.assertEqual(events, self.store.events())

    def test_missing_empty_duplicate_or_secret_outside_state_is_refused_before_artifact_write(self):
        secrets = ['sk-synthetic-key', 'ghp_synthetic_key', 'AKIASYNTHETICKEY',
                   '-----BEGIN PRIVATE KEY-----', 'password=example', 'token=example']
        cases = [OUTSIDE.replace(handover.HEADINGS[0], '## Unrelated'),
                 OUTSIDE.replace('nessuno', '', 1), OUTSIDE + '\n' + handover.HEADINGS[0] + '\nagain',
                 *[OUTSIDE + '\n' + value for value in secrets]]
        for text in cases:
            with self.subTest(text=text):
                self.outside.write_text(text)
                before = set((self.root / 'artifacts').iterdir())
                with self.assertRaises(Error):
                    self.prepare()
                self.assertEqual(before, set((self.root / 'artifacts').iterdir()))

    def test_outside_file_and_unknown_reason_are_required_and_loss_is_declared(self):
        with self.assertRaisesRegex(Error, 'outside'):
            handover.prepare(self.store, 'C', 'C', 'New', owner_words='Proceed', channel='typed-here')
        ident = handover.prepare(self.store, 'C', 'C', 'New', outside_unknown='Old session cannot answer',
                                 owner_words='Proceed', channel='typed-here')['id']
        note = context_brief.seat_context(self.store, 'C')
        self.assertTrue(note['outside']['loss_declared'])
        self.assertIn('cannot answer', note['outside']['unknown'])
        self.assertEqual(note['handovers'][0]['id'], ident)

    def test_authorization_cannot_be_forged_and_confirm_requires_active_coordinator(self):
        with self.assertRaisesRegex(Error, 'Origin'):
            handover.prepare(self.store, 'R', 'C', 'New', outside_file=self.outside,
                             owner_words='Proceed', channel='typed-here')
        with self.assertRaisesRegex(Error, 'channel'):
            handover.prepare(self.store, 'C', 'C', 'New', outside_file=self.outside, owner_words='Proceed')
        ident = self.prepare(when=['confirm:approved'])
        with self.assertRaisesRegex(Error, 'coordinator'):
            self.store.append('handover.confirm', 'A', {'id': ident, 'condition': 1, 'reason': 'I say so'})

    def test_pending_alias_duplicate_and_reused_session_are_refused(self):
        ident = self.prepare('A')
        with self.assertRaisesRegex(Error, 'already pending'):
            handover.prepare(self.store, 'C', 'A-alias', 'Other', outside_unknown='Unknown', owner_words='Proceed', channel='typed-here')
        with self.assertRaisesRegex(Error, 'already registered'):
            self.accept(ident, session='author', client='codex')
        with self.assertRaisesRegex(Error, 'for itself'):
            self.accept(ident, actor='Other', client='codex')

    def test_cancellation_by_unregistered_new_seat_is_allowed_and_prevents_accept(self):
        ident = self.prepare()
        self.assertEqual(self.cli('handover', 'cancel', '--actor', 'New', '--id', ident, '--reason', 'Session not available'), 0)
        with self.assertRaisesRegex(Error, 'Pending'):
            self.accept(ident)

    def test_recorded_owner_answer_resolves_the_ask_in_the_preparation_event(self):
        self.store.append('ask', 'C', {'id': 'handover-choice', 'question': 'Move the session?', 'options': [
            {'id': x, 'label': x, 'say': x} for x in ['yes', 'no']]})
        ident = handover.prepare(self.store, 'C', 'C', 'New', outside_file=self.outside,
                                 answers='handover-choice', choice='yes')['id']
        state = self.store.state()
        self.assertEqual(state['asks']['handover-choice']['status'], 'answered')
        self.assertEqual(state['handovers'][ident]['decision']['answers'], 'handover-choice')

    def test_seat_context_is_bounded_since_verified_and_does_not_change_the_registry(self):
        ident = self.prepare('A')
        cursor = context_brief.seat_context(self.store, 'A')['cursor']
        for n in range(12):
            self.store.append('message', 'C', {'id': 'long-' + str(n), 'to': 'A', 'body': 'Very long recorded work. ' * 300})
        before = self.store.events()
        packet = context_brief.seat_context(self.store, 'A', since=cursor, max_chars=4000)
        self.assertEqual(packet['schema'], 'squadra.seat-context/1')
        self.assertLessEqual(len(context_brief.render(packet, True)), 4000)
        self.assertGreater(packet['omitted']['pending_messages'], 0)
        self.assertEqual(before, self.store.events())
        with self.assertRaisesRegex(Error, 'foreign'):
            context_brief.seat_context(self.store, 'A', since='1:' + 'f' * 64)
        self.assertEqual(self.cli('context', '--seat', 'A'), 0)
        self.assertEqual(self.cli('handover', 'note', '--from', 'A'), 0)

    def test_old_journal_reader_refuses_the_first_format5_event(self):
        self.prepare()
        old = Path(__file__).parent / 'fixtures/published-5099f0c/scripts/squadra.py'
        result = subprocess.run([sys.executable, '-B', str(old), '--root', str(self.root), 'status'], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Unsupported event hash format', result.stderr)

    def test_accept_refuses_missing_or_malformed_condition_component(self):
        ident = self.prepare()
        with patch.dict(sys.modules, {'handover_conditions': None}):
            with self.assertRaisesRegex(Error, 'component unavailable'):
                self.accept(ident)
        with patch.dict(sys.modules, {'handover_conditions': SimpleNamespace(evaluate=lambda *args: [{'met': True}])}):
            with self.assertRaisesRegex(Error, 'evaluation'):
                self.accept(ident)

    def test_fake_coordinator_selftest_has_complete_structural_coverage(self):
        from selftest import handover_scenario
        result = handover_scenario(self.base / 'synthetic-handover')
        self.assertEqual(result['status'], 'passed', result['checks'])
        self.assertTrue(all(result['checks'].values()))

    def test_handover_cannot_be_submitted_as_an_ordinary_replace_to_bypass_the_gate(self):
        ident = self.prepare()
        data = self.accept(ident, dry_run=True)['plan']
        before = self.store.events()
        with self.assertRaisesRegex(Error, 'owner-authorized'):
            self.store.append('replace', 'New', data)
        self.assertEqual(before, self.store.events())

    def test_coordinator_role_survives_when_origin_is_its_reviewer_alias(self):
        self.store.append('agent', 'C-read', {'id': 'C-read', 'identity': 'coord', 'session': '',
                          'role': 'reviewer', 'client': 'manual', 'repo': str(self.repo), 'skills': []})
        ident = self.prepare('C-read')
        self.accept(ident)
        self.assertEqual(self.store.state()['agents']['New']['role'], 'coordinator')

    def test_cli_accept_registers_the_new_actor_and_accepts_root_after_the_subcommand(self):
        self.assertEqual(self.cli('handover', 'prepare', '--actor', 'C', '--from', 'C', '--to', 'New',
                                 '--outside-file', str(self.outside), '--owner-words', 'Proceed', '--channel', 'typed-here'), 0)
        ident = next(iter(self.store.state()['handovers']))
        self.assertEqual(self.cli('handover', 'status', '--from', 'C'), 0)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = main(['handover', 'accept', '--actor', 'New', '--id', ident, '--session', 'observed-new-session',
                       '--client', 'claude', '--root', str(self.root)])
        self.assertEqual(rc, 0)
        self.assertEqual(self.store.state()['agents']['New']['session'], 'observed-new-session')

    def test_stale_accept_plan_cannot_commit_after_an_incoming_message(self):
        ident = self.prepare()
        plan = self.accept(ident, dry_run=True)['plan']
        self.store.append('message', 'A', {'id': 'new-mail', 'to': 'C', 'body': 'New context before the handover'})
        before = self.store.events()
        with self.assertRaisesRegex(Error, 'changed'):
            self.store.append('handover', 'New', plan)
        self.assertEqual(before, self.store.events())
        self.assertNotIn('New', self.store.state()['agents'])


if __name__ == '__main__':
    unittest.main()
