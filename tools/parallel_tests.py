#!/usr/bin/env python3
"""Run unittest modules in isolated processes and reconcile their execution receipts."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unittest

ROOT = Path(__file__).resolve().parents[1]
SERIAL_MODULES = {}  # Module name: measured reason it cannot overlap another module.
# Workers run in their own sessions, so a kill of this runner's group never reaches them; the runner keeps them here.
LIVE, LIVE_LOCK = set(), threading.RLock()  # Reentrant: the TERM handler runs on the main thread, which may hold it.
DISPOSE_GRACE = 1.0  # Seconds between TERM and KILL, inside the 2 s a proof cancel gives this runner.


class Spawning:
    """The main thread's spawn-and-register section. The TERM handler runs on the main thread, so a TERM that
    lands inside it would dispose before the new worker is registered; it is held and replayed after it."""
    depth, pending = 0, None


@contextmanager
def registering():
    main = threading.current_thread() is threading.main_thread()
    if main:
        Spawning.depth += 1
    try:
        yield
    finally:
        if main:
            Spawning.depth -= 1
            if not Spawning.depth and Spawning.pending is not None:
                signum, Spawning.pending = Spawning.pending, None
                signal.raise_signal(signum)


def test_ids(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from test_ids(test)
        else:
            yield test.id()


class ModuleLoader(unittest.TestLoader):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.modules = {}

    def loadTestsFromModule(self, module, *args, **kwargs):
        self.depth += 1
        try:
            suite = super().loadTestsFromModule(module, *args, **kwargs)
        finally:
            self.depth -= 1
        if self.depth == 0:
            self.modules[module.__name__] = suite
        return suite


def discover(start, pattern):
    loader = ModuleLoader()
    suite = loader.discover(start, pattern=pattern)
    ids = list(test_ids(suite))
    groups = {name: list(test_ids(tests)) for name, tests in loader.modules.items()}
    partition = [item for group in groups.values() for item in group]
    errors = list(loader.errors)
    if Counter(partition) != Counter(ids):
        errors.append('Module partition differs from unittest discovery (including ID multiplicities).')
    return loader.modules, {'ids': ids, 'modules': groups, 'errors': errors}


def child(args):
    folder = Path(args._folder)
    modules, manifest = discover(args.start_directory, args.pattern)
    if args._mode == 'collect':
        (folder / 'result.json').write_text(json.dumps(manifest), encoding='utf-8')
        return 1 if manifest['errors'] else 0
    if manifest['errors']:
        raise RuntimeError('\n'.join(manifest['errors']))
    suite = modules[args._module]
    collected = list(test_ids(suite))
    with (folder / 'started.jsonl').open('w', encoding='utf-8') as journal:
        class Result(unittest.TextTestResult):
            def startTest(self, test):
                super().startTest(test)
                journal.write(json.dumps(test.id()) + '\n')
                journal.flush()

        result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(suite)
    receipt = {'collected': collected, 'executed': result.testsRun,
               'failed': len(result.failures) + len(result.unexpectedSuccesses),
               'errors': len(result.errors), 'skipped': len(result.skipped),
               'expected_failures': len(result.expectedFailures), 'passed': result.wasSuccessful()}
    (folder / 'result.json').write_text(json.dumps(receipt), encoding='utf-8')
    return 0 if result.wasSuccessful() and not result.skipped else 1


def environment(folder):
    env = os.environ.copy()
    for variable, name in [('HOME', 'home'), ('CODEX_HOME', 'codex'),
                           ('CLAUDE_CONFIG_DIR', 'claude'), ('TMPDIR', 'tmp')]:
        path = folder / name
        path.mkdir(mode=0o700)
        env[variable] = str(path)
    env['TMP'] = env['TEMP'] = env['TMPDIR']
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return env


def launch(args, folder, cancel, module=None):
    from runtime import terminate

    folder.mkdir(mode=0o700)
    command = [sys.executable, '-B', str(Path(__file__).resolve()),
               '--start-directory', args.start_directory, '--pattern', args.pattern,
               '--_mode', 'worker' if module else 'collect', '--_folder', str(folder)]
    if module:
        command += ['--_module', module]
    started = time.monotonic()
    problem = None
    code = None
    proc = None
    try:
        with (folder / 'stdout.log').open('w') as out, (folder / 'stderr.log').open('w') as err:
            try:
                # Checked under the lock, so no worker starts after a disposal has taken its snapshot.
                with LIVE_LOCK, registering():
                    if cancel.is_set():
                        raise RuntimeError('cancelled before start')
                    proc = subprocess.Popen(command, env=environment(folder), stdout=out, stderr=err,
                                            start_new_session=True)
                    LIVE.add(proc)
                while True:
                    if cancel.is_set():
                        raise RuntimeError('cancelled')
                    remaining = args.timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        raise TimeoutError(f'timed out after {args.timeout:g}s')
                    try:
                        code = proc.wait(timeout=min(0.2, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                if proc is not None:
                    try:
                        terminate(proc)
                    finally:
                        with LIVE_LOCK:
                            LIVE.discard(proc)
    except Exception as exc:
        problem = f'{type(exc).__name__}: {exc}'
    elapsed = time.monotonic() - started
    execution = {'module': module or '<discovery>', 'native_rc': code,
                 'problem': problem, 'seconds': elapsed}
    (folder / 'process.json').write_text(json.dumps(execution), encoding='utf-8')
    return execution


def dispose(grace=DISPOSE_GRACE):
    """Stop every live worker group: TERM at once, then KILL whatever is left after the grace."""
    with LIVE_LOCK:
        groups = list(LIVE)

    def signal_group(proc, sig):
        try:
            os.killpg(proc.pid, sig)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return proc.poll() is None

    remaining = [proc for proc in groups if signal_group(proc, signal.SIGTERM)]
    deadline = time.monotonic() + grace
    while remaining and time.monotonic() < deadline:
        time.sleep(0.02)
        for proc in remaining:
            proc.poll()
        remaining = [proc for proc in remaining if signal_group(proc, 0)]
    for proc in remaining:
        signal_group(proc, signal.SIGKILL)


def stopped(cancel):
    """A proof cancel or timeout TERMs this runner's group: refuse new workers, dispose of the live ones, exit."""
    def handler(signum, frame):
        if Spawning.depth:
            Spawning.pending = signum
            return
        signal.signal(signum, signal.SIG_IGN)
        cancel.set()
        dispose()
        raise KeyboardInterrupt
    return handler


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def reconcile(folder, expected, process):
    problems = []
    started = []
    counts = dict(failed=0, errors=0, skipped=0, expected_failures=0)
    if process['problem'] or process['native_rc'] != 0:
        problems.append(process['problem'] or f"worker exited with native rc {process['native_rc']}")
    try:
        started = [json.loads(line) for line in (folder / 'started.jsonl').read_text().splitlines()]
        if not all(isinstance(item, str) for item in started):
            raise ValueError('invalid started-test ID')
    except (OSError, ValueError) as exc:
        problems.append(f'missing or invalid execution journal: {exc}')
        started = []
    try:
        receipt = read_json(folder / 'result.json')
        if receipt['collected'] != expected or receipt['executed'] != len(started):
            problems.append('worker collection/count receipt differs from expected execution')
        for name in counts:
            value = receipt[name]
            if type(value) is not int or value < 0:
                raise ValueError(f'invalid {name} count')
            counts[name] = value
        if receipt['passed'] is not True or any(counts[name] for name in ('failed', 'errors', 'skipped')):
            problems.append('module did not pass every required test (skips are not a complete proof)')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        problems.append(f'missing or invalid completion receipt: {exc}')
    if started != expected:
        problems.append(f'ID/count mismatch: collected={len(expected)} executed={len(started)}')
    return started, counts, problems


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('must be a positive integer')
    return number


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('-j', '--jobs', type=positive, default=min(os.cpu_count() or 1, 8),
                        help='maximum concurrent module processes (default: min(cpu count, 8))')
    result.add_argument('-s', '--start-directory', default='tests', help='unittest discovery directory (default: tests)')
    result.add_argument('-p', '--pattern', default='test*.py', help='unittest discovery pattern (default: test*.py)')
    result.add_argument('--timeout', type=positive, default=300,
                        help='seconds per module and discovery process (default: 300)')
    result.add_argument('--output', help='new log/profile directory outside the source tree (default: system temp)')
    result.add_argument('--_mode', choices=['collect', 'worker'], help=argparse.SUPPRESS)
    result.add_argument('--_module', help=argparse.SUPPRESS)
    result.add_argument('--_folder', help=argparse.SUPPRESS)
    return result


