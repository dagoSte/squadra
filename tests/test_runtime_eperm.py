"""Cleanup waits through an exit window without hiding persistent signal denial."""
import errno
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import runtime


class TerminateRace(unittest.TestCase):
    def exercise(self, denied_signal, *, descendants=False, persistent=False):
        proc = Mock(pid=123456)
        calls = []
        denied = False
        reapable_at = None
        reaped = False
        clock = 0.0
        polls = []
        sleeps = []

        def sleep(seconds):
            nonlocal clock
            self.assertGreater(seconds, 0, 'retry must yield while the leader is exiting')
            self.assertLessEqual(seconds, 0.05)
            if denied:
                sleeps.append(seconds)
            clock += seconds
            self.assertLess(clock, 4.1, 'retry must have a deadline')

        def poll():
            nonlocal reaped
            calls.append('poll')
            result = -signal.SIGTERM if denied and clock >= reapable_at and not persistent else None
            reaped = result is not None
            polls.append(result)
            return result

        def killpg(pid, sig):
            nonlocal denied, reapable_at
            self.assertEqual(pid, proc.pid)
            calls.append(sig)
            if sig == denied_signal:
                if not denied:
                    reapable_at = clock + 0.012
                denied = True
                if clock < reapable_at or persistent or (not descendants and not reaped):
                    raise PermissionError(errno.EPERM, 'leader not yet reapable or group inaccessible')
                if not descendants:
                    raise ProcessLookupError(errno.ESRCH, 'group gone after reap')

        proc.poll.side_effect = poll
        timer = SimpleNamespace(monotonic=lambda: clock, sleep=sleep)
        with patch.object(runtime.os, 'name', 'posix'), \
             patch.object(runtime.os, 'killpg', side_effect=killpg), \
             patch.object(runtime, 'time', timer):
            if persistent:
                with self.assertRaises(PermissionError):
                    runtime.terminate(proc)
                proc.wait.assert_not_called()
                self.assertGreaterEqual(clock, 2.0)
                self.assertLess(clock, 2.02)
                self.assertLessEqual(calls.count(denied_signal), 402)
            else:
                try:
                    runtime.terminate(proc)
                except PermissionError as exc:
                    self.fail('transient EPERM escaped cleanup: ' + str(exc))
                proc.wait.assert_called_once_with(timeout=10)
                if descendants:
                    self.assertIn(signal.SIGKILL, calls, 'a reaped leader does not prove its group is gone')
        self.assertGreaterEqual(calls.count(denied_signal), 4)
        self.assertGreaterEqual(len(sleeps), 3)
        self.assertTrue(all(abs(s - 0.005) < 1e-9 for s in sleeps[:3]))
        self.assertEqual(polls[:3], [None] * 3, 'first denials precede a reapable leader')
        first = calls.index(denied_signal)
        self.assertEqual(calls[first + 1:first + 3], ['poll', denied_signal])

    def test_sigterm_eperm_reaps_and_retries(self):
        self.exercise(signal.SIGTERM)

    def test_probe_eperm_after_poll_reaps_and_retries(self):
        self.exercise(0)

    def test_eperm_with_live_descendants_still_kills_the_group(self):
        for sig in (signal.SIGTERM, 0):
            with self.subTest(signal=sig):
                self.exercise(sig, descendants=True)

    def test_persistent_permission_denial_never_reports_cleanup(self):
        for sig in (signal.SIGTERM, 0):
            with self.subTest(signal=sig):
                self.exercise(sig, persistent=True)

    def test_sigkill_exit_race_also_reaps_before_retry(self):
        self.exercise(signal.SIGKILL)

    @unittest.skipIf(os.name == 'nt', 'POSIX process-group contract')
    def test_probe_real_process_exit_window_does_not_escape_eperm(self):
        real_killpg = os.killpg
        denials = 0
        escaped = []

        def observed_killpg(pid, sig):
            nonlocal denials
            try:
                return real_killpg(pid, sig)
            except PermissionError:
                denials += 1
                raise

        timer = SimpleNamespace(monotonic=time.monotonic,
                                sleep=lambda seconds: time.sleep(0 if seconds >= 0.05 else seconds))
        deadline = time.monotonic() + 15
        for trial in range(20):
            self.assertLess(time.monotonic(), deadline, 'real-process probe exceeded its budget')
            proc = subprocess.Popen([sys.executable, '-B', '-c',
                                     'import os,time;os.write(1,b"ready\\n");time.sleep(10)'],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                self.assertTrue(select.select([proc.stdout], [], [], 3)[0], 'child must be ready')
                self.assertEqual(proc.stdout.readline(), b'ready\n')
                with patch.object(runtime.os, 'killpg', side_effect=observed_killpg), \
                     patch.object(runtime, 'time', timer):
                    try:
                        runtime.terminate(proc)
                    except PermissionError:
                        escaped.append(trial)
                    else:
                        self.assertIsNotNone(proc.returncode)
            finally:
                if proc.poll() is None:
                    try:
                        real_killpg(proc.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
                proc.wait(timeout=3)
                proc.stdout.close()
        if sys.platform == 'darwin':
            self.assertGreater(denials, 0, 'positive control: real macOS EPERM must be observed')
        self.assertEqual(escaped, [], 'real EPERM escaped cleanup in trials ' + repr(escaped))
