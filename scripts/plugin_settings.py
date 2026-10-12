"""Declared local settings, private per-file writes, and secret-free projections."""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import fcntl
import json
import os
import re
import secrets
import stat
import unicodedata
from urllib.parse import urlsplit

import plugins as p

LIMIT = 16 * 1024


def validate_value(kind, value, *, allow_loopback=False):
    p.require(type(value) is str)
    try:
        value.encode('utf-8')
        if kind == 'secret':
            p.require(1 <= len(value) <= 4096 and all('!' <= c <= '~' for c in value))
        elif kind == 'text':
            p.require(len(value) <= 256 and not any(unicodedata.category(c).startswith('C') for c in value))
        else:
            p.require(kind == 'url' and 0 < len(value) <= 2048)
            p.require(not any(c.isspace() or unicodedata.category(c).startswith('C') or c == '\\' for c in value))
            url = urlsplit(value)
            local = allow_loopback and bool(re.fullmatch(r'http://127\.0\.0\.1:[0-9]+/', value))
            p.require((url.scheme == 'https' or local) and bool(url.hostname) and bool(url.netloc))
            p.require(url.username is None and url.password is None and '#' not in value)
            p.require(url.port is None or 1 <= url.port <= 65535)
    except (ValueError, UnicodeError):
        raise p.Refused('bad_value') from None


def private(fd, directory=False):
    info = os.fstat(fd)
    p.require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600)
              and (directory or info.st_nlink == 1), 'unsafe_permissions')


@contextmanager
def installed(home, name, capability='settings'):
    p.require(type(name) is str and p.NAME.fullmatch(name), 'bad_name')
    with p._root(home) as root:
        fd = p._open(root, name, directory=True)
        try:
            info = os.fstat(fd)
            p.require(info.st_uid == os.getuid() and not (info.st_mode & 0o022), 'unsafe_permissions')
            manifest = p._manifest(fd, name)
            p.require(capability in manifest, 'not_configurable')
            yield fd, manifest[capability]
        finally:
            os.close(fd)


