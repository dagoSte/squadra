"""Materialize a proved candidate without retaining the builder's working tree."""
from contextlib import contextmanager
import os
import stat
import uuid
from pathlib import Path
import shutil
import subprocess
import tempfile

from registry import Error, canonical, digest, git, require, snapshot


def repository(task, root):
    frozen = task['delivery']['frozen']
    require(root is not None and isinstance(frozen.get('sha256'), str) and
            len(frozen['sha256']) == 64 and all(c in '0123456789abcdef' for c in frozen['sha256']),
            'Invalid frozen candidate address')
    path = Path(root).resolve() / 'candidates' / frozen['sha256'] / 'repo'
    require(path.resolve().is_relative_to(Path(root).resolve() / 'candidates'),
            'Frozen candidate must remain inside this registry')
    return path


def measured(task, path):
    measured = snapshot(path)
    measured['repo'] = task['repo']
    measured['sha256'] = digest(canonical({k: measured[k] for k in ('repo', 'head', 'files')}).encode())
    return measured


MAX_COPY_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024


def limits(store):
    value = next((o['candidate_action'] for o in reversed(store.view()['outputs'])
                  if o.get('candidate_action', {}).get('action') == 'limits'), {})
    return value.get('max_copy_bytes', MAX_COPY_BYTES), value.get('max_total_bytes', MAX_TOTAL_BYTES)


def size(path):
    return sum(p.lstat().st_size for p in Path(path).rglob('*') if p.is_file() and not p.is_symlink())


@contextmanager
def cache_lock(store):
    import fcntl
    folder = store.root / 'candidates'
    folder.mkdir(mode=0o700, exist_ok=True)
    require(not folder.is_symlink(), 'Candidate folder cannot be a symlink')
    fd = os.open(folder / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode) and os.fstat(fd).st_nlink == 1, 'Unsafe candidate lock')
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def readonly(path):
    for parent, dirs, files in os.walk(path, topdown=False, followlinks=False):
        for name in files:
            leaf = Path(parent) / name
            if not leaf.is_symlink():
                leaf.chmod(0o555 if leaf.stat().st_mode & 0o111 else 0o444)
        for name in dirs:
            leaf = Path(parent) / name
            if not leaf.is_symlink():
                leaf.chmod(0o555)
    Path(path).chmod(0o555)


def remove(path):
    def retry(function, name, _):
        Path(name).parent.chmod(0o700)
        if not Path(name).is_symlink():
            Path(name).chmod(0o700)
        function(name)
    shutil.rmtree(path, onerror=retry)


def current(task, root=None):
    frozen = task.get('delivery', {}).get('frozen')
    if not frozen:
        return snapshot(task['repo'], root)
    path = Path(root) / 'candidates' / frozen['sha256'] / 'repo'
    label = 'Frozen candidate task ' + task.get('id', '?') + ' ' + frozen['sha256'] + ' at ' + str(path)
    try:
        path = repository(task, root)
        result = measured(task, path)
    except (Error, OSError) as exc:
        raise Error(label + ' unavailable: ' + str(exc) + '; use candidate status/repair') from exc
    if result['sha256'] != frozen['sha256']:
        expected = task.get('candidate', task.get('delivery', {}).get('snapshot', {}))
        old = {r['path']: r for r in expected.get('files', [])}
        new = {r['path']: r for r in result['files']}
        changed = sorted(k for k in old.keys() | new.keys() if old.get(k) != new.get(k))
        raise Error(label + ' changed paths: ' + ', '.join(changed[:20] or ['HEAD or index']) +
                    '; use candidate repair (original is retained)')
    return result


