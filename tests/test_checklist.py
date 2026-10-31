"""Checklist decisions use recorded facts and leave the registry unchanged."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

from checklist import run
from registry import plain, snapshot
from squadra import main
import test_squadra as support


class Checklist(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    ack = support.Workflow.ack
    cli = support.Workflow.cli
    proof = support.Workflow.proof
    delivered = support.Workflow.delivered

    def task(self, name='T', owner='A', deps=None, full_gate=False, repo=None):
        data = {'id': name, 'title': 'Synthetic checklist task', 'owner': owner,
                'repo': str(repo or self.repo), 'base': snapshot(repo or self.repo)['head'],
                'scope': self.store.artifact(self.scope), 'proof': None, 'expected': None,
                'depends': deps or [], 'full_gate': full_gate, 'reviewer': 'R',
                'cross_client': True, 'delivery_policy': 'frozen-v1'}
        self.store.batch([('task', 'C', data, None), ('message', 'C', {'id': 'assign-' + name,
                          'to': owner, 'task': name, 'body': 'Synthetic assignment'}, None)])
        self.store.append('brief', 'C', {'task': name, 'text': self.scope.read_text(),
                                       'artifact': self.store.artifact(self.scope)})

    def inventory(self):
        return {str(p.relative_to(self.root)): (hashlib.sha256(p.read_bytes()).hexdigest(),
                                               p.stat().st_mtime_ns, p.stat().st_mode & 0o777)
                for p in self.root.rglob('*') if p.is_file() and not p.name.endswith('-shm')}

    def check(self, moment, state=None, **kwargs):
        events, files = self.store.events(), self.inventory()
        if state is None:
            result = run(self.store, moment, **kwargs)
        else:
            with patch.object(self.store, 'resolved', return_value=state):
                result = run(self.store, moment, **kwargs)
        self.assertEqual(self.store.events(), events)
        self.assertEqual(self.inventory(), files)
        self.assertEqual(result['ready'], not any(i['status'] == 'fail' for i in result['items']))
        return {i['id']: i for i in result['items']}

    def state(self):
        return plain(self.store.resolved())

    def invoke(self, *args):
        events, files = self.store.events(), self.inventory()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(['--root', str(self.root), 'checklist', *args])
        self.assertEqual(self.store.events(), events)
        self.assertEqual(self.inventory(), files)
        result = json.loads(out.getvalue())
        self.assertEqual(rc, 0 if result['ready'] else 1, err.getvalue())
        return rc, result

    def test_resume_checks_registered_coordinator_active_and_readiness(self):
        items = self.check('resume', actor='C')
        self.assertTrue(all(items[k]['status'] == 'ok' for k in ('actor', 'coordinator', 'active', 'readiness')))
        self.assertEqual(items['mail_listener']['status'], 'unverifiable')
        self.assertTrue(items['mail_listener']['detail'])
        self.assertEqual(self.invoke('--moment', 'resume', '--actor', 'C')[0], 0)

    def test_resume_unknown_or_missing_actor_fails(self):
        for actor in (None, 'missing'):
            with self.subTest(actor=actor):
                self.assertEqual(self.check('resume', actor=actor)['actor']['status'], 'fail')
        self.assertEqual(self.invoke('--moment', 'resume', '--actor', 'missing')[0], 1)

    def test_resume_non_coordinator_fails(self):
        self.assertEqual(self.check('resume', actor='A')['coordinator']['status'], 'fail')

    def test_resume_standby_coordinator_fails(self):
        self.store.append('availability', 'C', {'agent': 'C', 'status': 'stand-by', 'reason': 'Synthetic owner decision'})
        self.assertEqual(self.check('resume', actor='C')['active']['status'], 'fail')

    def test_resume_checkout_blocker_fails(self):
        shutil.rmtree(self.repo)
        self.assertEqual(self.check('resume', actor='C')['readiness']['status'], 'fail')

    def test_assign_all_recorded_requirements_ok(self):
        self.ack()
        self.assertTrue(all(i['status'] == 'ok' for i in self.check('assign', task_id='T').values()))
        self.assertEqual(self.invoke('--moment', 'assign', '--task', 'T')[0], 0)

    def test_assign_missing_task_is_a_json_failure(self):
        self.assertEqual(self.check('assign', task_id='missing')['task']['status'], 'fail')
        self.assertEqual(self.invoke('--moment', 'assign', '--task', 'missing')[0], 1)

    def test_assign_closed_or_superseded_task_fails(self):
        for change in ({'status': 'done'}, {'superseded_by': 'OTHER'}):
            state = self.state(); state['tasks']['T'].update(change)
            self.assertEqual(self.check('assign', state, task_id='T')['task_open']['status'], 'fail')

    def test_assign_missing_brief_fails(self):
        state = self.state(); state['tasks']['T'].pop('brief')
        self.assertEqual(self.check('assign', state, task_id='T')['brief']['status'], 'fail')

    def test_assign_reader_must_exist_and_be_independent(self):
        for reviewer in (None, 'missing', 'A-alias'):
            state = self.state(); state['tasks']['T']['reviewer'] = reviewer
            self.assertEqual(self.check('assign', state, task_id='T')['readers']['status'], 'fail')

    def test_assign_same_client_needs_recorded_reason(self):
        state = self.state(); state['agents']['R']['client'] = 'codex'
        self.assertEqual(self.check('assign', state, task_id='T')['readers']['status'], 'fail')
        state['tasks']['T']['same_client_reason'] = 'Owner selected a Codex-only read'
        self.assertEqual(self.check('assign', state, task_id='T')['readers']['status'], 'ok')

    def test_assign_pool_checks_all_members(self):
        self.store.append('agent', 'R2', {'id': 'R2', 'identity': 'another-reader', 'role': 'reviewer',
                                         'client': 'claude', 'repo': str(self.repo), 'skills': [], 'session': ''})
        state = self.state(); state['tasks']['T'].update(reviewer=None, pool=['R', 'R2'])
        self.assertEqual(self.check('assign', state, task_id='T')['readers']['status'], 'ok')
        state['agents']['R2']['client'] = 'codex'
        self.assertEqual(self.check('assign', state, task_id='T')['readers']['status'], 'fail')

    def test_assign_same_checkout_or_symlink_alias_fails(self):
        other = self.base / 'linked-checkout'; other.symlink_to(self.repo, target_is_directory=True)
        self.task('T2', repo=other)
        self.assertEqual(self.check('assign', task_id='T')['checkout']['status'], 'fail')

    def test_assign_pool_stays_usable_with_one_member_in_standby(self):
        self.store.append('agent', 'R2', {'id': 'R2', 'identity': 'another-reader', 'role': 'reviewer',
                                         'client': 'claude', 'repo': str(self.repo), 'skills': [], 'session': ''})
        self.store.append('availability', 'C', {'agent': 'R2', 'status': 'stand-by', 'reason': 'Synthetic owner decision'})
        state = self.state(); state['tasks']['T'].update(reviewer=None, pool=['R', 'R2'])
        self.assertEqual(self.check('assign', state, task_id='T')['readers']['status'], 'ok')

    def test_assign_distinct_branch_and_tree_in_same_git_repository_ok(self):
        other = self.base / 'other-checkout'
        self.git('worktree', 'add', '-qb', 'second-lane', str(other), 'HEAD')
        self.task('T2', repo=other)
        items = self.check('assign', task_id='T')
        self.assertEqual(items['checkout']['status'], 'ok')
        self.assertEqual(items['branch']['status'], 'ok')

    def test_assign_same_branch_in_shared_git_storage_fails(self):
        other = self.base / 'other-checkout'; branch = self.git('branch', '--show-current').decode().strip()
        self.git('worktree', 'add', '--force', '-q', str(other), branch)
        self.task('T2', repo=other)
        self.assertEqual(self.check('assign', task_id='T')['branch']['status'], 'fail')

    def test_assign_same_branch_in_clones_with_same_origin_fails(self):
        other = self.base / 'cloned-checkout'
        self.git('clone', '-q', str(self.repo), str(other))
        self.git('remote', 'add', 'origin', '/synthetic/remote.git')
        self.git('-C', str(other), 'remote', 'set-url', 'origin', '/synthetic/remote.git')
        self.task('T2', repo=other)
        self.assertEqual(self.check('assign', task_id='T')['branch']['status'], 'fail')

    def test_assign_transport_or_ack_is_recorded_without_claiming_read(self):
        self.assertEqual(self.check('assign', task_id='T')['assignment']['status'], 'fail')
        self.store.append('transport', 'C', {'message': 'assign-T', 'via': 'manual', 'delivered': None})
        item = self.check('assign', task_id='T')['assignment']
        self.assertEqual(item['status'], 'ok'); self.assertIn('not an ACK', item['detail'])
        self.ack()
        self.assertIn('ACK recorded', self.check('assign', task_id='T')['assignment']['detail'])

    def test_assign_missing_assignment_fails(self):
        state = self.state(); state['messages'].pop('assign-T')
        self.assertEqual(self.check('assign', state, task_id='T')['assignment']['status'], 'fail')

    def test_git_timeout_in_the_middle_is_unverifiable(self):
        from checklist import git_process
        def slow(repo, *args, **kwargs):
            if args[:2] == ('rev-parse', '--git-common-dir'):
                raise subprocess.TimeoutExpired('git', 10)
            return git_process(repo, *args, **kwargs)
        with patch('checklist.git_process', side_effect=slow):
            item = self.check('assign', task_id='T')['branch']
        self.assertEqual(item['status'], 'unverifiable'); self.assertIn('TimeoutExpired', item['detail'])

    def deliver_and_ack(self):
        self.delivered()
        handoff = self.store.view()['tasks']['T']['delivery']['handoff']
        self.store.append('ack', 'R', {'message': handoff})

    def test_deliver_current_proof_and_handoff_ack_ok(self):
        self.deliver_and_ack()
        self.assertTrue(all(i['status'] == 'ok' for i in self.check('deliver', task_id='T').values()))
        self.assertEqual(self.invoke('--moment', 'deliver', '--task', 'T')[0], 0)

    def test_deliver_requires_recorded_delivery(self):
        self.assertEqual(self.check('deliver', task_id='T')['delivery']['status'], 'fail')
        self.assertEqual(self.invoke('--moment', 'deliver', '--task', 'T')[0], 1)

    def test_deliver_requires_a_current_proof(self):
        self.deliver_and_ack()
        state = self.state(); state['tasks']['T']['proofs'] = []
        self.assertEqual(self.check('deliver', state, task_id='T')['proof']['status'], 'fail')

    def test_deliver_later_failed_proof_invalidates_the_old_pass(self):
        self.deliver_and_ack()
        state = self.state(); proofs = state['tasks']['T']['proofs']
        proofs.append(dict(proofs[-1], status='failed', native_rc=1, ended='9999-12-31T00:00:00+00:00'))
        self.assertEqual(self.check('deliver', state, task_id='T')['proof']['status'], 'fail')

    def test_deliver_frozen_candidate_drift_fails(self):
        self.deliver_and_ack()
        task = self.store.view()['tasks']['T']
        code = self.root / 'candidates' / task['delivery']['frozen']['sha256'] / 'repo/code.py'
        code.chmod(0o644); code.write_text('VALUE = 999\n')
        self.assertEqual(self.check('deliver', task_id='T')['proof']['status'], 'fail')

    def test_deliver_changed_proof_artifact_fails(self):
        self.deliver_and_ack()
        task = self.store.view()['tasks']['T']
        artifact = next(a for a in task['proofs'][-1]['artifacts'] if a['name'] == 'stdout.log')
        path = self.root / 'artifacts' / artifact['sha256']
        path.chmod(0o644); path.write_text('forged proof log')
        self.assertEqual(self.check('deliver', task_id='T')['proof']['status'], 'fail')

    def test_deliver_needs_ack_of_current_not_old_handoff(self):
        self.delivered()
        self.assertEqual(self.check('deliver', task_id='T')['handoff_ack']['status'], 'fail')
        self.store.append('ack', 'R', {'message': self.store.view()['tasks']['T']['delivery']['handoff']})
        state = self.state(); state['tasks']['T']['delivery']['handoff'] = 'new-unacked-handoff'
        self.assertEqual(self.check('deliver', state, task_id='T')['handoff_ack']['status'], 'fail')

    def upstream(self):
        branch = self.git('branch', '--show-current').decode().strip()
        self.git('remote', 'add', 'origin', '/synthetic/remote.git')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        self.git('config', f'branch.{branch}.remote', 'origin')
        self.git('config', f'branch.{branch}.merge', 'refs/heads/main')
        return self.git('rev-parse', 'HEAD').decode().strip()

    def commit(self, *options, marker='next commit'):
        code = self.repo / 'code.py'; code.write_text(code.read_text() + '# ' + marker + '\n')
        self.git('-c', 'commit.gpgsign=false', 'commit', '-qam', 'Synthetic change', *options)
        return self.git('rev-parse', 'HEAD').decode().strip()

    def test_push_known_emails_and_fast_forward_ok(self):
        self.upstream(); self.commit()
        items = self.check('push', repo=str(self.repo))
        self.assertTrue(all(i['status'] == 'ok' for i in items.values()))
        self.assertEqual(self.invoke('--moment', 'push', '--repo', str(self.repo))[0], 0)

    def test_push_new_author_email_fails_and_names_the_email(self):
        self.upstream(); self.commit('--author', 'New author <new-author@example.invalid>')
        item = self.check('push', repo=str(self.repo))['emails']
        self.assertEqual(item['status'], 'fail'); self.assertIn('new-author@example.invalid', item['detail'])
        self.assertEqual(self.invoke('--moment', 'push', '--repo', str(self.repo))[0], 1)

    def test_push_new_committer_email_fails(self):
        self.upstream()
        with patch.dict(self.env, GIT_COMMITTER_EMAIL='new-committer@example.invalid'):
            self.commit()
        item = self.check('push', repo=str(self.repo))['emails']
        self.assertEqual(item['status'], 'fail'); self.assertIn('new-committer@example.invalid', item['detail'])

    def test_push_does_not_learn_new_emails_inside_the_range(self):
        self.upstream()
        self.commit('--author', 'New author <new-author@example.invalid>')
        self.commit('--author', 'New author <new-author@example.invalid>')
        self.assertEqual(self.check('push', repo=str(self.repo))['emails']['status'], 'fail')

    def test_push_empty_range_is_checked(self):
        self.upstream()
        item = self.check('push', repo=str(self.repo), revision_range='HEAD..HEAD')['emails']
        self.assertEqual(item['status'], 'ok'); self.assertIn('0 commits', item['detail'])

    def test_push_range_is_a_commit_pair_not_git_options(self):
        self.upstream()
        for value in ('--all', 'HEAD...HEAD', 'missing..HEAD'):
            with self.subTest(value=value):
                self.assertEqual(self.check('push', repo=str(self.repo), revision_range=value)['emails']['status'], 'fail')

    def test_push_missing_repository_fails(self):
        self.assertEqual(self.check('push', repo=str(self.base / 'absent'))['repository']['status'], 'fail')

    def test_push_missing_upstream_is_explicitly_unverifiable(self):
        self.commit()
        items = self.check('push', repo=str(self.repo), revision_range='HEAD^..HEAD')
        self.assertEqual(items['emails']['status'], 'ok')
        self.assertEqual(items['fast_forward']['status'], 'unverifiable')
        self.assertTrue(items['fast_forward']['detail'])

    def test_push_diverged_upstream_fails_fast_forward(self):
        self.upstream(); branch = self.git('branch', '--show-current').decode().strip()
        self.git('checkout', '-qb', 'remote-only'); remote = self.commit(marker='remote side')
        self.git('checkout', '-q', branch); local = self.commit(marker='local side')
        self.assertNotEqual(remote, local)
        self.git('update-ref', 'refs/remotes/origin/main', remote)
        self.assertEqual(self.check('push', repo=str(self.repo))['fast_forward']['status'], 'fail')

    def closed_task(self):
        self.deliver_and_ack()
        self.assertEqual(self.cli('review', '--actor', 'R', '--task', 'T', '--verdict', 'accepted',
                                  '--report', str(self.report)), 0)
        self.assertEqual(self.cli('close', '--actor', 'C', '--task', 'T'), 0)

    def test_push_closed_task_and_candidate_in_head_ok(self):
        self.upstream(); self.closed_task(); self.commit()
        items = self.check('push', repo=str(self.repo), task_id='T')
        self.assertEqual(items['task_closed']['status'], 'ok')
        self.assertEqual(items['candidate_ancestor']['status'], 'ok')

    def test_push_open_or_missing_task_fails(self):
        self.upstream()
        for name in ('T', 'missing'):
            self.assertEqual(self.check('push', repo=str(self.repo), task_id=name)['task_closed']['status'], 'fail')

    def test_push_candidate_on_an_unmerged_branch_fails(self):
        self.upstream(); self.closed_task()
        branch = self.git('branch', '--show-current').decode().strip()
        self.git('checkout', '-qb', 'unmerged'); other = self.commit()
        self.git('checkout', '-q', branch)
        state = self.state(); state['tasks']['T']['candidate']['head'] = other
        self.assertEqual(self.check('push', state, repo=str(self.repo), task_id='T')['candidate_ancestor']['status'], 'fail')


if __name__ == '__main__':
    unittest.main()
