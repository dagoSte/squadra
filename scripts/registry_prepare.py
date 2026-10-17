"""Prepare authenticated derived tables without owning the journal write lock."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time

import registry_cache as cache
from registry_radix import IntegrityError


class StalePreparation(Exception):
    pass


class StaleSnapshot(Exception):
    """The live registry file changed while it was copied; the copy is discarded, never audited."""


def identity(store):
    # Only AuditSnapshot binds a copy to the live file it came from, after a complete, consistent copy.
    bound = getattr(store, '_snapshot_of', None)
    if bound is not None:
        return bound
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


class AuditSnapshot:
    """A consistent private copy of the live registry, for a long read-only audit.

    SQLite's backup API copies the file inside one read transaction of a read-only connection, so writers
    arriving meanwhile neither restart nor wait for the copy; the live write-ahead log is held only while it
    copies, and the audit then reads the copy. The copy lives in a 0700 folder outside the registry, its Store keeps
    the live root for the cache anchor and the artifacts, and close() removes it."""
    PAGES = 1024
    SECONDS = 300
    TRY_MS = 50

    def __init__(self, root, expected, cancelled=None):
        from registry import Error, Store
        self.store = self.folder = self.copy_seconds = None
        try:
            source = Store(root, read_only=True)
            try:
                if identity(source) != expected:
                    raise StaleSnapshot('Registry file changed before the audit copy')
                self.folder = Path(tempfile.mkdtemp(prefix='squadra-audit-', dir=scratch_parent(source)))
                os.chmod(self.folder, 0o700)
                path = self.folder / 'registry.sqlite3'
                started = time.monotonic()

                def step(status=None, remaining=None, total=None):
                    if cancelled is not None and cancelled():
                        raise Error('Registry audit cancelled')
                    if time.monotonic() - started > self.SECONDS:
                        raise Error('Registry audit copy did not finish within %d seconds' % self.SECONDS)

                target = sqlite3.connect(path, isolation_level=None)
                try:
                    # One read transaction for every backup step: one snapshot, never restarted by writers.
                    # A writer's lock is waited out in short tries, so cancellation and the deadline stay prompt.
                    source.db.execute('PRAGMA busy_timeout=%d' % self.TRY_MS)
                    while True:
                        step()
                        source.db.execute('BEGIN')
                        try:
                            source.db.execute('SELECT count(*) FROM sqlite_master').fetchone()
                            break
                        except sqlite3.OperationalError as exc:
                            if source.db.in_transaction:
                                source.db.execute('ROLLBACK')
                            if 'locked' not in str(exc) and 'busy' not in str(exc):
                                raise
                            time.sleep(self.TRY_MS / 1000)
                    if identity(source) != expected:
                        raise StaleSnapshot('Registry file changed before the audit copy')
                    step()
                    source.db.backup(target, pages=self.PAGES, progress=step, sleep=0)
                    if identity(source) != expected:
                        raise StaleSnapshot('Registry file changed during the audit copy')
                    target.execute('PRAGMA journal_mode=DELETE')
                finally:
                    target.close()
                self.copy_seconds = time.monotonic() - started
            finally:
                source.close()
            os.chmod(path, 0o600)
            store = Store(root, read_only=True, database=path)
            store._snapshot_of = expected
            self.store = store
        except BaseException as exc:
            try:
                self.close()
            except BaseException as cleanup:
                raise cleanup from exc
            raise

    def close(self):
        """Close the copy and remove it; both steps run, and the first failure is raised afterwards."""
        failure = None
        store, self.store = self.store, None
        if store is not None:
            try:
                store.close()
            except BaseException as exc:
                failure = exc
        folder, self.folder = self.folder, None
        if folder is not None:
            try:
                shutil.rmtree(folder)
            except BaseException as exc:
                failure = failure or exc
        if failure is not None:
            raise failure


# The WAL-index header SQLite keeps at the start of the -shm file (wal.c: WalIndexHdr twice, then WalCkptInfo).
WAL_INDEX_BYTES = 136
WAL_INDEX_VERSION = 3007000
READ_MARK_UNUSED = 0xffffffff
# Two samples of the header, and which read-lock bytes (120 + 3 + slot) other processes hold, taken by F_GETLK.
_WAL_PROBE = r'''
import json, os, struct, sys
fd = os.open(sys.argv[1], os.O_RDONLY)
try:
    try:
        import fcntl
    except ImportError:
        fcntl = None
    samples, held = [os.pread(fd, 136, 0).hex()], None
    if fcntl is not None and struct.calcsize('P') == 8 and sys.platform in ('darwin', 'linux'):
        held = []
        for slot in range(5):
            if sys.platform == 'darwin':
                layout, fields, kind = 'qqihh', (123 + slot, 1, 0, fcntl.F_WRLCK, os.SEEK_SET), 3
            else:
                layout, fields, kind = '@hhqqi4x', (fcntl.F_WRLCK, os.SEEK_SET, 123 + slot, 1, 0), 0
            answer = fcntl.fcntl(fd, fcntl.F_GETLK, struct.pack(layout, *fields))
            held.append(struct.unpack(layout, answer)[kind] != fcntl.F_UNLCK)
    samples.append(os.pread(fd, 136, 0).hex())
finally:
    os.close(fd)
print(json.dumps({'samples': samples, 'held': held}))
'''


def wal_probe(shm):
    """([two header samples], held read slots or None), read by a child process; None when unreadable.

    Closing any descriptor of the -shm file drops every lock this process holds on it, SQLite's included, so the
    file is never opened here. F_GETLK only tests a lock: nothing is acquired or written."""
    try:
        done = subprocess.run([sys.executable, '-I', '-B', '-c', _WAL_PROBE, str(shm)],
                              capture_output=True, text=True, timeout=10)
        if done.returncode:
            return None
        value = json.loads(done.stdout)
        samples = [bytes.fromhex(sample) for sample in value['samples']]
        held = value['held']
        if not (held is None or (isinstance(held, list) and len(held) == 5 and all(type(h) is bool for h in held))):
            held = None
        return samples, held
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return None


def _index_checksum(data):
    first = second = 0
    words = struct.unpack('=%dI' % (len(data) // 4), data)
    for a, b in zip(words[0::2], words[1::2]):
        first = (first + a + second) & 0xffffffff
        second = (second + b + first) & 0xffffffff
    return first, second


def wal_index(samples, wal_bytes):
    """Frames, backfilled frames and read marks of the WAL index, or None unless the two samples are equal, both
    header copies agree, the header checksum holds and the counters fit the journal file's size."""
    if len(samples) != 2 or samples[0] != samples[1] or len(samples[0]) < WAL_INDEX_BYTES:
        return None
    header = samples[0]
    if header[:48] != header[48:96]:
        return None
    version, _, _, initialized, _, page, frames = struct.unpack('=IIIBBHI', header[:20])
    if (version != WAL_INDEX_VERSION or initialized != 1
            or _index_checksum(header[:40]) != struct.unpack('=II', header[40:48])):
        return None
    backfilled, *marks = struct.unpack('=6I', header[96:120])
    page = 65536 if page == 1 else page
    if (backfilled > frames or any(mark != READ_MARK_UNUSED and mark > frames for mark in marks[1:])
            or (frames and wal_bytes < 32 + frames * (page + 24))):
        return None
    return {'frames': frames, 'backfilled': backfilled, 'marks': marks}


