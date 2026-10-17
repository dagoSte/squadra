"""Loopback dashboard: saves plugin settings/selections, never executes actions."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
import math
import unicodedata
from urllib.parse import unquote, urlparse
import webbrowser

from registry import (Store, Error, digest, attention, waits, now, requirement_matrix, initial, apply, decision_graph, decision_id,
                      task_progress, presence, fix_status, plain, wait_view, scrub_index, fold_recorded, pending_ack_ids)
from comms import communications
from panel import activity
from reads import read_value
from source_manifests import materialize_known
import plugins
import plugin_settings
import plugin_actions
import plugin_store
import system_skills
import forecast
import balance
import usage

SETTINGS_BODY_TIMEOUT = 5.0
SETTINGS_STATUS = {'host_refused': 403, 'token_required': 401, 'origin_refused': 403,
                   'content_type': 415, 'length_required': 411, 'too_large': 413, 'body_timeout': 408,
                   'invalid_length': 400, 'invalid_body': 400, 'invalid_json': 400, 'unknown_key': 400,
                   'unknown_id': 400, 'bad_value': 400, 'bad_name': 400, 'not_found': 404, 'save_failed': 500}


def compact_records(value):
    """Project retained fingerprints without traversing their source inventories."""
    if isinstance(value, dict):
        fingerprint = {'head', 'sha256', 'files'} <= value.keys()
        return {key: compact_records(item) for key, item in value.items()
                if not (fingerprint and key == 'files')}
    if isinstance(value, list):
        return [compact_records(item) for item in value]
    return value


def preview(text):
    text = str(text or '')
    short = text.split('\n', 1)[0][:240]
    return short + '…' if short != text else text


# Only these human fields enter search; source inventories and metadata stay out.
SEARCH_FIELDS = {
    'tasks': 'id title brief.text owner reviewer authors.* depends.* requirements.*.text scope.name plan.name brief.artifact.name',
    'runs': 'task agent',
    'messages': 'id from to task related_task body',
    'outputs': 'task agent text next artifacts.*.name',
    'decisions': 'id name actor reason owner_words answers withdraws choice tasks.*',
    'asks': 'id actor task question options.*.label options.*.say evidence.*.name',
    'reviews': 'task reviewer notes findings.*.title report.name',
}


def search_fields(kind, record):
    def leaves(value, path):
        if not path:
            return [value] if isinstance(value, str) else []
        key, *tail = path
        if key == '*':
            return [text for item in value for text in leaves(item, tail)] if isinstance(value, list) else []
        return leaves(value.get(key), tail) if isinstance(value, dict) else []
    return [text for path in SEARCH_FIELDS.get(kind, '').split()
            for text in leaves(record, path.split('.'))]


def search_normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text)
                   if unicodedata.category(c)[0] != 'M').lower()


def search_match(fields, query):
    text = '\n'.join(re.sub(r'(?<!\w)[a-f0-9]{32,}(?!\w)', ' ', search_normalize(s)) for s in fields)
    return all(re.search(r'(?<!\w)' + re.escape(word), text) for word in search_normalize(query).split())


def search_records(recorded, events, query):
    result = {'query': query, **{kind: [] for kind in SEARCH_FIELDS}, 'results': [], 'total': 0}
    if not query.strip():
        return result
    positions = {kind: {} for kind in SEARCH_FIELDS}
    decisions, reviews = {}, {}

    def position(kind, identity, event):
        if identity:
            positions[kind][identity] = {key: event[key] for key in ('seq', 'utc', 'actor')}

    for event in events:
        kind, d = event['kind'], event['data']
        if kind == 'task':
            position('tasks', d['id'], event)
            position('tasks', d.get('supersedes'), event)
        elif kind in {'brief', 'plan', 'start', 'deliver', 'review', 'route', 'fix', 'close', 'supersede'}:
            position('tasks', d.get('task'), event)
        if kind in {'run.start', 'run.end', 'run.cancel', 'run.abandon'}:
            position('runs', d['id'], event)
        elif kind == 'message':
            position('messages', d['id'], event)
        elif kind in {'ack', 'transport'}:
            position('messages', d['message'], event)
            if kind == 'ack':
                position('tasks', recorded['messages'].get(d['message'], {}).get('task'), event)
        elif kind == 'output':
            position('outputs', event['id'], event)
        elif kind == 'ask':
            position('asks', d['id'], event)
        elif kind == 'decision':
            identity = decision_id(d, event['seq'])
            decisions[identity] = {**d, 'id': identity, 'actor': event['actor']}
            position('decisions', identity, event)
            position('asks', d.get('answers') or d.get('withdraws'), event)
        elif kind == 'review':
            reviews[event['id']] = {**d, 'reviewer': event['actor']}
            position('reviews', event['id'], event)

    records = {**{kind: recorded[kind] for kind in ('tasks', 'runs', 'messages', 'asks')},
               'outputs': {o['event']: o for o in recorded['outputs']},
               'decisions': decisions, 'reviews': reviews}
    rows = []
    for kind, values in records.items():
        for identity, record in values.items():
            if not search_match(search_fields(kind, record), query):
                continue
            result[kind].append(identity)
            headline = next((record.get(key) for key in ('title', 'body', 'text', 'question', 'reason', 'notes', 'task')
                             if isinstance(record.get(key), str) and record[key].strip()), identity)
            rows.append({'kind': kind, 'id': identity, **positions[kind].get(identity, {'seq': 0, 'utc': '', 'actor': ''}),
                         'text': preview(headline), 'outcome': record.get('status') or record.get('verdict') or '',
                         'task': identity if kind == 'tasks' else record.get('task')})
    rows.sort(key=lambda row: (-row['seq'], row['kind'], row['id']))
    result.update(results=rows[:100], total=len(rows))
    return result


def compact_map(records, kept, kept_compact):
    """compact_records over a record map, reusing the projection of every record that is still the same object."""
    if {'head', 'sha256', 'files'} <= records.keys():
        return compact_records(records)
    return {name: kept_compact[name] if kept.get(name) is record and name in kept_compact else compact_records(record)
            for name, record in records.items()}


def landing_records(recorded, compact):
    """Transfer summaries; the detail routes retain the original evidence."""
    result = dict(recorded)
    result.update(compact)
    result['tasks'] = {}
    for key, task in compact['tasks'].items():
        item = dict(task)
        if item.get('delivery'):
            item['delivery'] = {k: v for k, v in item['delivery'].items() if k != 'proofs'}
        item['proofs'] = [{k: v for k, v in proof.items() if k in {
            'id', 'utc', 'status', 'native_rc', 'seconds', 'counts', 'error', 'covers', 'shared_from'}}
            for proof in item.get('proofs', [])]
        result['tasks'][key] = item
    result['messages'] = {key: {**message, 'body': preview(message['body']),
                               'detail_pending': preview(message['body']) != message['body']}
                          for key, message in recorded['messages'].items()}
    result['outputs'] = [{**output, 'text': preview(output['text']), 'artifacts': [],
                          'detail_pending': True} for output in recorded['outputs']]
    result['graph'] = {**recorded['graph'], 'nodes': [
        {**node, 'artifacts': []} for node in recorded['graph']['nodes']]}
    result['transfer'] = 1
    return result


def detail_patch(recorded, events, compact, section, record):
    if section == 'mail':
        return {'messages': recorded['messages']}
    if section == 'outputs':
        return {'outputs': recorded['outputs']}
    if section == 'history':
        return {'events': events[-120:]}
    if section in ('tasks', 'runs'):
        return {section: compact[section]}
    if section == 'graph':
        return {'graph': recorded['graph']}
    if section == 'story' and record in recorded['tasks']:
        return {'tasks': {record: compact['tasks'][record]},
                'messages': {key: value for key, value in recorded['messages'].items()
                             if value.get('task') == record or value.get('related_task') == record},
                'outputs': [value for value in recorded['outputs'] if value.get('task') == record],
                'graph': {'nodes': [node for node in recorded['graph']['nodes']
                                    if record in node.get('tasks', [])]}}
    return None


def transfer_signature(head, query, plugins_value, waiting, present, activity_value, audit=None):
    """Time ages are local; threshold membership, a stand-by row's members and bucket changes are semantic."""
    value = [head, query, plugins_value, audit,
             [(w['kind'], w.get('id'), w.get('task'), w.get('who', []), w.get('threshold'))
              + ((w.get('messages'), w.get('runs')) if w['kind'] == 'stand-by' else ())
              for w in waiting],
             [(p['agent'], p['due']) for p in present],
             {k: v for k, v in activity_value.items() if k != 'as_of'}]
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True).encode())


