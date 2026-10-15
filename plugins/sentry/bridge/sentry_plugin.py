"""Import sanitised observations and link requested analysis; no network or registry writes."""
from contextlib import contextmanager
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import unicodedata

# Isolated Python omits the script directory; use only this installed bundle.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import plugin_actions as actions
import plugin_settings as files
import plugins as p
from registry import Store, Error, canonical

NAME, ACTION = 'sentry', 'analyse'
LIMIT = p.PANEL_BYTES

SENSITIVE = re.compile(
    r'(?i)sntry[a-z]*_[a-z0-9._~-]+|https?://[^\s/@]+@[^\s/]+'
    r'|\bbearer\s+[^\s]+'
    r'|\b(?:[a-z0-9_-]*(?:token|secret|password|passwd|api[_-]?key)|key|dsn|authorization)'
    r'\s*[=:]\s*[\x22\x27]?[^\s\x22\x27]+')
EMAIL = re.compile(r'(?i)[a-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+')


class ImportRefused(p.Refused):
    def __init__(self, refused):
        super().__init__(refused[0]['error'])
        self.refused = refused


def safe(value):
    """A backstop for known shapes, not a substitute for agent sanitisation."""
    if isinstance(value, str):
        p.require(not SENSITIVE.search(value), 'sensitive_text')
        p.require(not EMAIL.search(value), 'personal_text')
    elif isinstance(value, dict):
        for key, item in value.items():
            safe(key)
            safe(item)
    elif isinstance(value, list):
        for item in value:
            safe(item)


def text(value, maximum=1000):
    p._text(value)
    safe(value)
    p.require(0 < len(value) <= maximum and not any(unicodedata.category(c) in ('Cc', 'Cf') for c in value))


def timestamp(value):
    text(value, 40)
    try:
        p.require('T' in value and datetime.fromisoformat(value.replace('Z', '+00:00')).utcoffset().total_seconds() == 0)
    except (ValueError, AttributeError, OverflowError):
        raise p.Refused('bad_value') from None


def scope(organization, project):
    p.require(organization and project, 'not_configured')
    for name in (organization, project):
        text(name, 100)
        p.require(re.fullmatch(r'[a-z0-9][a-z0-9_-]*', name), 'bad_value')


def observation(value):
    safe(value)
    p._fields(value, ('organization', 'project', 'observed', 'issue'))
    scope(value['organization'], value['project'])
    timestamp(value['observed'])
    issue = value['issue']
    p._fields(issue, ('id', 'title'), ('status', 'severity', 'count', 'window', 'first_seen', 'last_seen', 'context'))
    text(issue['id'], 100)
    text(issue['title'], 200)
    for name in ('status', 'severity', 'context'):
        if name in issue:
            text(issue[name], 1000 if name == 'context' else 60)
    for name in ('first_seen', 'last_seen'):
        if name in issue:
            timestamp(issue[name])
    if 'count' in issue:
        p.require(type(issue['count']) is int and 0 <= issue['count'] <= 10**12)
        p.require('window' in issue, 'count_window_required')
    if 'window' in issue:
        p.require('count' in issue, 'count_window_required')
        text(issue['window'], 200)
    return hashlib.sha256(encode(value)).hexdigest()


def association(value):
    safe(value)
    p._fields(value, ('task', 'note'), ('run',))
    text(value['task'], 200)
    text(value['note'], 1000)
    p._references([{k: value[k] for k in ('task', 'run') if k in value}])

def encode(value):
    body = canonical(value).encode('utf-8')
    p.require(len(body) <= LIMIT, 'too_large')
    return body


def load(path):
    path = Path(path)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        return p._read(parent, path.name, LIMIT, files.private)
    finally:
        os.close(parent)


def registry(root):
    try:
        store = Store(root, read_only=True)
        try:
            events = store.events()
            p.require(events and events[0]['kind'] == 'init', 'registry_invalid')
            return events[0]['hash'], store.state(events, resolve=False)
        finally:
            store.close()
    except (Error, KeyError, ValueError, sqlite3.Error):
        raise p.Refused('registry_invalid') from None


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


def saved(parent, key):
    target = files.optional(parent, 'observations', True)
    if target is None:
        return None
    try:
        try:
            data = p._read(target, key + '.json', LIMIT, files.private)
        except p.Refused as exc:
            if exc.code == 'not_found':
                return None
            raise
    finally:
        os.close(target)
    safe(data)
    p._fields(data, ('schema', 'key', 'organization', 'project', 'observed', 'availability', 'issues'))
    p._schema(data)
    p.require(data['key'] == key, 'registry_mismatch')
    scope(data['organization'], data['project'])
    timestamp(data['observed'])
    p.require(data['availability'] in ('available', 'unavailable'))
    p.require(type(data['issues']) is dict and len(data['issues']) <= 500)
    for name, record in data['issues'].items():
        p._fields(record, ('observation',), ('association',))
        spec = record['observation']
        p.require(observation(spec) == name, 'observation_changed')
        p.require((spec['organization'], spec['project']) == (data['organization'], data['project']), 'scope_mismatch')
        if 'association' in record:
            association(record['association'])
    return data


