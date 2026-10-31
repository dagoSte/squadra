"""doctor --resume: one read-only readiness check before a coordinator assigns work."""
import contextlib
import datetime as dt
import io
import json
import shutil
import unittest

from registry import now
from ripresa import readiness, SILENT_DAYS
from squadra import main
import test_squadra as support


class ResumeReadiness(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task
    ack = support.Workflow.ack

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(['--root', str(self.root), *args])
        return rc, out.getvalue(), err.getvalue()

    def test_live_checkout_is_ready_and_names_its_task_branch_and_head(self):
        result = readiness(self.store.view(), now())
        self.assertTrue(result['ready'])
        self.assertEqual(result['blockers'], [])
        row = result['checkouts'][0]
        self.assertEqual(row['repo'], str(self.repo))
        self.assertEqual(row['tasks'], ['T'])
        self.assertTrue(row['git'])
        self.assertEqual(row['head'], self.git('rev-parse', '--short', 'HEAD').decode().strip())
        self.assertFalse(row['dirty'])
        self.assertIn('version', result['python'])

    def test_missing_checkout_of_an_open_task_blocks_and_doctor_exits_one(self):
        shutil.rmtree(self.repo)
        before = len(self.store.events())
        rc, out, _ = self.run_cli('doctor', '--resume')
        self.assertEqual(rc, 1)
        resume = json.loads(out)['resume']
        self.assertFalse(resume['ready'])
        self.assertIn('missing, used by open tasks T', resume['blockers'][0])
        self.assertEqual(json.loads(out)['verification'], 'quick')
        self.assertEqual(len(self.store.events()), before, 'the resume check never writes')

    def test_missing_checkout_without_open_tasks_is_only_a_notice(self):
        state = self.store.view(); state['tasks']['T']['status'] = 'done'
        shutil.rmtree(self.repo)
        result = readiness(state, now())
        self.assertTrue(result['ready'])
        self.assertTrue(any('missing, registered by' in n for n in result['notices']))

    def test_commits_after_the_last_tag_are_reported(self):
        self.git('tag', 'v1.0.0')
        (self.repo / 'code.py').write_text('VALUE = 2\n')
        self.git('-c', 'commit.gpgsign=false', 'commit', '-qam', 'after tag')
        row = readiness(self.store.view(), now())['checkouts'][0]
        self.assertEqual((row['last_tag'], row['commits_since_tag']), ('v1.0.0', 1))

    def test_silent_windows_are_listed_and_a_recent_answer_clears_one(self):
        # Decision silenzio-dalla-registrazione-20261031: silence counts from the latest answer or registration.
        from unittest.mock import patch
        clock = dt.datetime.fromisoformat(now())
        self.assertEqual(readiness(self.store.view(), clock)['silent_windows'], [], 'seats registered now are not silent')
        with patch('registry.now', return_value=(clock + dt.timedelta(days=1)).isoformat()):
            self.ack()
        later = clock + dt.timedelta(days=SILENT_DAYS)
        silent = {r['agent'] for r in readiness(self.store.view(), later)['silent_windows']}
        self.assertNotIn('A', silent)
        self.assertIn('R', silent)
        silent = {r['agent'] for r in readiness(self.store.view(), later + dt.timedelta(days=1))['silent_windows']}
        self.assertIn('A', silent)

    def test_plain_doctor_quick_output_is_unchanged(self):
        rc, out, _ = self.run_cli('doctor', '--quick')
        self.assertEqual(rc, 0)
        self.assertNotIn('resume', json.loads(out))


if __name__ == '__main__':
    unittest.main()


class ResumeReadinessReview(unittest.TestCase):
    """Review finding on 245e119: a Git timeout is unknown, not 'not a Git checkout'."""
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task

    def test_a_git_timeout_is_a_notice_and_never_a_blocker(self):
        import subprocess
        from unittest.mock import patch
        with patch('ripresa.git_process', side_effect=subprocess.TimeoutExpired('git', 10)):
            result = readiness(self.store.view(), now())
        self.assertTrue(result['ready'], result['blockers'])
        self.assertIsNone(result['checkouts'][0]['git'])
        self.assertTrue(any('Git did not answer' in n for n in result['notices']))

    def test_a_timeout_after_the_first_probe_is_also_a_notice(self):
        import subprocess
        from unittest.mock import patch
        import ripresa
        self.git('tag', 'v1.0.0')
        real = ripresa.git_process
        for command in ('branch', 'status', 'describe', 'rev-list'):
            with self.subTest(command=command):
                def slow(repo, *args, **kwargs):
                    if args[0] == command:
                        raise subprocess.TimeoutExpired('git', 10)
                    return real(repo, *args, **kwargs)
                with patch('ripresa.git_process', side_effect=slow):
                    result = readiness(self.store.view(), now())
                self.assertTrue(result['ready'], result['blockers'])
                self.assertIsNone(result['checkouts'][0]['git'])
                self.assertNotIn('commits_since_tag', result['checkouts'][0])
                self.assertTrue(any('Git did not answer' in n for n in result['notices']))


class StaleWindows(unittest.TestCase):
    """Rule 3: silent windows are proposed for stand-by only when nothing is held by them."""
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task

    def silent(self, state=None):
        # Seats registered by setUp become silent only SILENT_DAYS after their registration.
        later = dt.datetime.fromisoformat(now()) + dt.timedelta(days=SILENT_DAYS)
        rows = readiness(state or self.store.view(), later)['silent_windows']
        return {row['agent']: row for row in rows}

    def test_a_silent_window_holding_nothing_is_archivable_with_its_command(self):
        row = self.silent()['R']
        self.assertTrue(row['archivable'])
        self.assertEqual(row['keeps'], [])
        self.assertIn('squadra standby --actor <coordinator> --agent R', row['command'])

    def test_owner_reader_pool_resource_and_coordinator_are_kept(self):
        self.store.append('resource.acquire', 'A-alias', {'name': 'port', 'reason': 'Synthetic gate'})
        state = self.store.view()
        state['tasks']['T']['pool'] = ['A-alias']
        state['tasks']['T']['reviewer'] = 'R'
        self.assertEqual(state['resources']['port']['owner'], 'A-alias')
        rows = self.silent(state)
        self.assertEqual(rows['A']['keeps'], ['owns open tasks T', 'same identity as A-alias: reads open tasks T',
                                              'same identity as A-alias: holds resources port'])
        self.assertEqual(rows['R']['keeps'], ['reads open tasks T'])
        self.assertEqual(rows['A-alias']['keeps'], ['same identity as A: owns open tasks T',
                                                    'reads open tasks T', 'holds resources port'])
        self.assertEqual(rows['C']['keeps'], ['coordinator: reconcile it with Rule 0'])
        self.assertFalse(any(row['archivable'] for row in rows.values()))
        self.assertFalse(any('command' in row for row in rows.values()))

    def test_a_closed_task_no_longer_keeps_its_owner(self):
        state = self.store.view(); state['tasks']['T']['status'] = 'done'
        self.assertTrue(self.silent(state)['A']['archivable'])

    def test_a_parked_window_leaves_the_silent_list_and_the_composition(self):
        from registry import composition_windows
        self.assertIn('outside', composition_windows(self.store.view()))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = main(['--root', str(self.root), 'standby', '--actor', 'C', '--agent', 'R', '--reason', 'Owner: archive R'])
        self.assertEqual(rc, 0)
        self.assertNotIn('R', self.silent())
        self.assertNotIn('outside', composition_windows(self.store.view()))

    def test_a_free_seat_sharing_an_identity_is_kept_by_its_other_seats(self):
        # standby parks every seat of an identity: a free alias of a coordinator or a task owner is never archivable
        self.store.append('agent', 'C-build', {'id': 'C-build', 'role': 'builder', 'identity': 'coord', 'client': 'manual',
                                               'repo': str(self.repo), 'skills': [], 'session': ''})
        rows = self.silent()
        self.assertEqual(rows['C-build']['keeps'], ['same identity as C: coordinator: reconcile it with Rule 0'])
        self.assertEqual(rows['A-alias']['keeps'], ['same identity as A: owns open tasks T'])
        self.assertFalse(rows['C-build']['archivable'] or rows['A-alias']['archivable'])
        self.assertNotIn('command', rows['C-build'])
        self.assertTrue(rows['R']['archivable'])


class StaleWindowsReview(unittest.TestCase):
    """Two minor defects of the stale-windows rule found on the real registry (2026-10-31)."""
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task
    silent = StaleWindows.silent

    def seat(self, name, identity, role='executor'):
        self.store.append('agent', name, {'id': name, 'role': role, 'identity': identity, 'client': 'codex',
                                          'repo': str(self.repo), 'skills': [], 'session': ''})

    def test_a_seat_registered_now_that_never_answered_is_not_silent(self):
        self.seat('fresh', 'fresh-identity')
        rows = {r['agent'] for r in readiness(self.store.view(), now())['silent_windows']}
        self.assertNotIn('fresh', rows)
        later = dt.datetime.fromisoformat(now()) + dt.timedelta(days=SILENT_DAYS)
        rows = {r['agent']: r for r in readiness(self.store.view(), later)['silent_windows']}
        self.assertEqual(rows['fresh']['days'], SILENT_DAYS)
        self.assertIsNone(rows['fresh']['last_answer'])

    def test_one_standby_command_per_identity(self):
        self.seat('P1', 'parked'); self.seat('P2', 'parked'); self.seat('P3', 'parked')
        rows = self.silent()
        commands = [name for name in ('P1', 'P2', 'P3') if 'command' in rows[name]]
        self.assertEqual(commands, ['P1'])
        self.assertEqual((rows['P2']['parked_with'], rows['P3']['parked_with']), ('P1', 'P1'))
        self.assertTrue(all(rows[name]['archivable'] for name in ('P1', 'P2', 'P3')))
        self.assertIn('command', rows['R'])
