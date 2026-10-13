"""Prepare browser checks and retain agent reports; never launch a browser or MCP."""
from contextlib import contextmanager
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import unicodedata
from urllib.parse import urlsplit

# Isolated Python omits the script directory; use this bundle's helpers only.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import plugin_actions as actions
import plugin_settings as files
import plugins as p
from registry import Store, Error, canonical

NAME, ACTION = 'playwright', 'check'
LIMIT = p.PANEL_BYTES
OUTCOMES = {'passed': 'approved', 'failed': 'rejected', 'error': 'error'}


def text(value, maximum=2000):
    p._text(value)
    p.require(0 < len(value) <= maximum and not any(unicodedata.category(c) in ('Cc', 'Cf') for c in value))


def encode(value):
    body = canonical(value).encode('utf-8')
    p.require(len(body) <= LIMIT, 'too_large')
    return body


def load(path):
    path = Path(path)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        return p._read(parent, path.name, LIMIT)
    finally:
        os.close(parent)


def registry(root):
    try:
        store = Store(root, read_only=True)
        try:
            events = store.events()
            p.require(events and events[0]['kind'] == 'init', 'registry_invalid')
            return events[0]['hash'], store.state(events)
        finally:
            store.close()
    except (Error, KeyError, ValueError, sqlite3.Error):
        raise p.Refused('registry_invalid') from None


def check(value):
    p._fields(value, ('task', 'title', 'url', 'expect'), ('revision',))
    text(value['task'], 200)
    text(value['title'], 200)
    text(value['expect'], 1000)
    text(value['url'], 2048)
    if 'revision' in value:
        text(value['revision'], 80)
    try:
        target = urlsplit(value['url'])
        p.require(target.scheme == 'https' or (target.scheme == 'http' and target.hostname in ('127.0.0.1', '[::1]', '::1', 'localhost')))
        p.require(target.hostname and target.username is None and target.password is None and not target.fragment)
        p.require(target.port is None or 1 <= target.port <= 65535)
        p.require(not any(c.isspace() or c == '\\' for c in value['url']))
    except ValueError:
        raise p.Refused('bad_value') from None
    return hashlib.sha256(encode(value)).hexdigest()


def outcome(value):
    p._fields(value, ('status', 'observed', 'evidence'))
    p.require(type(value['status']) is str and value['status'] in OUTCOMES)
    text(value['observed'])
    p.require(type(value['evidence']) is list and 1 <= len(value['evidence']) <= 8)
    for receipt in value['evidence']:
        text(receipt, 200)
    p.require(len(set(value['evidence'])) == len(value['evidence']))


def directory(parent, name):
    found = files.optional(parent, name, True)
    if found is None:
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
            os.fsync(parent)
        except FileExistsError:
            pass
        found = files.optional(parent, name, True)
        p.require(found is not None, 'io_error')
    return found


@contextmanager
def locked(home):
    with files.installed(home, NAME, 'actions') as (parent, fields):
        actions.declared(fields, ACTION)
        target = directory(parent, 'actions')
        try:
            with actions.locked(target, ACTION, create=True):
                yield parent, target
        finally:
            os.close(target)


def saved(parent, key):
    target = files.optional(parent, 'checks', True)
    if target is None:
        return {}
    try:
        try:
            value = p._read(target, key + '.json', LIMIT, files.private)
        except p.Refused as exc:
            if exc.code == 'not_found':
                return {}
            raise
        p._fields(value, ('schema', 'key', 'checks'))
        p._schema(value)
        p.require(value['key'] == key and type(value['checks']) is dict and len(value['checks']) <= 500)
        for identity, record in value['checks'].items():
            p._fields(record, ('check',), ('outcome',))
            p.require(check(record['check']) == identity)
            if 'outcome' in record:
                outcome(record['outcome'])
        return value['checks']
    finally:
        os.close(target)


def panel(records):
    items = []
    for identity, record in records.items():
        value, result = record['check'], record.get('outcome')
        items.append({'id': identity, 'title': value['title'],
                      'text': result['observed'] if result else value['expect'],
                      'status': OUTCOMES[result['status']] if result else 'draft',
                      'selectable': not bool(result), 'refs': [{'task': value['task']}],
                      'meta': [
                          {'label': {'it': 'Pagina', 'en': 'Page'}, 'value': value['url']},
                          {'label': {'it': 'Testo atteso', 'en': 'Expected text'}, 'value': value['expect']},
                          *([{'label': {'it': 'Ricevute della relazione', 'en': 'Report receipts'},
                               'value': ', '.join(result['evidence'])}] if result else [])]})
    return p._panel({'schema': 1, 'updated': datetime.now(timezone.utc).isoformat(),
                     'summary': {'it': '1 controllo del browser' if len(items) == 1 else f'{len(items)} controlli del browser',
                                 'en': '1 browser check' if len(items) == 1 else f'{len(items)} browser checks'},
                     'note': {'it': 'Esiti dichiarati da un agente; non sono prove vincolate né approvazioni. Le ricevute rimandano agli output dell’incarico.',
                              'en': 'Agent-reported outcomes; these are not bound proofs or approvals. Receipts refer to the task outputs.'},
                     'items': items})


def persist(parent, key, records):
    p.require(len(records) <= 500, 'too_large')
    durable = encode({'schema': 1, 'key': key, 'checks': records})
    projected = encode(panel(records))
    target = directory(parent, 'checks')
    try:
        files.atomic(target, key + '.json', durable)
    finally:
        os.close(target)
    target = directory(parent, 'panels')
    try:
        files.atomic(target, key + '.json', projected)
    finally:
        os.close(target)