FAR_SECONDS = 10 * 365 * 86400
# The complete view materializes every source inventory; past this many entries the compact view and details apply.
FULL_FILES_LIMIT = 200000


def inventory_size(state):
    """Source-inventory entries the complete view would materialize: listed files plus manifest counts."""
    total, stack = 0, [state.get(key) for key in ('tasks', 'runs', 'challenges')]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            files, manifest = value.get('files'), value.get('manifest')
            if 'head' in value and isinstance(files, (list, dict)):
                total += len(files)
            if isinstance(manifest, dict) and isinstance(manifest.get('count'), int):
                total += manifest['count']
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return total

ACTIVITY_STEP = 900
# Lists whose items carry a stable unique field; a delta sends their changed items and the new order.
KEYED_LISTS = {'outputs': 'event', 'events': 'seq'}


def transfer_delta(old, new):
    """What changed between two landing replies: whole values, record maps and keyed lists.

    A landing's server_time is the instant its clock-bearing rows were computed, so it travels like any value."""
    delta = {'set': {}, 'remove': [key for key in old if key not in new], 'maps': {}, 'lists': {}}
    for key, value in new.items():
        if key == 'change_token' or (key in old and old[key] == value):
            continue
        before = old.get(key)
        if (isinstance(value, dict) and isinstance(before, dict) and value
                and all(isinstance(item, dict) for item in value.values())):
            delta['maps'][key] = {'order': list(value), 'set': {name: item for name, item in value.items()
                                                                 if before.get(name) != item}}
        elif key in KEYED_LISTS and isinstance(value, list) and isinstance(before, list):
            field = KEYED_LISTS[key]
            known = {item[field]: item for item in before}
            delta['lists'][key] = {'by': field, 'order': [item[field] for item in value],
                                   'set': {str(item[field]): item for item in value if known.get(item[field]) != item}}
        else:
            delta['set'][key] = value
    return delta


