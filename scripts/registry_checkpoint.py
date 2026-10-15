"""Self-contained immutable checkpoint files, with optional native COW copies.

The Store owns the consistent SQLite backup and complete exports/checksums. These
helpers copy files only; they never link a checkpoint to an external object store.
Allocated blocks describe st_blocks, not exclusive physical ownership or savings.
"""
import ctypes
from functools import lru_cache
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
import sys


CHUNK_BYTES = 1024 * 1024


class CheckpointError(ValueError):
    pass


def _owned(info, directory=False):
    valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not valid or info.st_uid != os.getuid() or (not directory and info.st_nlink != 1):
        raise CheckpointError("Checkpoint input must be an owned directory or single-link regular file")


def _identity(info):
    return info.st_dev, info.st_ino


def _version(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _open_directory(path, *, dir_fd=None):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=dir_fd)
    try:
        _owned(os.fstat(fd), directory=True)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _hash_fd(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while True:
        part = os.read(fd, CHUNK_BYTES)
        if not part:
            return digest.hexdigest()
        digest.update(part)


@lru_cache(maxsize=1)
def _clone_api():
    if sys.platform != "darwin":
        return None
    try:
        function = ctypes.CDLL(None, use_errno=True).fclonefileat
    except AttributeError:
        return None
    # Signature and NOOWNERCOPY flag are from the platform's sys/clonefile.h.
    function.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    function.restype = ctypes.c_int
    return function


def _native_clone(source_fd, destination_dir_fd, name):
    function = _clone_api()
    if function is None:
        return False
    if function(source_fd, destination_dir_fd, os.fsencode(name), 0x0002) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), name)
    return True


def _copy_bytes(source_fd, destination_fd):
    os.lseek(source_fd, 0, os.SEEK_SET)
    os.ftruncate(destination_fd, 0)
    while True:
        part = os.read(source_fd, CHUNK_BYTES)
        if not part:
            break
        view = memoryview(part)
        while view:
            written = os.write(destination_fd, view)
            if not written:
                raise OSError("Checkpoint copy made no progress")
            view = view[written:]


def _copy_file(source_dir_fd, destination_dir_fd, name, expected):
    source_fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=source_dir_fd)
    destination_fd = None
    try:
        before = os.fstat(source_fd)
        _owned(before)
        if _version(before) != _version(expected):
            raise CheckpointError("Source changed before checkpoint copy")
        source_digest = _hash_fd(source_fd)
        if _version(os.fstat(source_fd)) != _version(before):
            raise CheckpointError("Source changed during checkpoint hash")
        try:
            cloned = _native_clone(source_fd, destination_dir_fd, name)
        except OSError:
            cloned = False
        if cloned:
            destination_fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=destination_dir_fd)
        else:
            try:
                destination_fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                                         0o600, dir_fd=destination_dir_fd)
            except FileExistsError:
                # A failed native clone may leave its own partial regular file.
                destination_fd = os.open(name, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), dir_fd=destination_dir_fd)
        copied = os.fstat(destination_fd)
        _owned(copied)
        if _identity(copied) == _identity(before):
            raise CheckpointError("A checkpoint must not hardlink its source")
        os.fchmod(destination_fd, 0o600)
        if not cloned:
            _copy_bytes(source_fd, destination_fd)
        os.fsync(destination_fd)
        ready = os.fstat(destination_fd)
        destination_digest = _hash_fd(destination_fd)
        after_digest = _hash_fd(source_fd)
        after = os.fstat(source_fd)
        current_path = os.stat(name, dir_fd=source_dir_fd, follow_symlinks=False)
        if (_version(after) != _version(before) or _version(current_path) != _version(before)
                or after_digest != source_digest or destination_digest != source_digest):
            raise CheckpointError("Source or destination changed during checkpoint copy")
        final = os.fstat(destination_fd)
        current_destination = os.stat(name, dir_fd=destination_dir_fd, follow_symlinks=False)
        if _version(final) != _version(ready) or _version(current_destination) != _version(final):
            raise CheckpointError("Destination changed after checkpoint verification")
        return {"sha256": source_digest, "bytes": final.st_size, "mode": 0o600}, cloned, final.st_blocks * 512
    finally:
        if destination_fd is not None:
            os.close(destination_fd)
        os.close(source_fd)