def panel(data):
    items = []
    missing = 'Non disponibile / Unavailable'
    for name, record in data['issues'].items():
        spec = record['observation']
        issue, result = spec['issue'], record.get('association')
        count = f"{issue['count']} ({issue['window']})" if 'count' in issue else missing
        meta = [
            ('Organizzazione', 'Organization', spec['organization']),
            ('Progetto', 'Project', spec['project']),
            ('Problema sorgente', 'Source issue', issue['id']),
            ('Stato sorgente osservato', 'Observed source status', issue.get('status', missing)),
            ('Gravità osservata', 'Observed severity', issue.get('severity', missing)),
            ('Occorrenze e periodo', 'Occurrences and window', count),
            ('Prima occorrenza', 'First seen', issue.get('first_seen', missing)),
            ('Ultima occorrenza', 'Last seen', issue.get('last_seen', missing)),
            ('Osservazione', 'Observation', spec['observed']),
        ]
        if result:
            meta.append(('Nota del coordinatore', 'Coordinator note', result['note']))
        items.append({'id': name, 'title': issue['title'], 'text': issue.get('context', missing),
                      'status': 'sent' if result else 'draft', 'selectable': not bool(result),
                      'refs': [{k: result[k] for k in ('task', 'run') if k in result}] if result else [],
                      'meta': [{'label': {'it': it, 'en': en}, 'value': value} for it, en, value in meta]})
    note = {'it': 'Riepiloghi selezionati e dichiarati dall’agente. Un incarico chiuso non risolve il problema in Sentry.',
            'en': 'Summaries selected and reported by the agent. Closing a task does not resolve the issue in Sentry.'}
    if data['availability'] == 'unavailable':
        note = {'it': 'MCP non disponibile: nessuna nuova lettura. Le osservazioni precedenti sono conservate; le richieste restano in attesa.',
                'en': 'MCP unavailable: no new read. Previous observations are retained; requests stay pending.'}
    return p._panel({'schema': 1, 'updated': data['observed'],
                     'summary': {'it': f"Osservazioni conservate: {len(items)}", 'en': f"Retained observations: {len(items)}"},
                     'note': note, 'items': items})


def persist(parent, key, data):
    p.require(len(data['issues']) <= 500, 'too_large')
    body, projected = encode(data), encode(panel(data))
    folder = directory(parent, 'observations')
    try:
        files.atomic(folder, key + '.json', body)
    finally:
        os.close(folder)
    folder = directory(parent, 'panels')
    try:
        files.atomic(folder, key + '.json', projected)
    finally:
        os.close(folder)


def import_data(home, root, value, organization=None, project=None):
    key, _ = registry(root)
    safe({k: v for k, v in value.items() if k != 'issues'} if isinstance(value, dict) else value)
    scope(organization, project)
    p._fields(value, ('schema', 'organization', 'project', 'observed', 'availability', 'issues'))
    p._schema(value)
    p.require((value['organization'], value['project']) == (organization, project), 'scope_mismatch')
    timestamp(value['observed'])
    p.require(type(value['availability']) is str and value['availability'] in ('available', 'unavailable'))
    p.require(type(value['issues']) is list and len(value['issues']) <= 50)
    p.require(value['availability'] == 'available' or not value['issues'], 'capability_unavailable')
    additions, refused = {}, []
    for index, item in enumerate(value['issues'], 1):
        spec = {k: value[k] for k in ('organization', 'project', 'observed')}
        spec['issue'] = item
        try:
            name = observation(spec)
        except p.Refused as exc:
            if exc.code not in ('sensitive_text', 'personal_text'):
                raise
            refused.append({'index': index, 'error': exc.code})
            continue
        p.require(name not in additions, 'duplicate_issue')
        additions[name] = {'observation': spec}
    if refused and not additions:
        raise ImportRefused(refused)
    with locked(home) as (parent, _):
        data = saved(parent, key)
        if data is None:
            data = {k: value[k] for k in ('schema', 'organization', 'project', 'observed', 'availability')}
            data.update(key=key, issues={})
        p.require((data['organization'], data['project']) == (organization, project), 'scope_mismatch')
        p.require(datetime.fromisoformat(value['observed'].replace('Z', '+00:00')) >=
                  datetime.fromisoformat(data['observed'].replace('Z', '+00:00')), 'stale_observation')
        data.update(observed=value['observed'], availability=value['availability'])
        for name, record in additions.items():
            data['issues'].setdefault(name, record)
        persist(parent, key, data)
    result = {'saved': True, 'key': key, 'ids': list(additions), 'availability': value['availability']}
    if refused:
        result['refused'] = refused
    return result


