"""An opt-in local FIFO; only the journal can reserve or release a resource."""
from contextlib import contextmanager
import errno
import hashlib
import json
import math
import os
import re
import stat
import sys
import time
import uuid

from registry import Store, Error, require

POLL_SECONDS = 0.2
RESERVED = 'Resource already reserved; expiry is not release'
QUEUE_BUSY = 'Resource queue mutex busy; wait deadline expired'
TICKET = re.compile(r'[0-9]{20}-[0-9a-f]{32}\.ticket\Z')
PRIVATE = '--wait requires a private queue for one OS user (0700 directories, 0600 files); shared-user queues are unsupported'


class CacheUnavailable(Error):
    pass


def resource_state(root, name, actor):
    """Demand only authenticated leaves, close the read transaction before returning."""
    from registry_cache import load, plain
    from registry_radix import IntegrityError
    reader = Store(root, read_only=True)
    tree = None
    try:
        reader.db.execute('BEGIN')
        try:
            tree, meta = load(reader)
            tip = reader.db.execute('SELECT seq,hash FROM events ORDER BY seq DESC LIMIT 1').fetchone()
            if tip is None or (tip['seq'], tip['hash']) != (meta['seq'], meta['head']):
                raise CacheUnavailable('Resource wait needs a current cache; run an ordinary write first')
            state = tree.root
            require(actor in state['agents'], 'Unknown agent: ' + actor)
            holder = plain(state['resources'].get(name))
            return {'holder': holder, 'lang': state['workspace'].get('lang', 'it'),
                    'identity': state['agents'][actor]['identity'],
                    'holder_identity': state['agents'][holder['owner']]['identity'] if holder else None}
        except (IntegrityError, KeyError, ValueError) as exc:
            raise CacheUnavailable('Resource wait cache unavailable; use an ordinary command to prepare it, '
                                   'or squadra doctor for integrity details') from exc
    finally:
        if tree is not None:
            tree.close(); tree.current_tree.close()
        reader.close()


def private(info, directory=False):
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)) and
            info.st_uid == os.getuid() and not info.st_mode & 0o077 and
            (directory or info.st_nlink == 1), PRIVATE)


@contextmanager
def queue_folder(root, name):
    descriptors = []
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        descriptors.append(fd)
        for part in ('resource-waits', hashlib.sha256(name.encode()).hexdigest()):
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            descriptors.append(fd)
            private(os.fstat(fd), directory=True)
        yield fd
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR, errno.EACCES, errno.EPERM}:
            raise Error(PRIVATE) from exc
        raise
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def queue_file(folder, name, exclusive=False):
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
    if exclusive:
        flags |= os.O_EXCL
    fd = os.open(name, flags, 0o600, dir_fd=folder)
    try:
        private(os.fstat(fd))
        return fd
    except BaseException:
        os.close(fd)
        raise


def live_tickets(folder):
    """Under the queue mutex, remove only tickets whose OS lock is demonstrably free."""
    import fcntl
    live = []
    for name in sorted(os.listdir(folder)):
        if not TICKET.fullmatch(name):
            continue
        fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=folder)
        try:
            private(os.fstat(fd))
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                live.append(name)
            else:
                require(os.stat(name, dir_fd=folder, follow_symlinks=False).st_ino == os.fstat(fd).st_ino, PRIVATE)
                os.unlink(name, dir_fd=folder)
        finally:
            os.close(fd)
    return live


@contextmanager
def queue_lock(fd, deadline):
    import fcntl
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            require(time.monotonic() < deadline, QUEUE_BUSY)
            time.sleep(min(POLL_SECONDS, max(0, deadline - time.monotonic())))
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)


