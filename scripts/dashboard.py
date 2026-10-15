"""Loopback dashboard: saves plugin settings/selections, never executes actions."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import unquote, urlparse
import webbrowser

from registry import (Store, Error, digest, attention, waits, now, requirement_matrix, initial, apply, decision_graph,
                      task_progress, presence, fix_status)
from comms import communications
from panel import activity
from reads import read_value
import plugins
import plugin_settings
import plugin_actions
import plugin_store
import system_skills
import forecast
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
            'id', 'utc', 'status', 'native_rc', 'seconds', 'counts', 'error', 'covers'}}
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


def transfer_signature(head, query, plugins_value, waiting, present, activity_value):
    """Time ages are local; threshold membership and bucket changes are semantic."""
    value = [head, query, plugins_value,
             [(w['kind'], w['id'], w.get('threshold')) for w in waiting],
             [(p['agent'], p['due']) for p in present],
             {k: v for k, v in activity_value.items() if k != 'as_of'}]
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True).encode())


class VerifiedView:
    """Reuse verified data until SQLite or the underlying files change."""
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / 'registry.sqlite3'
        self.lock = threading.Lock()
        self.monitor = None
        self.identity = self.marker = self.value = None
        self.reads_head = self.reads_value = None

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
            before = self.stamp()
            if self.value is not None and before == self.marker:
                return self.value
            self.value = self.marker = None
            store = Store(self.root, read_only=True)
            try:
                events = store.events()
            finally:
                store.close()
            state = initial()
            for event in events:
                apply(state, event)
            state.update(root=str(self.root), graph=decision_graph(events), progress=task_progress(events),
                         requirements={key: requirement_matrix(task) for key, task in state['tasks'].items()})
            for task in state['tasks'].values():
                if task.get('fix'):
                    task['fix_status'] = fix_status(task)
            compact = {key: compact_records(state[key]) for key in ('tasks', 'runs')}
            value = events, state, compact
            after = self.stamp()
            # A concurrent commit may leave a valid older snapshot, but it must not be cached as current.
            if before == after:
                self.marker, self.value = after, value
            return value

    def reads(self):
        events, _, _ = self.read()
        head = events[-1]['hash'] if events else None
        with self.lock:
            if self.reads_value is None or head != self.reads_head:
                self.reads_value = read_value(events)
                self.reads_head = head
            return self.reads_value

    def close(self):
        with self.lock:
            if self.monitor is not None:
                self.monitor.close()
                self.monitor = None
            self.value = self.marker = None
            self.reads_head = self.reads_value = None


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
        # Reuse the verified key while the first record is unchanged.
        first = store.db.execute("SELECT * FROM events WHERE seq=1").fetchone()
        plugins.require(first is not None, "registry_invalid")
        record = dict(first)
        record["data"] = json.loads(record["data"])
        if record == verified_first:
            return record["hash"]
        return remember_key(store.events())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def answer(self, status, body, content="application/json; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", content)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; font-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            expected = "127.0.0.1:" + str(self.server.server_port)
            if self.headers.get("Host") != expected:
                self.answer(403, b'{"error":"host refused"}'); return
            path = urlparse(self.path).path
            if path == "/":
                self.answer(200, page, "text/html; charset=utf-8"); return
            # A stylesheet's font request cannot carry the token header; the fonts are public, fixed files.
            if path in fonts:
                self.answer(200, fonts[path], "font/woff2"); return
            if not secrets.compare_digest(self.headers.get("X-Squadra-Token", ""), token):
                self.answer(401, b'{"error":"token required"}'); return
            if path == "/api/plugin-store":
                try:
                    body = plugin_store.catalogue(plugin_home)
                    self.answer(200, json.dumps(body, ensure_ascii=False).encode())
                except (plugins.Refused, OSError, ValueError, KeyError):
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
                    self.answer(200, json.dumps(forecast.calculate(recorded, events, task_id), ensure_ascii=False).encode())
                elif path == '/api/usage':
                    self.answer(200, json.dumps(usage.collect(events, recorded, roots=usage_roots), ensure_ascii=False).encode())
                elif path == "/api/state":
                    key = remember_key(events)
                    stamp = now()
                    plugin_value = {**plugin_actions.discover(plugin_home, key), "key": key}
                    waiting, present = waits(state, stamp), presence(state, stamp)
                    activity_value = activity(events, stamp)
                    is_compact = self.headers.get('X-Squadra-View') == 'compact'
                    conditional = is_compact and 'X-Squadra-State-Token' in self.headers
                    query = unquote(self.headers.get('X-Squadra-Search', '')).lower() if is_compact else ''
                    revision = transfer_signature(events[-1]['hash'], query, plugin_value,
                                                  waiting, present, activity_value) if conditional else None
                    if conditional and self.headers.get('X-Squadra-State-Token') == revision:
                        self.answer(200, json.dumps({'not_modified': True, 'change_token': revision,
                                                     'server_time': stamp}).encode())
                        return
                    if conditional:
                        state = landing_records(recorded, compact)
                        state.update(change_token=revision, server_time=stamp)
                    state["plugins"] = plugin_value
                    state["attention"] = attention(recorded)
                    state["waits"], state["presence"] = waiting, present
                    state["project_started"] = events[0]["utc"]
                    state["events"] = [{**event, 'data': {}, 'detail_pending': True} for event in events[-120:]] if conditional else events[-120:]
                    state["integrity"] = "verified"
                    state['registry_head'] = {'seq': events[-1]['seq'], 'hash': events[-1]['hash']}
                    state["comms"] = communications(recorded, events, waiting, stamp)
                    state["activity"] = activity_value
                    if is_compact:
                        state['search'] = {'query': query, **{
                            kind: [key for key, record in recorded[kind].items()
                                   if query and query in json.dumps(record, ensure_ascii=False,
                                                                    separators=(',', ':')).lower()]
                            for kind in (('tasks', 'runs', 'messages') if conditional else ('tasks', 'runs'))}}
                        if conditional:
                            state['search']['outputs'] = [output.get('event') for output in recorded['outputs']
                                if query and query in json.dumps(output, ensure_ascii=False).lower()]
                        else:
                            state.update(compact)
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
                        self.answer(200, json.dumps(record, ensure_ascii=False).encode())
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
                    body = (Path(root) / "artifacts" / sha).read_bytes()
                    if digest(body) != sha:
                        raise Error("Artifact integrity failed")
                    self.answer(200, body, "text/plain; charset=utf-8")
                else:
                    self.answer(404, b'{"error":"not found"}')
            except plugins.Refused as exc:
                self.answer(409, json.dumps({"error": exc.code}).encode())
            except (Error, OSError, ValueError, sqlite3.Error) as exc:
                self.answer(409, json.dumps({"error": str(exc)}).encode())

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
                expected = '127.0.0.1:' + str(self.server.server_port)
                plugins.require(self.headers.get_all('Host', []) == [expected], 'host_refused')
                tokens = self.headers.get_all('X-Squadra-Token', [])
                plugins.require(len(tokens) == 1 and secrets.compare_digest(tokens[0].encode('utf-8'), token.encode()), 'token_required')
                origin = self.headers.get_all('Origin', [])
                plugins.require(not origin or origin == ['http://' + expected], 'origin_refused')
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
