#!/usr/bin/env python3
"""Measure repeated checkpoints on a disposable directory or a private macOS disk image.

Both versions run the same synthetic append/deliver workload. Physical deltas come
from df: only a dedicated mounted volume isolates them from other filesystem work.
The saved JSON names that limit instead of treating st_blocks as exclusive bytes.
"""
import argparse
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback


def command(argv, **kwargs):
    return subprocess.run(argv, check=True, capture_output=True, text=True, timeout=kwargs.pop('timeout', 120), **kwargs)


def df_used(path):
    line = command(['df', '-kP', str(path)]).stdout.splitlines()[-1].split()
    return int(line[2]) * 1024


def logical_bytes(path):
    return sum(p.lstat().st_size for p in Path(path).rglob('*') if p.is_file() and not p.is_symlink())


def worker(args):
    source = Path(args.source).resolve()
    sys.path.insert(0, str(source / 'scripts'))
    from registry import Store, canonical, snapshot
    from runtime import execute
    from squadra import deliver
    from recovery import save, restore_check
    folder = Path(args.dir).resolve()
    folder.mkdir(mode=0o700)
    repo, root = folder / 'source', folder / 'registry'
    repo.mkdir()
    command(['git', 'init', '-q', str(repo)])
    for key, value in [('user.name', 'Synthetic storage measurement'), ('user.email', 'storage@example.invalid')]:
        command(['git', '-C', str(repo), 'config', key, value])
    (repo / 'payload.txt').write_text('baseline\n' * 8192)
    command(['git', '-C', str(repo), 'add', '.'])
    command(['git', '-C', str(repo), '-c', 'commit.gpgsign=false', 'commit', '-qm', 'Synthetic baseline'])
    store = Store(root, create=True)
    try:
        store.append('init', 'system', {'name': 'Synthetic storage growth', 'project': str(repo),
                     'version': '3.12.1', 'limits': {'plan': 3, 'code': 2, 'build': 3}})
        for name, role in [('C', 'coordinator'), ('A', 'builder')]:
            store.append('agent', name, {'id': name, 'role': role, 'identity': name,
                         'client': 'manual', 'repo': str(repo), 'skills': [], 'session': ''})
        scope = store.artifact_bytes(b'Synthetic checkpoint concurrency workload.\n', 'scope.txt', 'synthetic-growth')
    finally:
        store.close()
    stop, request, prepared = threading.Event(), threading.Event(), threading.Event()
    failures, writes = [], []

    def write():
        writer = Store(root)
        try:
            for i in range(args.count):
                if not request.wait(120) or stop.is_set():
                    return
                request.clear()
                name = 'growth-' + str(i)
                (repo / 'payload.txt').write_text(str(i) + '\n' + 'same bytes across candidates\n' * 8192)
                proof = [sys.executable, '-c', 'print("synthetic native proof")']
                writer.batch([
                    ('task', 'C', {'id': name, 'title': 'Synthetic checkpoint workload', 'owner': 'A',
                     'repo': str(repo), 'base': snapshot(repo)['head'], 'scope': scope, 'proof': proof,
                     'expected': None, 'depends': [], 'full_gate': False, 'delivery_policy': 'frozen-v1'}, None),
                    ('message', 'C', {'id': 'assign-' + name, 'to': 'A', 'task': name, 'body': 'Synthetic workload'}, None),
                    ('ack', 'A', {'message': 'assign-' + name}, None),
                    ('start', 'A', {'task': name}, None)])
                prepared.set()
                result = execute(writer, 'A', name, argv=proof, timeout=30)
                if result['status'] != 'passed':
                    raise RuntimeError('Synthetic native proof failed')
                deliver(writer, 'A', name)
                writes.append(name)
                writer.append('output', 'A', {'task': name, 'text': 'Synthetic append and delivery completed',
                                             'next': 'Measure the next checkpoint', 'artifacts': []})
        except BaseException:
            failures.append(traceback.format_exc())
            prepared.set()
        finally:
            writer.close()

    thread = threading.Thread(target=write, name='synthetic-checkpoint-writer')
    thread.start()
    rows = []
    prior = None
    try:
        for i in range(args.count):
            checkpoint = folder / ('checkpoint-' + str(i))
            prepared.clear()
            request.set()
            if not prepared.wait(120) or failures:
                raise RuntimeError('Concurrent writer failed: ' + '\n'.join(failures))
            before = df_used(folder)
            started = time.monotonic()
            receipt = {}
            store = Store(root)
            try:
                import inspect
                import importlib.util
                options = {'report': receipt}
                parameters = inspect.signature(store.checkpoint).parameters
                if 'compact' in parameters:
                    options['compact'] = True
                if 'reuse' in parameters and prior is not None and importlib.util.find_spec('storage_reuse') is not None:
                    options['reuse'] = [prior]
                store.checkpoint(checkpoint, **options)
            finally:
                store.close()
            elapsed = time.monotonic() - started
            after = df_used(folder)
            rows.append({'index': i, 'seconds': round(elapsed, 6), 'logical_bytes': logical_bytes(checkpoint),
                         'df_before': before, 'df_after': after, 'physical_delta': after - before,
                         'checkpoint': str(checkpoint), 'boundary': receipt})
            prior = checkpoint
            # The next round starts only after this round's real delivery completes.
            deadline = time.monotonic() + 120
            while len(writes) <= i and not failures:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Concurrent delivery did not finish')
                time.sleep(0.02)
        if failures:
            raise RuntimeError('\n'.join(failures))
    finally:
        stop.set()
        request.set()
        thread.join(timeout=150)
        if thread.is_alive():
            raise TimeoutError('Synthetic writer did not stop; work retained at ' + str(folder))
    # A real restore-check of a save made from the sampled checkpoint proves the
    # sampled journal and retained candidates, on both 3.12.1 and the candidate.
    sample = Store(prior)
    try:
        sample.audit()
        save(sample, folder / 'sample-save', repos=[str(repo)], temporary=True)
    finally:
        sample.close()
    restored = restore_check(folder / 'sample-save', folder / 'sample-restored')
    result = {'source': str(source), 'count': args.count, 'rows': rows, 'delivered': writes,
              'restore_check': restored['status'], 'dedicated_volume': os.path.ismount(folder.parent),
              'physical_method': 'df -kP used bytes; concurrent workload included; shared-volume noise possible',
              'logical_total': sum(r['logical_bytes'] for r in rows),
              'physical_delta_total': sum(r['physical_delta'] for r in rows)}
    (folder / 'growth.json').write_text(json.dumps(result, indent=2) + '\n')
    with (folder / 'growth.csv').open('w', newline='') as out:
        columns = ['index', 'seconds', 'logical_bytes', 'df_before', 'df_after', 'physical_delta', 'checkpoint']
        csv_writer = csv.DictWriter(out, fieldnames=columns, extrasaction='ignore')
        csv_writer.writeheader()
        csv_writer.writerows(rows)
    print(json.dumps(result))


