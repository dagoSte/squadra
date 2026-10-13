"""Copy stopped standalone-plugin settings and ledgers into an installed Store bundle."""
from contextlib import ExitStack
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plugins as p
import plugin_settings as files
from squadra_memories import connector


class LinkedComponent(p.Refused):
    def __init__(self, component):
        super().__init__('symlink')
        self.component = component


def directory(path):
    """Follow protected system aliases only before entering a user-controlled tree."""
    path = Path(path).absolute()
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    pending = list(path.parts[1:])
    system, links = True, 0
    try:
        while pending:
            name = pending.pop(0)
            parent = os.fstat(fd)
            system = system and parent.st_uid == 0 and not parent.st_mode & 0o022
            entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISLNK(entry.st_mode):
                if not system or entry.st_uid != 0 or not pending or links >= 40:
                    raise LinkedComponent(name)
                links += 1
                target = Path(os.readlink(name, dir_fd=fd))
                if target.is_absolute():
                    child = os.open(target.anchor, os.O_RDONLY | os.O_DIRECTORY)
                    os.close(fd)
                    fd = child
                    pending = list(target.parts[1:]) + pending
                else:
                    pending = list(target.parts) + pending
                continue
            try:
                child = p._open(fd, name, directory=True)
            except p.Refused as exc:
                if exc.code == 'symlink':
                    raise LinkedComponent(name) from None
                raise
            os.close(fd)
            fd = child
        files.private(fd, directory=True)
        return fd
    except BaseException:
        os.close(fd)
        raise


def migrate(home, old_plugin, old_data):
    """Idempotent only for identical inputs; a partial import blocks all bridge commands."""
    with ExitStack() as stack:
        source = directory(old_plugin)
        stack.callback(os.close, source)
        data = directory(old_data)
        stack.callback(os.close, data)
        parent, fields = stack.enter_context(files.installed(home, 'memoranda'))
        stack.enter_context(files.locked(parent, write=True))
        # A ledger's lock is never recreated while a stopped old reader could hold it.
        names = sorted(os.listdir(data))
        p.require(len(names) <= 10000, 'too_large')
        ledgers = [n for n in names if re.fullmatch(r'ledger-[a-f0-9]{64}\.json', n)]
        p.require(bool(ledgers), 'migration_missing_ledger')
        p.require(not any(n.startswith('ledger-') and n not in ledgers and
                         not re.fullmatch(r'ledger-[a-f0-9]{64}\.lock', n) for n in names),
                  'migration_invalid')
        bodies = {'settings.json': p._read_bytes(source, 'settings.json', files.LIMIT, files.private)}
        files.current(source, fields)
        secrets = files.optional(source, 'secrets', True)
        if secrets is not None:
            stack.callback(os.close, secrets)
            p.require(set(os.listdir(secrets)) <= {'key'}, 'migration_invalid')
            key = files.optional(secrets, 'key')
            if key is not None:
                os.close(key)
                bodies['secrets/key'] = p._read_bytes(secrets, 'key', 4096, files.private)
        total = sum(map(len, bodies.values()))
        for name in ledgers:
            lock = files.optional(data, name[:-5] + '.lock')
            p.require(lock is not None, 'migration_missing_lock')
            stack.callback(os.close, lock)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise p.Refused('busy') from None
            body = p._read_bytes(data, name, connector.LEDGER_LIMIT, files.private)
            value = json.loads(body, object_pairs_hook=p._pairs, parse_constant=p._constant)
            p.require(type(value) is dict and value.get('schema') == 1
                      and value.get('registry_id') == name[7:-5]
                      and type(value.get('items')) is dict and type(value.get('binding')) is dict,
                      'migration_invalid')
            total += len(body)
            p.require(total <= 64 * 1024**2, 'too_large')
            bodies['data/' + name] = body
        fingerprint = json.dumps({n: hashlib.sha256(b).hexdigest() for n, b in sorted(bodies.items())},
                                 sort_keys=True).encode()
        marker = files.optional(parent, '.migration-pending')
        if marker is not None:
            os.close(marker)
            p.require(p._read_bytes(parent, '.migration-pending', 2 * 1024**2, files.private) == fingerprint,
                      'migration_conflict')
        targets = {'': parent}
        for name in ('secrets', 'data'):
            fd = files.optional(parent, name, True)
            if fd is not None:
                stack.callback(os.close, fd)
                targets[name] = fd
        if 'data' in targets:
            p.require(set(os.listdir(targets['data'])) <= {Path(n).name for n in bodies if n.startswith('data/')},
                      'migration_conflict')
        if 'secrets' in targets:
            p.require(set(os.listdir(targets['secrets'])) <= {'key'} if 'secrets/key' in bodies
                      else not os.listdir(targets['secrets']), 'migration_conflict')
        for name, body in bodies.items():
            prefix, _, leaf = name.rpartition('/')
            if prefix in targets:
                fd = files.optional(targets[prefix], leaf)
                if fd is not None:
                    os.close(fd)
                    p.require(p._read_bytes(targets[prefix], leaf, len(body) + 1, files.private) == body,
                              'migration_conflict')
        files.atomic(parent, '.migration-pending', fingerprint)
        for name in ('secrets', 'data'):
            if name not in targets:
                os.mkdir(name, 0o700, dir_fd=parent)
                os.fsync(parent)
                fd = files.optional(parent, name, True)
                targets[name] = fd
                stack.callback(os.close, fd)
        for name, body in bodies.items():
            prefix, _, leaf = name.rpartition('/')
            files.atomic(targets[prefix], leaf, body)
        os.unlink('.migration-pending', dir_fd=parent)
        os.fsync(parent)
        return {'migrated': True, 'ledgers': len(ledgers), 'network': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from-plugin', type=Path, required=True)
    parser.add_argument('--from-data', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(migrate(Path.home(), args.from_plugin, args.from_data)))
        return 0
    except LinkedComponent as exc:
        print(f'Migration refused: linked component {exc.component!r}; '
              'preserve the old folders and any pending marker.', file=sys.stderr)
        return 3
    except (p.Refused, OSError, ValueError):
        print('Migration refused; preserve the old folders and any pending marker.', file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
