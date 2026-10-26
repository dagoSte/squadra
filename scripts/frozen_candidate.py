"""Materialize a proved candidate without retaining the builder's working tree."""
from contextlib import contextmanager
import os
import stat
import uuid
from pathlib import Path
import shutil
import tempfile

from registry import Error, canonical, digest, git, git_probe, git_process, plain, require, snapshot


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
QUOTA_HELP = ('; inspect with candidate status; coordinator: candidate retire --actor COORD --sha256 SHA --reason TEXT '
              'for an obsolete copy, or candidate limits --actor COORD --max-copy-mib N --max-total-mib N --reason TEXT')


def limits(store):
    try:
        value = next((o['candidate_action'] for o in reversed(store.view()['outputs'])
                      if o.get('candidate_action', {}).get('action') == 'limits'), {})
        return value.get('max_copy_bytes', MAX_COPY_BYTES), value.get('max_total_bytes', MAX_TOTAL_BYTES)
    finally:
        store.release_views()


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


def candidate_records(store):
    """Materialize journal facts before any potentially slow Git or file operation."""
    try:
        events = store.events()
    finally:
        store.release_views()
    retired = {e['data']['candidate_action']['sha256'] for e in events
               if e['kind'] == 'output' and e['data'].get('candidate_action', {}).get('action') == 'retire'}
    tasks, seen = [], set()
    for event in events:
        data = event['data']
        if event['kind'] != 'deliver' or not data.get('frozen') or data['frozen']['sha256'] in seen:
            continue
        sha = data['frozen']['sha256']; seen.add(sha)
        expected = store.resolve_snapshot(data['snapshot'])
        tasks.append({'id': data['task'], 'repo': expected['repo'], 'candidate': expected, 'delivery': data})
    return tasks, retired


def inventory_records(store, source_root=None):
    tasks, retired = candidate_records(store)
    root = Path(source_root) if source_root is not None else store.root
    rows = []
    for task in tasks:
        sha = task['delivery']['frozen']['sha256']
        path = root / 'candidates' / sha / 'repo'
        row = {'task': task['id'], 'sha256': sha, 'path': str(path), 'status': 'available'}
        try:
            repository(task, root)
        except Error as exc:
            rows.append(({**row, 'status': 'damaged', 'reason': str(exc)}, task))
            continue
        if not path.exists():
            row['status'] = 'retired' if sha in retired else 'missing'
        else:
            try:
                current(task, root)
            except (Error, OSError) as exc:
                row.update(status='damaged', reason=str(exc))
        rows.append((row, task))
    return rows


def inventory(store):
    return [row for row, _ in inventory_records(store)]


def freeze(store, task, candidate):
    task = plain(task)
    store.release_views()
    with cache_lock(store):
        return _freeze(store, task, candidate)


def validate_candidate(repo, files=None, *, working_only=False, label='Frozen candidate'):
    """Check the bytes that a frozen checkout or its Git metadata would carry."""
    from recovery import record_file, secret, private_key, inside
    repo = Path(repo)
    heads = {}
    for row in git(repo, 'ls-tree', '-r', '-z', 'HEAD').split(b'\0'):
        if row:
            meta, name = row.split(b'\t', 1)
            mode, _, sha = meta.split()
            heads[os.fsdecode(name)] = (mode, sha.decode('ascii'))
    if files is None:
        files = snapshot(repo)['files']
    for row in files:
        rel = row['path']; path = repo / rel
        require(not Path(rel).is_absolute() and '..' not in Path(rel).parts and
                all(p.lower() != '.git' for p in Path(rel).parts), label + ' invalid path: ' + rel)
        require(all(not p.is_symlink() for p in path.parents if p.is_relative_to(repo)),
                label + ' symlinked parent refused: ' + rel)
        if row['kind'] == 'deleted':
            continue
        record_file(path, repo)
        body = os.fsencode(os.readlink(path)) if path.is_symlink() else path.read_bytes()
        head = heads.get(rel)
        mode = b'120000' if row['kind'] == 'symlink' else b'100755' if row.get('executable') else b'100644'
        unchanged = head and head[0] == mode and git(repo, 'cat-file', 'blob', head[1]) == body
        if unchanged and working_only:
            continue
        require(not secret(path, body, tracked=rel in heads), label + ' secret file refused: ' + rel +
                ('; commit the template at HEAD or ignore it before delivery' if rel not in heads else ''))
    if working_only:
        return
    for rel, (mode, sha) in heads.items():
        body = git(repo, 'cat-file', 'blob', sha)
        require(not secret(Path(rel), body, tracked=True), label + ' secret HEAD file refused: ' + rel)
        if mode == b'120000':
            target = os.fsdecode(body)
            require(not Path(target).is_absolute() and inside(((repo / rel).parent / target).resolve(), repo),
                    label + ' escaping HEAD symlink refused: ' + rel)
    for row in git(repo, 'ls-files', '--stage', '-z').split(b'\0'):
        if row:
            meta, name = row.split(b'\t', 1)
            mode, sha, _ = meta.split()
            if mode != b'160000':
                require(not private_key(git(repo, 'cat-file', 'blob', sha.decode('ascii'))),
                        label + ' private-key index blob refused: ' + os.fsdecode(name))


