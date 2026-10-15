"""Read-only local usage: retain numeric observations, never conversation content."""
from __future__ import annotations

from collections import OrderedDict, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import time

KINDS = ('input', 'cache_read', 'cache_creation', 'output', 'reasoning', 'total')
PRICE_KINDS = KINDS[:4]
MAX_NUMBER = 2**53 - 1
MAX_RECORD = 16 * 1024 * 1024
SLICE_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 100000
MAX_FILES = 1000
MODEL = re.compile(r'(?:claude-(?:opus|sonnet|haiku)[-a-z0-9.]{0,60}|gpt-[0-9][-a-z0-9.]{0,60}|o[134](?:[-a-z0-9.]{0,60})|codex[-a-z0-9.]{0,60})\Z')
SESSION = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_-]{5,79}\Z')


class Invalid(ValueError):
    """Fixed public reason, without data from the input."""


def utc(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z')


def instant(value):
    if isinstance(value, str) and len(value) <= 40:
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is not None:
                return parsed.timestamp()
        except (ValueError, OverflowError):
            pass
    raise Invalid('timestamp_invalid')


def number(value, *, fractional=False):
    if type(value) not in ((int, float) if fractional else (int,)) or not 0 <= value <= MAX_NUMBER:
        raise Invalid('counter_invalid')
    return value


def model_name(value):
    return value if isinstance(value, str) and MODEL.fullmatch(value) else None


def identity_key(value):
    if not isinstance(value, str) or not value or len(value) > 256:
        raise Invalid('sample_identity_missing')
    return hashlib.sha256(value.encode()).hexdigest()


def fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Invalid('duplicate_json_key')
        result[key] = value
    return result


def project(raw, client):
    """Parse once, then return only the structural and numeric allowlist."""
    if len(raw) > MAX_RECORD:
        raise Invalid('record_too_large')
    try:
        data = json.loads(raw, object_pairs_hook=fields,
                          parse_constant=lambda _: (_ for _ in ()).throw(Invalid('counter_invalid')))
        pending = [(data, 0)]
        while pending:
            obj, depth = pending.pop()
            if depth > 32:
                raise Invalid('record_too_deep')
            if isinstance(obj, dict):
                pending.extend((v, depth + 1) for v in obj.values() if isinstance(v, (dict, list)))
            elif isinstance(obj, list):
                pending.extend((v, depth + 1) for v in obj if isinstance(v, (dict, list)))
        if not isinstance(data, dict):
            raise Invalid('format_unknown')
        kind = data.get('type')
        stamp = data.get('timestamp')
        if client == 'claude':
            if kind != 'assistant':
                return None
            msg = data.get('message')
            if not isinstance(msg, dict) or not isinstance(msg.get('usage'), dict):
                raise Invalid('usage_missing')
            u = msg['usage']
            counts = dict(input=number(u.get('input_tokens')), output=number(u.get('output_tokens')),
                          cache_read=number(u.get('cache_read_input_tokens', 0)),
                          cache_creation=number(u.get('cache_creation_input_tokens', 0)), reasoning=None)
            counts['total'] = number(sum(counts[k] for k in PRICE_KINDS))
            session = data.get('sessionId')
            if session is not None and (not isinstance(session, str) or not SESSION.fullmatch(session)):
                raise Invalid('identity_mismatch')
            return {'type': 'sample', 'at': instant(stamp), 'model': model_name(msg.get('model')),
                    'key': identity_key(msg.get('id') or data.get('requestId')), 'tokens': counts,
                    'session': session if isinstance(session, str) and SESSION.fullmatch(session) else None}
        payload = data.get('payload')
        if not isinstance(payload, dict):
            return None
        if kind == 'session_meta':
            session = payload.get('id')
            return {'type': 'session', 'session': session if isinstance(session, str) and SESSION.fullmatch(session) else None}
        if kind == 'turn_context':
            return {'type': 'model', 'model': model_name(payload.get('model')), 'at': instant(stamp)}
        if kind != 'event_msg' or payload.get('type') != 'token_count':
            return None
        result = {'type': 'sample', 'at': instant(stamp), 'tokens': None, 'quota': []}
        info = payload.get('info')
        if info is not None:
            if not isinstance(info, dict) or not isinstance(info.get('total_token_usage'), dict):
                raise Invalid('usage_missing')
            u = info['total_token_usage']
            inp, cache, out, reason, total = (number(u.get(k)) for k in (
                'input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens', 'total_tokens'))
            if cache > inp or reason > out or total != inp + out:
                raise Invalid('counter_inconsistent')
            result['tokens'] = dict(input=inp - cache, cache_read=cache, cache_creation=0,
                                    output=out, reasoning=reason, total=total)
        quota = payload.get('rate_limits')
        if quota is not None:
            if not isinstance(quota, dict):
                raise Invalid('quota_invalid')
            plan = quota.get('plan_type')
            plan = plan if plan in {'free', 'plus', 'pro', 'team', 'business', 'enterprise', 'edu', 'api', 'unknown'} else None
            for slot in ('primary', 'secondary'):
                q = quota.get(slot)
                if q is None:
                    continue
                if not isinstance(q, dict):
                    raise Invalid('quota_invalid')
                percent = number(q.get('used_percent'), fractional=True)
                minutes, reset = number(q.get('window_minutes')), number(q.get('resets_at'))
                if percent > 100 or not 0 < minutes <= 525600 or reset > 4102444800:
                    raise Invalid('quota_invalid')
                result['quota'].append({'limit': slot, 'used_percent': percent, 'window_minutes': minutes,
                                        'resets_at': utc(reset), 'plan_type': plan})
        if result['tokens'] is None and not result['quota']:
            raise Invalid('usage_missing')
        return result
    except Invalid:
        raise
    except (UnicodeError, ValueError, RecursionError, TypeError, KeyError, OverflowError):
        raise Invalid('format_unknown') from None


def trusted(path, root, directory=False):
    """Check the chosen client root and each opened descendant without following links."""
    path, root = Path(path), Path(root)
    try:
        rel = path.relative_to(root)
        parts = [root] + [root.joinpath(*rel.parts[:n]) for n in range(1, len(rel.parts) + 1)]
        for i, part in enumerate(parts):
            s = part.lstat()
            is_dir = i < len(parts) - 1 or directory
            if s.st_uid != os.getuid() or (not stat.S_ISDIR(s.st_mode) if is_dir else not stat.S_ISREG(s.st_mode)):
                raise Invalid('unsafe_source')
        return s
    except (OSError, ValueError):
        raise Invalid('source_unavailable') from None


def stamp(s):
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def read_chunks(fd, size):
    chunks = []
    while size:
        chunk = os.read(fd, min(size, 65536))
        if not chunk:
            break
        chunks.append(chunk)
        size -= len(chunk)
    return b''.join(chunks)


def source_roots():
    home = Path.home()
    return {'claude': Path(os.environ.get('CLAUDE_CONFIG_DIR', home / '.claude')) / 'projects',
            'codex': Path(os.environ.get('CODEX_HOME', home / '.codex')) / 'sessions'}


def seats(state):
    grouped = {}
    for actor, agent in state.get('agents', {}).items():
        identity = agent.get('identity', actor)
        row = grouped.setdefault(identity, {'identity': identity, 'actors': [], 'client': agent.get('client'), 'sessions': set()})
        row['actors'].append(actor)
        session = agent.get('session')
        if not session:
            for prefix in ('codex-thread-', 'claude-window-'):
                if identity.startswith(prefix):
                    session = identity[len(prefix):]
        if isinstance(session, str) and SESSION.fullmatch(session):
            row['sessions'].add(session)
        if row['client'] != agent.get('client'):
            row['client'] = 'ambiguous'
    for row in grouped.values():
        row['actors'].sort()
    return sorted(grouped.values(), key=lambda x: x['identity'])


class Source:
    def __init__(self, path, root, client, session):
        self.path, self.root, self.client, self.session = path, root, client, session
        self.stamp = None
        self.cursor = 0
        self.records = {}
        self.reasons = set()
        self.observed = None
        self.pending = False
        self.checked = False
        self.allowed_sessions = {session}
        if client == 'claude' and 'subagents' in path.parts:
            self.allowed_sessions.add(path.stem.removeprefix('agent-'))

    def read(self, deadline, observed_at=None, max_records=MAX_RECORDS):
        self.checked = True
        try:
            s = trusted(self.path, self.root)
            marker = stamp(s)
            if marker != self.stamp:
                self.stamp, self.cursor, self.records = marker, s.st_size, {}
                self.reasons, self.pending = set(), False
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                if stamp(os.fstat(fd)) != marker:
                    raise Invalid('source_changed')
                self.observed = time.time() if observed_at is None else observed_at
                if self.cursor == 0:
                    return
                if self.client == 'codex' and 0 not in self.records:
                    os.lseek(fd, 0, os.SEEK_SET)
                    header = os.read(fd, 65536).split(b'\n', 1)
                    if len(header) == 2:
                        try:
                            first = project(header[0], self.client)
                            if first and first['type'] == 'session':
                                self.records[0] = first
                        except Invalid as exc:
                            self.reasons.add(str(exc))
                end = self.cursor
                size = min(end, SLICE_BYTES)
                start = end - size
                os.lseek(fd, start, os.SEEK_SET)
                block = read_chunks(fd, size)
                if len(block) != size:
                    raise Invalid('source_changed')
                if start > 0:
                    boundary = block.find(b'\n')
                    while (boundary < 0 or boundary == len(block) - 1) and start > 0 and len(block) <= MAX_RECORD:
                        amount = min(start, 65536)
                        start -= amount
                        os.lseek(fd, start, os.SEEK_SET)
                        block = os.read(fd, amount) + block
                        boundary = block.find(b'\n')
                    if (boundary < 0 or boundary == len(block) - 1) and start > 0:
                        self.reasons.add('record_too_large'); self.cursor = 0
                        return
                    if start > 0:
                        start += boundary + 1
                        block = block[boundary + 1:]
                if end == s.st_size and block and not block.endswith(b'\n'):
                    last = block.rfind(b'\n')
                    self.pending = True
                    block = block[:last + 1] if last >= 0 else b''
                lines = block.splitlines(keepends=True)
                offset = start + len(block)
                for line in reversed(lines):
                    if time.monotonic() >= deadline:
                        break
                    offset -= len(line)
                    self.cursor = offset
                    if len(self.records) >= max_records:
                        self.reasons.add('sample_limit')
                        self.cursor = 0
                        break
                    if not line.strip():
                        continue
                    try:
                        row = project(line, self.client)
                        if row is not None:
                            if row.get('session') is not None and row['session'] not in self.allowed_sessions:
                                self.reasons.add('identity_mismatch')
                                continue
                            self.records[offset] = row
                    except Invalid as exc:
                        self.reasons.add(str(exc))
                else:
                    self.cursor = start
                if stamp(os.fstat(fd)) != marker or stamp(trusted(self.path, self.root)) != marker:
                    raise Invalid('source_changed')
            finally:
                os.close(fd)
        except (OSError, Invalid) as exc:
            self.records = {}
            self.reasons.add(str(exc) if isinstance(exc, Invalid) else 'source_unavailable')
            self.cursor = 0

    def normalized(self):
        rows = [r for _, r in sorted(self.records.items())]
        samples, quotas, reasons = [], [], set(self.reasons)
        if self.pending:
            reasons.add('incomplete_record')
        if self.cursor:
            reasons.add('collecting')
        model, previous, previous_model, previous_at = None, None, None, None
        dedup = {}
        conflicted = set()
        valid_session = self.client == 'claude'
        for row in rows:
            if row['type'] == 'session':
                valid_session = row['session'] == self.session
                if not valid_session:
                    reasons.add('identity_mismatch')
            elif row['type'] == 'model':
                model = row['model']
            elif self.client == 'claude':
                if row['session'] is not None and row['session'] not in self.allowed_sessions:
                    reasons.add('identity_mismatch')
                    continue
                old = dedup.get(row['key'])
                if row['key'] in conflicted:
                    continue
                if old and any(row['tokens'][k] < old['tokens'][k] for k in PRICE_KINDS):
                    reasons.add('duplicate_conflict')
                    conflicted.add(row['key']); dedup.pop(row['key'])
                    continue
                dedup[row['key']] = {**row, 'from': row['at'], 'estimated': False, 'reasons': []}
            else:
                at = row['at']
                for q in row['quota']:
                    quotas.append({**q, 'observed_at': utc(at), 'model': model})
                counts = row['tokens']
                if counts is None:
                    continue
                if previous is None:
                    if counts['total']:
                        reasons.add('baseline_unattributed')
                elif any(counts[k] < previous[k] for k in KINDS):
                    reasons.add('counter_reset')
                else:
                    delta = {k: counts[k] - previous[k] for k in KINDS}
                    if delta['reasoning'] > delta['output'] or delta['total'] != sum(delta[k] for k in PRICE_KINDS):
                        reasons.add('counter_inconsistent')
                    else:
                        crossed_model = model != previous_model
                        samples.append({'at': at, 'from': previous_at, 'tokens': delta,
                                        'model': None if crossed_model else model,
                                        'estimated': at != previous_at and delta['total'] != 0,
                                        'reasons': ['observation_attribution'] if at != previous_at and delta['total'] else []})
                previous, previous_at, previous_model = counts, at, model
        if self.client == 'claude':
            samples = list(dedup.values())
        elif not valid_session:
            reasons.add('identity_unverified')
            samples, quotas = [], []
        if 'identity_mismatch' in reasons:
            samples, quotas = [], []
        return samples, quotas, reasons


class Collector:
    def __init__(self, roots):
        self.roots = {k: Path(v).absolute() for k, v in roots.items()}
        self.sources = {}
        self.inventory = None
        self.discovered = 0
        self.lock = threading.Lock()

    def discover(self, windows, deadline):
        signature = tuple((w['identity'], w['client'], tuple(sorted(w['sessions']))) for w in windows)
        if self.inventory is not None and self.inventory[0] == signature and time.monotonic() - self.discovered < 30:
            return self.inventory[1]
        found, errors = {}, {}
        for client in ('claude', 'codex'):
            root = self.roots.get(client)
            if root is None:
                errors[client] = 'source_unavailable'
                continue
            try:
                trusted(root, root, directory=True)
                matches = []
                seen = 0
                for folder, dirs, names in os.walk(root, followlinks=False):
                    if time.monotonic() >= deadline or seen > 100000:
                        errors[client] = 'discovery_limited'
                        break
                    dirs[:] = sorted(d for d in dirs if not (Path(folder) / d).is_symlink())
                    for name in sorted(names):
                        seen += 1
                        if not name.endswith('.jsonl'):
                            continue
                        path = Path(folder) / name
                        if client == 'codex':
                            match = re.search(r'([0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})\.jsonl\Z', name)
                            session = match.group(1) if match else None
                        else:
                            session = path.stem if SESSION.fullmatch(path.stem) else None
                            if 'subagents' in path.parts:
                                session = path.parent.parent.name
                                child = path.stem.removeprefix('agent-')
                                if SESSION.fullmatch(child):
                                    matches.append((child, path))
                        if session:
                            matches.append((session, path))
                found[client] = matches
            except Invalid as exc:
                errors[client] = str(exc)
        result = {}
        for w in windows:
            identity, client = w['identity'], w['client']
            reasons, selected = set(), []
            if client not in ('claude', 'codex'):
                reasons.add('client_unsupported')
            else:
                candidates = found.get(client, [])
                full = set()
                for prefix in w['sessions']:
                    exact = {s for s, _ in candidates if s == prefix}
                    options = exact or {s for s, _ in candidates if s.startswith(prefix)}
                    if len(options) == 1:
                        full.update(options)
                    elif len(options) > 1:
                        reasons.add('identity_ambiguous')
                if len(full) != 1:
                    reasons.add('identity_ambiguous' if full else errors.get(client, 'session_not_found'))
                else:
                    session = next(iter(full))
                    selected = [(s, p) for s, p in candidates if s == session]
                    if client == 'codex' and len(selected) != 1:
                        reasons.add('identity_ambiguous'); selected = []
                    if len(selected) > MAX_FILES:
                        reasons.add('source_limit'); selected = selected[:MAX_FILES]
                if client in errors:
                    reasons.add(errors[client])
            result[identity] = (selected, reasons)
        # A file selected by two independently registered identities is not counted twice.
        owners = defaultdict(list)
        for w in windows:
            for session, path in result[w['identity']][0]:
                owners[path].append(w['identity'])
        for path, identities in owners.items():
            if len(identities) > 1:
                child = path.stem.removeprefix('agent-') if 'subagents' in path.parts else None
                exact = [w['identity'] for w in windows if w['identity'] in identities and child in w['sessions']]
                chosen = exact[0] if len(exact) == 1 else None
                for identity in identities:
                    if identity == chosen:
                        continue
                    selected, reasons = result[identity]
                    selected[:] = [(s, p) for s, p in selected if p != path]
                    if chosen is None:
                        reasons.add('identity_ambiguous')
        if not errors:
            self.inventory, self.discovered = (signature, result), time.monotonic()
        return result

    def collect(self, events, state, now, budget_seconds):
        deadline = time.monotonic() + max(0, min(budget_seconds, 30))
        if not self.lock.acquire(blocking=False):
            return {'schema': 1, 'observed_at': utc(now), 'status': 'not_measured',
                    'coverage': {'status': 'collecting', 'sources': 0, 'measured': 0, 'reasons': ['collector_busy']},
                    'windows': [], 'models': [], 'tasks': [], 'days': [], 'quota_history': [], 'suggestions': [], 'cost': None}
        try:
            windows = seats(state)
            discovery = self.discover(windows, deadline)
            used, result, all_samples, all_quotas = set(), [], [], []
            measured_sources = 0
            spans = task_spans(events, state, now)
            for w in windows:
                selected, discovered_reasons = discovery[w['identity']]
                samples, quotas, reasons, observed, dedup = [], [], set(discovered_reasons), None, {}
                conflicts = set()
                complete = bool(selected)
                for session, path in selected:
                    key = (w['client'], str(path))
                    used.add(key)
                    source_session = path.parent.parent.name if w['client'] == 'claude' and 'subagents' in path.parts else session
                    source = self.sources.setdefault(key, Source(path, self.roots[w['client']], w['client'], source_session))
                    available = MAX_RECORDS - sum(len(s.records) for s in self.sources.values() if s is not source)
                    if available <= 0 and (source.cursor or not source.checked):
                        source.reasons.add('sample_limit'); source.cursor = 0; source.checked = True
                    elif time.monotonic() < deadline:
                        source.read(deadline, now, available)
                    src_samples, src_quota, src_reasons = source.normalized()
                    measured_sources += bool(src_samples)
                    reasons.update(src_reasons)
                    complete = complete and source.checked and source.cursor == 0 and not source.pending and not source.reasons
                    observed = min(observed, source.observed) if observed and source.observed else source.observed
                    for sample in src_samples:
                        skey = sample.get('key')
                        if skey is not None:
                            old = dedup.get(skey)
                            if old and (old['tokens'] != sample['tokens'] or old['model'] != sample['model']):
                                reasons.add('duplicate_conflict'); complete = False
                                conflicts.add(skey)
                            if old:
                                continue
                            dedup[skey] = sample
                        samples.append({**sample, 'identity': w['identity']})
                    quotas.extend({**q, 'identity': w['identity']} for q in src_quota)
                samples = [s for s in samples if s['at'] <= now and s.get('key') not in conflicts]
                if not samples:
                    reasons.add('no_usage_observed')
                totals = add_tokens(samples) if samples else None
                if totals is not None and totals['total'] is None:
                    reasons.add('counter_limit')
                complete = complete and not (reasons - {'baseline_unattributed'})
                status = 'measured' if complete and samples and not reasons else 'partial' if samples else 'not_measured'
                row = {k: w[k] for k in ('identity', 'actors', 'client')}
                row.update(status=status, reasons=sorted(reasons), totals=totals,
                           models=model_rows(samples, complete, now),
                           hourly=hourly(samples, complete, now), rolling=rolling(samples, complete, now),
                           quota=latest_quotas(quotas, now))
                recent = interval(samples, now - 900, now, complete)
                fresh = observed is not None and now - observed <= 60
                row['recent'] = {'seconds': 900, 'total_tokens': recent['total'] if fresh and recent['status'] == 'measured' else None,
                                 'tokens_per_minute': recent['total'] / 15 if fresh and recent['status'] == 'measured' and recent['total'] is not None else None,
                                 'observed_until': utc(observed) if observed is not None else None,
                                 'last_sample_at': utc(max(s['at'] for s in samples)) if samples else None,
                                 'status': recent['status'] if fresh else 'not_measured',
                                 'reason': recent['reason'] if fresh else 'observation_stale',
                                 'estimated': recent['estimated'], 'reasons': recent['reasons']}
                result.append(row); all_samples.extend(samples); all_quotas.extend(quotas)
            self.sources = {k: v for k, v in self.sources.items() if k in used}
            complete = bool(result) and all(w['status'] == 'measured' for w in result)
            models = model_rows(all_samples, complete, now)
            days = []
            for day in sorted({utc(s['at'])[:10] for s in all_samples}):
                group = [s for s in all_samples if utc(s['at']).startswith(day)]
                days.append({'day': day, 'status': 'measured' if complete else 'partial', 'totals': add_tokens(group),
                             'estimated': any(s['estimated'] for s in group)})
            tasks = attribute(all_samples, spans, complete)
            return {'schema': 1, 'observed_at': utc(now), 'status': 'measured' if complete else 'partial' if all_samples else 'not_measured',
                    'coverage': {'status': 'collecting' if any(s.cursor or not s.checked for s in self.sources.values()) else 'ready' if complete else 'limited',
                                 'sources': len(self.sources), 'measured': measured_sources,
                                 'reasons': sorted({r for w in result for r in w['reasons']})},
                    'windows': result, 'models': models, 'tasks': tasks, 'days': days,
                    'quota_history': [quota_status(q, now) for q in all_quotas],
                    'suggestions': advice(models, result, now), 'cost': None}
        finally:
            self.lock.release()


def add_tokens(samples):
    result = {k: 0 for k in KINDS}
    for sample in samples:
        for k in KINDS:
            value = sample['tokens'][k]
            result[k] = None if value is None or result[k] is None or result[k] + value > MAX_NUMBER else result[k] + value
    return result


def total_tokens(samples):
    value = sum(s['tokens']['total'] for s in samples)
    return value if value <= MAX_NUMBER else None


def interval(samples, start, end, complete, *, include_end=True):
    group = [s for s in samples if start <= s['at'] and (s['at'] <= end if include_end else s['at'] < end)]
    covered = complete and bool(samples) and min(s['from'] for s in samples) <= start
    estimated = any(s['estimated'] for s in group)
    return {'from': utc(start), 'to': utc(end), 'status': 'not_measured' if total_tokens(group) is None else 'measured' if covered else 'partial' if group else 'not_measured',
            'total': total_tokens(group) if covered or group else None,
            'reason': 'counter_limit' if total_tokens(group) is None else None if covered else 'interval_not_fully_observed', 'estimated': estimated,
            'reasons': ['observation_attribution'] if estimated else []}


def hourly(samples, complete, now):
    hour = math.floor(now / 3600) * 3600
    groups = defaultdict(list)
    earliest = min((s['from'] for s in samples), default=now + 1)
    for sample in samples:
        groups[math.floor(sample['at'] / 3600) * 3600].append(sample)
    rows = []
    for start in range(hour - 167 * 3600, hour + 1, 3600):
        group = groups[start]
        covered = complete and earliest <= start
        estimated = any(s['estimated'] for s in group)
        rows.append({'hour': utc(start), 'from': utc(start), 'to': utc(min(start + 3600, now)),
                     'partial_hour': start == hour, 'status': 'not_measured' if total_tokens(group) is None else 'measured' if covered else 'partial' if group else 'not_measured',
                     'total': total_tokens(group) if group or covered else None,
                     'reason': 'counter_limit' if total_tokens(group) is None else None if covered else 'interval_not_fully_observed', 'estimated': estimated,
                     'reasons': ['observation_attribution'] if estimated else []})
    return rows


def rolling(samples, complete, now):
    return {key: interval(samples, now - seconds, now, complete) for key, seconds in (('h5', 18000), ('d7', 604800))}


def model_rows(samples, complete, now):
    rows = []
    for model in sorted({s['model'] for s in samples}, key=lambda v: v or ''):
        group = [s for s in samples if s['model'] == model]
        rows.append({'model': model, 'status': 'measured' if complete and model else 'partial',
                     'totals': add_tokens(group), 'windows': sorted({s.get('identity', '') for s in group}),
                     'hourly': hourly(group, complete and model is not None, now),
                     'rolling': rolling(group, complete and model is not None, now)})
    return rows


def quota_status(q, now):
    age = now - instant(q['observed_at'])
    return {**q, 'status': 'expired' if instant(q['resets_at']) <= now else 'stale' if age > 900 or age < 0 else 'measured'}


def latest_quotas(quota, now):
    latest = {}
    for q in sorted(quota, key=lambda q: q['observed_at']):
        latest[q['limit']] = quota_status(q, now)
    return list(latest.values())


def task_spans(events, state, now):
    agents = state.get('agents', {})
    identity = lambda actor: agents.get(actor, {}).get('identity')
    messages, deliveries, tasks, open_spans, spans = {}, {}, {}, {}, []
    acknowledged, closed = set(), set()
    def finish(task, end, role=None):
        for key, start in list(open_spans.items()):
            if key[1] == task and (role is None or key[2] == role):
                spans.append((key[0], task, start, end)); del open_spans[key]
    for event in events:
        kind, d, actor = event['kind'], event['data'], event['actor']
        at = instant(event['utc'])
        if kind == 'task':
            tasks[d['id']] = d
            if d.get('supersedes'):
                finish(d['supersedes'], at)
        elif kind == 'message':
            # Only routing keys are needed; message bodies never enter the collector cache.
            messages[d['id']] = {k: d[k] for k in ('task', 'related_task', 'to', 'packet', 'review', 'pool') if k in d}
        elif kind == 'deliver':
            finish(d['task'], at, 'build'); deliveries[d['task']] = d
        elif kind == 'review' and d.get('stage') == 'code':
            finish(d['task'], at, 'read')
        elif kind in {'close', 'supersede', 'cancel'}:
            finish(d['task'], at)
            closed.add(d['task'])
        elif kind in {'ack', 'start'}:
            task, role = d.get('task'), 'build'
            if kind == 'ack':
                if d.get('message') in acknowledged:
                    continue
                acknowledged.add(d.get('message'))
                m = messages.get(d.get('message'), {})
                task = m.get('task')
                if not task and m.get('related_task'):
                    task = m['related_task']; delivery = deliveries.get(task, {})
                    handoffs = [delivery.get('handoff')] + list(delivery.get('handoffs', {}).values()) if isinstance(delivery.get('handoffs', {}), dict) else delivery.get('handoffs', [])
                    if d.get('message') not in handoffs or m.get('to') != actor:
                        continue
                    if any(k[1:] == (task, 'read') for k in open_spans):
                        continue
                    role = 'read'
            if task in tasks and task not in closed and identity(actor) and (role == 'read' or identity(tasks[task].get('owner')) == identity(actor)):
                open_spans.setdefault((identity(actor), task, role), at)
    spans.extend((i, task, start, now) for (i, task, _), start in open_spans.items())
    return spans


def attribute(samples, spans, complete):
    grouped, reasons = defaultdict(list), defaultdict(set)
    for sample in samples:
        targets = sorted({task for identity, task, start, end in spans
                          if identity == sample['identity'] and start <= sample['at'] < end}) or [None]
        crossing = any(identity == sample['identity'] and sample['from'] < boundary < sample['at']
                       for identity, _, start, end in spans for boundary in (start, end))
        for index, task in enumerate(targets):
            part = {k: None if value is None else value // len(targets) + (index < value % len(targets))
                    for k, value in sample['tokens'].items()}
            part['total'] = sum(part[k] for k in PRICE_KINDS)
            grouped[task].append({'tokens': part})
            if len(targets) > 1: reasons[task].add('overlap_equal_split')
            if crossing or sample['estimated']: reasons[task].add('observation_attribution')
    return [{'task': task, 'status': 'measured' if complete else 'partial', 'totals': add_tokens(grouped[task]),
             'estimated': bool(reasons[task]), 'reasons': sorted(reasons[task])}
            for task in sorted(grouped, key=lambda t: t or '')]


def advice(models, windows, now):
    rows = []
    for model in models:
        totals = model['totals']
        if not model['model'] or model['status'] != 'measured' or totals['total'] is None or totals['total'] < 1000:
            continue
        for kind, threshold, key, label in [('cache_read', .75, 'cache', 'usage.adviceCache'), ('output', .5, 'output', 'usage.adviceOutput')]:
            share = totals[kind] / totals['total']
            if share >= threshold:
                values = {'total': totals['total'], 'share': round(share * 100, 1)}
                rows.append({'id': key, 'model': model['model'], 'window': None, 'metrics': values, 'vars': values, 'message_key': label})
    for window in windows:
        for q in window['quota']:
            if q['status'] == 'measured' and q['used_percent'] >= 80:
                values = {'percent': q['used_percent'], 'minutes': q['window_minutes']}
                rows.append({'id': 'quota', 'model': q['model'], 'window': window['identity'],
                             'metrics': values, 'vars': values, 'message_key': 'usage.adviceQuota'})
    return rows


def estimate(data, prices):
    if prices is None:
        return None
    if not isinstance(prices, dict) or set(prices) != {'currency', 'models'} or not re.fullmatch('[A-Z]{3}', str(prices.get('currency', ''))) or not isinstance(prices['models'], dict):
        raise Invalid('price_table_invalid')
    if not prices['models']:
        return None
    if len(prices['models']) > 100:
        raise Invalid('price_table_invalid')
    for model, rates in prices['models'].items():
        if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', model) or not isinstance(rates, dict) or set(rates) - set(PRICE_KINDS):
            raise Invalid('price_table_invalid')
        try:
            for value in rates.values(): number(value, fractional=True)
        except Invalid:
            raise Invalid('price_table_invalid') from None
    rows, excluded = [], []
    for m in data['models']:
        model, tokens = m['model'], m['totals']
        rates = prices['models'].get(model)
        reason = ('unknown_model' if not model else 'missing_tokens' if tokens is None or any(tokens.get(k) is None for k in PRICE_KINDS)
                  else 'missing_rate' if rates is None or any(k not in rates and tokens[k] != 0 for k in PRICE_KINDS) else None)
        amount = None if reason else sum(tokens[k] * rates.get(k, 0) / 1000000 for k in PRICE_KINDS)
        rows.append({'model': model, 'amount': amount, 'status': 'not_measured' if reason else 'estimated' if m.get('status') == 'measured' else 'partial'})
        if reason: excluded.append({'model': model, 'reason': reason})
    known = [r['amount'] for r in rows if r['amount'] is not None]
    return {'currency': prices['currency'], 'amount': sum(known) if known else None,
            'status': 'not_measured' if not known else 'partial' if excluded or data['status'] != 'measured' or any(r['status'] == 'partial' for r in rows) else 'estimated',
            'excluded': excluded, 'models': rows}


_COLLECTORS = OrderedDict()
_COLLECTOR_LOCK = threading.Lock()


def read_prices(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            s = os.fstat(fd)
            if not stat.S_ISREG(s.st_mode) or s.st_size > 65536:
                raise Invalid('price_table_invalid')
            data = json.loads(os.read(fd, 65537), object_pairs_hook=fields)
        finally:
            os.close(fd)
        return data['prices'] if isinstance(data, dict) and data.get('schema') == 1 else data
    except (OSError, ValueError, KeyError, TypeError):
        raise Invalid('price_table_invalid') from None


def collect(events, state, *, roots=None, now=None, budget_seconds=0.25):
    roots = source_roots() if roots is None else roots
    key = (events[0].get('hash', events[0].get('id')) if events else None,
           tuple(sorted((k, str(v)) for k, v in roots.items())))
    with _COLLECTOR_LOCK:
        if key not in _COLLECTORS:
            _COLLECTORS[key] = Collector(roots)
        collector = _COLLECTORS[key]
        _COLLECTORS.move_to_end(key)
        while len(_COLLECTORS) > 4:
            _COLLECTORS.popitem(last=False)
    return collector.collect(events, state, time.time() if now is None else now, budget_seconds)


def text_report(data):
    def count(row):
        total = row.get('totals', {}).get('total') if row.get('totals') else None
        return str(total) if total is not None else 'not measured'
    lines = ['Usage — tokens, not a subscription bill', 'Coverage: ' + data['status']]
    for w in data['windows']:
        total = count(w)
        lines.append(f"{w['identity']}: {total} tokens ({w['status']}; {', '.join(w['reasons']) or 'observed'})")
    for m in data['models']:
        lines.append(f"Model {m['model'] or 'unknown'}: {count(m)} tokens ({m['status']})")
    for t in data['tasks']:
        lines.append(f"Task {t['task'] or 'unassigned'}: {count(t)} tokens" + (' (estimated attribution)' if t['estimated'] else ''))
    if data['cost']:
        lines.append('Estimated cost: ' + json.dumps(data['cost'], ensure_ascii=False))
    lines.append(json.dumps({k: data[k] for k in ('days', 'quota_history', 'suggestions')}, ensure_ascii=False))
    return '\n'.join(lines)
