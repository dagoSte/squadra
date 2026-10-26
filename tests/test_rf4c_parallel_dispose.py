"""A cancelled or timed-out proof running tools/parallel_tests.py leaves no worker behind."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from runtime import terminate  # noqa: E402

RUNNER = ROOT / 'tools/parallel_tests.py'


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@unittest.skipIf(os.name == 'nt', 'process groups are POSIX')
class Dispose(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='parallel-dispose-')
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name).resolve()
        self.tests = self.folder / 'tests'; self.tests.mkdir()
        self.pids = self.folder / 'pids'; self.pids.mkdir()
        self.survived = self.folder / 'survived'
        for name in ('test_sleep_a.py', 'test_sleep_b.py'):
            (self.tests / name).write_text(textwrap.dedent(f'''
                import os, time, unittest
                class Sleep(unittest.TestCase):
                    def test_sleep(self):
                        open(os.path.join({str(self.pids)!r}, str(os.getpid())), 'w').close()
                        time.sleep(30)
                        open({str(self.survived)!r}, 'a').write('worker outlived its proof\\n')
            '''))
        self.workers = []
        self.addCleanup(self.reap)

    def reap(self):
        for pid in self.workers:
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    def started(self, *options):
        proc = subprocess.Popen([sys.executable, '-B', str(RUNNER), '-s', str(self.tests), '--jobs', '2',
                                 '--output', str(self.folder / 'receipts'), *options],
                                cwd=self.folder, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        deadline = time.monotonic() + 30
        while len(list(self.pids.iterdir())) < 2:
            self.assertIsNone(proc.poll(), 'runner exited before its workers started')
            self.assertLess(time.monotonic(), deadline, 'workers did not start')
            time.sleep(0.05)
        self.workers = [int(p.name) for p in self.pids.iterdir()]
        return proc

    def assert_no_worker_left(self):
        deadline = time.monotonic() + 3
        while any(alive(pid) for pid in self.workers) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual([pid for pid in self.workers if alive(pid)], [], 'workers outlived the disposed proof')

    def test_proof_cancel_terminates_the_runner_and_its_workers(self):
        proc = self.started('--timeout', '60')
        started = time.monotonic()
        terminate(proc)
        self.assertLess(time.monotonic() - started, 2.0, 'the runner needed the KILL fallback')
        self.assert_no_worker_left()
        self.assertEqual(proc.returncode, 130)

    def test_runner_timeout_still_disposes_of_its_workers(self):
        proc = self.started('--timeout', '2')
        self.assertEqual(proc.wait(timeout=30), 1)
        self.assert_no_worker_left()


# Runs the runner in-process with Popen wrapped so that SIGTERM reaches the runner after the discovery child
# exists and before Popen returns, the narrowest point between spawn and registration.
BOUNDARY = r"""
import os, signal, subprocess, sys
sys.path.insert(0, sys.argv[1])
import parallel_tests
real, record = subprocess.Popen, sys.argv[2]
class Spawned(real):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not os.path.exists(record):
            open(record, 'w').write(str(self.pid))
            os.kill(os.getpid(), signal.SIGTERM)
subprocess.Popen = Spawned
sys.argv = ['parallel_tests.py', '-s', sys.argv[3], '--output', sys.argv[4], '--timeout', '60']
raise SystemExit(parallel_tests.main())
"""


@unittest.skipIf(os.name == 'nt', 'process groups are POSIX')
class SpawnBoundary(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='parallel-boundary-')
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name).resolve()
        self.tests = self.folder / 'tests'; self.tests.mkdir()
        # Discovery imports this module, so the discovery child is still alive when the signal lands.
        (self.tests / 'test_slow_import.py').write_text('import time, unittest\ntime.sleep(30)\n'
                                                       'class Slow(unittest.TestCase):\n    def test_x(self): pass\n')
        self.record = self.folder / 'child.pid'
        self.child = None
        self.addCleanup(self.reap)

    def reap(self):
        if self.child:
            try:
                os.killpg(self.child, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    def test_term_between_spawn_and_registration_still_disposes_of_the_child(self):
        run = subprocess.run([sys.executable, '-B', '-c', BOUNDARY, str(ROOT / 'tools'), str(self.record),
                              str(self.tests), str(self.folder / 'receipts')],
                             cwd=self.folder, capture_output=True, text=True, timeout=60)
        self.child = int(self.record.read_text())
        self.assertEqual(run.returncode, 130, run.stderr)
        deadline = time.monotonic() + 3
        while alive(self.child) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(alive(self.child), 'the discovery child outlived the cancelled runner')


if __name__ == '__main__':
    unittest.main()
