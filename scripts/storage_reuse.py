"""Verified whole-file sharing; writable copies never use hardlinks.

saved_bytes estimates newly avoided allocations. st_blocks cannot distinguish
exclusive from shared COW extents, so it is not a measurement of free disk space.
"""
from collections import defaultdict
from contextlib import nullcontext
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import uuid

from registry_checkpoint import _copy_bytes, _hash_fd, _native_clone


class ReuseError(ValueError):
    pass


def _identity(info):
    return info.st_dev, info.st_ino


def _version(info):
    return (_identity(info), info.st_size, info.st_mtime_ns,
            stat.S_IMODE(info.st_mode))


def _regular(info):
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise ReuseError('Reuse requires an owned regular file')


def _mutable(path):
    path = Path(path)
    return (path.name.endswith(('.lock', '.sqlite3', '.sqlite3-wal', '.sqlite3-shm'))
            or 'run-leases' in path.parts or 'runs' in path.parts)


def _clone(source_fd, directory_fd, name):
    if sys.platform == 'linux':
        import fcntl
        fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW,
                     0o600, dir_fd=directory_fd)
        try:
            fcntl.ioctl(fd, 0x40049409, source_fd)  # Linux FICLONE, include/uapi/linux/fs.h.
        finally:
            os.close(fd)
        return True
    return _native_clone(source_fd, directory_fd, name)


def place(source, target, *, sha256, readonly, prefer=('clone', 'hardlink', 'copy'), mode=None):
    """Create a new verified file, preserving executable class on readonly output.

    An exclusive empty destination reserves the name until the verified temporary
    file replaces it. Neither failure cleanup nor a mode change touches the input.
    mode, when given, is the exact mode of an independent result (clone or copy);
    a hardlink is then refused unless the source already has that mode.
    """
    if (not isinstance(sha256, str) or len(sha256) != 64
            or any(c not in '0123456789abcdef' for c in sha256)):
        raise ReuseError('Expected a sha256 digest')
    if not prefer or any(method not in {'clone', 'hardlink', 'copy'} for method in prefer):
        raise ReuseError('Unknown or empty reuse preference')
    source, target = Path(source), Path(target)
    source_parent = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    target_parent = source_fd = reservation = destination_fd = None
    temporary = '.reuse-' + uuid.uuid4().hex
    published = False
    try:
        target_parent = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        source_fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=source_parent)
        before = os.fstat(source_fd)
        _regular(before)
        if _hash_fd(source_fd) != sha256 or _version(os.fstat(source_fd)) != _version(before):
            raise ReuseError('Source checksum or metadata changed')
        wanted = mode
        mode = (0o555 if before.st_mode & 0o111 else 0o444) if readonly else stat.S_IMODE(before.st_mode)
        if wanted is not None:
            if wanted & ~0o777:
                raise ReuseError('Invalid file mode')
            mode = wanted
        reservation = os.open(target.name, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW,
                              0o600, dir_fd=target_parent)
        reserved = _identity(os.fstat(reservation))
        method = None
        for choice in prefer:
            try:
                if choice == 'clone':
                    if not _clone(source_fd, target_parent, temporary):
                        continue
                elif choice == 'hardlink':
                    if (not readonly or stat.S_IMODE(before.st_mode) not in (0o444, 0o555)
                            or stat.S_IMODE(before.st_mode) != mode
                            or _mutable(source) or _mutable(target)
                            or before.st_dev != os.fstat(target_parent).st_dev):
                        continue
                    os.link(source.name, temporary, src_dir_fd=source_parent,
                            dst_dir_fd=target_parent, follow_symlinks=False)
                else:
                    destination_fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW,
                                             0o600, dir_fd=target_parent)
                    _copy_bytes(source_fd, destination_fd)
                method = choice
                break
            except OSError as exc:
                if choice == 'copy' or exc.errno == errno.EEXIST:
                    raise
                # A failed clone may leave a partial output. It is unlinked, never modified.
                try:
                    os.unlink(temporary, dir_fd=target_parent)
                except FileNotFoundError:
                    pass
        if method is None:
            raise OSError(errno.ENOTSUP, 'No supported sharing method')
        if destination_fd is None:
            destination_fd = os.open(temporary, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=target_parent)
        copied = os.fstat(destination_fd)
        _regular(copied)
        linked = _identity(copied) == _identity(before)
        if ((method == 'hardlink') != linked or (method != 'hardlink' and copied.st_nlink != 1)):
            raise ReuseError('Copy unexpectedly shares a mutable inode')
        if method != 'hardlink':
            os.fchmod(destination_fd, mode)
        ready = os.fstat(destination_fd)
        if (_hash_fd(destination_fd) != sha256 or _hash_fd(source_fd) != sha256
                or _version(os.fstat(source_fd)) != _version(before)
                or _version(os.stat(source.name, dir_fd=source_parent, follow_symlinks=False)) != _version(before)
                or _identity(os.stat(source.name, dir_fd=source_parent, follow_symlinks=False)) != _identity(before)
                or _version(os.fstat(destination_fd)) != _version(ready)
                or _identity(os.stat(temporary, dir_fd=target_parent, follow_symlinks=False)) != _identity(ready)
                or _identity(os.stat(target.name, dir_fd=target_parent, follow_symlinks=False)) != reserved):
            raise ReuseError('Reuse source or destination changed during verification')
        os.fsync(destination_fd)
        os.replace(temporary, target.name, src_dir_fd=target_parent, dst_dir_fd=target_parent)
        published = True
        return method
    finally:
        if target_parent is not None:
            try:
                os.unlink(temporary, dir_fd=target_parent)
            except FileNotFoundError:
                pass
            if reservation is not None and not published:
                try:
                    if _identity(os.stat(target.name, dir_fd=target_parent, follow_symlinks=False)) == reserved:
                        os.unlink(target.name, dir_fd=target_parent)
                except FileNotFoundError:
                    pass
        for fd in (destination_fd, reservation, source_fd, target_parent, source_parent):
            if fd is not None:
                os.close(fd)


