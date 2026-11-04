"""Recorded recovery points and conservative, preview-first storage maintenance."""
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat

from registry import Error, Store, atomic, canonical, digest, now, plain, require

OMITTED = ['state.json', 'posta', 'squadra-posta.md', 'stato.md']
FORM = 'squadra-checkpoint-compact/1'
DEFAULTS = {'checkpoints': 3, 'saves': 3, 'hours': 24}
LIVE = {'running', 'cleanup-unknown'}


def safe_path(value):
    """Refuse symlinks in every existing component, including the final name."""
    path = Path(os.path.abspath(value))
    for part in [*reversed(path.parents), path]:
        require(not part.is_symlink(), 'Storage path contains a symlink: ' + str(part))
    return path


def tree_facts(path):
    """Allocated blocks are an upper estimate, not exclusive COW ownership."""
    path = safe_path(path)
    rows, logical, physical = [], 0, 0
    for folder, dirs, files in os.walk(path, followlinks=False):
        for name in sorted(dirs + files):
            item = Path(folder) / name
            info = item.lstat()
            require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode),
                    'Special storage entry refused')
            rows.append((str(item.relative_to(path)), info.st_dev, info.st_ino, info.st_mode,
                         info.st_size, info.st_mtime_ns, info.st_ctime_ns))
            if stat.S_ISREG(info.st_mode):
                logical += info.st_size
                physical += info.st_blocks * 512
    info = path.lstat()
    rows.append(('.', info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
    return {'logical': logical, 'new_physical_estimate': physical,
            'signature': digest(canonical(sorted(rows)).encode())}


def checksums(root):
    result = {}
    for folder, dirs, files in os.walk(root, followlinks=False):
        links = [n for n in dirs if (Path(folder) / n).is_symlink()]
        for name in files + links:
            p = Path(folder) / name
            if name in {'registry.sqlite3-wal', 'registry.sqlite3-shm'} or p == root / 'CHECKSUMS.json':
                continue
            require(p.is_symlink() or p.is_file(), 'Checkpoint special file refused')
            result[str(p.relative_to(root))] = digest(os.fsencode(os.readlink(p)) if p.is_symlink() else p.read_bytes())
    return result


def verify_checkpoint(path):
    path = safe_path(path)
    marker = path / 'CHECKSUMS.json'
    require(marker.is_file() and not marker.is_symlink(), 'Unrecognized checkpoint')
    require(json.loads(marker.read_text()) == checksums(path), 'Checkpoint checksums changed')
    database = safe_path(path / 'registry.sqlite3')
    connection = sqlite3.connect(database.as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        seq, head = connection.execute('SELECT seq,hash FROM events ORDER BY seq DESC LIMIT 1').fetchone() or (0, '0' * 64)
    finally:
        connection.close()
    return {'event_count': seq, 'event_head': head}


def compact_marker(destination, boundary):
    for name in OMITTED:
        path = destination / name
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    atomic(destination / 'COMPACT.json', (canonical({'form': FORM, 'omitted': OMITTED, **boundary}) + '\n').encode())


def restore_projections(root):
    """Explicit CLI/recovery entry: verify the compact copy before materializing views."""
    root = Path(root)
    marker = root / 'COMPACT.json'
    if not marker.exists() and not marker.is_symlink():
        return False
    safe_path(marker)
    data = json.loads(marker.read_text())
    require(data.get('form') == FORM and data.get('omitted') == OMITTED, 'Invalid compact checkpoint marker')
    boundary = verify_checkpoint(root)
    require(boundary == {k: data.get(k) for k in ('event_count', 'event_head')}, 'Compact checkpoint head changed')
    check = Store(root)
    try:
        require(check.audit()['cache'] != 'mismatch', 'Compact checkpoint cache mismatch')
        check.project(audited=True)
    finally:
        check.close()
    marker.unlink()
    atomic(root / 'CHECKSUMS.json', (canonical(checksums(root)) + '\n').encode())
    return True


def record(store, actor, action, **data):
    return store.append('output', actor, {'text': 'Storage ' + action, 'next': 'Inspect storage status before maintenance.',
                                        'artifacts': [], 'storage_action': {'action': action, **data}})


def policy_values(values=None):
    policy = {**DEFAULTS, **(values or {})}
    require(set(policy) == set(DEFAULTS) and all(type(v) is int and v >= 0 for v in policy.values()),
            'Retention counts/hours must be nonnegative integers')
    return policy


def records(store):
    state = plain(store.view())
    store.release_views()
    points, keeps, policy = {}, {}, dict(DEFAULTS)
    for item in state['outputs']:
        data = item.get('storage_action', {})
        if data.get('action') == 'checkpoint':
            points[data['path']] = {**data, 'kind': 'checkpoint', 'utc': item['utc']}
        elif data.get('action') == 'keep':
            keeps[data['path']] = data['reason']
        elif data.get('action') == 'policy':
            policy = policy_values(data['policy'])
    for item in state['saves']:
        try:
            body = (store.root / 'artifacts' / item['manifest']['sha256']).read_bytes()
            require(digest(body) == item['manifest']['sha256'], 'Save manifest artifact changed')
            saved = json.loads(body)
            points[item['path']] = {'path': item['path'], 'kind': 'save', 'utc': item['utc'], **saved['registry']}
        except (OSError, KeyError, ValueError):
            continue
    return state, points, keeps, policy


def recognized(point):
    path = safe_path(point['path'])
    if point['kind'] == 'checkpoint':
        boundary = verify_checkpoint(path)
    else:
        from recovery import verify_package
        boundary = verify_package(path)['registry']
    require(all(point.get(k) == boundary[k] for k in ('event_count', 'event_head')), 'Recorded recovery head changed')
    return path


def previous(store, kind, destination):
    """Newest verified registered recovery point on the destination filesystem."""
    _, points, _, _ = records(store)
    parent = Path(destination).absolute().parent
    while not parent.exists():
        parent = parent.parent
    device = parent.stat().st_dev
    for point in sorted(points.values(), key=lambda r: r['utc'], reverse=True):
        if point['kind'] != kind or point['path'] == str(Path(destination).absolute()):
            continue
        try:
            path = recognized(point)
            if path.stat().st_dev == device:
                return path
        except (OSError, Error, ValueError, sqlite3.Error):
            continue
    return None


def latest_restore(store, state):
    for item in reversed(state['restores']):
        ref = item.get('result', {})
        try:
            body = (store.root / 'artifacts' / ref['sha256']).read_bytes()
            if digest(body) == ref['sha256']:
                result = json.loads(body)
                if result.get('status') == 'passed':
                    return result['package']
        except (OSError, KeyError, ValueError):
            continue
    return None


def plan(store, *, policy=None, at=None):
    state, points, keeps, configured = records(store)
    configured = policy_values(policy or configured)
    at = at or now()
    clock = dt.datetime.fromisoformat(at)
    retained = set(keeps)
    restored = latest_restore(store, state)
    if restored:
        retained.add(restored)
    verified, ignored = {}, []
    for point in points.values():
        try:
            verified[point['path']] = recognized(point)
        except (Error, OSError, ValueError, sqlite3.Error) as exc:
            ignored.append({'path': point['path'], 'reason': str(exc)})
    for kind, count in (('checkpoint', configured['checkpoints']), ('save', configured['saves'])):
        available = sorted((r for r in points.values() if r['kind'] == kind and r['path'] in verified),
                           key=lambda r: r['utc'], reverse=True)
        retained.update(r['path'] for r in available[:count])
    protected = {store.root}
    live_shas = set()
    for task in state['tasks'].values():
        if task['status'] != 'done' and not task.get('superseded_by'):
            protected.add(Path(task['repo']).resolve())
            sha = task.get('delivery', {}).get('frozen', {}).get('sha256')
            if sha:
                live_shas.add(sha)
                protected.add(store.root / 'candidates' / sha)
    for run in state['runs'].values():
        if run['status'] in LIVE:
            protected.add(store.root / 'runs' / run['id'])
            protected.add(store.root / 'run-leases')
    rows = []

    def add(path, kind, utc, extra=None):
        path = safe_path(path)
        if not path.is_dir() or any(row['path'] == str(path) for row in rows):
            return
        reasons = []
        if any(path == p or path in p.parents for p in protected):
            reasons.append('active registry, task source, candidate or run')
        if any(p != store.root and p in path.parents for p in protected):
            reasons.append('inside an active task source or run')
        if str(path) in retained or any(path == Path(p) or path in Path(p).parents or Path(p) in path.parents for p in keeps):
            reasons.append('retention or explicit keep')
        age = (clock - dt.datetime.fromisoformat(utc)).total_seconds()
        if age < configured['hours'] * 3600:
            reasons.append('younger than retention age')
        facts = tree_facts(path)
        rows.append({'path': str(path), 'kind': kind, 'eligible': not reasons, 'protected': reasons,
                     **facts, **(extra or {})})

    for point in points.values():
        if point['path'] in verified:
            add(verified[point['path']], point['kind'], point['utc'])
    from frozen_candidate import candidate_records
    candidates, retired = candidate_records(store)
    for task in candidates:
        sha = task['delivery']['frozen']['sha256']
        path = store.root / 'candidates' / sha
        if sha not in retired and path.exists():
            event = next(e for e in reversed(store.events()) if e['kind'] == 'deliver'
                         and e['data'].get('frozen', {}).get('sha256') == sha)
            add(path, 'candidate', event['utc'], {'sha256': sha, 'task': task['id']})
    for run in state['runs'].values():
        if run['status'] in LIVE:
            continue
        path = store.root / 'runs' / run['id']
        try:
            safe_path(path)
            if not path.is_dir():
                continue
            files = [p for p in path.rglob('*') if p.is_file() or p.is_symlink()]
            references = {ref['sha256'] for ref in run.get('artifacts', [])}
            if not files or any(p.is_symlink() or digest(p.read_bytes()) not in references or
                                not (store.root / 'artifacts' / digest(p.read_bytes())).is_file() or
                                (store.root / 'artifacts' / digest(p.read_bytes())).read_bytes() != p.read_bytes()
                                for p in files):
                continue
            add(path, 'run', run.get('ended', run['utc']))
        except (Error, OSError):
            continue
    for item in state['outputs']:
        action = item.get('candidate_action', {})
        if action.get('quarantine'):
            path = Path(action['quarantine'])
            if path.parent == store.root / 'candidates' and path.name.startswith('damaged-'):
                try:
                    add(path, 'quarantine', item['utc'])
                except (Error, OSError):
                    pass
    eligible = [r for r in rows if r['eligible']]
    return {'root': str(store.root), 'at': at, 'policy': configured, 'entries': rows, 'ignored': ignored,
            'logical_bytes': sum(r['logical'] for r in eligible),
            'physical_estimate': sum(r['new_physical_estimate'] for r in eligible),
            'physical_note': 'Allocated blocks are an upper estimate; COW/shared blocks may free less.',
            'plan_hash': digest(canonical({'policy': configured, 'entries': rows, 'ignored': ignored}).encode())}


def remove_tree(path):
    """Only directories become writable; shared file inodes keep their mode."""
    path = safe_path(path)
    for folder, dirs, _ in os.walk(path, followlinks=False):
        Path(folder).chmod(0o700)
    shutil.rmtree(path)


def coordinator(store, actor, reason):
    require(actor in store.view()['agents'] and store.view()['agents'][actor]['role'] == 'coordinator',
            'Coordinator role required')
    require(isinstance(reason, str) and bool(reason.strip()), 'Storage action reason required')
    store.release_views()


def purge(store, actor=None, reason=None, *, apply=False, policy=None, expected=None):
    preview = plan(store, policy=policy)
    if not apply:
        return {**preview, 'dry_run': True}
    coordinator(store, actor, reason)
    from frozen_candidate import cache_lock
    with cache_lock(store):
        current = plan(store, policy=policy, at=preview['at'])
        require(current == preview and (expected is None or expected == current['plan_hash']),
                'Storage plan changed; inspect a new dry-run')
        if policy is not None:
            record(store, actor, 'policy', policy=current['policy'], reason=reason)
        paths = [r for r in current['entries'] if r['eligible']]
        record(store, actor, 'purge', phase='started', removed=[r['path'] for r in paths],
               reason=reason, plan_hash=current['plan_hash'])
        removed, error = [], None
        try:
            for row in paths:
                require(tree_facts(row['path'])['signature'] == row['signature'], 'Storage target changed')
                if row['kind'] == 'candidate':
                    store.append('output', actor, {'task': row['task'], 'text': 'Retire frozen candidate ' + row['sha256'],
                                 'next': 'Historical proofs remain.', 'artifacts': [],
                                 'candidate_action': {'action': 'retire', 'sha256': row['sha256'], 'reason': reason}})
                remove_tree(row['path'])
                removed.append(row['path'])
        except BaseException as exc:
            error = str(exc)
            raise
        finally:
            record(store, actor, 'purge', phase='completed' if error is None else 'failed',
                   removed=removed, reason=reason, error=error)
        return {**current, 'dry_run': False, 'removed': removed}


def keep(store, actor, path, reason):
    require(actor in store.view()['agents'] and reason and reason.strip(), 'Registered actor and keep reason required')
    path = safe_path(path)
    require(path.is_dir(), 'Keep path must be an existing regular directory')
    record(store, actor, 'keep', path=str(path), reason=reason)
    return {'kept': str(path), 'reason': reason}


def compact(store, actor=None, reason=None, *, apply=False):
    from storage_reuse import compact as reuse_compact
    preview = plan(store)
    targets = [r['path'] for r in preview['entries'] if r['kind'] in {'checkpoint', 'save', 'candidate', 'run'}]
    if not apply:
        return reuse_compact(store.root, targets, dry_run=True)
    coordinator(store, actor, reason)
    from frozen_candidate import cache_lock
    with cache_lock(store):
        require(plan(store, at=preview['at']) == preview, 'Storage plan changed; inspect a new dry-run')
        record(store, actor, 'compact', phase='started', targets=targets, reason=reason)
        try:
            result = reuse_compact(store.root, targets, dry_run=False)
        except BaseException as exc:
            record(store, actor, 'compact', phase='failed', reason=reason, error=str(exc))
            raise
        record(store, actor, 'compact', phase='completed', reason=reason, result=result)
        return result
