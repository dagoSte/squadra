"""Exercise the parallel proof command with real, isolated unittest subprocesses."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / 'tools/parallel_tests.py'
PASS = '''
import unittest
class Example(unittest.TestCase):
    def test_pass(self): self.assertTrue(True)
'''


class ParallelProof(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='parallel-proof-test-')
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name).resolve()
        self.tests = self.folder / 'tests'
        self.tests.mkdir()
        self.output = self.folder / 'receipts'

    def write(self, name, body):
        path = self.tests / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body), encoding='utf-8')

    def run_suite(self, *options):
        result = subprocess.run([sys.executable, '-B', str(RUNNER), '-s', str(self.tests),
                                 '--output', str(self.output), '--jobs', '2', '--timeout', '10', *options],
                                cwd=self.folder, capture_output=True, text=True, timeout=45)
        self.log = result.stdout + result.stderr
        self.summary = json.loads((self.output / 'summary.json').read_text())
        return result.returncode

    def test_recursive_discovery_load_tests_duplicate_ids_and_fixtures(self):
        self.write('nested/__init__.py', '')
        self.write('nested/test_nested.py', PASS)
        self.write('test_dynamic.py', '''
            import unittest
            state = []
            def setUpModule(): state.append('module')
            def tearDownModule():
                assert state == ['module', 'class', 'done'], state
            class Dynamic(unittest.TestCase):
                @classmethod
                def setUpClass(cls): state.append('class')
                @classmethod
                def tearDownClass(cls): state.append('done')
                def test_same(self): self.assertEqual(state, ['module', 'class'])
            def load_tests(loader, tests, pattern):
                return unittest.TestSuite([Dynamic('test_same'), Dynamic('test_same')])
        ''')
        self.assertEqual(self.run_suite(), 0, self.log)
        self.assertEqual((self.summary['collected'], self.summary['executed']), (3, 3))
        self.assertEqual(self.summary['skipped'], 0)
        self.assertIn('SUMMARY collected=3 executed=3 failed=0 errors=0 skipped=0', self.log)
        self.assertFalse(list(self.tests.rglob('__pycache__')))

    def test_workers_overlap_with_private_profiles_before_import(self):
        events = self.folder / 'events.jsonl'
        body = '''
            import json, os, tempfile, time, unittest
            from pathlib import Path
            home = Path(os.environ['HOME'])
            (home / ('import-marker-' + __name__)).write_text(__name__)
            class Isolation(unittest.TestCase):
                def test_overlap(self):
                    paths = [os.environ[n] for n in ('HOME', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'TMPDIR')]
                    self.assertEqual(len(set(paths)), 4)
                    self.assertEqual(Path(tempfile.gettempdir()).resolve(), Path(paths[-1]).resolve())
                    with Path(EVENTS).open('a') as f:
                        f.write(json.dumps([__name__, 'start', time.monotonic(), paths]) + '\\n')
                    deadline = time.monotonic() + 5
                    while len(Path(EVENTS).read_text().splitlines()) < 2:
                        self.assertLess(time.monotonic(), deadline, 'workers did not overlap')
                        time.sleep(.01)
                    self.assertEqual((home / ('import-marker-' + __name__)).read_text(), __name__)
        '''.replace('EVENTS', repr(str(events)))
        self.write('test_one.py', body)
        self.write('test_two.py', body)
        self.assertEqual(self.run_suite(), 0, self.log)
        values = [json.loads(line) for line in events.read_text().splitlines()]
        self.assertEqual(len(values), 2)
        self.assertTrue(set(values[0][3]).isdisjoint(values[1][3]))
        self.assertEqual(self.summary['executed'], 2)

    def test_omitted_execution_refuses_and_prints_both_counts(self):
        self.write('test_omit.py', PASS + '''
class Omit(unittest.TestSuite):
    def run(self, result, debug=False): return result
def load_tests(loader, tests, pattern): return Omit([Example('test_pass')])
''')
        self.assertNotEqual(self.run_suite(), 0, self.log)
        self.assertEqual((self.summary['collected'], self.summary['executed']), (1, 0))
        self.assertIn('ID/count mismatch: collected=1 executed=0', self.log)

    def test_equal_counts_cannot_hide_lost_and_duplicated_ids(self):
        self.write('test_multiplicity.py', '''
            import unittest
            class Items(unittest.TestCase):
                def test_a(self): pass
                def test_b(self): pass
            class Substitute(unittest.TestSuite):
                def run(self, result, debug=False):
                    return unittest.TestSuite([Items('test_a'), Items('test_b'), Items('test_b')]).run(result)
            def load_tests(loader, tests, pattern):
                return Substitute([Items('test_a'), Items('test_a'), Items('test_b')])
        ''')
        self.assertNotEqual(self.run_suite(), 0, self.log)
        self.assertEqual((self.summary['collected'], self.summary['executed']), (3, 3))
        self.assertIn('ID/count mismatch', self.log)

    def test_changed_collection_in_worker_is_refused(self):
        self.write('test_changed.py', '''
            import sys, unittest
            class Example(unittest.TestCase):
                def test_a(self): pass
                def test_b(self): pass
            def load_tests(loader, tests, pattern):
                method = 'test_b' if '--_module' in sys.argv else 'test_a'
                return unittest.TestSuite([Example(method)])
        ''')
        self.assertNotEqual(self.run_suite(), 0, self.log)
        self.assertIn('worker collection/count receipt differs', self.log)

    def test_crashed_and_killed_workers_are_named_failures_with_progress(self):
        for action in ('os._exit(9)', 'os.kill(os.getpid(), signal.SIGKILL)', 'os._exit(0)'):
            with self.subTest(action=action):
                self.output = self.folder / ('receipts-' + str(len(list(self.folder.glob('receipts-*')))))
                self.write('test_crash.py', '''
                    import os, signal, unittest
                    class Crash(unittest.TestCase):
                        def test_a(self): pass
                        def test_b(self): ACTION
                '''.replace('ACTION', action))
                self.assertNotEqual(self.run_suite(), 0, self.log)
                self.assertEqual((self.summary['collected'], self.summary['executed']), (2, 2))
                self.assertEqual(self.summary['skipped'], 0)
                self.assertIn('WORKER FAILED test_crash', self.log)
                self.assertIn('missing or invalid completion receipt', self.log)

    def test_timeout_names_module_and_elapsed_seconds(self):
        self.write('test_slow.py', '''
            import time, unittest
            class Slow(unittest.TestCase):
                def test_sleep(self): time.sleep(60)
        ''')
        self.assertNotEqual(self.run_suite('--timeout', '1'), 0, self.log)
        self.assertIn('WORKER FAILED test_slow: TimeoutError: timed out after 1s', self.log)
        self.assertRegex(self.log, r'test_slow: collected=1 executed=1 rc=None wall=\d+\.\d+s')
        self.assertGreaterEqual(self.summary['wall_seconds'], 1)
        self.assertLess(self.summary['wall_seconds'], 15)

    def test_failure_name_and_traceback_survive_other_passing_modules(self):
        self.write('test_good.py', PASS)
        self.write('test_bad.py', PASS.replace('self.assertTrue(True)', 'self.fail("seeded failure")'))
        self.assertNotEqual(self.run_suite(), 0, self.log)
        self.assertEqual((self.summary['collected'], self.summary['executed']), (2, 2))
        self.assertEqual(self.summary['failed'], 1)
        self.assertIn('test_bad.Example.test_pass', self.log)
        self.assertIn('AssertionError: seeded failure', self.log)
        self.assertIn('Traceback (most recent call last)', self.log)
        self.assertIn('test_good.Example.test_pass', self.log)

    def test_discovery_errors_never_qualify_a_partial_suite(self):
        self.write('test_good.py', PASS)
        self.write('test_import_error.py', 'raise RuntimeError("seeded import error")')
        self.assertNotEqual(self.run_suite(), 0, self.log)
        self.assertIn('DISCOVERY FAILED', self.log)
        self.assertIn('seeded import error', self.log)
        self.assertEqual(self.summary['executed'], 0)

    def test_skipped_test_is_counted_and_cannot_make_a_complete_proof(self):
        self.write('test_skip.py', PASS.replace('    def test_pass', '    @unittest.skip("missing capability")\n    def test_pass'))
        self.assertNotEqual(self.run_suite(), 0, self.log)
        self.assertEqual((self.summary['collected'], self.summary['executed'], self.summary['skipped']), (1, 1, 1))
        self.assertIn('missing capability', self.log)

    def test_options_and_source_output_refusal(self):
        help_run = subprocess.run([sys.executable, '-B', str(RUNNER), '--help'], capture_output=True, text=True)
        self.assertEqual(help_run.returncode, 0)
        help_text = ' '.join(help_run.stdout.split())
        self.assertIn('default: 300', help_text)
        self.assertIn('min(cpu count, 8)', help_text)
        for option in ('--jobs', '--timeout'):
            result = subprocess.run([sys.executable, '-B', str(RUNNER), option, '0'], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
        target = self.tests / 'receipts'
        result = subprocess.run([sys.executable, '-B', str(RUNNER), '-s', str(self.tests), '--output', str(target)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('outside the source and discovery trees', result.stderr)
        self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()


RED = '''
import unittest
class Red(unittest.TestCase):
    def test_red(self): self.assertEqual(1, 2)
'''
RED_ID = 'test_red.Red.test_red'


class KnownReds(unittest.TestCase):
    """Known reds of main are named and separated; only --accept-known turns them into exit 0."""
    setUp = ParallelProof.setUp
    write = ParallelProof.write
    run_suite = ParallelProof.run_suite

    def known(self, *ids, **fields):
        entry = {'reason': 'Synthetic fixture lost', 'since': 'v0.0.1', 'retire_when': 'Fixture restored'} | fields
        (self.tests / 'known_reds.json').write_text(json.dumps(
            {'version': 1, 'known_reds': [{'id': name, **entry} for name in ids]}), encoding='utf-8')

    def test_only_known_reds_pass_with_accept_known_and_stay_red_without(self):
        self.write('test_ok.py', PASS); self.write('test_red.py', RED); self.known(RED_ID)
        self.assertEqual(self.run_suite(), 1, self.log)
        self.assertFalse(self.summary['passed'])
        self.assertTrue(self.summary['only_known_reds'])
        self.assertIn(f'KNOWN RED {RED_ID}: Synthetic fixture lost (since v0.0.1)', self.log)
        self.output = self.folder / 'receipts-accept'
        self.assertEqual(self.run_suite('--accept-known'), 0, self.log)
        self.assertEqual((self.summary['known_red'], self.summary['new_red']), (1, 0))
        self.assertFalse(self.summary['passed'], 'the strict verdict never changes')
        self.assertIn('not a release proof', self.log)

    def test_a_new_red_always_fails(self):
        self.write('test_red.py', RED)
        self.write('test_other.py', RED.replace('Red', 'Other'))
        self.known(RED_ID)
        self.assertEqual(self.run_suite('--accept-known'), 1, self.log)
        self.assertEqual(self.summary['new_red'], 1)
        self.assertIn('NEW RED test_other.Other.test_red', self.log)

    def test_a_known_red_that_passes_or_vanishes_must_be_removed(self):
        self.write('test_ok.py', PASS); self.known('test_ok.Example.test_pass', 'test_gone.Gone.test_gone')
        self.assertEqual(self.run_suite(), 0, self.log)
        self.assertEqual(self.summary['stale_known'], 2)
        self.assertIn('STALE KNOWN RED test_gone.Gone.test_gone: not collected', self.log)
        self.output = self.folder / 'receipts-accept'
        self.assertEqual(self.run_suite('--accept-known'), 1, self.log)
        self.assertIn('REFUSED: a listed known red no longer fails', self.log)

    def test_instrument_errors_and_skips_are_never_accepted(self):
        self.write('test_red.py', RED)
        self.write('test_crash.py', 'import os, unittest\nclass C(unittest.TestCase):\n    def test_a(self): os._exit(1)\n')
        self.write('test_skip.py', 'import unittest\nclass S(unittest.TestCase):\n    @unittest.skip("x")\n    def test_s(self): pass\n')
        self.known(RED_ID)
        self.assertEqual(self.run_suite('--accept-known'), 1, self.log)
        self.assertFalse(self.summary['only_known_reds'])

    def test_an_entry_without_a_reason_refuses_the_run(self):
        self.write('test_red.py', RED); self.known(RED_ID, reason=' ')
        result = subprocess.run([sys.executable, '-B', str(RUNNER), '-s', str(self.tests), '--output', str(self.output),
                                 '--accept-known'], cwd=self.folder, capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 1)
        self.assertIn('every known red needs non-empty id, reason, since, retire_when', result.stderr)
        self.assertFalse((self.output / 'summary.json').exists())