def capabilities(directory):
    """Probe the actual filesystem; a reported clone means a native call succeeded."""
    result = {'clone': False, 'hardlink': False, 'device': Path(directory).stat().st_dev}
    with tempfile.TemporaryDirectory(prefix='.reuse-probe-', dir=directory) as scratch:
        source = Path(scratch) / 'source'
        source.write_bytes(b'squadra storage capability probe\n')
        source.chmod(0o444)
        sha = hashlib.sha256(source.read_bytes()).hexdigest()
        for method in ('clone', 'hardlink'):
            try:
                result[method] = place(source, Path(scratch) / method, sha256=sha,
                                       readonly=True, prefer=(method,)) == method
            except OSError:
                pass
    return result


def _capabilities_near(path, cache):
    """Probe the nearest writable ancestor on the same device; a sealed tree is never written."""
    path = Path(path)
    device = path.lstat().st_dev
    if device in cache:
        return cache[device]
    result = {'clone': False, 'hardlink': False, 'device': device}
    for folder in (path, *path.parents):
        try:
            if folder.lstat().st_dev != device:
                break
            if os.access(folder, os.W_OK | os.X_OK):
                result = capabilities(folder)
                break
        except OSError:
            continue
    cache[device] = result
    return result


class ReuseIndex:
    """Hints for verified reuse: content entries by sha256, plus roots probed at the same relative path.

    Roots let a caller offer the previous candidate or checkpoint without hashing it first;
    place() still opens and hashes the chosen hint before sharing it.
    """

    def __init__(self, paths=None, roots=()):
        self.paths = defaultdict(list)
        self.roots = [Path(root) for root in roots]
        for sha, values in (paths or {}).items():
            for path in ([values] if isinstance(values, (str, Path)) else values):
                self.add(sha, path)

    def add(self, sha, path):
        path = Path(path)
        if path not in self.paths[sha]:
            self.paths[sha].append(path)

    def candidates(self, sha, mode, relative=None, size=None):
        roots = (root / relative for root in self.roots) if relative is not None else ()
        seen = set()
        for path in (*tuple(self.paths.get(sha, ())), *roots):
            if path in seen:
                continue
            seen.add(path)
            try:
                info = path.lstat()
            except OSError:
                continue
            if (stat.S_ISREG(info.st_mode) and (mode is None or stat.S_IMODE(info.st_mode) == mode)
                    and (size is None or info.st_size == size)):
                yield path


