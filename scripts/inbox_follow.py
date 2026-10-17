"""Read-only, resumable JSONL mail for windows without a native queue."""
from __future__ import annotations

import json
import shlex
import signal
import sqlite3
import time

from registry import Error, Store, attention, require
from registry_current import attached, message_ids


def command(root, agents, *extra):
    argv = ['squadra', '--root', str(root), 'inbox']
    for agent in agents:
        argv.extend(['--agent', agent])
    return shlex.join([*argv, *extra])


def message_line(root, message, event):
    needed = message.get('info') is not True and message.get('ack') is None
    first = message['body'][:160].splitlines()
    return {'kind': 'message', 'event': event, 'from': message['from'],
            'to': message['to'], 'task': message.get('task') or message.get('related_task'),
            'id': message['id'], 'info': message.get('info') is True, 'ack_needed': needed,
            'preview': first[0] if first else '',
            'ack_command': shlex.join(['squadra', '--root', str(root), 'ack', '--actor',
                                       message['to'], '--message', message['id']]) if needed else None}


class Follower:
    """A poll returns detached lines; advance only after their successful flush.

    The first poll reads a verified view. A later poll on a changed head decodes only the events
    after the last verified head, whose hash anchors their chain, and opens a view only for named mail."""
    def __init__(self, store, agents, since=None):
        self.store, self.agents = store, tuple(dict.fromkeys(agents))
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
            end, index = state['event_count'], attached(state)
            require(end >= self.cursor, '--since exceeds the verified registry head')
            if index is None:
                orders = {e['data']['id']: e['seq'] for e in self.store.events()
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
            keys = sorted(keys, key=lambda key: orders[key])
            rows = []
            if backlog:
                rows.append({'kind': 'pending', 'event': 0, 'count': len(keys),
                             'text': f'{len(keys)} messages wait for your ACK'})
            for key in keys[:10] if backlog else keys:
                rows.append(message_line(self.store.root, state['messages'][key], orders[key]))
            if backlog and len(keys) > 10:
                rest = len(keys) - 10
                inbox = command(self.store.root, self.agents)
                rows.append({'kind': 'more', 'event': rows[-1]['event'], 'count': rest,
                             'inbox_command': inbox, 'text': f'{rest} more: {inbox}'})
            if backlog or keys:
                rows.append({'kind': 'cursor', 'event': end})
            return tip, end, rows, self.anchor_at(end)
        finally:
            self.store.release_views()

    def anchor_at(self, seq):
        if not seq:
            return 0, '0' * 64
        row = self.store.db.execute('SELECT hash FROM events WHERE seq=?', (seq,)).fetchone()
        require(row is not None, 'Event anchor changed')
        return seq, row['hash']

    def arrivals(self, tip):
        """Rows for the named mail among the events after the anchor, chain-verified by events_since."""
        events = self.store.events_since(*self.anchor)
        anchor = (events[-1]['seq'], events[-1]['hash']) if events else self.anchor
        orders = {e['data']['id']: e['seq'] for e in events
                  if e['kind'] == 'message' and e['data']['to'] in self.agents}
        rows = []
        if orders:
            try:
                state = self.store.view()
                require(state['event_count'] >= anchor[0], 'Registry view is behind its verified events')
                rows = [message_line(self.store.root, state['messages'][key], event) for key, event in orders.items()]
            finally:
                self.store.release_views()
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


def follow(root, agents, timeout=None, since=None, *, until_first=False):
    """Follow until the timeout, a signal or, with until_first, the end of the first batch that showed mail."""
    deadline = time.monotonic() + timeout if timeout is not None else None
    store = None
    previous = signal.signal(signal.SIGTERM, _interrupt)
    try:
        store = Store(root, read_only=True)
        follower = Follower(store, agents, since)
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                break
            started = time.monotonic()
            follower.emit(follower.poll())
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
