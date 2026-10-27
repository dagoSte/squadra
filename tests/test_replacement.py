"""Window replacement and service waits: synthetic end-to-end regressions."""
import contextlib
import datetime as dt
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_squadra as support
from registry import Store, Error, board, canonical, digest, pending_ack_ids, now, run_count
from replacement import execute, key_for, Plan
from squadra import main


class Replacement(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task
    ack = support.Workflow.ack
    cli = support.Workflow.cli
    proof = support.Workflow.proof

    def req(self, mapping=None, **values):
        return {'map': mapping or {'A': 'B'}, 'sessions': {'B': 'new-session'}, 'client': 'codex',
                'carry': 'useful', 'force': False, 'reason': 'Window exhausted its budget', **values}

    def replace(self, req=None, dry=False):
        with patch('squadra.codex_queue', return_value={'delivered': True, 'native_rc': 0}):
            return execute(self.store, 'C', req or self.req(), dry)

    def test_single_event_successor_alias_history_and_idempotent_rerun(self):
        old = self.store.state()['tasks']['T']
        result = self.replace()
        state = self.store.state()
        task = state['tasks'][result['successors']['T']]
        self.assertEqual(task['repo'], old['repo'])
        self.assertEqual(task['proof'], old['proof'])
        self.assertEqual(task['authors'], ['A', 'B'])
        self.assertEqual(task['base'], self.git('rev-parse', 'HEAD').decode().strip())
        self.assertEqual((self.root / 'artifacts' / task['scope']['sha256']).read_text(),
                         'taken over from A\n' + self.scope.read_text())
        self.assertEqual(state['agents']['A-alias']['availability']['status'], 'stand-by')
        self.assertEqual(len([e for e in self.store.events() if e['kind'] == 'replace']), 1)
        before = self.store.events()
        self.assertTrue(self.replace()['already_replaced'])
        self.assertEqual(before, self.store.events())
        self.assertIn('B (replaces A)', board(state, lang='en'))
        self.assertNotIn('assign-T', pending_ack_ids(self.store.view()))
        self.assertEqual(state['tasks']['T']['superseded_by'], task['id'])

    def test_dry_run_writes_nothing_including_artifacts_and_projections(self):
        self.store.project()
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = self.replace(dry=True)
        self.assertTrue(result['dry_run'])
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_map_many_to_one_and_one_digest(self):
        self.store.append('agent', 'D', {'id': 'D', 'identity': 'second', 'role': 'builder',
                          'client': 'codex', 'session': 'second-session', 'repo': str(self.repo), 'skills': []})
        self.task('U', owner='D')
        result = self.replace(self.req({'A': 'B', 'D': 'B'}))
        self.assertEqual(set(result['successors']), {'T', 'U'})
        self.assertEqual(len(result['outbox']), 1)
        self.assertEqual(self.store.state()['agents']['B']['replaces'], ['A', 'D'])

    def test_digest_keeps_useful_mail_and_retires_stop_resume(self):
        for ident, body in [('note', 'Preserve the API contract'), ('stop-old', 'STOP yesterday'), ('resume-old', 'Resume yesterday')]:
            self.store.append('message', 'C', {'id': ident, 'to': 'A', 'body': body})
        result = self.replace()
        state = self.store.state()
        body = state['messages'][result['outbox'][0]]['body']
        self.assertIn('note: Preserve', body)
        self.assertNotIn('yesterday', body)
        for ident in ('note', 'stop-old', 'resume-old'):
            self.assertIsNone(state['messages'][ident]['ack'])
            self.assertIn('replaced_by', state['messages'][ident])
            self.assertNotIn(ident, pending_ack_ids(self.store.view()))

    def test_reader_author_alias_refused_and_no_partial_write(self):
        # A-alias is an author alias; replacement must not silently create independence.
        self.store.append('agent', 'B', {'id': 'B', 'identity': 'author', 'role': 'reviewer',
                          'client': 'codex', 'session': 'author-session', 'repo': str(self.repo), 'skills': []})
        task = self.store.state()['tasks']['T']
        # Give the task a legitimate independent reader through ordinary task creation.
        self.store.append('task', 'C', {k: v for k, v in {**task, 'id': 'U', 'reviewer': 'R'}.items()
                                     if k in __import__('registry').TASK_FIELDS})
        before = self.store.events()
        with self.assertRaisesRegex(Error, 'Author'):
            self.replace(self.req({'R': 'B'}, sessions={}))
        self.assertEqual(before, self.store.events())

    def test_early_reader_reassignment_has_no_invented_delivery(self):
        task = self.store.state()['tasks']['T']
        self.store.append('task', 'C', {k: v for k, v in {**task, 'id': 'U', 'reviewer': 'R'}.items()
                                     if k in __import__('registry').TASK_FIELDS})
        self.replace(self.req({'R': 'B'}))
        state = self.store.state()
        self.assertEqual(state['tasks']['U']['reviewer'], 'B')
        self.assertNotIn('delivery', state['tasks']['U'])

    def test_recent_answer_requires_force(self):
        self.ack()
        with self.assertRaisesRegex(Error, 'answered recently'):
            self.replace()
        self.assertIn('successors', self.replace(self.req(force=True)))

    def test_carry_none_and_onboarding_template(self):
        self.store.append('settings', 'C', {'wait_seconds': {}, 'onboarding_template': 'Always read PROJECT_RULES.'})
        self.store.append('message', 'C', {'id': 'note', 'to': 'A', 'body': 'Carry this useful message'})
        result = self.replace(self.req(carry='none'))
        body = self.store.state()['messages'][result['outbox'][0]]['body']
        self.assertIn('PROJECT_RULES', body)
        self.assertNotIn('Carry this useful', body)

    def test_wait_visible_in_status_board_and_presence_then_expires(self):
        until = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat()
        self.assertEqual(self.cli('wait', '--actor', 'A', '--until', until, '--cause', '429'), 0)
        from registry import presence
        state = self.store.view()
        self.assertIn('waiting for service until', board(state, lang='en'))
        row = next(r for r in presence(state, now()) if r['agent'] == 'A')
        self.assertTrue(row['service_wait']['active'])
        row = next(r for r in presence(state, until) if r['agent'] == 'A')
        self.assertFalse(row['service_wait']['active'])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(['--root', str(self.root), 'status']), 0)
        self.assertIn('429', output.getvalue())

    def test_duplicate_send_generates_new_id_and_transport_refuses_stale_message(self):
        old = self.store.state()['agents']['A']
        self.store.append('agent', 'A', {k: v for k, v in {**old, 'session': 'previous-session'}.items() if k != 'updated'})
        self.report.write_text('Yesterday')
        self.assertEqual(self.cli('send', '--actor', 'C', '--to', 'A', '--id', 'resume-A', '--body-file', str(self.report)), 0)
        self.report.write_text('Today')
        self.assertEqual(self.cli('send', '--actor', 'C', '--to', 'A', '--id', 'resume-A', '--body-file', str(self.report)), 0)
        messages = self.store.state()['messages']
        self.assertEqual(messages['resume-A']['body'], 'Yesterday')
        self.assertEqual(len([m for m in messages.values() if m['body'] == 'Today']), 1)
        old = self.store.state()['agents']['A']
        self.store.append('agent', 'A', {k: v for k, v in {**old, 'session': 'new-session'}.items() if k != 'updated'})
        with patch('squadra.codex_queue') as queue:
            self.assertEqual(self.cli('transport', '--actor', 'C', '--message', 'resume-A', '--via', 'codex-queue'), 1)
            queue.assert_not_called()

    def test_explicit_save_warns_missing_unselected_repo_and_restores(self):
        from recovery import save, restore_check
        self.store.append('agent', 'gone', {'id': 'gone', 'identity': 'gone', 'role': 'builder', 'client': 'manual',
                          'repo': str(self.base / 'deleted-worktree'), 'session': '', 'skills': []})
        folder = self.base / 'saved'
        result = save(self.store, folder, repos=[str(self.repo)], temporary=True)
        manifest = json.loads((folder / 'MANIFEST.json').read_text())
        self.assertTrue(any('deleted-worktree' in row for row in manifest['warnings']))
        self.assertEqual(restore_check(folder, self.base / 'restored')['status'], 'passed')
        with self.assertRaises((Error, OSError)):
            save(self.store, self.base / 'bad', repos=[str(self.base / 'deleted-worktree')], temporary=True)

    def test_replay_cache_audit_checkpoint_and_older_reader_refusal(self):
        result = self.replace()
        self.assertEqual(self.store.state()['tasks'], dict(self.store.resolved()['tasks']))
        self.assertNotEqual(self.store.audit()['cache'], 'mismatch')
        checkpoint = self.base / 'checkpoint'
        self.store.checkpoint(checkpoint)
        check = Store(checkpoint, read_only=True)
        try:
            self.assertIn(result['id'], check.state()['workspace']['replacements'])
        finally:
            check.close()
        fixture = Path(__file__).parent / 'fixtures/format3_reader.py'
        probe = subprocess.run([sys.executable, '-B', str(fixture), str(self.root)], capture_output=True)
        self.assertTrue(any(e.get('format') == 4 for e in self.store.events()))
        self.assertNotEqual(probe.returncode, 0)
        self.assertIn(b'Unsupported event hash format', probe.stderr)

    def test_session_candidates_filter_roots_registered_and_malformed(self):
        from census import unregistered
        folder = self.base / 'sessions'; folder.mkdir()
        for name, cwd in [('candidate', self.repo), ('foreign', self.base / 'elsewhere')]:
            (folder / (name + '.jsonl')).write_text(json.dumps({'type': 'session_meta', 'payload': {'id': name, 'cwd': str(cwd)}}) + '\n')
        (folder / 'broken.jsonl').write_text('{broken')
        result = unregistered(self.store.state(), folder, [{'session': 'claude-peer', 'cwd': str(self.repo)}])
        self.assertEqual({r['session'] for r in result['candidates']}, {'candidate', 'claude-peer'})

    def test_cli_map_and_cli_dry_run(self):
        before = self.store.events()
        args = ['replace', '--actor', 'C', '--map', 'A=B', '--session', 'B=new-session', '--reason', 'Exhausted']
        self.assertEqual(self.cli(*args, '--dry-run'), 0)
        self.assertEqual(before, self.store.events())
        with patch('squadra.codex_queue', return_value={'delivered': True, 'native_rc': 0}):
            self.assertEqual(self.cli(*args), 0)
        self.assertEqual(len([e for e in self.store.events() if e['kind'] == 'replace']), 1)

    def test_current_head_dependencies_and_consumed_build_budget(self):
        self.ack()
        self.task('dependent', owner='R', deps=['T'])
        for n in range(3):
            ident = 'build-' + str(n)
            self.store.append('run.start', 'A', {'id': ident, 'task': 'T', 'mode': 'build'})
            self.store.append('run.end', 'A', {'id': ident, 'status': 'failed', 'native_rc': 1})
        (self.repo / 'code.py').write_text('VALUE = 2\n')
        self.git('add', '.'); self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'current head')
        result = self.replace(self.req(force=True))
        state = self.store.state(); successor = result['successors']['T']
        self.assertEqual(state['tasks'][successor]['base'], self.git('rev-parse', 'HEAD').decode().strip())
        self.assertEqual(state['tasks']['dependent']['depends'], [successor])
        self.assertEqual(run_count(self.store.view(), successor, 'build'), 3)
        self.store.append('ack', 'B', {'message': 'assign-' + successor})
        with self.assertRaisesRegex(Error, 'budget exhausted'):
            self.store.append('run.start', 'B', {'id': 'new-build', 'task': successor, 'mode': 'build'})
        self.assertNotEqual(self.store.audit()['cache'], 'mismatch')

    def test_force_preserves_unknown_run_and_blocks_successor(self):
        self.ack()
        self.store.append('run.start', 'A', {'id': 'unknown', 'task': 'T', 'mode': 'proof'})
        result = self.replace(self.req(force=True))
        successor = result['successors']['T']
        self.assertEqual(self.store.state()['runs']['unknown']['status'], 'running')
        self.store.append('ack', 'B', {'message': 'assign-' + successor})
        with self.assertRaisesRegex(Error, 'inherited live runs'):
            self.store.append('start', 'B', {'task': successor})

    def test_verified_absent_run_abandoned_and_resource_released(self):
        from registry import run_lease_paths, atomic
        self.ack()
        self.store.append('run.start', 'A', {'id': 'gone', 'task': 'T', 'mode': 'proof', 'lease_version': 1})
        folder, _, metadata, _ = run_lease_paths(self.root, 'gone')
        folder.mkdir(mode=0o700)
        atomic(metadata, canonical({'run': 'gone', 'phase': 'disposed', 'pgid': None}).encode())
        self.store.append('resource.acquire', 'A', {'name': 'cpu-heavy', 'task': 'T', 'reason': 'Build'})
        self.replace(self.req(force=True))
        state = self.store.state()
        self.assertEqual(state['runs']['gone']['status'], 'abandoned')
        self.assertNotIn('cpu-heavy', state['resources'])
        self.assertNotEqual(self.store.audit()['cache'], 'mismatch')

    def test_stale_or_tampered_plan_refused_atomically(self):
        planner = Plan(self.root, self.store.resolved(), 'C', self.req())
        data = planner.build()
        data['operations'][0]['data']['role'] = 'coordinator'
        before = self.store.events()
        with self.assertRaisesRegex(Error, 'differs'):
            self.store.append('replace', 'C', data, planner.key)
        self.assertEqual(before, self.store.events())
        data = planner.build()
        self.store.append('message', 'C', {'id': 'fresh', 'to': 'A', 'body': 'New evidence'})
        with self.assertRaisesRegex(Error, 'changed'):
            self.store.append('replace', 'C', data, planner.key)

    def test_committed_outbox_without_an_attempt_is_resumed_once(self):
        with patch('squadra.dispatch', side_effect=RuntimeError('Crash before delivery attempt')):
            with self.assertRaises(RuntimeError):
                execute(self.store, 'C', self.req())
        key = key_for('C', self.req())
        ident = self.store.state()['workspace']['replacements'][key]['outbox'][0]
        self.assertNotIn('transport', self.store.state()['messages'][ident])
        with patch('squadra.codex_queue', return_value={'delivered': True, 'native_rc': 0}) as queue:
            self.assertTrue(execute(self.store, 'C', self.req())['already_replaced'])
            self.assertEqual(queue.call_count, 1)
            execute(self.store, 'C', self.req())
            self.assertEqual(queue.call_count, 1)
        self.assertEqual(len([e for e in self.store.events() if e['kind'] == 'replace']), 1)

    def test_interrupted_transport_attempt_is_never_silently_replayed(self):
        with patch('squadra.codex_queue', side_effect=RuntimeError('Crash after intent')):
            with self.assertRaises(RuntimeError):
                execute(self.store, 'C', self.req())
        before = self.store.events()
        with patch('squadra.codex_queue') as queue:
            self.assertTrue(execute(self.store, 'C', self.req())['already_replaced'])
            queue.assert_not_called()
        self.assertEqual(before, self.store.events())

    def test_hotskills_four_to_three_and_eleven_standby_aliases(self):
        # Illustrative synthetic IDs; eleven old seats across four real sessions.
        self.store.append('agent', 'A', {'id': 'A', 'identity': 'author', 'role': 'builder', 'client': 'codex',
                          'repo': str(self.repo), 'session': 'old-a', 'skills': []})
        for name, identity, session in [('a-read', 'distinct-a-alias', 'old-a'),
                ('D', 'd', 'old-d'), ('d-read', 'd-read', 'old-d'), ('d-extra', 'd-extra', 'old-d'),
                ('E', 'e', 'old-e'), ('e-read', 'e-read', 'old-e'), ('e-extra', 'e-extra', 'old-e'),
                ('F', 'f', 'old-f'), ('f-read', 'f-read', 'old-f')]:
            self.store.append('agent', name, {'id': name, 'identity': identity, 'role': 'builder', 'client': 'codex',
                              'repo': str(self.repo), 'session': session, 'skills': []})
        req = self.req({'A': 'B', 'D': 'B', 'E': 'N', 'F': 'P'}, sessions={'B': 'b', 'N': 'n', 'P': 'p'})
        result = self.replace(req)
        self.assertEqual(len(result['outbox']), 3)
        self.assertEqual(sum(a.get('availability', {}).get('status') == 'stand-by'
                             for a in self.store.state()['agents'].values()), 11)

    def test_format4_registry_roundtrips_through_save_and_restore(self):
        from recovery import save, restore_check
        result = self.replace()
        folder = self.base / 'replacement-save'
        save(self.store, folder, repos=[str(self.repo)], temporary=True)
        restored = self.base / 'replacement-restored'
        self.assertEqual(restore_check(folder, restored)['status'], 'passed')
        check = Store(restored / 'registry', read_only=True)
        try:
            self.assertIn(result['id'], check.state()['workspace']['replacements'])
            self.assertTrue(check.state()['paused'])
        finally:
            check.close()

    def test_unknown_session_candidates_do_not_mutate_registry(self):
        before = self.store.events()
        with patch('census.unregistered', return_value={'candidates': [], 'limitations': ['No live peer source']}):
            result = self.replace(self.req(sessions={}))
        self.assertEqual(result['selection_required'], ['B'])
        self.assertEqual(before, self.store.events())

    def test_force_live_run_stays_blocking_through_a_second_replacement(self):
        self.ack()
        self.store.append('run.start', 'A', {'id': 'unknown', 'task': 'T', 'mode': 'proof'})
        first = self.replace(self.req(force=True))
        second = self.replace(self.req({'B': 'D'}, sessions={'D': 'third'}, force=True))
        successor = second['successors'][first['successors']['T']]
        self.assertEqual(self.store.state()['tasks'][successor]['inherited_runs'], ['unknown'])
        self.store.append('ack', 'D', {'message': 'assign-' + successor})
        with self.assertRaisesRegex(Error, 'inherited live runs'):
            self.store.append('start', 'D', {'task': successor})

    def test_same_session_reregistration_does_not_make_mail_stale(self):
        old = self.store.state()['agents']['A']
        seat = {k: v for k, v in {**old, 'session': 'same-session'}.items() if k != 'updated'}
        self.store.append('agent', 'A', seat)
        self.store.append('message', 'C', {'id': 'current', 'to': 'A', 'body': 'Current session work'})
        self.store.append('agent', 'A', seat)
        with patch('squadra.codex_queue', return_value={'delivered': True, 'native_rc': 0}):
            self.assertEqual(self.cli('transport', '--actor', 'C', '--message', 'current', '--via', 'codex-queue'), 0)