def tree_bytes(repo, revisions):
    return sum(int(row.split(b'\t', 1)[0].split()[-1]) for revision in set(revisions) for row in
               git(repo, 'ls-tree', '-rlz', revision).split(b'\0')
               if row and row.split(b'\t', 1)[0].split()[-1].isdigit())


def fetch(repo, source, revisions):
    result = git_process(repo, '-c', 'gc.auto=0', '-c', 'maintenance.auto=false',
                             'fetch', '--quiet', '--depth=1', '--no-tags', '--no-write-fetch-head', '--',
                             Path(source).resolve().as_uri(), *dict.fromkeys(revisions),
                             local_only=True, timeout=120)
    require(result.returncode == 0, 'Cannot fetch the delivered commit or required light base')


def retain_base(store, task, repo):
    if task.get('route') != 'light':
        return
    base = task['base']
    if git_probe('-C', str(repo), 'cat-file', '-e', base + '^{tree}').returncode == 0:
        return
    extra = 2 * tree_bytes(task['repo'], [base]) + 16 * 1024 * 1024
    max_copy, max_total = limits(store)
    require(size(repo.parent) + extra <= max_copy and size(store.root / 'candidates') + extra <= max_total,
            'Frozen storage limit for light base' + QUOTA_HELP)
    metadata = repo / '.git'
    require(metadata.is_dir() and not metadata.is_symlink(), 'Frozen Git metadata must be a directory')
    try:
        # Git adds immutable objects and atomically updates shallow metadata; checkout bytes stay sealed.
        for parent, dirs, _ in os.walk(metadata, followlinks=False):
            Path(parent).chmod(0o755)
            require(all(not (Path(parent) / name).is_symlink() for name in dirs), 'Linked frozen Git directory')
        fetch(repo, task['repo'], [base])
        require(size(repo.parent) <= max_copy and size(store.root / 'candidates') <= max_total,
                'Frozen storage limit exceeded while retaining light base' + QUOTA_HELP)
    finally:
        readonly(metadata)


def _freeze(store, task, candidate):
    validate_candidate(task['repo'], candidate['files'], working_only=True,
                       label='Frozen candidate ' + candidate['sha256'])
    parent = store.root / 'candidates'
    parent.mkdir(mode=0o700, exist_ok=True)
    require(not parent.is_symlink(), 'Candidate folder cannot be a symlink')
    destination = parent / candidate['sha256']
    record = {'repo': str(destination / 'repo'), 'sha256': candidate['sha256']}
    if destination.exists():
        require(current({**task, 'delivery': {'frozen': record}}, store.root) == candidate,
                'Existing frozen candidate differs')
        retain_base(store, task, destination / 'repo')
        require(measured(task, destination / 'repo') == candidate, 'Frozen checkout changed while retaining light base')
        return record
    revisions = [candidate['head']]
    if task.get('route') == 'light':
        revisions.append(task['base'])
    base_bytes = tree_bytes(task['repo'], revisions)
    working_bytes = sum((Path(task['repo']) / row['path']).lstat().st_size for row in candidate['files']
                        if row['kind'] != 'deleted')
    max_copy, max_total = limits(store)
    estimate = 2 * base_bytes + working_bytes + 16 * 1024 * 1024
    require(estimate <= max_copy and size(parent) + estimate <= max_total,
            'Frozen storage limit (per-copy/total including quarantine)' + QUOTA_HELP)
    stage = Path(tempfile.mkdtemp(prefix='.freeze-', dir=parent))
    try:
        copied = stage / 'repo'
        git(store.root, 'init', '--quiet', str(copied))
        fetch(copied, task['repo'], revisions)
        result = git_process(copied, 'checkout', '--quiet', '--detach', candidate['head'], timeout=120)
        require(result.returncode == 0, 'Cannot check out the delivered Git head')
        listed = {row['path'] for row in candidate['files']}
        removed = [path for path in git(copied, 'ls-files', '-z').split(b'\0') if path and os.fsdecode(path) not in listed]
        if removed:
            result = git_process(copied,
                                     '--literal-pathspecs', 'rm', '--quiet', '--force', '--pathspec-from-file=-', '--pathspec-file-nul',
                                    input=b'\0'.join(removed) + b'\0', timeout=30)
            require(result.returncode == 0, 'Cannot remove checkout paths absent from the proved snapshot')
        for row in candidate['files']:
            relative = Path(row['path'])
            require(not relative.is_absolute() and '..' not in relative.parts and all(p.lower() != '.git' for p in relative.parts),
                    'Invalid frozen candidate path')
            source, target = Path(task['repo']) / relative, copied / relative
            require(all(not p.is_symlink() for p in source.parents if p.is_relative_to(Path(task['repo']))),
                    'Frozen source parent is a symlink: ' + row['path'])
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
        tracked = {os.fsdecode(path) for path in git(copied, 'ls-files', '-z').split(b'\0') if path}
        added = [os.fsencode(row['path']) for row in candidate['files'] if row['kind'] != 'deleted' and row['path'] not in tracked]
        if added:
            result = git_process(copied, '--literal-pathspecs', 'add', '--intent-to-add', '--force', '--pathspec-from-file=-', '--pathspec-file-nul',
                                    input=b'\0'.join(added) + b'\0', timeout=30)
            require(result.returncode == 0, 'Cannot retain added snapshot paths in the frozen index')
        missing = [row['path'] for row in candidate['files'] if row['kind'] == 'deleted' and row['path'] not in tracked]
        if missing:
            empty = git_process(copied, 'hash-object', '-w', '--stdin', input=b'', timeout=30)
            require(empty.returncode == 0, 'Cannot create empty frozen index placeholder')
            for path in missing:
                git(copied, 'update-index', '--add', '--cacheinfo', '100644', empty.stdout.decode().strip(), path)
        require(snapshot(task['repo'], store.root) == candidate, 'Source changed while freezing delivery')
        require(measured(task, copied) == candidate,
                'Frozen copy does not match the proved source')
        require(size(stage) <= max_copy and size(parent) <= max_total, 'Frozen storage limit exceeded' + QUOTA_HELP)
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