def prepare(home, root, value):
    key, state = registry(root)
    p._fields(value, ('schema', 'task', 'checks'))
    p._schema(value)
    text(value['task'], 200)
    p.require(value['task'] in state['tasks'], 'unknown_task')
    p.require(type(value['checks']) is list and 1 <= len(value['checks']) <= 50)
    added = {}
    for item in value['checks']:
        p._fields(item, ('title', 'url', 'expect'), ('revision',))
        spec = {'task': value['task'], **item}
        identity = check(spec)
        p.require(identity not in added, 'duplicate_check')
        added[identity] = {'check': spec}
    with locked(home) as (parent, _):
        records = saved(parent, key)
        for identity, record in added.items():
            records.setdefault(identity, record)
        persist(parent, key, records)
    return {'key': key, 'ids': list(added), 'saved': True}


def identity(info):
    return [info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size]


def request(target):
    fd = files.optional(target, ACTION + '.json')
    if fd is None:
        return None, None
    try:
        before = identity(os.fstat(fd))
        p.require(before[-1] <= actions.LIMIT, 'too_large')
        body = bytearray()
        while len(body) <= actions.LIMIT:
            chunk = os.read(fd, min(65536, actions.LIMIT + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        p.require(len(body) <= actions.LIMIT, 'too_large')
        p.require(before == identity(os.fstat(fd)), 'request_changed')
        try:
            value = json.loads(body.decode('utf-8'), object_pairs_hook=p._pairs, parse_constant=p._constant)
            p._fields(value, ('schema', 'key', 'ids', 'requested'))
            p._schema(value)
            p.require(type(value['key']) is str and p.KEY.fullmatch(value['key']))
            actions.ids_valid(value['ids'])
            p.require(type(value['requested']) is str and actions.STAMP.fullmatch(value['requested']))
            datetime.strptime(value['requested'], '%Y-%m-%dT%H:%M:%SZ')
        except (p.Refused, UnicodeError, ValueError, RecursionError):
            raise p.Refused('request_invalid') from None
        return value, before
    finally:
        os.close(fd)


def pending(home, root):
    key, _ = registry(root)
    with locked(home) as (parent, target):
        value, stamp = request(target)
        if value is None or value['key'] != key:
            return None
        records = saved(parent, key)
        p.require(all(i in records for i in value['ids']), 'unknown_check')
        return {'schema': 1, 'key': key, 'request': value, 'identity': stamp,
                'checks': {i: records[i] for i in value['ids']}}


def record(home, root, snapshot, result):
    key, state = registry(root)
    p._fields(snapshot, ('schema', 'key', 'request', 'identity', 'checks'))
    p._schema(snapshot)
    p.require(snapshot['key'] == key, 'registry_mismatch')
    selection = snapshot['request']
    p._fields(selection, ('schema', 'key', 'ids', 'requested'))
    p._schema(selection)
    p.require(selection['key'] == key, 'registry_mismatch')
    actions.ids_valid(selection['ids'])
    p.require(type(selection['requested']) is str and actions.STAMP.fullmatch(selection['requested']))
    stamp = snapshot['identity']
    p.require(type(stamp) is list and len(stamp) == 5 and all(type(n) is int and n >= 0 for n in stamp))
    p.require(type(snapshot['checks']) is dict and set(snapshot['checks']) == set(selection['ids']))
    p._fields(result, ('schema', 'results'))
    p._schema(result)
    p.require(type(result['results']) is list and len(result['results']) <= len(selection['ids']))
    additions = {}
    outputs = {item['event']: item for item in state['outputs']}
    for item in result['results']:
        p._fields(item, ('id', 'status', 'observed', 'evidence'))
        text(item['id'], 64)
        p.require(item['id'] in snapshot['checks'] and item['id'] not in additions, 'unknown_check')
        additions[item['id']] = {k: item[k] for k in ('status', 'observed', 'evidence')}
        outcome(additions[item['id']])
    with locked(home) as (parent, target):
        records = saved(parent, key)
        for name, previous in snapshot['checks'].items():
            p._fields(previous, ('check',), ('outcome',))
            p.require(name in records and records[name]['check'] == previous['check'] and check(previous['check']) == name, 'unknown_check')
        for name, value in additions.items():
            task = records[name]['check']['task']
            p.require(all(receipt in outputs and outputs[receipt].get('task') == task and outputs[receipt].get('artifacts')
                          for receipt in value['evidence']), 'evidence_required')
            p.require('outcome' not in records[name] or records[name]['outcome'] == value, 'outcome_conflict')
            records[name]['outcome'] = value
        # A crash or projection failure leaves the request recoverable from these durable reports.
        persist(parent, key, records)
        complete = all('outcome' in records[name] for name in selection['ids'])
        current, current_stamp = request(target)
        consumed = complete and current == selection and current_stamp == stamp
        if consumed:
            os.unlink(ACTION + '.json', dir_fd=target)
            os.fsync(target)
        return {'saved': True, 'complete': complete, 'consumed': consumed,
                'reported': len(additions), 'claim': 'agent-reported'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'pending', 'record'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--home', type=Path, default=Path.home())
        cmd.add_argument('--root', type=Path, required=True)
        if name in ('prepare', 'record'):
            cmd.add_argument('--file', type=Path, required=True)
        if name == 'record':
            cmd.add_argument('--snapshot', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            result = prepare(args.home, args.root, load(args.file))
        elif args.command == 'pending':
            result = pending(args.home, args.root)
        else:
            result = record(args.home, args.root, load(args.snapshot), load(args.file))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (p.Refused, Error, OSError, ValueError) as exc:
        print(json.dumps({'error': exc.code if isinstance(exc, p.Refused) else 'io_error'}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
