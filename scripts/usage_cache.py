"""Disposable, strictly projected usage records in a bounded private cache."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat

VERSION = 1
MAX_ENTRY_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_SOURCES = 1000
MAX_RECORDS = 100000
MAX_NUMBER = 2**53 - 1
HEX = re.compile(r'[0-9a-f]{64}\Z')
MODEL = re.compile(r'(?:claude-(?:opus|sonnet|haiku)[-a-z0-9.]{0,60}|gpt-[0-9][-a-z0-9.]{0,60}|o[134](?:[-a-z0-9.]{0,60})|codex[-a-z0-9.]{0,60})\Z')
SESSION = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_-]{5,79}\Z')
ENTRY = re.compile(r'[0-9a-f]{64}\.json\Z')
TEMP = re.compile(r'\.tmp-[0-9a-f]{64}-[0-9a-f]{32}\Z')
KINDS = ('input', 'cache_read', 'cache_creation', 'output', 'reasoning', 'total')
REASONS = frozenset(('timestamp_invalid', 'counter_invalid', 'sample_identity_missing',
    'duplicate_json_key', 'record_too_large', 'record_too_deep', 'format_unknown',
    'usage_missing', 'identity_mismatch', 'counter_inconsistent', 'quota_invalid',
    'unsafe_source', 'source_unavailable', 'source_changed', 'sample_limit',
    'incomplete_record', 'collecting', 'duplicate_conflict', 'baseline_unattributed',
    'counter_reset', 'identity_unverified'))


def require(condition):
    if not condition:
        raise ValueError('invalid_usage_cache')


def fields(value, names):
    require(type(value) is dict and value.keys() == set(names))


def integer(value, maximum=MAX_NUMBER):
    require(type(value) is int and 0 <= value <= maximum)
    return value


def timestamp(value):
    require(type(value) in (int, float) and math.isfinite(value)
            and -62135596800 <= value < 253402300800)
    return value


def name(value, pattern, nullable=False):
    require((nullable and value is None) or (type(value) is str and pattern.fullmatch(value)))
    return value


def tokens(value, client):
    fields(value, KINDS)
    result = {k: (None if k == 'reasoning' and client == 'claude' and value[k] is None
                  else integer(value[k])) for k in KINDS}
    require(result['total'] == sum(result[k] for k in KINDS[:4]))
    if client == 'claude':
        require(result['reasoning'] is None)
    else:
        require(result['cache_creation'] == 0 and result['reasoning'] <= result['output'])
    return result


def record(value, client):
    require(type(value) is dict)
    kind = value.get('type')
    if client == 'claude':
        fields(value, ('type', 'at', 'model', 'key', 'tokens', 'session'))
        require(kind == 'sample')
        return dict(type=kind, at=timestamp(value['at']), model=name(value['model'], MODEL, True),
                    key=name(value['key'], HEX), tokens=tokens(value['tokens'], client),
                    session=name(value['session'], SESSION, True))
    if kind == 'session':
        fields(value, ('type', 'session'))
        return dict(type=kind, session=name(value['session'], SESSION, True))
    if kind == 'model':
        fields(value, ('type', 'model', 'at'))
        return dict(type=kind, model=name(value['model'], MODEL, True), at=timestamp(value['at']))
    fields(value, ('type', 'at', 'tokens', 'quota'))
    require(kind == 'sample' and type(value['quota']) is list and len(value['quota']) <= 2)
    quota, slots = [], set()
    for q in value['quota']:
        fields(q, ('limit', 'used_percent', 'window_minutes', 'resets_at', 'plan_type'))
        require(type(q['limit']) is str and q['limit'] in ('primary', 'secondary') and q['limit'] not in slots)
        slots.add(q['limit'])
        percent = q['used_percent']
        require(type(percent) in (int, float) and math.isfinite(percent) and 0 <= percent <= 100)
        require(0 < integer(q['window_minutes']) <= 525600)
        reset = q['resets_at']
        require(type(reset) is str and len(reset) <= 40)
        parsed = datetime.fromisoformat(reset.replace('Z', '+00:00'))
        require(parsed.tzinfo is not None and 0 <= parsed.timestamp() <= 4102444800
                and parsed.timestamp().is_integer())
        require(q['plan_type'] is None or (type(q['plan_type']) is str and q['plan_type'] in
                ('free', 'plus', 'pro', 'team', 'business', 'enterprise', 'edu', 'api', 'unknown')))
        quota.append(dict(limit=q['limit'], used_percent=percent, window_minutes=q['window_minutes'],
                          resets_at=datetime.fromtimestamp(parsed.timestamp(), timezone.utc).isoformat().replace('+00:00', 'Z'),
                          plan_type=q['plan_type']))
    counts = None if value['tokens'] is None else tokens(value['tokens'], client)
    require(counts is not None or quota)
    return dict(type=kind, at=timestamp(value['at']), tokens=counts, quota=quota)


def sources(value):
    require(type(value) is dict and len(value) <= MAX_SOURCES)
    result, total = {}, 0
    for key, blob in value.items():
        name(key, HEX)
        fields(blob, ('client', 'session', 'stamp', 'cursor', 'end', 'append_cursor',
                      'backfill_end', 'fingerprint', 'records', 'reasons', 'observed', 'pending', 'checked'))
        client = blob['client']
        require(type(client) is str and client in ('claude', 'codex'))
        session = name(blob['session'], SESSION)
        require(type(blob['stamp']) is list and len(blob['stamp']) == 5)
        marker = [integer(n, 2**64 - 1) for n in blob['stamp']]
        end = integer(blob['end'], 2**63 - 1)
        require(marker[2] >= end)
        offsets = {k: integer(blob[k], end) for k in ('cursor', 'append_cursor', 'backfill_end')}
        require(offsets['cursor'] <= offsets['backfill_end'])
        require(type(blob['fingerprint']) is list and len(blob['fingerprint']) == 2)
        fingerprint = [name(n, HEX) for n in blob['fingerprint']]
        require(type(blob['records']) is list and len(blob['records']) <= MAX_RECORDS)
        total += len(blob['records'])
        require(total <= MAX_RECORDS)
        records, seen = [], set()
        for pair in blob['records']:
            require(type(pair) is list and len(pair) == 2)
            offset = integer(pair[0], end)
            require(offset not in seen)
            seen.add(offset)
            records.append([offset, record(pair[1], client)])
        require(type(blob['reasons']) is list and len(blob['reasons']) <= len(REASONS))
        reasons = blob['reasons']
        require(all(type(r) is str and r in REASONS for r in reasons) and len(set(reasons)) == len(reasons))
        observed = None if blob['observed'] is None else timestamp(blob['observed'])
        require(type(blob['pending']) is bool and type(blob['checked']) is bool)
        result[key] = dict(client=client, session=session, stamp=marker, end=end, **offsets,
                           fingerprint=fingerprint, records=records, reasons=list(reasons),
                           observed=observed, pending=blob['pending'], checked=blob['checked'])
    return result


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result)
        result[key] = value
    return result


def directory(fd, private=False):
    info = os.fstat(fd)
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and (stat.S_IMODE(info.st_mode) == 0o700 if private else not info.st_mode & 0o022))


def regular(info):
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1)


OPEN = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


class Cache:
    def __init__(self, key, *, home=None, excluded=()):
        self.key = key
        self.home = Path(os.path.abspath(Path.home() if home is None else home))
        self.folder = self.home / '.cache' / 'squadra' / 'usage-v1'
        self.excluded = [Path(os.path.abspath(p)) for p in excluded]

    @contextmanager
    def _folder(self, create=False):
        name(self.key, HEX)
        require(not any(self.folder == p or p in self.folder.parents or self.folder in p.parents
                        for p in self.excluded))
        fd = os.open('/', OPEN | os.O_DIRECTORY)
        try:
            # Above HOME only the no-follow rule applies: system ancestors have different owners.
            for part in self.home.parts[1:]:
                child = os.open(part, OPEN | os.O_DIRECTORY, dir_fd=fd)
                os.close(fd)
                fd = child
            directory(fd)
            for part in ('.cache', 'squadra', 'usage-v1'):
                if create:
                    try:
                        os.mkdir(part, 0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                child = os.open(part, OPEN | os.O_DIRECTORY, dir_fd=fd)
                os.close(fd)
                fd = child
                directory(fd, private=part == 'usage-v1')
            yield fd
        finally:
            os.close(fd)

    @contextmanager
    def _lock(self, parent, write=False):
        flags = OPEN | (os.O_CREAT if write else 0)
        fd = os.open('.lock', flags, 0o600, dir_fd=parent)
        try:
            regular(os.fstat(fd))
            fcntl.flock(fd, (fcntl.LOCK_EX if write else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    def load(self):
        try:
            with self._folder() as parent, self._lock(parent):
                fd = os.open(self.key + '.json', OPEN, dir_fd=parent)
                try:
                    before = os.fstat(fd)
                    regular(before)
                    require(before.st_size <= MAX_ENTRY_BYTES)
                    chunks, remaining = [], MAX_ENTRY_BYTES + 1
                    while remaining:
                        block = os.read(fd, min(remaining, 65536))
                        if not block:
                            break
                        chunks.append(block)
                        remaining -= len(block)
                    raw = b''.join(chunks)
                    after = os.fstat(fd)
                    require(len(raw) <= MAX_ENTRY_BYTES and all(getattr(before, k) == getattr(after, k)
                            for k in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')))
                    data = json.loads(raw, object_pairs_hook=pairs,
                                      parse_constant=lambda _: require(False))
                    fields(data, ('version', 'digest', 'sources'))
                    require(type(data['version']) is int and data['version'] == VERSION)
                    name(data['digest'], HEX)
                    require(hashlib.sha256(encoded(data['sources'])).hexdigest() == data['digest'])
                    result = sources(data['sources'])
                    # Eviction uses the last successful read or write of these disposable calculations.
                    os.utime(fd, None)
                    return result
                finally:
                    os.close(fd)
        except (OSError, ValueError, TypeError, OverflowError, RecursionError):
            return {}

    def _room(self, parent, size):
        total, candidates = 0, []
        for entry in os.listdir(parent):
            info = os.stat(entry, dir_fd=parent, follow_symlinks=False)
            regular(info)
            if TEMP.fullmatch(entry):
                os.unlink(entry, dir_fd=parent)
                continue
            total += info.st_size
            if ENTRY.fullmatch(entry) and entry != self.key + '.json':
                candidates.append((info.st_mtime_ns, entry, info.st_size))
        for _, entry, old_size in sorted(candidates):
            if total + size <= MAX_TOTAL_BYTES:
                break
            os.unlink(entry, dir_fd=parent)
            total -= old_size
        require(total + size <= MAX_TOTAL_BYTES)

    def save(self, value):
        temp = None
        try:
            projected = sources(value)
            payload = encoded(projected)
            require(len(payload) <= MAX_ENTRY_BYTES)
            raw = (b'{"version":' + str(VERSION).encode('ascii') + b',"digest":"'
                   + hashlib.sha256(payload).hexdigest().encode('ascii') + b'","sources":' + payload + b'}')
            require(len(raw) <= MAX_ENTRY_BYTES and len(raw) <= MAX_TOTAL_BYTES)
            with self._folder(create=True) as parent, self._lock(parent, write=True):
                self._room(parent, len(raw))
                temp = '.tmp-' + self.key + '-' + secrets.token_hex(16)
                try:
                    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                    try:
                        os.fchmod(fd, 0o600)
                        view = memoryview(raw)
                        while view:
                            written = os.write(fd, view)
                            require(written > 0)
                            view = view[written:]
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                    os.replace(temp, self.key + '.json', src_dir_fd=parent, dst_dir_fd=parent)
                    temp = None
                    os.fsync(parent)
                    return True
                finally:
                    if temp is not None:
                        try:
                            os.unlink(temp, dir_fd=parent)
                        except FileNotFoundError:
                            pass
        except (OSError, ValueError, TypeError, OverflowError, RecursionError):
            return False