def reuse_index(paths_by_sha, roots=()):
    return ReuseIndex(paths_by_sha, roots)


def _files(tree):
    tree = Path(tree)
    if tree.is_symlink() or not tree.is_dir():
        raise ReuseError('Reuse tree must be a real directory')
    for parent, directories, files in os.walk(tree, followlinks=False):
        directories[:] = sorted(d for d in directories if not (Path(parent) / d).is_symlink())
        for name in sorted(files):
            path = Path(parent) / name
            if stat.S_ISREG(path.lstat().st_mode):
                yield path


def tree_index(trees):
    """Index hints only; place still opens and verifies the chosen file on every reuse."""
    index = reuse_index({})
    for tree in trees:
        if Path(tree).is_dir() and not Path(tree).is_symlink():
            for path in _files(tree):
                try:
                    with path.open('rb') as stream:
                        index.add(hashlib.file_digest(stream, 'sha256').hexdigest(), path)
                except OSError:
                    continue
    return index


def _summary():
    return dict(files=0, reused=0, clone=0, hardlink=0, copied=0, logical_bytes=0,
                saved_bytes=0, new_physical_estimate=0)


def dedupe_tree(tree, index, *, readonly=True):
    """Atomically replace each duplicate; preserve bytes and exact modes, including on fallback."""
    summary = _summary()
    for path in _files(tree):
        info = path.lstat()
        with path.open('rb') as stream:
            sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        mode = stat.S_IMODE(info.st_mode)
        summary['files'] += 1
        summary['logical_bytes'] += info.st_size
        method = None
        can_link = readonly and mode in (0o444, 0o555) and not _mutable(path)
        for source in index.candidates(sha, mode if can_link else None, path.relative_to(tree), info.st_size):
            if source == path:
                continue
            temporary = None
            parent_mode = stat.S_IMODE(path.parent.stat().st_mode)
            try:
                if _identity(source.lstat()) == _identity(info):
                    method = 'hardlink'
                    break
                temporary = path.with_name('.reuse-swap-' + uuid.uuid4().hex)
                if not parent_mode & 0o200:
                    path.parent.chmod(parent_mode | 0o700)
                method = place(source, temporary, sha256=sha, readonly=can_link,
                               prefer=('clone', 'hardlink') if can_link else ('clone',),
                               mode=None if can_link else mode)
                if _version(path.lstat()) != _version(info):
                    raise ReuseError('Dedupe target changed')
                os.replace(temporary, path)
                if info.st_nlink == 1:
                    summary['saved_bytes'] += info.st_blocks * 512
                break
            except (OSError, ReuseError):
                method = None
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink()
                if stat.S_IMODE(path.parent.stat().st_mode) != parent_mode:
                    path.parent.chmod(parent_mode)
        if method:
            summary['reused'] += 1
            summary[method] += 1
        else:
            summary['copied'] += 1
            summary['new_physical_estimate'] += info.st_blocks * 512
        index.add(sha, path)
    return summary


def _check_checksums(tree):
    tree = Path(tree)
    checks = json.loads((tree / 'CHECKSUMS.json').read_text())
    if not isinstance(checks, dict):
        raise ReuseError('Invalid checkpoint checksums')
    for relative, sha in checks.items():
        path = Path(relative)
        if (path.is_absolute() or '..' in path.parts
                or not (tree / path).resolve().is_relative_to(tree.resolve())):
            raise ReuseError('Escaping checkpoint checksum path')
        source = tree / path
        body = os.fsencode(os.readlink(source)) if source.is_symlink() else source.read_bytes()
        if hashlib.sha256(body).hexdigest() != sha:
            raise ReuseError('Checkpoint checksum mismatch: ' + relative)
    return checks


def _tree_manifest(tree):
    return {str(p.relative_to(tree)): (hashlib.sha256(p.read_bytes()).hexdigest(),
                                     stat.S_IMODE(p.lstat().st_mode)) for p in _files(tree)}