def refresh(home, root):
    key, _ = registry(root)
    with locked(home) as (parent, _):
        data = saved(parent, key)
        p.require(data is not None, 'not_configured')
        persist(parent, key, data)
    return {'saved': True, 'availability': data['availability']}


def pending(home, root):
    key, _ = registry(root)
    with locked(home) as (parent, target):
        value, stamp = request(target)
        if value is None or value['key'] != key:
            return None
        data = saved(parent, key)
        p.require(data is not None, 'not_configured')
        p.require(all(i in data['issues'] for i in value['ids']), 'unknown_issue')
        return {'schema': 1, 'key': key, 'request': value, 'identity': stamp,
                'issues': {i: data['issues'][i] for i in value['ids']}}


def record(home, root, snapshot, result):
    key, state = registry(root)
    safe(snapshot)
    safe(result)
    p._fields(snapshot, ('schema', 'key', 'request', 'identity', 'issues'))
    p._schema(snapshot)
    p.require(snapshot['key'] == key, 'registry_mismatch')
    selection = snapshot['request']
    p._fields(selection, ('schema', 'key', 'ids', 'requested'))
    p._schema(selection)
    p.require(selection['key'] == key, 'registry_mismatch')
    actions.ids_valid(selection['ids'])
    p.require(type(selection['requested']) is str and actions.STAMP.fullmatch(selection['requested']))
    datetime.strptime(selection['requested'], '%Y-%m-%dT%H:%M:%SZ')
    stamp = snapshot['identity']
    p.require(type(stamp) is list and len(stamp) == 5 and all(type(n) is int and n >= 0 for n in stamp))
    p.require(type(snapshot['issues']) is dict and set(snapshot['issues']) == set(selection['ids']))
    p._fields(result, ('schema', 'results'))
    p._schema(result)
    p.require(type(result['results']) is list and len(result['results']) <= len(selection['ids']))
    additions = {}
    for item in result['results']:
        p._fields(item, ('id', 'task', 'note'), ('run',))
        text(item['id'], 64)
        p.require(item['id'] in snapshot['issues'] and item['id'] not in additions, 'unknown_issue')
        value = {k: item[k] for k in ('task', 'run', 'note') if k in item}
        association(value)
        p.require(value['task'] in state['tasks'], 'unknown_task')
        if 'run' in value:
            p.require(value['run'] in state['runs'] and state['runs'][value['run']]['task'] == value['task'], 'foreign_run')
        additions[item['id']] = value
    with locked(home) as (parent, target):
        data = saved(parent, key)
        p.require(data is not None, 'not_configured')
        records = data['issues']
        for name, previous in snapshot['issues'].items():
            p._fields(previous, ('observation',), ('association',))
            p.require(name in records and records[name]['observation'] == previous['observation']
                      and observation(previous['observation']) == name, 'observation_changed')
        for name, value in additions.items():
            p.require('association' not in records[name] or records[name]['association'] == value, 'association_conflict')
            records[name]['association'] = value
        # Durable associations survive a panel-write failure before request acknowledgment.
        persist(parent, key, data)
        complete = all('association' in records[name] for name in selection['ids'])
        current, current_stamp = request(target)
        consumed = complete and current == selection and current_stamp == stamp
        if consumed:
            os.unlink(ACTION + '.json', dir_fd=target)
            os.fsync(target)
        return {'saved': True, 'complete': complete, 'consumed': consumed, 'associated': len(additions),
                'claim': 'agent-reported association'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('import', 'refresh', 'pending', 'record'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--home', type=Path, default=Path.home())
        cmd.add_argument('--root', type=Path, required=True)
        if name in ('import', 'record'):
            cmd.add_argument('--file', type=Path, required=True)
        if name == 'import':
            cmd.add_argument('--organization')
            cmd.add_argument('--project')
        if name == 'record':
            cmd.add_argument('--snapshot', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'import':
            result = import_data(args.home, args.root, load(args.file), args.organization, args.project)
        elif args.command == 'refresh':
            result = refresh(args.home, args.root)
        elif args.command == 'pending':
            result = pending(args.home, args.root)
        else:
            result = record(args.home, args.root, load(args.snapshot), load(args.file))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (p.Refused, Error, OSError, ValueError, RecursionError) as exc:
        result = {'error': exc.code if isinstance(exc, p.Refused) else 'io_error'}
        if isinstance(exc, ImportRefused):
            result['refused'] = exc.refused
        print(json.dumps(result), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