def run(args):
    sys.path.insert(0, str(ROOT / 'scripts'))
    start = time.monotonic()
    args.start_directory = str(Path(args.start_directory).resolve())
    if args.output:
        output = Path(args.output).resolve()
        if output.is_relative_to(ROOT) or output.is_relative_to(Path(args.start_directory)):
            raise ValueError('logs and profiles must be outside the source and discovery trees')
        output.mkdir(parents=True, mode=0o700)
    else:
        output = Path(tempfile.mkdtemp(prefix='squadra-parallel-')).resolve()
        if output.is_relative_to(ROOT) or output.is_relative_to(Path(args.start_directory)):
            output.rmdir()
            raise ValueError('temporary directory must be outside the source and discovery trees')
    cancel = threading.Event()
    if os.name != 'nt':
        signal.signal(signal.SIGTERM, stopped(cancel))
    print(f'Logs and isolated profiles: {output}', flush=True)
    process = launch(args, output / 'discovery', cancel)
    collected, executed = [], []
    totals = dict(failed=0, errors=0, skipped=0, expected_failures=0)
    worker_errors = 0
    manifest = {}
    try:
        manifest = read_json(output / 'discovery/result.json')
        collected = manifest['ids']
        if process['problem'] or process['native_rc'] != 0 or manifest['errors']:
            raise RuntimeError(f"{process}; {'; '.join(manifest['errors'])}")
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(f'DISCOVERY FAILED: {exc}')
        print((output / 'discovery/stderr.log').read_text(errors='replace'))
        worker_errors += 1
    if not worker_errors:
        modules = [(name, ids) for name, ids in manifest['modules'].items() if ids]
        parallel = [(name, ids) for name, ids in modules if name not in SERIAL_MODULES]
        serial = [(name, ids) for name, ids in modules if name in SERIAL_MODULES]
        print(f'Collected {len(collected)} tests in {len(modules)} modules; jobs={args.jobs}', flush=True)
        for name, _ in serial:
            if not SERIAL_MODULES[name].strip():
                raise ValueError(f'missing serial reason for {name}')
            print(f'SERIAL {name}: {SERIAL_MODULES[name]}', flush=True)

        def consume(folder, name, ids, outcome):
            nonlocal worker_errors
            found, counts, problems = reconcile(folder, ids, outcome)
            executed.extend(found)
            for key, value in counts.items():
                totals[key] += value
            worker_errors += bool(problems)
            print(f"\n=== {name}: collected={len(ids)} executed={len(found)} "
                  f"rc={outcome['native_rc']} wall={outcome['seconds']:.3f}s ===")
            for log in ('stdout.log', 'stderr.log'):
                path = folder / log
                if path.exists():
                    print(path.read_text(errors='replace'), end='')
            for problem in problems:
                print(f'WORKER FAILED {name}: {problem}')
            sys.stdout.flush()

        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            pending = {}
            try:
                for index, (name, ids) in enumerate(sorted(parallel, key=lambda pair: -len(pair[1]))):
                    folder = output / f'module-{index:03d}'
                    pending[pool.submit(launch, args, folder, cancel, name)] = (folder, name, ids)
                for future in as_completed(pending):
                    folder, name, ids = pending[future]
                    consume(folder, name, ids, future.result())
            except BaseException:
                cancel.set()
                raise
        for index, (name, ids) in enumerate(serial):
            folder = output / f'serial-{index:03d}'
            consume(folder, name, ids, launch(args, folder, cancel, name))
    mismatch = Counter(executed) != Counter(collected)
    if mismatch:
        print(f'COUNT/ID MISMATCH: collected={len(collected)} executed={len(executed)}')
    passed = not (worker_errors or mismatch or any(totals[k] for k in ('failed', 'errors', 'skipped')))
    elapsed = time.monotonic() - start
    summary = {'collected': len(collected), 'executed': len(executed), **totals,
               'worker_errors': worker_errors, 'wall_seconds': round(elapsed, 3), 'passed': passed}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(f"\nRan {len(executed)} tests in {elapsed:.3f}s\n\n{'OK' if passed else 'FAILED'}")
    print('SUMMARY ' + ' '.join(f'{key}={value}' for key, value in summary.items()))
    return 0 if passed else 1


def main():
    args = parser().parse_args()
    try:
        return child(args) if args._mode else run(args)
    except KeyboardInterrupt:
        print('Interrupted: workers cancelled; no passing proof.', file=sys.stderr)
        return 130
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