def checkpoint(store, destination, *, source_root=None, locked=False):
    """Copy healthy retained candidates; damaged/missing caches cannot block journal recovery."""
    store.release_views()
    if not locked:
        require(source_root is None, 'Relocated candidate checkpoint requires its source storage lock')
        with cache_lock(store):
            return checkpoint(store, destination, locked=True)
    root = Path(source_root) if source_root is not None else store.root
    records = inventory_records(store, root)
    for row, task in records:
        if row['status'] != 'available':
            continue
        source = Path(row['path'])
        validate_candidate(source, label='Frozen candidate ' + row['sha256'])
        target = Path(destination) / 'candidates' / row['sha256'] / 'repo'
        target.parent.mkdir(parents=True, mode=0o700)
        try:
            shutil.copytree(source, target, symlinks=True)
        except OSError as exc:
            if target.parent.exists():
                remove(target.parent)
            row.update(status='damaged', reason='Source copy unavailable: ' + str(exc))
            continue
        try:
            current(task, root)
            current(task, destination)
        except (Error, OSError) as exc:
            remove(target.parent)
            row.update(status='damaged', reason=str(exc))
    return [row for row, _ in records]


def manage(store, actor, task_id, action, reason, max_copy_mib=None, max_total_mib=None, *, sha256=None):
    if action == 'status':
        max_copy, max_total = limits(store)
        return {'candidates': inventory(store), 'bytes': size(store.root / 'candidates'),
                'max_copy_bytes': max_copy, 'max_total_bytes': max_total}
    require(store.view()['agents'][actor]['role'] == 'coordinator', 'Candidate repair/retire requires coordinator')
    require(reason and reason.strip(), 'Recorded candidate action reason required')
    store.release_views()
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
        if sha256 is not None:
            require(action == 'retire' and task_id is None, 'SHA retirement cannot name a current task')
            historical = next((t for t in candidate_records(store)[0] if
                               t['delivery']['frozen']['sha256'] == sha256), None)
            require(historical is not None, 'Unknown historical frozen candidate: ' + sha256)
            task_id = historical['id']
            task = historical
        else:
            task = plain(store.task(task_id))
            store.release_views()
        require(task.get('delivery', {}).get('frozen'), 'Task has no frozen delivery')
        path = repository(task, store.root)
        sha = task['delivery']['frozen']['sha256']
        record = {'action': action, 'sha256': sha, 'reason': reason}
        if action == 'retire':
            live = [t['id'] for t in store.view()['tasks'].values() if t.get('delivery', {}).get('frozen', {}).get('sha256') == sha
                    and t['status'] != 'done' and not t.get('superseded_by')]
            store.release_views()
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