def run(args):
    parent = Path(args.dir).resolve()
    parent.mkdir(parents=True, exist_ok=True)
    image, mount = None, None
    if args.image:
        if sys.platform != 'darwin':
            raise ValueError('--image requires macOS hdiutil; use --dir on a dedicated volume')
        image, mount = parent / 'growth.sparseimage', parent / 'volume'
        if image.exists() or mount.exists():
            raise ValueError('Image and mountpoint must be new')
        command(['hdiutil', 'create', '-size', str(args.image_size_mib) + 'm', '-fs', args.image,
                 '-volname', 'SquadraGrowth', '-type', 'SPARSE', str(image)])
        mount.mkdir()
        command(['hdiutil', 'attach', '-nobrowse', '-mountpoint', str(mount), str(image)])
        parent = mount
    results = {}
    try:
        for label, source in [('candidate', args.source), *([('baseline', args.baseline)] if args.baseline else [])]:
            destination = parent / label
            if destination.exists():
                raise ValueError('Measurement directory must be new: ' + str(destination))
            argv = [sys.executable, '-B', str(Path(__file__).resolve()), '--_worker', '--source', str(source),
                    '--dir', str(destination), '--count', str(args.count)]
            receipt = command(argv, timeout=args.timeout)
            result = json.loads(receipt.stdout.splitlines()[-1])
            results[label] = result
        comparison = {'results': results, 'physical_note': 'Use a dedicated idle volume; APFS clone savings require volume-level df deltas.'}
        if 'baseline' in results:
            comparison['logical_difference'] = results['candidate']['logical_total'] - results['baseline']['logical_total']
            comparison['physical_difference'] = results['candidate']['physical_delta_total'] - results['baseline']['physical_delta_total']
        output = Path(args.output).resolve() if args.output else Path(args.dir).resolve() / 'comparison.json'
        output.write_text(json.dumps(comparison, indent=2) + '\n')
        print(json.dumps({'output': str(output), 'results': list(results)}))
    finally:
        if mount is not None:
            command(['hdiutil', 'detach', str(mount)])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dir', required=True, help='Disposable directory, preferably a dedicated mounted volume')
    parser.add_argument('--source', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--baseline', help='A complete 3.12.1 checkout for comparison')
    parser.add_argument('--count', type=int, default=5)
    parser.add_argument('--timeout', type=int, default=1800)
    parser.add_argument('--image', choices=['APFS', 'HFS+'], help='Create and detach a private macOS image')
    parser.add_argument('--image-size-mib', type=int, default=4096)
    parser.add_argument('--output')
    parser.add_argument('--_worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.count < 1 or args.timeout < 1 or args.image_size_mib < 64:
        parser.error('count/timeout must be positive; image size must be at least 64 MiB')
    worker(args) if args._worker else run(args)


if __name__ == '__main__':
    main()