def compact(root, targets, *, dry_run=True, locked=False):
    """Share recognized trees under the candidate lock, retaining their complete bytes and modes.

    Dry-run savings are potential allocations avoided, not exclusive COW blocks.
    Archived runs stay independent: their mutable inode classes are never linked.
    """
    from registry import Store
    from frozen_candidate import cache_lock, inventory_records, current
    from recovery import verify_package

    root = Path(root).resolve()
    store = Store(root, read_only=True)
    try:
        # locked=True: the caller already holds candidates/.lock (a second flock on another fd would wait forever).
        with nullcontext() if dry_run or locked else cache_lock(store):
            candidates = {Path(row['path']).resolve(): task for row, task in inventory_records(store)
                          if row['status'] == 'available'}
            trees = []
            verifiers = {}
            for value in targets:
                path = Path(value).absolute()
                if path.is_symlink():
                    raise ReuseError('Compact target must not be a symlink')
                path = path.resolve()
                if path == root:
                    raise ReuseError('Cannot compact the active registry')
                if path == root / 'candidates':
                    selected = list(candidates)
                else:
                    selected = [path / 'repo' if path / 'repo' in candidates else path]
                for tree in selected:
                    if tree in trees:
                        continue
                    if tree in candidates:
                        task = candidates[tree]
                        check = lambda task=task: current(task, root)
                    elif (tree / 'MANIFEST.json').is_file():
                        check = lambda tree=tree: verify_package(tree)
                    elif (tree / 'CHECKSUMS.json').is_file():
                        check = lambda tree=tree: _check_checksums(tree)
                    elif tree.parent == root / 'runs':
                        run = store.view()['runs'].get(tree.name)
                        # cleanup-unknown also records ended: its producer may still hold the log open.
                        if (run is None or not run.get('ended') or run.get('status') in {'running', 'cleanup-unknown'}
                                or run.get('cleanup') == 'not verified'):
                            raise ReuseError('Cannot compact a live, unreconciled or unknown run')

                        def check(tree=tree):
                            for file in _files(tree):
                                sha = hashlib.sha256(file.read_bytes()).hexdigest()
                                archive = root / 'artifacts' / sha
                                if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest() != sha:
                                    raise ReuseError('Run file is not archived: ' + str(file))
                            return True
                    else:
                        raise ReuseError('Unrecognized compact target: ' + str(tree))
                    check()
                    trees.append(tree)
                    verifiers[tree] = check
            # All targets are validated before the first change. Index paths are
            # hints; each selected hint is still rehashed by place().
            index = tree_index([])
            total = {**_summary(), 'dry_run': dry_run, 'targets': []}
            probes = {}
            for tree in trees:
                before = _tree_manifest(tree)
                if tree.parent == root / 'runs':
                    # An archived run file has its twin at artifacts/<sha256> (mode 0600).
                    for sha, _ in before.values():
                        index.add(sha, root / 'artifacts' / sha)
                if dry_run:
                    summary = _summary()
                    caps = _capabilities_near(tree, probes)
                    for path in _files(tree):
                        sha, mode = before[str(path.relative_to(tree))]
                        info = path.lstat()
                        summary['files'] += 1
                        summary['logical_bytes'] += info.st_size
                        matched = False
                        linkable = mode in (0o444, 0o555) and not _mutable(path) and caps['hardlink']
                        if not (caps['clone'] or linkable):
                            linkable = False
                        for hint in [] if not (caps['clone'] or linkable) else index.candidates(sha, mode if linkable else None,
                                                     path.relative_to(tree), info.st_size):
                            if hint == path:
                                continue
                            if hashlib.sha256(hint.read_bytes()).hexdigest() == sha:
                                matched = True
                                if _identity(hint.stat()) != _identity(info) and info.st_nlink == 1:
                                    summary['saved_bytes'] += info.st_blocks * 512
                                break
                        summary['reused' if matched else 'copied'] += 1
                        if not matched:
                            summary['new_physical_estimate'] += info.st_blocks * 512
                        index.add(sha, path)
                else:
                    summary = dedupe_tree(tree, index)
                if before != _tree_manifest(tree):
                    raise ReuseError('Compaction changed bytes, paths or modes')
                verifiers[tree]()
                total['targets'].append({'path': str(tree), **summary})
                for key in _summary():
                    total[key] += summary[key]
            return total
    finally:
        store.close()