import test_board as board_support


class ReplacementReads(unittest.TestCase):
    setUp = board_support.Board.setUp
    cli = board_support.Board.cli
    state = board_support.Board.state
    task = board_support.Board.task
    start = board_support.Board.start

    def test_delivered_read_preserves_proof_and_frozen_copy_and_needs_new_ack(self):
        self.task(); self.start()
        self.cli('prove', '--actor', 'A', '--task', 'T', '--', sys.executable, '-c', 'pass')
        self.cli('deliver', '--actor', 'A', '--task', 'T')
        before = self.state()['tasks']['T']
        self.cli('replace', '--actor', 'C', '--old', 'R', '--new', 'Other', '--reason', 'Reader unavailable')
        state = self.state(); task = state['tasks']['T']
        self.assertEqual(task['proofs'], before['proofs'])
        self.assertEqual(task['delivery']['frozen'], before['delivery']['frozen'])
        self.assertEqual(task['reviewer'], 'Other')
        message = state['messages'][task['delivery']['handoff']]
        self.assertIsNone(message['ack'])
        self.assertIn('candidates/' + task['delivery']['frozen']['sha256'], message['body'])
        self.cli('doctor')

    def test_delivered_pool_replaces_only_departing_reader(self):
        self.cli('agent', '--id', 'NewReader', '--identity', 'newreader', '--role', 'reviewer', '--client', 'manual', '--repo', str(self.repo))
        self.cli('task', '--actor', 'C', '--id', 'T', '--title', 'Pool', '--owner', 'A', '--repo', str(self.repo),
                 '--scope-file', str(self.scope), '--proof-file', str(self.proof), '--reviewer', 'R', '--reviewer', 'Other')
        self.start()
        self.cli('prove', '--actor', 'A', '--task', 'T', '--', sys.executable, '-c', 'pass')
        self.cli('deliver', '--actor', 'A', '--task', 'T')
        before = self.state()['tasks']['T']
        self.cli('replace', '--actor', 'C', '--old', 'R', '--new', 'NewReader', '--reason', 'Reader unavailable')
        task = self.state()['tasks']['T']
        self.assertEqual(task['pool'], ['NewReader', 'Other'])
        self.assertEqual(task['proofs'], before['proofs'])
        self.assertEqual(len(task['delivery']['handoffs']), 2)
        self.cli('doctor')


if __name__ == '__main__':
    unittest.main()

