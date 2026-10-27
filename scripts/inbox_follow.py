"""Read-only, resumable JSONL mail for windows without a native queue."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import signal
import sqlite3
import time

from registry import Error, Store, attention, require
from registry_current import attached, message_ids
from comms import mail_recipients, mailbox_keys


def command(root, agents, *extra):
    argv = ['squadra', '--root', str(root), 'inbox']
    for agent in agents:
        argv.extend(['--agent', agent])
    return shlex.join([*argv, *extra])


def message_line(root, message, event):
    needed = message.get('info') is not True and message.get('ack') is None and not message.get('replaced_by')
    first = message['body'][:160].splitlines()
    return {'kind': 'message', 'event': event, 'from': message['from'],
            'to': message['to'], 'task': message.get('task') or message.get('related_task'),
            'id': message['id'], 'info': message.get('info') is True, 'ack_needed': needed,
            'preview': first[0] if first else '',
            'ack_command': shlex.join(['squadra', '--root', str(root), 'ack', '--actor',
                                       message['to'], '--message', message['id']]) if needed else None}


def mail_events(events):
    """Compound replacement events contain ordinary mail and registration operations."""
    for event in events:
        if event['kind'] == 'replace':
            yield from mail_events([{**event, **operation} for operation in event['data']['operations']])
        else:
            yield event


class Follower:
    """A poll returns detached lines; advance only after their successful flush.

    The first poll reads a verified view. A later poll on a changed head decodes only the events
    after the last verified head, whose hash anchors their chain, and opens a view only for named mail."""
    def __init__(self, store, agents, since=None, *, pending_on_start=False):
        self.store, self.requested = store, tuple(dict.fromkeys(agents))
        self.agents = set(self.requested)
        self.pending_on_start = pending_on_start
        self.since, self.cursor, self.head = since, since or 0, None
        self.first, self.printed, self.anchor = True, False, None

    def poll(self):
        tip = self.store.db.execute('SELECT seq,hash FROM events ORDER BY seq DESC LIMIT 1').fetchone()
        tip = (tip['seq'], tip['hash']) if tip else (0, '0' * 64)
        if tip == self.head:
            return None
        if self.anchor is not None:
            return self.arrivals(tip)
        try:
            state = self.store.view()
            self.agents = mail_recipients(state, self.requested)
            end, index = state['event_count'], attached(state)
            require(end >= self.cursor, '--since exceeds the verified registry head')
            if index is None:
                orders = {e['data']['id']: e['seq'] for e in mail_events(self.store.events())
                          if e['kind'] == 'message' and e['seq'] <= end}
            else:
                orders = index['message_order']
            backlog = self.first and self.since is None
            if backlog:
                keys = set()
                for wait in attention(state)['waiting']:
                    if wait['kind'] == 'ack' and set(wait['who']).intersection(self.agents):
                        keys.update(wait.get('handoffs', [wait['id']]))
                keys = [k for k in keys if state['messages'][k]['to'] in self.agents]
            elif index is not None:
                keys = {key for agent in self.agents for key in message_ids(index, recipient=agent)
                        if orders[key] > self.cursor}
            else:
                keys = [key for key, m in state['messages'].items()
                        if m['to'] in self.agents and orders[key] > self.cursor]
            if not backlog:
                recent = [e for e in mail_events(self.store.events_since(*self.anchor_at(self.cursor)))
                          if e['seq'] <= end]
                registrations = self.changed_registrations(recent, self.cursor, self.agents)
                if registrations:
                    orders, keys = dict(orders), set(keys)
                    for key, message in state['messages'].items():
                        registered = registrations.get(message['to'])
                        if (registered is not None and message.get('ack') is None and
                                message.get('info') is not True and not message.get('replaced_by')):
                            keys.add(key)
                            orders[key] = max(orders[key], registered)
            keys = sorted(keys, key=lambda key: (orders[key], key))
            rows = []
            if self.first and not backlog and self.pending_on_start:
                pending = [k for k, m in state['messages'].items() if m['to'] in self.agents and
                           m.get('info') is not True and m.get('ack') is None and not m.get('replaced_by')]
                rows.append({'kind': 'pending', 'event': self.cursor, 'count': len(pending),
                             'ids': pending, 'text': f'{len(pending)} messages still wait for your ACK'})
            if backlog:
                rows.append({'kind': 'pending', 'event': 0, 'count': len(keys),
                             'text': f'{len(keys)} messages wait for your ACK'})
            shown = keys[:10] if backlog else keys
            # A sequence cursor cannot split the messages of one atomic event.
            if backlog and shown:
                shown += [key for key in keys[len(shown):] if orders[key] == orders[shown[-1]]]
            for key in shown:
                rows.append(message_line(self.store.root, state['messages'][key], orders[key]))
            if backlog and len(keys) > len(shown):
                rest = len(keys) - len(shown)
                inbox = command(self.store.root, sorted(self.agents))
                rows.append({'kind': 'more', 'event': rows[-1]['event'], 'count': rest,
                             'inbox_command': inbox, 'text': f'{rest} more: {inbox}'})
            if backlog or keys or (self.first and self.pending_on_start):
                rows.append({'kind': 'cursor', 'event': end})
            return tip, end, rows, self.anchor_at(end)
        finally:
            self.store.release_views()

    def changed_registrations(self, recent, since, names):
        candidates = {e['data']['id'] for e in recent
                      if e['kind'] == 'agent' and e['data']['id'] in names}
        if not candidates:
            return {}
        previous, changed = {}, {}
        end = recent[-1]['seq']
        # Compare bindings at the saved revision; a repeated registration is not an arrival.
        for event in mail_events(self.store.events()):
            if event['seq'] > end:
                break
            if event['kind'] != 'agent' or event['data']['id'] not in candidates:
                continue
            seat = event['data']
            binding = tuple(seat.get(key) or '' for key in ('identity', 'role', 'client', 'session'))
            if event['seq'] > since and previous.get(seat['id']) != binding:
                changed[seat['id']] = event['seq']
            previous[seat['id']] = binding
        return changed

    def anchor_at(self, seq):
        if not seq:
            return 0, '0' * 64
        row = self.store.db.execute('SELECT hash FROM events WHERE seq=?', (seq,)).fetchone()
        require(row is not None, 'Event anchor changed')
        return seq, row['hash']

    def arrivals(self, tip):
        """Rows for the named mail among the events after the anchor, chain-verified by events_since."""
        events = list(mail_events(self.store.events_since(*self.anchor)))
        anchor = (events[-1]['seq'], events[-1]['hash']) if events else self.anchor
        orders = {e['data']['id']: e['seq'] for e in events
                  if e['kind'] == 'message' and e['data']['to'] in self.agents}
        rows = []
        membership = any(e['kind'] == 'agent' for e in events)
        if orders or membership:
            try:
                state = self.store.view()
                require(state['event_count'] >= anchor[0], 'Registry view is behind its verified events')
                names = mail_recipients(state, self.requested)
                registrations = self.changed_registrations(events, self.anchor[0], names)
                self.agents = names
                orders = {e['data']['id']: e['seq'] for e in events
                          if e['kind'] == 'message' and e['data']['to'] in names}
                # A new role or session exposes pending mail, with its real recipient intact.
                index = attached(state)
                created = (index['message_order'] if index is not None else {
                    e['data']['id']: e['seq'] for e in mail_events(self.store.events()) if e['kind'] == 'message'}) if registrations else {}
                for key, message in state['messages'].items() if registrations else ():
                    registered = registrations.get(message['to'])
                    if (created[key] <= anchor[0] and registered is not None and message.get('ack') is None and
                            message.get('info') is not True and not message.get('replaced_by')):
                        orders[key] = max(created[key], registered)
                rows = [message_line(self.store.root, state['messages'][key], event)
                        for key, event in sorted(orders.items(), key=lambda item: item[1])]
            finally:
                self.store.release_views()
            if rows:
                rows.append({'kind': 'cursor', 'event': anchor[0]})
        return tip, anchor[0], rows, anchor

    def emit(self, batch):
        if batch is None:
            return
        tip, end, rows, anchor = batch
        for row in rows:
            print(json.dumps(row, ensure_ascii=False), flush=True)
            if row['kind'] == 'message':
                self.printed = True
            self.cursor = max(self.cursor, row['event'])
        self.head, self.cursor, self.first, self.anchor = tip, end, False, anchor


def cursor_path(root, agents):
    from registry import canonical, digest
    key = digest(canonical({'root': str(Path(root).resolve()), 'agents': sorted(set(agents))}).encode())
    return Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'squadra/inbox' / (key + '.json')


def load_cursor(path, store, agents):
    if not path.exists():
        return None
    require(not path.is_symlink() and path.stat().st_size < 8192, 'Invalid inbox cursor file')
    try:
        saved = json.loads(path.read_bytes().decode('utf-8'))
    except (UnicodeError, ValueError, RecursionError):
        saved = None
    # The shape save_cursor writes; anything else is one plain refusal, not a traceback.
    require(isinstance(saved, dict) and isinstance(saved.get('root'), str) and isinstance(saved.get('agents'), list)
            and isinstance(saved.get('hash'), str), 'Invalid inbox cursor file')
    require(all(isinstance(name, str) for name in saved['agents']), 'Invalid inbox cursor file')
    recipients = saved['agents']
    if recipients != sorted(set(agents)):
        try:
            recipients = mailbox_keys(store.view(), recipients)
        finally:
            store.release_views()
    require(saved.get('root') == str(store.root) and recipients == sorted(set(agents)),
            'Inbox cursor belongs to another registry or recipient')
    seq = saved.get('event')
    require(type(seq) is int and seq >= 0, 'Invalid inbox cursor event')
    row = store.db.execute('SELECT hash FROM events WHERE seq=?', (seq,)).fetchone()
    require((row['hash'] if row else '0' * 64 if seq == 0 else None) == saved.get('hash'),
            'Inbox cursor does not match this registry history')
    return seq


def save_cursor(path, follower, agents):
    from registry import atomic, canonical
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(not path.is_symlink(), 'Inbox cursor cannot be a symlink')
    seq, sha = follower.anchor_at(follower.cursor)
    value = {'root': str(follower.store.root), 'agents': sorted(set(agents)), 'event': seq, 'hash': sha}
    atomic(path, (canonical(value) + '\n').encode())
    path.chmod(0o600)


def follow(root, agents, timeout=None, since=None, *, until_first=False, cursor_file=None, persist=True):
    """Follow until the timeout, a signal or, with until_first, the end of the first batch that showed mail."""
    deadline = time.monotonic() + timeout if timeout is not None else None
    store = None
    previous = signal.signal(signal.SIGTERM, _interrupt)
    try:
        store = Store(root, read_only=True)
        keys = mailbox_keys(store.view(), agents)
        store.release_views()
        path = Path(cursor_file).absolute() if cursor_file else cursor_path(root, keys)
        resumed = False
        if persist and since is None:
            legacy = cursor_path(root, agents)
            source = legacy if not cursor_file and not path.exists() and legacy.exists() else path
            since = load_cursor(source, store, keys)
            resumed = since is not None
        follower = Follower(store, agents, since, pending_on_start=resumed)
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                break
            started = time.monotonic()
            batch = follower.poll()
            follower.emit(batch)
            if persist and batch is not None:
                # A bounded initial backlog must not skip messages that were not emitted.
                more = next((row for row in batch[2] if row['kind'] == 'more'), None)
                if more:
                    follower.cursor = more['event']
                    follower.anchor = follower.anchor_at(follower.cursor)
                    follower.head = None
                save_cursor(path, follower, keys)
            if until_first and follower.printed:
                return 0
            delay = max(0, 2 - (time.monotonic() - started))
            if deadline is not None:
                delay = min(delay, max(0, deadline - time.monotonic()))
            time.sleep(delay)
        if follower.printed:
            print(json.dumps({'kind': 'cursor', 'event': follower.cursor}), flush=True)
        return 0
    except KeyboardInterrupt:
        return 0
    except sqlite3.Error as exc:
        raise Error('inbox follow registry error: ' + str(exc)) from exc
    finally:
        if store is not None:
            store.close()
        signal.signal(signal.SIGTERM, previous)


def _interrupt(signum, frame):
    raise KeyboardInterrupt
