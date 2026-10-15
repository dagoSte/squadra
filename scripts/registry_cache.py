"""Authenticated, transactional projections of the registry reducer's JSON state."""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from collections.abc import MutableMapping, MutableSequence

from registry_radix import Index, IntegrityError, create_schema

SCHEMA = 5
MISSING = object()


def canonical(value):
    return json.dumps(plain(value), ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def plain(value):
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def namespace(path):
    return 'state:' + hashlib.sha256(canonical(path).encode()).hexdigest()


class Tree:
    """One SQLite snapshot; all node roots are reached through its bound root."""
    def __init__(self, db, descriptor, writable=False, owns_connection=False, domain=""):
        self.db, self.writable = db, writable
        self.domain = domain
        self.current_tree = None
        self.owns_connection = owns_connection
        self.nodes = {}
        self.recover_callback = None
        self.recovered = False
        self.recovery_db = None
        self.root = self.open((), descriptor)

    def ns(self, path):
        return (self.domain + ":" if self.domain else "") + namespace(path)

    def open(self, path, descriptor):
        if not isinstance(descriptor, dict) or set(descriptor) != {'type', 'root', 'next'}:
            raise IntegrityError('Invalid container descriptor')
        if descriptor['type'] not in {'dict', 'list'} or type(descriptor['next']) is not int or descriptor['next'] < 0:
            raise IntegrityError('Invalid container metadata')
        key = tuple(path)
        if key not in self.nodes:
            cls = Map if descriptor['type'] == 'dict' else Sequence
            self.nodes[key] = cls(self, key, copy.deepcopy(descriptor))
        elif self.nodes[key].descriptor != descriptor and not self.nodes[key].dirty:
            raise IntegrityError('Conflicting container roots')
        return self.nodes[key]

    def recover(self):
        if self.recover_callback is None or self.recovered:
            raise IntegrityError('Derived cache recovery unavailable')
        self.recovered = True
        state = self.recover_callback()
        memory = sqlite3.connect(':memory:', isolation_level=None)
        memory.row_factory = sqlite3.Row
        replacement = Tree.build(memory, state, domain=self.domain)
        self.recovery_db = memory
        old_nodes = self.nodes
        self.db = memory
        self.nodes = replacement.nodes
        for path, node in list(self.nodes.items()):
            node.tree = self
            if path in old_nodes:
                old_nodes[path].setup(self, path, copy.deepcopy(node.descriptor))
                self.nodes[path] = old_nodes[path]
        self.root = self.nodes[()]
        self.writable = False

    def close(self):
        if self.recovery_db is not None:
            self.recovery_db.close()
            self.recovery_db = None
        if self.owns_connection and self.db is not None:
            self.db.close()
            self.db = None

    def __del__(self):
        self.close()

    def flush(self):
        for path in sorted(self.nodes, key=len, reverse=True):
            node = self.nodes[path]
            if node.dirty and path:
                parent = self.nodes[path[:-1]]
                parent.replace_descriptor(path[-1], node.descriptor)
                node.dirty = False
        return copy.deepcopy(self.root.descriptor)

    @classmethod
    def build(cls, db, value, domain=""):
        create_schema(db)
        empty = Index(db, (domain + ":" if domain else "") + namespace(())).root()
        tree = cls(db, {'type': 'dict', 'root': empty, 'next': 0}, writable=True, domain=domain)
        tree.root.update(value)
        tree.flush()
        return tree


class Node:
    def setup(self, tree, path, descriptor):
        self.tree, self.path, self.descriptor = tree, path, descriptor
        self.index = Index(tree.db, tree.ns(path))
        self.loaded = {}
        self.dirty = False

    def unordered(self):
        return self.tree.domain == 'current' and self.descriptor['type'] == 'dict'

    def read(self, key):
        if key not in self.loaded:
            try:
                self.loaded[key] = self.index.get(str(key), self.descriptor['root'], MISSING)
            except IntegrityError:
                self.tree.recover()
                self.loaded[key] = self.index.get(str(key), self.descriptor['root'], MISSING)
        return self.loaded[key]

    def decode(self, key, row):
        if row is MISSING:
            raise KeyError(key)
        if not isinstance(row, dict) or set(row) != {'order', 'value'}:
            raise IntegrityError('Invalid state entry')
        if ((self.unordered() and row['order'] != str(key)) or
                (not self.unordered() and (type(row['order']) is not int or row['order'] < 0))):
            raise IntegrityError('Invalid state entry order')
        data = row['value']
        if not isinstance(data, dict) or len(data) != 1:
            raise IntegrityError('Invalid state value')
        if 'atomic' in data:
            return copy.deepcopy(data['atomic'])
        if 'scalar' in data:
            if isinstance(data['scalar'], (dict, list)):
                raise IntegrityError('Unnormalized state container')
            return data['scalar']
        if 'container' not in data:
            raise IntegrityError('Unknown state value')
        return self.tree.open((*self.path, str(key)), data['container'])

    def write_row(self, key, row):
        if not self.tree.writable:
            raise TypeError('Read-only state view')
        self.descriptor['root'] = self.index.set(str(key), row, self.descriptor['root'])
        self.loaded[key] = row
        self.dirty = True

    def replace_descriptor(self, key, descriptor):
        row = copy.deepcopy(self.read(key))
        row['value'] = {'container': copy.deepcopy(descriptor)}
        self.write_row(key, row)

    def remove(self, key):
        old = self.read(key)
        if old is MISSING:
            raise KeyError(key)
        if not self.tree.writable:
            raise TypeError('Read-only state view')
        value = self.decode(key, old)
        if isinstance(value, Node):
            value.clear_tree()
        self.descriptor['root'] = self.index.delete(str(key), self.descriptor['root'])
        self.loaded[key] = MISSING
        self.dirty = True

    def clear_tree(self):
        for key, row in list(self.entries()):
            value = self.decode(key, row)
            if isinstance(value, Node):
                value.clear_tree()
            self.descriptor['root'] = self.index.delete(str(key), self.descriptor['root'])
        self.loaded.clear()
        self.tree.nodes.pop(self.path, None)

    def set_value(self, key, value):
        # Detach aliases: the reducer may copy a run into a task proof history.
        value = plain(value)
        old = self.read(key)
        if old is not MISSING:
            previous = self.decode(key, old)
            if plain(previous) == value:
                return
            if isinstance(previous, Node):
                previous.clear_tree()
        order = str(key) if self.unordered() else old['order'] if old is not MISSING else self.descriptor['next']
        if old is MISSING:
            self.descriptor['next'] += 1
        path = (*self.path, str(key))
        atomic = (isinstance(value, dict) and (
            {'repo', 'head', 'sha256'} <= value.keys() or
            {'name', 'original', 'bytes', 'sha256'} <= value.keys() or
            key == 'packet_data' or
            (len(path) == 2 and path[0] in {'outputs', 'decisions', 'saves', 'restores', 'challenges', 'benchmarks', 'lesson_recalls'}) or
            (len(path) == 4 and path[0] == 'tasks' and path[2] in {'proofs', 'reviews', 'route_history', 'fix_history', 'gate_deferrals'})))
        if atomic:
            encoded = {'atomic': value}
        elif isinstance(value, (dict, list)):
            empty = Index(self.tree.db, self.tree.ns(path)).root()
            desc = {'type': 'dict' if isinstance(value, dict) else 'list', 'root': empty, 'next': 0}
            child = self.tree.open(path, desc)
            if isinstance(value, dict):
                child.update(value)
            else:
                child.extend(value)
            encoded = {'container': copy.deepcopy(child.descriptor)}
        else:
            encoded = {'scalar': value}
        self.write_row(key, {'order': order, 'value': encoded})

    def entries(self):
        try:
            rows = list(self.index.items(self.descriptor['root']))
        except IntegrityError:
            self.tree.recover()
            rows = list(self.index.items(self.descriptor['root']))
        if self.unordered():
            if self.descriptor['next'] != len(rows) or any(not isinstance(row, dict) or row.get('order') != key for key, row in rows):
                raise IntegrityError('Invalid index ordering')
        else:
            orders = [row['order'] for _, row in rows if isinstance(row, dict) and type(row.get('order')) is int]
            if len(orders) != len(rows) or len(set(orders)) != len(rows) or any(x >= self.descriptor['next'] for x in orders):
                raise IntegrityError('Invalid state ordering')
        for key, row in sorted(rows, key=lambda item: item[1]['order']):
            self.loaded[key] = row
            yield key, row

    def __len__(self):
        return self.descriptor['root']['count']

    def __bool__(self):
        return bool(len(self))

    def __eq__(self, other):
        return plain(self) == plain(other)

    def __ne__(self, other):
        return not self == other

    def __repr__(self):
        return repr(plain(self))

    def __deepcopy__(self, memo):
        return copy.deepcopy(plain(self), memo)


class Map(Node, dict):
    def __init__(self, tree, path, descriptor):
        dict.__init__(self)
        self.setup(tree, path, descriptor)

    def __getitem__(self, key):
        if not isinstance(key, str):
            raise KeyError(key)
        return self.decode(key, self.read(key))

    def __setitem__(self, key, value):
        if not isinstance(key, str):
            raise TypeError('JSON state keys must be strings')
        if not self.path and not self.tree.domain and key != 'root' and key not in self and 'root' in self:
            root = self['root']
            del self['root']
            self.set_value(key, value)
            self.set_value('root', root)
        else:
            self.set_value(key, value)

    def __delitem__(self, key):
        self.remove(key)
        if not self.unordered():
            for order, (name, row) in enumerate(list(self.entries())):
                if row['order'] != order:
                    self.write_row(name, {**row, 'order': order})
        self.descriptor['next'] = len(self)
        self.dirty = True

    def __contains__(self, key):
        return isinstance(key, str) and self.read(key) is not MISSING

    def __iter__(self):
        return (key for key, _ in self.entries())

    def keys(self):
        return list(iter(self))

    def values(self):
        return (self.decode(key, row) for key, row in self.entries())

    def items(self):
        return ((key, self.decode(key, row)) for key, row in self.entries())

    def get(self, key, default=None):
        return self[key] if key in self else default

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]

    def pop(self, key, *default):
        if key not in self:
            if default:
                return default[0]
            raise KeyError(key)
        value = plain(self[key])
        del self[key]
        return value

    def update(self, other=(), **kwargs):
        for key, value in (other.items() if hasattr(other, 'items') else other):
            self[key] = value
        for key, value in kwargs.items():
            self[key] = value

    def copy(self):
        return dict(self.items())


