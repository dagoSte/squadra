"""Storage compaction: share verified trees of registered recovery points, candidates and archived runs.

Top of the storage layer: it imports storage_reuse primitives, frozen_candidate and recovery_package;
none of them imports it back (storage_policy is its only caller).
"""
from contextlib import nullcontext
import hashlib
from pathlib import Path
import stat

from registry import Store
from frozen_candidate import cache_lock, current, inventory_records
from recovery_package import verify_package
from storage_reuse import (ReuseError, _capabilities_near, _check_checksums, _files, _identity, _mutable,
                           _seal_candidate, _summary, _tree_manifest, dedupe_tree, tree_index)


def compact(root, targets, *, dry_run=True, locked=False):
    """Share verified trees, sealing candidates while retaining their source fingerprint.

    Dry-run savings are potential allocations avoided, not exclusive COW blocks.
    Recovery packages keep their modes; archived mutable inode classes are never linked.
    """
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
            planned_modes = {}
            for tree in trees:
                before = _tree_manifest(tree)
                expected = ({rel: (sha, 0o555 if mode & 0o111 else 0o444)
                             for rel, (sha, mode) in before.items()} if tree in candidates else before)
                if not dry_run and tree in candidates:
                    _seal_candidate(tree, before)
                if tree.parent == root / 'runs':
                    # An archived run file has its twin at artifacts/<sha256> (mode 0600).
                    for sha, _ in before.values():
                        index.add(sha, root / 'artifacts' / sha)
                if dry_run:
                    summary = _summary()
                    caps = _capabilities_near(tree, probes)
                    for path in _files(tree):
                        sha, mode = expected[str(path.relative_to(tree))]
                        info = path.lstat()
                        summary['files'] += 1
                        summary['logical_bytes'] += info.st_size
                        matched = False
                        linkable = mode in (0o444, 0o555) and not _mutable(path) and caps['hardlink']
                        if not (caps['clone'] or linkable):
                            linkable = False
                        for hint in [] if not (caps['clone'] or linkable) else index.candidates(sha, None,
                                                     path.relative_to(tree), info.st_size):
                            if hint == path:
                                continue
                            if not caps['clone'] and (planned_modes.get(hint, stat.S_IMODE(hint.stat().st_mode)) != mode
                                                      or _mutable(hint)):
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
                        planned_modes[path] = mode
                else:
                    summary = dedupe_tree(tree, index)
                if (before if dry_run else expected) != _tree_manifest(tree):
                    raise ReuseError('Compaction changed bytes, paths or modes')
                verifiers[tree]()
                total['targets'].append({'path': str(tree), **summary})
                for key in _summary():
                    total[key] += summary[key]
            return total
    finally:
        store.close()