def same_chain(events, prior):
    """A freshly decoded journal equal to the verified one; decoding re-checks every row's hash."""
    return [event['hash'] for event in events] == [event['hash'] for event in prior]


# Event kinds that add or link decision-graph nodes; any other suffix leaves the graph as it was.
GRAPH_KINDS = {'ask', 'close', 'decision', 'deliver', 'plan', 'restore.freeze', 'resume', 'pause', 'review',
               'run.end', 'run.start', 'supersede', 'task'}


def refreshed(previous, view, old, new):
    """Reuse every plain record whose reducer record is unchanged; materialize the rest from the view."""
    state = {}
    for key in view.keys():
        before, after = old.get(key), new.get(key)
        if key not in previous:
            state[key] = plain(view[key])
        elif before == after:
            state[key] = previous[key]
        elif isinstance(after, dict) and isinstance(before, dict) and all(isinstance(v, dict) for v in after.values()):
            records, kept = view[key], previous[key]
            state[key] = {name: kept[name] if name in kept and before.get(name) == after.get(name) else plain(records[name])
                          for name in records.keys()}
        else:
            state[key] = plain(view[key])
    return state


class VerifiedView:
    """Reuse verified data until SQLite or the underlying files change."""
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / 'registry.sqlite3'
        self.lock = threading.Lock()
        self.monitor = None
        self.identity = self.marker = self.value = None
        self.reads_head = self.reads_value = None
        self.verified_events = None
        self.base = None
        self.held = None
        self.replies = {}
        self.reply_lock = threading.Lock()
        self.audit_lock = threading.Lock()
        self.audit_thread = self.audit_cancel = self.audit_store = None
        self.audit_pending = self.audit_failure = None
        self.audit_generation = 0
        self.audit_state = 'running'
        self.audit_scrubbed = []
        self.closed = False

    def audit_info(self):
        with self.audit_lock:
            if self.audit_failure is not None:
                raise Error(self.audit_failure)
            if self.audit_state == 'complete' and self.audit_scrubbed:
                return {'status': self.audit_state, 'scrubbed': list(self.audit_scrubbed)}
            return {'status': self.audit_state}

    def _queue_audit(self, identity):
        with self.audit_lock:
            if self.closed:
                raise Error('Dashboard view closed')
            self.audit_generation += 1
            self.audit_pending = self.audit_generation, identity
            self.audit_state = 'running'
            if self.audit_cancel is not None:
                self.audit_cancel.set()
            if self.audit_thread is None:
                self.audit_thread = threading.Thread(target=self._audit_worker,
                    name='squadra-registry-audit', daemon=True)
                self.audit_thread.start()

    def _audit_worker(self):
        """Audit a private copy of the queued file, so the audit's long read never holds the live write-ahead log."""
        from registry_prepare import StaleSnapshot
        while True:
            with self.audit_lock:
                if self.closed or self.audit_pending is None:
                    self.audit_thread = self.audit_store = self.audit_cancel = None
                    return
                generation, identity = self.audit_pending
                self.audit_pending = None
                cancel = self.audit_cancel = threading.Event()
            store = None
            audited = failure = None
            stale = False
            try:
                if self.path.is_symlink():
                    raise Error('Registry symlink refused')
                before = self.path.stat()
                if (before.st_dev, before.st_ino) != identity:
                    continue
                store = Store(self.root, read_only=True)
                store._audit_copy = identity
                with self.audit_lock:
                    self.audit_store = store
                audited = store.audit(cancelled=cancel.is_set)
                if audited.get('cache') == 'mismatch':
                    raise Error('Cache mismatch; run squadra doctor --repair')
            except StaleSnapshot:
                stale = True  # The next read sees the new file and queues its own audit.
            except Exception as exc:
                failure = str(exc)
            finally:
                if store is not None:
                    try:
                        store.close()
                    except Exception as exc:
                        failure = failure or 'Registry audit cleanup failed: ' + str(exc)
                with self.audit_lock:
                    self.audit_store = None
            if stale:
                continue
            try:
                current = self.path.lstat()
                same_file = not self.path.is_symlink() and (current.st_dev, current.st_ino) == identity
            except OSError:
                same_file = False
            with self.audit_lock:
                if self.closed or cancel.is_set() or generation != self.audit_generation or not same_file:
                    continue
                if failure is not None:
                    self.audit_failure = failure
                    continue
                known = self.verified_events
                rows = audited['events']
                common = min(len(known or []), len(rows))
                if common and known[common - 1]['hash'] == rows[common - 1]['hash']:
                    self.audit_state = 'complete'
                    self.audit_scrubbed = audited.get('scrubbed') or []

    def stamp(self):
        if self.path.is_symlink():
            raise Error('Registry symlink refused')
        stat = self.path.stat()
        identity = (stat.st_dev, stat.st_ino)
        if self.monitor is None or identity != self.identity:
            if self.monitor is not None:
                self.monitor.close()
            self.monitor = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True,
                                           isolation_level=None, check_same_thread=False)
            self.identity, self.marker, self.value = identity, None, None
            self.verified_events = self.base = None
        version = self.monitor.execute('PRAGMA data_version').fetchone()[0]
        def changed(path):
            try:
                row = path.stat()
                return (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_ctime_ns)
            except FileNotFoundError:
                return None
        return version, changed(self.path), changed(Path(str(self.path) + '-wal'))

    def read(self):
        with self.lock:
            if self.closed:
                raise Error('Dashboard view closed')
            self.audit_info()
            before = self.stamp()
            if self.value is not None and before == self.marker:
                return self.value
            previous, self.value, self.marker = self.base, None, None
            store = Store(self.root, read_only=True)
            try:
                prior = self.verified_events
                suffix = None
                if prior:
                    tip = store.db.execute('SELECT seq,hash FROM events ORDER BY seq DESC LIMIT 1').fetchone()
                    if tip and tip['seq'] >= prior[-1]['seq']:
                        suffix = store.events_since(prior[-1]['seq'], prior[-1]['hash'])
                        if any(event.get('format') != 2 for event in suffix):
                            suffix = None
                if suffix == [] and previous is not None and previous['events'] is prior and (
                        previous['version'] == before[0] or same_chain(store.events(), prior)):
                    # File churn, or a write that left the journal as it was: the verified reply stands.
                    previous['version'] = before[0]
                    value = previous['value']
                else:
                    suffix = suffix or None
                    events, state, raw, cached = self.rebuild(store, prior, suffix, previous)
                    self.verified_events = events
                    graph = (previous['graph'] if suffix and previous is not None
                             and not any(event['kind'] in GRAPH_KINDS for event in suffix)
                             else decision_graph(events))
                    # A record refreshed() reused is the previous read's own object, so its derived values stand.
                    kept = previous['state'] if previous is not None else {'tasks': {}, 'runs': {}, 'requirements': {}}
                    kept_compact = previous['value'][2] if previous is not None else {'tasks': {}, 'runs': {}}
                    state.update(root=str(self.root), graph=graph, progress=task_progress(events),
                                 requirements={key: kept['requirements'][key] if kept['tasks'].get(key) is task
                                               else requirement_matrix(task) for key, task in state['tasks'].items()})
                    state['tasks'] = {key: task if kept['tasks'].get(key) is task
                                      else ({**task, 'fix_status': fix_status(task)} if task.get('fix') else task)
                                      for key, task in state['tasks'].items()}
                    compact = {key: compact_map(state[key], kept[key], kept_compact[key]) for key in ('tasks', 'runs')}
                    value = events, state, compact
                    self.base = {'events': events, 'raw': raw, 'state': state, 'graph': graph, 'value': value,
                                 'cached': cached, 'version': before[0]}
            finally:
                store.close()
            after = self.stamp()
            # A concurrent commit may leave a valid older snapshot, but it must not be cached as current.
            if before == after:
                self.marker, self.value = after, value
            if suffix is None:
                self._queue_audit(self.identity)
            self.audit_info()
            return value

    def rebuild(self, store, prior, suffix, previous):
        """Today's plain view; after an appended suffix only the records it changed are materialized again."""
        if suffix and previous is not None and previous['events'] is prior and previous['cached']:
            try:
                events = prior + suffix
                view = store.resolved(store.view(), compact=True)
                # Only two views read from the derived cache share one record form; a replay fallback does not.
                if getattr(view, 'current', None) is not None:
                    raw = store.state(events, resolve=False, with_waits=False)
                    shared_change = any(event['kind'] in {'run.start', 'run.end'} and
                                        raw['runs'].get(event['data'].get('id'), {}).get('shared_proof')
                                        for event in suffix)
                    state = plain(view) if shared_change else refreshed(previous['state'], view, previous['raw'], raw)
                    if state['event_count'] == events[-1]['seq']:
                        return events, state, raw, True
            except (Error, KeyError, TypeError, ValueError):
                pass
        events = store.events()
        view = store.resolved(store.view(), compact=True)
        cached, state = getattr(view, 'current', None) is not None, plain(view)
        if state['event_count'] != (events[-1]['seq'] if events else 0):
            store.db.execute('BEGIN')
            try:
                events = store.events()
                state = store.state(events, resolve=False)
                state = plain(store.resolved(wait_view(state, state.wait_context), compact=True))
            finally:
                store.db.execute('COMMIT')
            cached = False
        return events, state, store.state(events, resolve=False, with_waits=False), cached

    def hold_parts(self, events, state, stamp, audit, waiting, present, activity_value):
        """Keep this head's wait, presence and activity membership until the first instant one can change."""
        clock = datetime.fromisoformat(stamp).timestamp()
        far = datetime.fromtimestamp(clock + FAR_SECONDS, timezone.utc).isoformat()
        # Unfolded rows: a stand-by row hides the deadlines of the heartbeats it may come to count.
        crossings = [clock + FAR_SECONDS - row['seconds'] + row['threshold']
                     for row in waits(state, far, fold=False) + [r for r in presence(state, far) if r.get('ping')]]
        future = [moment for moment in crossings if moment > clock]
        boundary = (math.floor(clock / ACTIVITY_STEP) + 1) * ACTIVITY_STEP
        with self.reply_lock:
            self.held = ((events[-1]['hash'], json.dumps(audit, sort_keys=True)), min(future + [boundary]),
                         (waiting, present, activity_value))

    def held_parts(self, events, stamp, audit):
        """The held membership while head and audit status are unchanged and nothing has crossed a threshold."""
        with self.reply_lock:
            held = self.held
        if (held is None or held[0] != (events[-1]['hash'], json.dumps(audit, sort_keys=True))
                or datetime.fromisoformat(stamp).timestamp() >= held[1]):
            return None
        return held[2]

    def search_base(self, token, head, audit, plugins_value, held):
        """The landing held for this token when only the query can differ from it: same head, audit and plugins,
        and the wait, presence and activity membership still held, so its clock-bearing rows only aged."""
        with self.reply_lock:
            base = self.replies.get(token) if token else None
        if (base is None or held is None or base['registry_head']['hash'] != head or base['audit'] != audit
                or base['plugins'] != plugins_value
                or transfer_signature(head, '', plugins_value, base['waits'], base['presence'], base['activity'], audit)
                != transfer_signature(head, '', plugins_value, *held, audit)):
            return None
        return base

    def transfer_reply(self, token, revision, landing, stamp):
        """A delta against the client's held reply when the server still has it; otherwise the whole landing."""
        with self.reply_lock:
            base = self.replies.get(token) if token else None
            self.replies = {key: value for key, value in self.replies.items() if key != revision}
            self.replies[revision] = landing
            while len(self.replies) > 2:
                self.replies.pop(next(iter(self.replies)))
        if base is None:
            return json.dumps(landing, ensure_ascii=False).encode()
        return json.dumps({'delta': transfer_delta(base, landing), 'base_token': token, 'change_token': revision,
                           'server_time': stamp, 'registry_head': landing['registry_head']},
                          ensure_ascii=False).encode()

    def record(self, kind, value):
        """Resolve only a requested record, rechecking immutable inventory bytes."""
        return materialize_known({kind: {'record': value}}, self.root / 'artifacts')[kind]['record']

    def reads(self):
        events, _, _ = self.read()
        head = events[-1]['hash'] if events else None
        with self.lock:
            if self.reads_value is None or head != self.reads_head:
                self.reads_value = read_value(events)
                self.reads_head = head
            return self.reads_value

    def close(self):
        with self.audit_lock:
            self.closed = True
            self.audit_pending = None
            if self.audit_cancel is not None:
                self.audit_cancel.set()
            if self.audit_store is not None:
                try:
                    self.audit_store.db.interrupt()
                except sqlite3.ProgrammingError:
                    pass  # The worker can finish closing its owned connection concurrently.
            worker = self.audit_thread
        if worker is not None:
            worker.join(timeout=5)
            if worker.is_alive():
                raise Error('Registry audit cleanup did not finish within 5 seconds')
        with self.lock:
            if self.monitor is not None:
                self.monitor.close()
                self.monitor = None
            self.value = self.marker = None
            self.verified_events = self.base = None
            self.reads_head = self.reads_value = None