def optional(parent, name, directory=False):
    try:
        fd = p._open(parent, name, directory)
    except p.Refused as exc:
        if exc.code == 'not_found':
            return None
        raise
    try:
        private(fd, directory)
        return fd
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def locked(parent, write=False):
    fd = optional(parent, '.settings.lock')
    try:
        if fd is None and write:
            try:
                fd = os.open('.settings.lock', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                os.fchmod(fd, 0o600)
                os.fsync(fd)
                os.fsync(parent)
            except FileExistsError:
                fd = optional(parent, '.settings.lock')
        if fd is not None:
            private(fd)
            try:
                fcntl.flock(fd, (fcntl.LOCK_EX if write else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            except BlockingIOError:
                raise p.Refused('busy') from None
        yield
    finally:
        if fd is not None:
            os.close(fd)


def current(parent, fields, repair=()):
    kinds = {f['id']: f['kind'] for f in fields}
    try:
        saved = p._read(parent, 'settings.json', LIMIT, private)
    except p.Refused as exc:
        if exc.code == 'not_found':
            saved = {'schema': 1, 'updated': '1970-01-01T00:00:00Z', 'values': {}, 'secrets': []}
        elif exc.code in {'invalid_json', 'too_large'}:
            raise p.Refused('settings_invalid') from None
        else:
            raise
    try:
        p._fields(saved, ('schema', 'updated', 'values', 'secrets'))
        p._schema(saved)
        p.require(type(saved['updated']) is str and 'T' in saved['updated'])
        p.require(datetime.fromisoformat(saved['updated'].replace('Z', '+00:00')).utcoffset() == timedelta(0))
        p.require(type(saved['values']) is dict and type(saved['secrets']) is list)
        for key, value in saved['values'].items():
            p.require(key in kinds and kinds[key] != 'secret')
            validate_value(kinds[key], value)
        p.require(all(type(key) is str and kinds.get(key) == 'secret' for key in saved['secrets']))
        p.require(len(set(saved['secrets'])) == len(saved['secrets']))
    except (p.Refused, ValueError, TypeError, OverflowError):
        raise p.Refused('settings_invalid') from None
    present = set()
    directory = optional(parent, 'secrets', True)
    if directory is not None:
        try:
            for key, kind in kinds.items():
                if kind != 'secret':
                    continue
                fd = optional(directory, key)
                if fd is not None:
                    try:
                        p.require(1 <= os.fstat(fd).st_size <= 4096, 'settings_invalid')
                        present.add(key)
                    finally:
                        os.close(fd)
        finally:
            os.close(directory)
    p.require(not ((present ^ set(saved['secrets'])) - set(repair)), 'settings_invalid')
    return dict(saved['values']), present


def projection(fields, values, present):
    return [{'id': f['id'], 'set': f['id'] in (present if f['kind'] == 'secret' else values),
             **({'value': values[f['id']]} if f['kind'] != 'secret' and f['id'] in values else {})} for f in fields]


def read(home, name):
    try:
        with installed(home, name) as (parent, fields), locked(parent):
            return projection(fields, *current(parent, fields))
    except OSError:
        raise p.Refused('io_error') from None


def discover(home):
    found = p.discover(home)
    for manifest in found['plugins']:
        if 'settings' not in manifest:
            continue
        try:
            states = {s['id']: s for s in read(home, manifest['name'])}
            manifest['settings'] = [{**f, **states[f['id']]} for f in manifest['settings']]
        except (p.Refused, KeyError) as exc:
            manifest['settings'] = [{**f, 'set': False} for f in manifest['settings']]
            manifest['settings_error'] = exc.code if isinstance(exc, p.Refused) else 'settings_invalid'
    return found


def prepare(parent, fields, updates):
    p.require(type(updates) is dict)
    kinds = {f['id']: f['kind'] for f in fields}
    p.require(set(updates) <= set(kinds), 'unknown_id')
    for key, value in updates.items():
        if value is not None:
            validate_value(kinds[key], value)
    values, present = current(parent, fields, updates)
    for key, value in updates.items():
        if kinds[key] == 'secret':
            present.discard(key) if value is None else present.add(key)
        elif value is None:
            values.pop(key, None)
        else:
            values[key] = value
    saved = {'schema': 1, 'updated': datetime.now(timezone.utc).isoformat(), 'values': values, 'secrets': sorted(present)}
    body = json.dumps(saved, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    p.require(len(body) <= LIMIT, 'too_large')
    return body, projection(fields, values, present)


def atomic(parent, name, body):
    # Temporary secret bytes stay in the private secrets directory, never in a journal.
    existing = optional(parent, name)
    if existing is not None:
        os.close(existing)
    temp = '.tmp-' + name + '-' + secrets.token_hex(16)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(body)
        while view:
            size = os.write(fd, view)
            if size <= 0:
                raise OSError('write failed')
            view = view[size:]
        os.fsync(fd)
        existing = optional(parent, name)
        if existing is not None:
            os.close(existing)
        os.replace(temp, name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(fd)
        try:
            os.unlink(temp, dir_fd=parent)
        except FileNotFoundError:
            pass


def save(home, name, updates):
    try:
        with installed(home, name) as (parent, fields):
            prepare(parent, fields, updates)  # Validate before creating the lock file.
            with locked(parent, write=True):
                fields = p._manifest(parent, name).get('settings', [])
                p.require(fields, 'not_configurable')
                body, result = prepare(parent, fields, updates)
                secret_updates = {f['id']: updates[f['id']] for f in fields if f['kind'] == 'secret' and f['id'] in updates}
                directory = optional(parent, 'secrets', True)
                try:
                    if directory is None and any(v is not None for v in secret_updates.values()):
                        os.mkdir('secrets', 0o700, dir_fd=parent)
                        directory = p._open(parent, 'secrets', True)
                        os.fchmod(directory, 0o700)
                        os.fsync(parent)
                    for key, value in secret_updates.items():
                        if directory is None:
                            continue
                        if value is None:
                            fd = optional(directory, key)
                            if fd is not None:
                                os.close(fd)
                                os.unlink(key, dir_fd=directory)
                                os.fsync(directory)
                        else:
                            atomic(directory, key, value.encode('ascii'))
                    atomic(parent, 'settings.json', body)
                except (OSError, p.Refused):
                    raise p.Refused('save_failed') from None
                finally:
                    if directory is not None:
                        os.close(directory)
                return result
    except OSError:
        raise p.Refused('save_failed') from None
