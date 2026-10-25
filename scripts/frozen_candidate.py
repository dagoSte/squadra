"""Materialize a proved candidate without retaining the builder's working tree."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from registry import canonical, digest, require, snapshot


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


def current(task, root=None):
    frozen = task.get('delivery', {}).get('frozen')
    if not frozen:
        return snapshot(task['repo'], root)
    result = measured(task, repository(task, root))
    require(result['sha256'] == frozen['sha256'], 'Frozen candidate changed; deliver again')
    return result


def freeze(store, task, candidate):
    parent = store.root / 'candidates'
    parent.mkdir(mode=0o700, exist_ok=True)
    require(not parent.is_symlink(), 'Candidate folder cannot be a symlink')
    destination = parent / candidate['sha256']
    record = {'repo': str(destination / 'repo'), 'sha256': candidate['sha256']}
    if destination.exists():
        require(current({**task, 'delivery': {'frozen': record}}, store.root) == candidate,
                'Existing frozen candidate differs')
        return record
    stage = Path(tempfile.mkdtemp(prefix='.freeze-', dir=parent))
    try:
        copied = stage / 'repo'
        result = subprocess.run(['git', 'clone', '--quiet', '--no-hardlinks', '--single-branch', '--no-tags', '--no-checkout',
                                 '--', task['repo'], str(copied)], capture_output=True, timeout=120)
        require(result.returncode == 0, 'Cannot clone the delivered Git history')
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
        try:
            stage.rename(destination)
        except FileExistsError:
            require(current({**task, 'delivery': {'frozen': record}}, store.root) == candidate,
                    'Concurrent frozen copy differs')
        return record
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def checkpoint(store, destination):
    """Copy every delivered candidate, with no links to the live registry."""
    seen = set()
    for event in store.events():
        data = event['data']
        if event['kind'] != 'deliver' or not data.get('frozen') or data['frozen']['sha256'] in seen:
            continue
        expected = store.resolve_snapshot(data['snapshot'])
        task = {'repo': expected['repo'], 'delivery': data}
        source = repository(task, store.root)
        require(current(task, store.root) == expected, 'Frozen checkpoint input changed')
        target = repository(task, destination)
        target.parent.mkdir(parents=True, mode=0o700)
        shutil.copytree(source, target, symlinks=True)
        require(current(task, store.root) == expected and current(task, destination) == expected,
                'Frozen checkpoint copy changed')
        seen.add(data['frozen']['sha256'])
    return len(seen)