def wal_state(root):
    """The journal's size and checkpoint progress for doctor, read without SQLite, locks or a checkpoint.

    Counters come from a consistent WAL-index header or read "unknown". The oldest read mark another process
    holds is reported when it is behind the journal: no checkpoint copies or trims past it until it ends."""
    path = Path(root) / 'registry.sqlite3'
    wal, shm = Path(str(path) + '-wal'), Path(str(path) + '-shm')
    try:
        size = wal.stat().st_size
    except FileNotFoundError:
        size = None
    if size is None and not os.path.lexists(shm):
        return {'bytes': 0, 'frames': 0, 'backfilled': 0}
    size = size or 0
    sample = wal_probe(shm) if os.path.lexists(shm) and not shm.is_symlink() else None
    index = wal_index(sample[0], size) if sample else None
    if index is None:
        return {'bytes': size, 'frames': 'unknown', 'backfilled': 'unknown'}
    result = {'bytes': size, 'frames': index['frames'], 'backfilled': index['backfilled']}
    held = sample[1]
    if held is None:
        result['readers'] = 'unknown'
        return result
    # Slot 0 readers see only the database file: they stop the checkpoint where it stands.
    marks = [index['backfilled'] if slot == 0 else index['marks'][slot] for slot in range(5) if held[slot]]
    behind = [mark for mark in marks if mark != READ_MARK_UNUSED and mark < index['frames']]
    if behind:
        oldest = min(behind)
        result['oldest_held_read_mark'] = oldest
        result['note'] = ('A reader has held a snapshot since frame %d of %d; no checkpoint can copy or trim the '
                          'journal past it until that reader ends.' % (oldest, index['frames']))
    return result


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