def scrubbed_text(decision, sha, lang):
    """The plain answer for an artifact whose file a coordinator scrub decision removed."""
    if lang == "en":
        return ("Scrubbed by decision " + decision + ": this artifact was removed from the registry on purpose. "
                "The record keeps only its digest " + sha + ".")
    return ("Documento rimosso con la decisione " + decision + ": il file è stato tolto di proposito dal registro, "
            "che ne conserva solo l'impronta " + sha + ".")


def make_server(root, port=0):
    token = secrets.token_urlsafe(32)
    assets = Path(__file__).resolve().parents[1] / "assets"
    page = (assets / "dashboard.html").read_bytes()
    # Read the two bundled fonts once; the font route serves only these files.
    fonts = {"/fonts/" + name: (assets / "vendor/fonts" / name).read_bytes()
             for name in ("geist-var-latin.woff2", "fraunces-var-latin.woff2")}
    plugin_home = Path.home()
    usage_roots = usage.source_roots()
    verified_first = None
    view = VerifiedView(root)

    def remember_key(events):
        nonlocal verified_first
        plugins.require(events and events[0]["seq"] == 1 and plugins.KEY.fullmatch(events[0]["hash"]), "registry_invalid")
        verified_first = events[0]
        return verified_first["hash"]

    def plugin_key(store):
        view.audit_info()
        # Reuse the verified key while the first record is unchanged.
        first = store.db.execute("SELECT * FROM events WHERE seq=1").fetchone()
        plugins.require(first is not None, "registry_invalid")
        record = store.decode_event(first)
        if record == verified_first:
            return record["hash"]
        return remember_key(store.events())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def answer(self, status, body, content="application/json; charset=utf-8", headers=None):
            if status not in (401, 403, 405) and urlparse(self.path).path.startswith('/api/'):
                try:
                    view.audit_info()
                except Error:
                    status, body = 409, b'{"error":"registry_invalid"}'
                    content = "application/json; charset=utf-8"
            self.send_response(status)
            self.send_header("Content-Type", content)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; font-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def hosts(self):
            """Loopback names that reach this server; any other Host keeps DNS rebinding out."""
            port = str(self.server.server_port)
            return {"127.0.0.1:" + port, "localhost:" + port, "[::1]:" + port}

        def do_GET(self):
            if self.headers.get_all("Host", []) not in ([host] for host in self.hosts()):
                self.answer(403, b'{"error":"host refused"}'); return
            path = urlparse(self.path).path
            if path == "/":
                self.answer(200, page, "text/html; charset=utf-8"); return
            # A stylesheet's font request cannot carry the token header; the fonts are public, fixed files.
            if path in fonts:
                self.answer(200, fonts[path], "font/woff2"); return
            given = self.headers.get_all("X-Squadra-Token", [])
            if len(given) != 1 or not secrets.compare_digest(given[0].encode("utf-8", "surrogateescape"), token.encode()):
                self.answer(401, b'{"error":"token required"}'); return
            try:
                view.audit_info()
            except Error:
                self.answer(409, b'{"error":"registry_invalid"}')
                return
            if path == "/api/plugin-store":
                try:
                    body = plugin_store.catalogue(plugin_home)
                    self.answer(200, json.dumps(body, ensure_ascii=False).encode())
                except (Error, plugins.Refused, OSError, ValueError, KeyError):
                    self.answer(409, b'{"error":"store_unavailable"}')
                return
            if path == "/api/plugins" or path.startswith("/api/plugin/"):
                self.plugin_answer(path)
                return
            try:
                if path == '/api/reads':
                    self.answer(200, json.dumps(view.reads(), ensure_ascii=False).encode())
                    return
                events, recorded, compact = view.read()
                state = dict(recorded)
                if path == '/api/skills':
                    self.answer(200, json.dumps(system_skills.catalogue(events, recorded), ensure_ascii=False).encode())
                elif path == '/api/forecast':
                    task_id = unquote(self.headers.get('X-Squadra-Record', ''))
                    plugins.require(task_id in recorded['tasks'], 'unknown_task')
                    self.answer(200, json.dumps(forecast.calculate(recorded, events, task_id), ensure_ascii=False).encode())
                elif path == '/api/balance':
                    try:
                        reference = balance.parse_reference(self.headers.get('X-Squadra-Balance-Reference'))
                    except Error:
                        raise plugins.Refused('reference_invalid') from None
                    task_id = unquote(self.headers.get('X-Squadra-Record', '')) or None
                    plugins.require(task_id is None or task_id in recorded['tasks'], 'unknown_task')
                    self.answer(200, json.dumps(balance.calculate(recorded, events, task_id, usage.collect(events, recorded, roots=usage_roots),
                                                                  reference), ensure_ascii=False).encode())
                elif path == '/api/usage':
                    result = usage.response(events, recorded, roots=usage_roots,
                                            revision=self.headers.get('X-Squadra-Usage-Revision'),
                                            compact=self.headers.get('X-Squadra-Usage-View') == 'compact')
                    self.answer(200, json.dumps(result, ensure_ascii=False).encode())
                elif path == "/api/state":
                    key = remember_key(events)
                    audit = view.audit_info()
                    stamp = now()
                    plugin_value = {**plugin_actions.discover(plugin_home, key), "key": key}
                    is_compact = self.headers.get('X-Squadra-View') == 'compact'
                    conditional = is_compact and 'X-Squadra-State-Token' in self.headers
                    query = unquote(self.headers.get('X-Squadra-Search', '')).lower() if is_compact else ''
                    token_held = (self.headers.get('X-Squadra-State-Token')
                                  if conditional and self.headers.get('X-Squadra-Delta') == '1' else None)
                    if conditional:
                        held = view.held_parts(events, stamp, audit)
                        if held is not None and self.headers.get('X-Squadra-State-Token') == transfer_signature(
                                events[-1]['hash'], query, plugin_value, *held, audit):
                            self.answer(200, json.dumps({'not_modified': True, 'change_token': self.headers.get('X-Squadra-State-Token'),
                                                         'server_time': stamp}).encode())
                            return
                        # Only the query changed: the held landing stands, with new results and its own server_time.
                        base = view.search_base(token_held, events[-1]['hash'], audit, plugin_value, held)
                        if base is not None:
                            revision = transfer_signature(events[-1]['hash'], query, plugin_value, *held, audit)
                            landing = {**base, 'change_token': revision, 'search': search_records(recorded, events, query)}
                            self.answer(200, view.transfer_reply(token_held, revision, landing, stamp))
                            return
                    waiting, present = waits(state, stamp), presence(state, stamp)
                    activity_value = activity(events, stamp)
                    if conditional:
                        view.hold_parts(events, state, stamp, audit, waiting, present, activity_value)
                    revision = transfer_signature(events[-1]['hash'], query, plugin_value,
                                                  waiting, present, activity_value, audit) if conditional else None
                    if conditional and self.headers.get('X-Squadra-State-Token') == revision:
                        self.answer(200, json.dumps({'not_modified': True, 'change_token': revision,
                                                     'server_time': stamp}).encode())
                        return
                    if not is_compact:
                        plugins.require(inventory_size(recorded) <= FULL_FILES_LIMIT, 'full_state_too_large')
                        state = materialize_known(recorded, view.root / 'artifacts')
                    if conditional:
                        state = landing_records(recorded, compact)
                        state.update(change_token=revision, server_time=stamp)
                    state["plugins"] = plugin_value
                    recorded_view = attention(recorded)
                    # The page lists waits without the mail to windows in stand-by; its pending ACKs keep that mail.
                    state["attention"] = {**recorded_view, "waiting": fold_recorded(recorded, recorded_view["waiting"], waiting),
                                          "pending_ack": pending_ack_ids(recorded, attention_value=recorded_view)}
                    state["waits"], state["presence"] = waiting, present
                    state["project_started"] = events[0]["utc"]
                    state["events"] = [{**event, 'data': {}, 'detail_pending': True} for event in events[-120:]] if conditional else events[-120:]
                    state["integrity"] = "verified"
                    state['registry_head'] = {'seq': events[-1]['seq'], 'hash': events[-1]['hash']}
                    state['audit'] = audit
                    state["comms"] = communications(recorded, events, waiting, stamp)
                    state["activity"] = activity_value
                    if is_compact:
                        state['search'] = search_records(recorded, events, query)
                        if not conditional:
                            state.update(compact)
                    if conditional:
                        self.answer(200, view.transfer_reply(token_held, revision, state, stamp))
                    else:
                        self.answer(200, json.dumps(state, ensure_ascii=False).encode())
                elif path == '/api/detail/view':
                    if self.headers.get('X-Squadra-State-Head') != events[-1]['hash']:
                        self.answer(409, b'{"error":"state_changed"}')
                        return
                    section = self.headers.get('X-Squadra-Section', '')
                    record = unquote(self.headers.get('X-Squadra-Record', ''))
                    patch = detail_patch(recorded, events, compact, section, record)
                    if patch is None:
                        self.answer(400, b'{"error":"unknown_detail"}')
                        return
                    self.answer(200, json.dumps({'head': events[-1]['hash'], 'section': section,
                                                 'record': record, 'patch': patch}, ensure_ascii=False).encode())
                elif path in ('/api/detail/task', '/api/detail/run'):
                    kind = 'tasks' if path.endswith('/task') else 'runs'
                    record = recorded[kind].get(unquote(self.headers.get('X-Squadra-Record', '')))
                    if record is None:
                        self.answer(404, b'{"error":"record not found"}')
                    else:
                        self.answer(200, json.dumps(view.record(kind, record), ensure_ascii=False).encode())
                elif re.fullmatch(r"/api/artifact/[0-9a-f]{64}", path):
                    sha = path.rsplit("/", 1)[-1]
                    registered = set()
                    def collect(value):
                        if isinstance(value, dict):
                            if {"sha256", "name", "original", "bytes"} <= value.keys():
                                registered.add(value["sha256"])
                            for v in value.values():
                                collect(v)
                        elif isinstance(value, list):
                            for v in value:
                                collect(v)
                    collect(state)
                    collect(events)  # Keep replaced plans and briefs accessible through their recorded events.
                    if sha not in registered:
                        self.answer(404, b'{"error":"unregistered artifact"}'); return
                    path = Path(root) / "artifacts" / sha
                    scrub = None if os.path.lexists(path) else scrub_index(events).get(sha)
                    if scrub:
                        self.answer(200, scrubbed_text(scrub["decision"], sha, recorded["workspace"].get("lang")).encode(),
                                    "text/plain; charset=utf-8", {"X-Squadra-Artifact": "scrubbed"})
                        return
                    body = path.read_bytes()
                    if digest(body) != sha:
                        raise Error("Artifact integrity failed")
                    self.answer(200, body, "text/plain; charset=utf-8")
                else:
                    self.answer(404, b'{"error":"not found"}')
            except plugins.Refused as exc:
                self.answer(409, json.dumps({"error": exc.code}).encode())
            except (Error, OSError, ValueError, sqlite3.Error):
                self.answer(409, b'{"error":"registry_invalid"}')

        def plugin_answer(self, path):
            store = None
            try:
                name = path.removeprefix("/api/plugin/")
                if path != "/api/plugins":
                    plugins.require(plugins.NAME.fullmatch(name), "bad_name")
                store = Store(root, read_only=True)
                key = plugin_key(store)
                body = plugin_actions.discover(plugin_home, key) if path == "/api/plugins" else {
                    "name": name, "key": key, "panel": plugins.load_panel(plugin_home, name, key)}
                self.answer(200, json.dumps(body, ensure_ascii=False).encode())
            except plugins.Refused as exc:
                status = 400 if exc.code == "bad_name" else 404 if exc.code == "not_found" else 409
                self.answer(status, json.dumps({"error": exc.code}).encode())
            except (Error, OSError, ValueError, sqlite3.Error):
                self.answer(409, b'{"error":"registry_invalid"}')
            finally:
                if store is not None:
                    store.close()

        def do_POST(self):
            path = urlparse(self.path).path
            action = re.fullmatch(r'/api/plugin/([^/]+)/action/([^/]+)', path)
            settings = path.startswith('/api/plugin/') and path.endswith('/settings')
            if not settings and action is None:
                self.do_OPTIONS()
                return
            try:
                hosts = self.headers.get_all('Host', [])
                plugins.require(len(hosts) == 1 and hosts[0] in self.hosts(), 'host_refused')
                tokens = self.headers.get_all('X-Squadra-Token', [])
                plugins.require(len(tokens) == 1 and secrets.compare_digest(tokens[0].encode('utf-8'), token.encode()), 'token_required')
                origin = self.headers.get_all('Origin', [])
                plugins.require(not origin or origin == ['http://' + hosts[0]], 'origin_refused')
                types = self.headers.get_all('Content-Type', [])
                plugins.require(len(types) == 1 and re.fullmatch(r'application/json(?:\s*;\s*charset=utf-8)?', types[0], re.I), 'content_type')
                plugins.require(not self.headers.get_all('Transfer-Encoding'), 'invalid_length')
                lengths = self.headers.get_all('Content-Length', [])
                plugins.require(lengths, 'length_required')
                plugins.require(len(lengths) == 1 and re.fullmatch(r'[0-9]{1,10}', lengths[0]), 'invalid_length')
                length = int(lengths[0])
                plugins.require(length > 0, 'invalid_length')
                plugins.require(length <= (plugin_actions.LIMIT if action else plugin_settings.LIMIT), 'too_large')
                deadline = time.monotonic() + SETTINGS_BODY_TIMEOUT
                body = bytearray()
                try:
                    while len(body) < length:
                        remaining = deadline - time.monotonic()
                        plugins.require(remaining > 0, 'body_timeout')
                        self.connection.settimeout(remaining)
                        chunk = self.rfile.read1(length - len(body))
                        plugins.require(chunk, 'invalid_body')
                        body.extend(chunk)
                except TimeoutError:
                    raise plugins.Refused('body_timeout') from None
                finally:
                    self.connection.settimeout(None)
                try:
                    value = json.loads(body.decode('utf-8'), object_pairs_hook=plugins._pairs, parse_constant=plugins._constant)
                except (UnicodeError, ValueError, RecursionError):
                    raise plugins.Refused('invalid_json') from None
                view.audit_info()
                if action is None:
                    plugins._fields(value, ('values',))
                    name = path[len('/api/plugin/'):-len('/settings')]
                    result = {'settings': plugin_settings.save(plugin_home, name, value['values'])}
                else:
                    plugins._fields(value, ('ids',))
                    store = None
                    try:
                        store = Store(root, read_only=True)
                        key = plugin_key(store)
                    except (Error, OSError, ValueError, sqlite3.Error):
                        raise plugins.Refused('registry_invalid') from None
                    finally:
                        if store is not None:
                            store.close()
                    result = {'saved': True, 'request': plugin_actions.save(plugin_home, action[1], action[2], key, value['ids'])}
                self.answer(200, json.dumps(result, ensure_ascii=False).encode())
            except plugins.Refused as exc:
                self.answer(SETTINGS_STATUS.get(exc.code, 409), json.dumps({'error': exc.code}).encode())
            except Error:
                self.answer(409, b'{"error":"registry_invalid"}')
            except OSError:
                # A lost reply may follow a successful save; never echo request or backend details.
                self.close_connection = True

        def do_OPTIONS(self):
            self.answer(405, b'{"error":"read-only except declared plugin settings and saved selections; use tracked CLI commands for other actions"}')

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    close = server.server_close
    def close_view():
        try:
            close()
        finally:
            view.close()
    server.server_close = close_view
    return server, token


def serve(root, port=0, open_browser=False):
    server, token = make_server(root, port)
    url = f"http://127.0.0.1:{server.server_port}/#{token}"
    print("Squadra dash: " + url, flush=True)
    print("Dashboard locale in sola lettura, salvo impostazioni e selezioni dei plugin. Ctrl-C chiude la dashboard, non le finestre.", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
