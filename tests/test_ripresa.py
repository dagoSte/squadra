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
        self.ack()
        clock = dt.datetime.fromisoformat(now())
        silent = {r['agent'] for r in readiness(self.store.view(), clock)['silent_windows']}
        self.assertNotIn('A', silent)
        self.assertIn('R', silent)
        later = clock + dt.timedelta(days=SILENT_DAYS)
        silent = {r['agent'] for r in readiness(self.store.view(), later)['silent_windows']}
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