def acquire(store, actor, name, reason, seconds, *, task=None):
    require(os.name == 'posix', '--wait is supported on POSIX only; Windows must use immediate resource acquisition')
    require(math.isfinite(seconds) and seconds >= 0, '--wait requires finite nonnegative seconds')
    import fcntl
    deadline = time.monotonic() + seconds
    store.release_views()
    initial = resource_state(store.root, name, actor)
    holder = initial['holder']
    require(not holder or initial['identity'] != initial['holder_identity'],
            RESERVED + ('; holder: ' + holder['owner'] if holder else ''))
    ticket = None
    ticket_fd = mutex = None
    with queue_folder(store.root, name) as folder:
        try:
            mutex = queue_file(folder, 'mutex')
            with queue_lock(mutex, deadline):
                live = live_tickets(folder)
                number = int(live[-1].split('-', 1)[0]) + 1 if live else 1
                require(number < 10**20, 'Resource queue exhausted')
                ticket = f'{number:020d}-{uuid.uuid4().hex}.ticket'
                ticket_fd = queue_file(folder, ticket, exclusive=True)
                fcntl.flock(ticket_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                os.write(ticket_fd, json.dumps({'actor': actor, 'task': task, 'reason': reason,
                                               'since': time.time(), 'pid': os.getpid()}).encode())
                position = len(live) + 1
            who = holder['owner'] if holder else ('a prior waiter' if position > 1 else 'free')
            if initial['lang'] == 'en':
                text = f'Resource {name}: holder {who}; queue position {position}; wait up to {seconds:g} seconds.'
            else:
                who = holder['owner'] if holder else ('un richiedente precedente' if position > 1 else 'nessuno')
                text = f'Risorsa {name}: titolare {who}; posizione in coda {position}; attesa massima {seconds:g} secondi.'
            print(text, file=sys.stderr, flush=True)
            first = seconds == 0
            while first or time.monotonic() < deadline:
                first = False
                with queue_lock(mutex, deadline):
                    live = live_tickets(folder)
                    if live and live[0] == ticket and resource_state(store.root, name, actor)['holder'] is None:
                        previous_timeout = store.db.execute('PRAGMA busy_timeout').fetchone()[0]
                        remaining = max(0, deadline - time.monotonic())
                        timeout_ms = min(previous_timeout, math.ceil(min(remaining, previous_timeout / 1000) * 1000))
                        store.db.execute('PRAGMA busy_timeout=' + str(timeout_ms))
                        try:
                            store.append('resource.acquire', actor, {'name': name, 'reason': reason,
                                         **({'task': task} if task else {})})
                            return
                        except Error as exc:
                            if str(exc) != RESERVED:
                                raise
                        finally:
                            store.db.execute('PRAGMA busy_timeout=' + str(previous_timeout))
                if time.monotonic() >= deadline:
                    break
                time.sleep(min(POLL_SECONDS, max(0, deadline - time.monotonic())))
            raise Error(RESERVED)
        finally:
            # Cleanup never races a scanner. If its mutex is busy, leave an unlocked stale ticket.
            if ticket_fd is not None:
                try:
                    fcntl.flock(mutex, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    os.close(ticket_fd)
                else:
                    try:
                        os.unlink(ticket, dir_fd=folder)
                    finally:
                        os.close(ticket_fd)
                        fcntl.flock(mutex, fcntl.LOCK_UN)
            if mutex is not None:
                os.close(mutex)


def status(store, name):
    """Observe held reservations and locked queue tickets; never clean up or reserve."""
    import fcntl
    from registry import plain
    result = {'name': name, 'holder': plain(store.view()['resources'].get(name)),
              'waiting': [], 'process_observation': 'reservations and live wait tickets; holder process not inferred'}
    path = store.root / 'resource-waits' / hashlib.sha256(name.encode()).hexdigest()
    if not path.exists():
        return result
    descriptors = []
    try:
        fd = os.open(store.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(fd)
        for part in ('resource-waits', hashlib.sha256(name.encode()).hexdigest()):
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            descriptors.append(fd)
            private(os.fstat(fd), directory=True)
        for ticket in sorted(os.listdir(fd)):
            if not TICKET.fullmatch(ticket):
                continue
            try:
                leaf = os.open(ticket, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                continue
            try:
                private(os.fstat(leaf))
                try:
                    fcntl.flock(leaf, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    body = os.read(leaf, 8193)
                    try:
                        data = json.loads(body)
                        if not isinstance(data, dict):
                            raise ValueError('Invalid ticket metadata')
                    except (ValueError, UnicodeError):
                        data = {'actor': None, 'metadata': 'unavailable; older or starting waiter'}
                    result['waiting'].append({**data, 'position': len(result['waiting']) + 1})
            finally:
                os.close(leaf)
        return result
    finally:
        for fd in reversed(descriptors):
            os.close(fd)
