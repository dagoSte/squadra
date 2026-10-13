"""Persist user selections for a separate consumer; never execute an action."""
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
import re

import plugins as p
import plugin_settings as files

LIMIT = 64 * 1024
STAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z')


def ids_valid(ids, cancel=False):
    p.require(type(ids) is list and (0 if cancel else 1) <= len(ids) <= 500)
    for item in ids:
        p._text(item)
        p.require(item != '')
    p.require(len(set(ids)) == len(ids))


def declared(fields, action):
    p.require(type(action) is str and p.NAME.fullmatch(action), 'bad_name')
    p.require(any(field['id'] == action for field in fields), 'undeclared_action')


@contextmanager
def locked(directory, action, create=False, *, shared=False):
    name = action + '.lock'
    fd = files.optional(directory, name)
    try:
        if fd is None:
            existing = files.optional(directory, action + '.json')
            if existing is not None:
                os.close(existing)
                raise p.Refused('request_invalid')
            if create:
                try:
                    fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                    os.fchmod(fd, 0o600)
                    os.fsync(fd)
                    os.fsync(directory)
                except FileExistsError:
                    fd = files.optional(directory, name)
                    p.require(fd is not None, 'io_error')
        if fd is not None:
            files.private(fd)
            try:
                fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
            except BlockingIOError:
                raise p.Refused('busy') from None
        yield fd
    finally:
        if fd is not None:
            os.close(fd)


def current(directory, action):
    try:
        value = p._read(directory, action + '.json', LIMIT, files.private)
    except p.Refused as exc:
        if exc.code == 'not_found':
            return None
        if exc.code == 'invalid_json':
            raise p.Refused('request_invalid') from None
        raise
    try:
        p._fields(value, ('schema', 'key', 'ids', 'requested'))
        p._schema(value)
        p.require(type(value['key']) is str and p.KEY.fullmatch(value['key']))
        ids_valid(value['ids'])
        p.require(type(value['requested']) is str and STAMP.fullmatch(value['requested']))
        datetime.strptime(value['requested'], '%Y-%m-%dT%H:%M:%SZ')
    except (p.Refused, ValueError):
        raise p.Refused('request_invalid') from None
    return value


def projection(value, key):
    return {k: value[k] for k in ('ids', 'requested')} if value is not None and value['key'] == key else None


def eligible(parent, key, ids):
    panel = p.panel_at(parent, key)
    p.require(panel is not None, 'not_selectable')
    counts = Counter(item['id'] for item in panel['items'])
    allowed = {item['id'] for item in panel['items'] if item.get('selectable') is True and item['status'] == 'draft'}
    p.require(all(item in allowed and counts[item] == 1 for item in ids), 'not_selectable')


def read(home, name, action, key):
    p.require(type(key) is str and p.KEY.fullmatch(key), 'registry_invalid')
    try:
        with files.installed(home, name, 'actions') as (parent, fields):
            declared(fields, action)
            directory = files.optional(parent, 'actions', True)
            if directory is None:
                return None
            try:
                with locked(directory, action, shared=True) as lock:
                    return projection(current(directory, action), key) if lock is not None else None
            finally:
                os.close(directory)
    except OSError:
        raise p.Refused('io_error') from None


def discover(home, key):
    found = files.discover(home)
    for manifest in found['plugins']:
        for action in manifest.get('actions', []):
            try:
                action['pending'] = read(home, manifest['name'], action['id'], key)
            except p.Refused as exc:
                action['pending'] = None
                found['refused'].append({'name': manifest['name'], 'code': exc.code})
    return found


def encoded(key, ids):
    value = {'schema': 1, 'key': key, 'ids': ids, 'requested': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
    body = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    p.require(len(body) <= LIMIT, 'too_large')
    return value, body


def save(home, name, action, key, ids):
    p.require(type(key) is str and p.KEY.fullmatch(key), 'registry_invalid')
    ids_valid(ids, cancel=True)
    encoded(key, ids)  # Check the complete saved record size before writing.
    try:
        with files.installed(home, name, 'actions') as (parent, fields):
            declared(fields, action)
            if ids:
                eligible(parent, key, ids)
            directory = files.optional(parent, 'actions', True)
            if directory is None:
                p.require(ids, 'no_pending')
                try:
                    os.mkdir('actions', 0o700, dir_fd=parent)
                    os.fsync(parent)
                except FileExistsError:
                    pass
                directory = files.optional(parent, 'actions', True)
                p.require(directory is not None, 'io_error')
            try:
                with locked(directory, action, create=bool(ids)) as lock:
                    declared(p._manifest(parent, name).get('actions', []), action)
                    saved = current(directory, action) if lock is not None else None
                    p.require(saved is None or saved['key'] == key, 'registry_mismatch')
                    if not ids:
                        p.require(saved is not None, 'no_pending')
                        os.unlink(action + '.json', dir_fd=directory)
                        os.fsync(directory)
                        return None
                    eligible(parent, key, ids)
                    if saved is not None and saved['ids'] == ids:
                        return projection(saved, key)
                    value, body = encoded(key, ids)
                    files.atomic(directory, action + '.json', body)
                    return projection(value, key)
            finally:
                os.close(directory)
    except OSError:
        raise p.Refused('save_failed') from None