def inventory(store):
    retired = {o.get('candidate_action', {}).get('sha256') for o in store.view()['outputs']
               if o.get('candidate_action', {}).get('action') == 'retire'}
    rows, seen = [], set()
    for event in store.events():
        data = event['data']
        if event['kind'] != 'deliver' or not data.get('frozen') or data['frozen']['sha256'] in seen:
            continue
        sha = data['frozen']['sha256']; seen.add(sha)
        expected = store.resolve_snapshot(data['snapshot'])
        task = {'id': data['task'], 'repo': expected['repo'], 'candidate': expected, 'delivery': data}
        path = store.root / 'candidates' / sha / 'repo'
        row = {'task': data['task'], 'sha256': sha, 'path': str(path), 'status': 'available'}
        try:
            repository(task, store.root)
        except Error as exc:
            rows.append({**row, 'status': 'damaged', 'reason': str(exc)})
            continue
        if not path.exists():
            row['status'] = 'retired' if sha in retired else 'missing'
        else:
            try:
                current(task, store.root)
            except (Error, OSError) as exc:
                row.update(status='damaged', reason=str(exc))
        rows.append(row)
    return rows


def freeze(store, task, candidate):
    with cache_lock(store):
        return _freeze(store, task, candidate)


def _freeze(store, task, candidate):
    parent = store.root / 'candidates'
    parent.mkdir(mode=0o700, exist_ok=True)
    require(not parent.is_symlink(), 'Candidate folder cannot be a symlink')
    destination = parent / candidate['sha256']
    record = {'repo': str(destination / 'repo'), 'sha256': candidate['sha256']}
    if destination.exists():
        require(current({**task, 'delivery': {'frozen': record}}, store.root) == candidate,
                'Existing frozen candidate differs')
        return record
    base_bytes = sum(int(row.split(b'\t', 1)[0].split()[-1]) for row in
                     git(task['repo'], 'ls-tree', '-rlz', candidate['head']).split(b'\0') if row and row.split(b'\t', 1)[0].split()[-1].isdigit())
    working_bytes = sum((Path(task['repo']) / row['path']).lstat().st_size for row in candidate['files']
                        if row['kind'] != 'deleted')
    max_copy, max_total = limits(store)
    estimate = 2 * base_bytes + working_bytes + 16 * 1024 * 1024
    require(estimate <= max_copy and size(parent) + estimate <= max_total,
            'Frozen storage limit (configured per-copy/total quota including quarantine); retire closed copies, archive this registry, or ask the coordinator to record higher candidate limits')
    stage = Path(tempfile.mkdtemp(prefix='.freeze-', dir=parent))
    try:
        copied = stage / 'repo'
        git(store.root, 'init', '--quiet', str(copied))
        result = subprocess.run(['git', '-C', str(copied), '-c', 'core.hooksPath=/dev/null',
                                 'fetch', '--quiet', '--depth=1', '--no-tags', '--',
                                 Path(task['repo']).resolve().as_uri(), candidate['head']],
                                capture_output=True, timeout=120)
        require(result.returncode == 0, 'Cannot fetch the delivered Git commit')
        result = subprocess.run(['git', '-C', str(copied), '-c', 'core.hooksPath=/dev/null',
                                 'checkout', '--quiet', '--detach', candidate['head']],
                                capture_output=True, timeout=120)
        require(result.returncode == 0, 'Cannot check out the delivered Git head')
        for row in candidate['files']:
            relative = Path(row['path'])
            require(not relative.is_absolute() and '..' not in relative.parts and '.git' not in relative.parts,
                    'Invalid frozen candidate path')
            source, target = Path(task['repo']) / relative, copied / relative
            require(all(not p.is_symlink() for p in target.parents if p != copied.parent and p.is_relative_to(copied)),
                    'Frozen candidate parent is a symlink')
            if target.is_symlink() or target.is_file():
                target.unlink()
            if row['kind'] == 'deleted':
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if row['kind'] == 'symlink':
                target.symlink_to(os.readlink(source))
            else:
                shutil.copyfile(source, target, follow_symlinks=False)
                target.chmod(0o755 if row['executable'] else 0o644)
        require(snapshot(task['repo'], store.root) == candidate, 'Source changed while freezing delivery')
        require(measured(task, copied) == candidate,
                'Frozen copy does not match the proved source')
        require(size(stage) <= max_copy and size(parent) <= max_total, 'Frozen storage limit exceeded')
        readonly(stage)
        try:
            stage.rename(destination)
        except FileExistsError:
            require(current({**task, 'delivery': {'frozen': record}}, store.root) == candidate,
                    'Concurrent frozen copy differs')
        return record
    finally:
        if stage.exists():
            remove(stage)


