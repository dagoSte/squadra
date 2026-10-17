"""Prepare authenticated derived tables without owning the journal write lock."""
import os
from pathlib import Path
import sqlite3
import tempfile

import registry_cache as cache
from registry_radix import IntegrityError


class StalePreparation(Exception):
    pass


def identity(store):
    if store.path.is_symlink():
        raise IntegrityError('Registry symlink refused')
    info = store.path.stat()
    return info.st_dev, info.st_ino


def position(store):
    row = store.db.execute('SELECT seq,hash FROM events ORDER BY seq DESC LIMIT 1').fetchone()
    return (row['seq'], row['hash']) if row else (0, '0' * 64)


def saved_registry(root):
    """Exact checkpoint marker, or the registry member of a real save layout."""
    if 'CHECKSUMS.json' in os.listdir(root):
        return True
    names = set(os.listdir(root.parent))
    return ({'MANIFEST.json', 'CHECKSUMS.json', 'registry'} <= names and
            root.samefile(root.parent / 'registry'))


def scratch_parent(store):
    for candidate in dict.fromkeys((tempfile.gettempdir(), '/tmp')):
        folder = Path(candidate).resolve()
        if not folder.is_dir():
            continue
        try:
            for parent in (folder, *folder.parents):
                names = set(os.listdir(parent))
                if (parent == store.root or (parent / 'registry.sqlite3').exists() or
                        {'MANIFEST.json', 'CHECKSUMS.json'} <= names or (parent / '.git').exists()):
                    break
            else:
                return folder
        except OSError:
            continue
    raise IntegrityError('An external private temporary folder is required')


class PreparedCache:
    def __init__(self, store):
        import registry_current
        from registry import Store, apply, initial
        self.folder = tempfile.TemporaryDirectory(prefix='squadra-cache-', dir=scratch_parent(store))
        self.db = None
        self.attached = None
        try:
            os.chmod(self.folder.name, 0o700)
            path = self.path = Path(self.folder.name) / 'derived.sqlite3'
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            os.close(descriptor)
            self.db = sqlite3.connect(path, isolation_level=None)
            self.db.row_factory = sqlite3.Row
            self.identity = identity(store)
            self.version = store.db.execute('PRAGMA data_version').fetchone()[0]
            reader = Store(store.root, read_only=True)
            try:
                reader.db.execute('BEGIN')
                events = reader.events()
                normalized = reader.normalized_state(events, store.artifact_bytes)
                cache.initialize(self.db)
                self.db.execute('BEGIN')
                tree = cache.Tree.build(self.db, normalized)
                tree.current_tree = cache.Tree.build(self.db,
                    registry_current.build(events, apply, initial), domain='current')
                bound = type('Binding', (), {'db': self.db, 'root': store.root})()
                last = events[-1] if events else {'seq': 0, 'hash': '0' * 64}
                self.meta = cache.save(bound, tree, last['seq'], last['hash'],
                                       first=events[0]['hash'] if events else None)
                self.db.execute('COMMIT')
            finally:
                reader.close()
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.attached is not None:
            self.attached.db.execute('DETACH DATABASE prepared_cache')
            self.attached = None
        if self.db is not None:
            self.db.close()
            self.db = None
        self.folder.cleanup()

    def attach(self, store):
        if self.attached is None:
            store.db.execute('ATTACH DATABASE ? AS prepared_cache', (str(self.path),))
            self.attached = store

    def install(self, store):
        self.attach(store)
        if identity(store) != self.identity:
            raise StalePreparation('Registry file changed during cache preparation')
        tip = position(store)
        prepared = self.meta['seq'], self.meta['head']
        if tip == prepared and store.db.execute('PRAGMA data_version').fetchone()[0] != self.version:
            raise StalePreparation('Database changed without an appended suffix')
        suffix = store.events_since(*prepared)
        if len(suffix) > 128 or sum(len(cache.canonical(e).encode()) for e in suffix) > 8 * 1024 * 1024:
            raise StalePreparation('Appended suffix needs preparation outside the write lock')
        cache.initialize(store.db)
        for table, columns in (
                ('derived_radix_nodes', 'namespace,digest,data'),
                ('derived_radix_roots', 'namespace,digest,count'),
                ('derived_state_meta', 'id,body,digest')):
            store.db.execute('DELETE FROM main.' + table)
            store.db.execute('INSERT INTO main.' + table + '(' + columns + ') SELECT ' +
                             columns + ' FROM prepared_cache.' + table)


def cache_presence(store):
    """Missing/foreign hints are unavailable; compatible corrupt claims are mismatches."""
    import json
    try:
        row = store.db.execute('SELECT body,digest FROM derived_state_meta WHERE id=1').fetchone()
    except sqlite3.OperationalError as exc:
        if 'no such table' in str(exc):
            return 'unavailable'
        raise
    if row is None:
        return 'unavailable'
    try:
        meta = json.loads(row['body'])
        if not isinstance(meta, dict) or cache.metadata_digest(meta) != row['digest']:
            return 'mismatch'
        if meta.get('schema') != cache.SCHEMA or meta.get('registry') != str(store.root):
            return 'unavailable'
        return 'mismatch'
    except (ValueError, TypeError):
        return 'mismatch'


def quick(store):
    from registry import Error, wait_view, wait_facts
    if store._cache_mismatch:
        raise Error('Cache mismatch; run squadra doctor --repair')
    try:
        tree, meta = cache.load(store)
        suffix = store.events_since(meta['seq'], meta['head'])
        if suffix:
            state = store.state(resolve=False)
            state = wait_view(state, state.wait_context)
            status = 'unavailable'
        else:
            rows = store.db.execute("SELECT * FROM events WHERE kind IN ('transport','plan','settings') AND seq<=? ORDER BY seq",
                                    (meta['seq'],))
            state = wait_view(tree.root, wait_facts(store.decode_event(row) for row in rows))
            status = 'authenticated'
        return {'events': position(store)[0], 'state': state, 'cache': status}
    except sqlite3.DatabaseError as exc:
        if 'no such table' not in str(exc):
            raise
        state = store.state(resolve=False)
        return {'events': state['event_count'], 'state': wait_view(state, state.wait_context),
                'cache': 'unavailable'}
    except (IntegrityError, ValueError, KeyError, TypeError):
        if cache_presence(store) == 'mismatch':
            raise Error('Cache mismatch; run squadra doctor, then squadra doctor --repair')
        state = store.state(resolve=False)
        return {'events': state['event_count'], 'state': wait_view(state, state.wait_context),
                'cache': 'unavailable'}