def copy_immutable_tree(source, destination, *, skip_lease_locks=False):
    """Copy a tree to a new private folder; preserve failure evidence on error.

    skip_lease_locks excludes only regular names ending in .lock. Lease metadata
    and terminal .json files remain self-contained in the checkpoint.
    """
    source, destination = Path(source), Path(destination)
    original = source.lstat()
    _owned(original, directory=True)
    if destination.exists() or destination.is_symlink():
        raise CheckpointError("Checkpoint destination must be new")
    source_fd = _open_directory(source)
    destination_fd = None
    summary = {"files": {}, "file_count": 0, "logical_bytes": 0,
               "allocated_bytes": 0, "cloned": 0, "copied": 0}
    try:
        if _identity(os.fstat(source_fd)) != _identity(original):
            raise CheckpointError("Checkpoint source directory changed")
        destination.mkdir(mode=0o700)
        destination_fd = _open_directory(destination)

        def visit(source_dir_fd, destination_dir_fd, prefix):
            for name in sorted(os.listdir(source_dir_fd)):
                info = os.stat(name, dir_fd=source_dir_fd, follow_symlinks=False)
                relative = (prefix + "/" if prefix else "") + name
                if stat.S_ISDIR(info.st_mode):
                    _owned(info, directory=True)
                    child_source = _open_directory(name, dir_fd=source_dir_fd)
                    child_destination = None
                    try:
                        if _identity(os.fstat(child_source)) != _identity(info):
                            raise CheckpointError("Source directory changed")
                        os.mkdir(name, mode=0o700, dir_fd=destination_dir_fd)
                        child_destination = _open_directory(name, dir_fd=destination_dir_fd)
                        visit(child_source, child_destination, relative)
                        if _identity(os.stat(name, dir_fd=source_dir_fd, follow_symlinks=False)) != _identity(info):
                            raise CheckpointError("Source directory was replaced")
                        os.fsync(child_destination)
                    finally:
                        if child_destination is not None:
                            os.close(child_destination)
                        os.close(child_source)
                else:
                    _owned(info)
                    if skip_lease_locks and name.endswith(".lock"):
                        continue
                    record, cloned, blocks = _copy_file(source_dir_fd, destination_dir_fd, name, info)
                    summary["files"][relative] = record
                    summary["file_count"] += 1
                    summary["logical_bytes"] += record["bytes"]
                    summary["allocated_bytes"] += blocks
                    summary["cloned" if cloned else "copied"] += 1

        visit(source_fd, destination_fd, "")
        if _identity(source.lstat()) != _identity(original):
            raise CheckpointError("Source directory was replaced")
        os.fsync(destination_fd)
        return summary
    finally:
        if destination_fd is not None:
            os.close(destination_fd)
        os.close(source_fd)


def verify_tree(destination, manifest):
    """Verify a complete file manifest without following links or unsafe paths."""
    if not isinstance(manifest, dict):
        raise CheckpointError("Invalid checkpoint manifest")
    for relative, record in manifest.items():
        if (not isinstance(relative, str) or not relative or relative.startswith("/")
                or any(part in {"", ".", ".."} for part in relative.split("/"))
                or str(PurePosixPath(relative)) != relative
                or not isinstance(record, dict) or set(record) != {"sha256", "bytes", "mode"}
                or type(record["bytes"]) is not int or record["bytes"] < 0
                or record["mode"] != 0o600):
            raise CheckpointError("Invalid checkpoint manifest entry")
    fd = _open_directory(destination)
    root_before = os.fstat(fd)
    seen = set()
    try:
        def visit(parent, prefix):
            directory_before = os.fstat(parent)
            for name in sorted(os.listdir(parent)):
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                relative = (prefix + "/" if prefix else "") + name
                if stat.S_ISDIR(info.st_mode):
                    child = _open_directory(name, dir_fd=parent)
                    try:
                        if _identity(os.fstat(child)) != _identity(info):
                            raise CheckpointError("Checkpoint directory changed")
                        visit(child, relative)
                        if _identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _identity(info):
                            raise CheckpointError("Checkpoint directory was replaced")
                    finally:
                        os.close(child)
                else:
                    _owned(info)
                    if relative not in manifest:
                        raise CheckpointError("Unexpected checkpoint file")
                    child = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent)
                    try:
                        opened = os.fstat(child)
                        _owned(opened)
                        record = manifest[relative]
                        if (_version(opened) != _version(info) or opened.st_size != record["bytes"]
                                or stat.S_IMODE(opened.st_mode) != record["mode"]
                                or _hash_fd(child) != record["sha256"]
                                or _version(os.fstat(child)) != _version(opened)
                                or _version(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _version(opened)):
                            raise CheckpointError("Checkpoint checksum or metadata mismatch")
                    finally:
                        os.close(child)
                    seen.add(relative)
            if _version(os.fstat(parent)) != _version(directory_before):
                raise CheckpointError("Checkpoint directory changed during verification")
        visit(fd, "")
        if _identity(Path(destination).lstat()) != _identity(root_before):
            raise CheckpointError("Checkpoint root was replaced")
        if seen != set(manifest):
            raise CheckpointError("Missing checkpoint file")
        return True
    finally:
        os.close(fd)