def checkpoint(store, destination):
    """Copy healthy retained candidates; damaged/missing caches cannot block journal recovery."""
    rows = inventory(store)
    for row in rows:
        if row['status'] != 'available':
            continue
        source = Path(row['path'])
        target = Path(destination) / 'candidates' / row['sha256'] / 'repo'
        target.parent.mkdir(parents=True, mode=0o700)
        try:
            shutil.copytree(source, target, symlinks=True)
        except OSError as exc:
            if target.parent.exists():
                remove(target.parent)
            row.update(status='damaged', reason='Source copy unavailable: ' + str(exc))
            continue
        delivered = next(e['data'] for e in store.events() if e['kind'] == 'deliver' and e['data'].get('frozen', {}).get('sha256') == row['sha256'])
        expected = store.resolve_snapshot(delivered['snapshot'])
        task = {'id': row['task'], 'repo': expected['repo'], 'candidate': expected, 'delivery': delivered}
        try:
            current(task, store.root)
            current(task, destination)
        except (Error, OSError) as exc:
            remove(target.parent)
            row.update(status='damaged', reason=str(exc))
    return rows


def manage(store, actor, task_id, action, reason, max_copy_mib=None, max_total_mib=None):
    if action == 'status':
        max_copy, max_total = limits(store)
        return {'candidates': inventory(store), 'bytes': size(store.root / 'candidates'),
                'max_copy_bytes': max_copy, 'max_total_bytes': max_total}
    require(store.view()['agents'][actor]['role'] == 'coordinator', 'Candidate repair/retire requires coordinator')
    require(reason and reason.strip(), 'Recorded candidate action reason required')
    with cache_lock(store):
        if action == 'limits':
            require(type(max_copy_mib) is int and type(max_total_mib) is int and
                    1 <= max_copy_mib <= max_total_mib <= 102400, 'Limits need integer MiB: 1 <= copy <= total <= 102400')
            record = {'action': action, 'max_copy_bytes': max_copy_mib * 1024 * 1024,
                      'max_total_bytes': max_total_mib * 1024 * 1024, 'reason': reason}
            require(size(store.root / 'candidates') <= record['max_total_bytes'], 'New total quota is below retained bytes; retire first')
            store.append('output', actor, {'text': 'Configure frozen candidate quotas', 'next': 'Use the recorded storage limits.',
                         'artifacts': [], 'candidate_action': record})
            return record
        task = store.task(task_id)
        require(task.get('delivery', {}).get('frozen'), 'Task has no frozen delivery')
        path = repository(task, store.root)
        sha = task['delivery']['frozen']['sha256']
        record = {'action': action, 'sha256': sha, 'reason': reason}
        if action == 'retire':
            live = [t['id'] for t in store.view()['tasks'].values() if t.get('delivery', {}).get('frozen', {}).get('sha256') == sha
                    and t['status'] != 'done' and not t.get('superseded_by')]
            require(not live, 'Candidate still needed by active tasks: ' + ', '.join(live))
            store.append('output', actor, {'task': task_id, 'text': 'Retire frozen candidate ' + sha,
                         'next': 'Historical proofs remain; source cache is retired.', 'artifacts': [], 'candidate_action': record})
            if path.parent.exists():
                remove(path.parent)
        else:
            expected = task['candidate']
            require(snapshot(task['repo'], store.root) == expected, 'Repair needs the exact proved source; restore it or create a new delivery')
            if path.parent.exists():
                quarantine = path.parent.with_name('damaged-' + sha + '-' + uuid.uuid4().hex)
                path.parent.rename(quarantine)
                record['quarantine'] = str(quarantine)
            record['phase'] = 'started'
            store.append('output', actor, {'task': task_id, 'text': 'Rematerialize frozen candidate ' + sha,
                         'next': 'Read the rematerialized candidate; retained damage is evidence.', 'artifacts': [], 'candidate_action': record})
            _freeze(store, task, expected)
            record['phase'] = 'completed'
            store.append('output', actor, {'task': task_id, 'text': 'Frozen candidate rematerialized ' + sha,
                         'next': 'Review the exact restored candidate.', 'artifacts': [], 'candidate_action': record})
        return record