class Sequence(Node, list):
    def __init__(self, tree, path, descriptor):
        list.__init__(self)
        self.setup(tree, path, descriptor)

    def __iter__(self):
        return (self[i] for i in range(len(self)))

    def __reversed__(self):
        return (self[i] for i in range(len(self) - 1, -1, -1))

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        try:
            return self.decode(str(index), self.read(str(index)))
        except KeyError as exc:
            raise IntegrityError('Missing sequence item') from exc

    def __setitem__(self, index, value):
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        self.set_value(str(index), value)

    def append(self, value):
        self.set_value(str(len(self)), value)

    def extend(self, values):
        for value in values:
            self.append(value)

    def __contains__(self, value):
        return any(item == value for item in self)

    def count(self, value):
        return sum(item == value for item in self)

    def copy(self):
        return list(self)

    def __add__(self, other):
        return list(self) + list(other)

    def __radd__(self, other):
        return list(other) + list(self)


def metadata_digest(record):
    return hashlib.sha256(canonical(record).encode()).hexdigest()


def initialize(db):
    create_schema(db)
    db.execute('CREATE TABLE IF NOT EXISTS derived_state_meta (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL, digest TEXT NOT NULL)')


def load(store, writable=False):
    """Validate the anchor and replay its verified suffix in the same transaction."""
    row = store.db.execute('SELECT body,digest FROM derived_state_meta WHERE id=1').fetchone()
    if not row:
        raise IntegrityError('Missing derived anchor')
    meta = json.loads(row['body'])
    if metadata_digest(meta) != row['digest'] or meta.get('schema') != SCHEMA or meta.get('registry') != str(store.root):
        raise IntegrityError('Foreign or invalid derived anchor')
    seq, head = meta.get('seq'), meta.get('head')
    if type(seq) is not int or seq < 0:
        raise IntegrityError('Invalid sequence anchor')
    first = store.db.execute('SELECT hash FROM events ORDER BY seq LIMIT 1').fetchone()
    if meta.get('first') != (first['hash'] if first else None):
        raise IntegrityError('Registry identity changed')
    if seq:
        anchor = store.db.execute('SELECT * FROM events WHERE seq=?', (seq,)).fetchone()
        if not anchor or anchor['hash'] != head:
            raise IntegrityError('Missing or moved anchor')
        store.decode_event(anchor)
    elif head != '0' * 64:
        raise IntegrityError('Invalid empty anchor')
    tree = Tree(store.db, meta['state'], writable=writable)
    tree.current_tree = Tree(store.db, meta['current'], writable=writable, domain='current')
    tree.root.current = tree.current_tree.root
    return tree, meta


def save(store, tree, seq, head):
    first = store.db.execute('SELECT hash FROM events ORDER BY seq LIMIT 1').fetchone()
    record = {'schema': SCHEMA, 'registry': str(store.root), 'first': first['hash'] if first else None,
              'seq': seq, 'head': head, 'state': tree.flush(), 'current': tree.current_tree.flush()}
    store.db.execute('INSERT OR REPLACE INTO derived_state_meta(id,body,digest) VALUES(1,?,?)',
                     (canonical(record), metadata_digest(record)))
    return record
