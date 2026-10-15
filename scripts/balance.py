"""Read-only advice on windows and models for a task, from recorded load, quota and history."""
from datetime import datetime, timezone
import json
import statistics
import time

import forecast
from forecast import instant
from registry import Error, next_step, standby

NEAR_QUOTA = 80
FRESH_SECONDS = 900
PENDING_READS = 2
OPEN_TASKS = 3
REFERENCE_BYTES = 256
REFERENCE_LIMIT = 10 ** 12
CLI_SECONDS = 30
BUILD_ROLES = frozenset({'builder', 'executor'})
READ_ROLES = frozenset({'reviewer', 'coordinator'})
NOTICE = 'Advice only: assigns nothing and changes no route, reviewer, budget or permission'
ASSUMPTION = 'All registered Claude windows are assumed to share one account'


def parse_reference(raw):
    """Validate a viewer-declared Claude reference; never stored or echoed."""
    if raw is None or raw == '' or raw == b'':
        return None
    data = raw.encode('utf-8') if isinstance(raw, str) else raw
    if not isinstance(data, bytes) or len(data) > REFERENCE_BYTES:
        raise Error('reference_invalid')

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate')
            result[key] = value
        return result
    try:
        value = json.loads(data.decode('utf-8'), object_pairs_hook=pairs, parse_constant=lambda _: 1 / 0)
    except (UnicodeError, ValueError, ZeroDivisionError, RecursionError):
        raise Error('reference_invalid') from None
    if not isinstance(value, dict) or set(value) != {'claude'} or not isinstance(value['claude'], dict) \
            or set(value['claude']) != {'h5', 'week'}:
        raise Error('reference_invalid')
    for number in value['claude'].values():
        if type(number) is not int or not 0 < number <= REFERENCE_LIMIT:
            raise Error('reference_invalid')
    return {'claude': dict(value['claude'])}


def identities(state):
    """Group seats by registry identity: load and quota belong to the window, not the alias."""
    groups = {}
    for agent in state['agents'].values():
        row = groups.setdefault(agent['identity'], {'identity': agent['identity'], 'seats': [], 'roles': set(),
                                                    'clients': set(), 'declared': set(), 'standby': False})
        row['seats'].append(agent['id'])
        row['roles'].add(agent['role'])
        row['clients'].add(agent.get('client') or 'manual')
        if agent.get('model'):
            row['declared'].add(agent['model'])
        row['standby'] = row['standby'] or standby(state, agent['id'])
    return groups


def open_tasks(state):
    return [t for t in state['tasks'].values() if t['status'] != 'done' and not t.get('superseded_by')]


def load(state):
    """Owned open tasks and next steps held, per identity, from the recorded state only."""
    seat = {a['id']: a['identity'] for a in state['agents'].values()}
    result = {i: {'owned_open': 0, 'build_steps': 0, 'read_steps': 0, 'reads': []} for i in set(seat.values())}
    for task in open_tasks(state):
        owner = seat.get(task['owner'])
        if owner in result:
            result[owner]['owned_open'] += 1
        step = next_step(task)
        for identity in {seat[w] for w in step['who'] if w in seat}:
            if step['role'] == 'reviewer':
                result[identity]['read_steps'] += 1
                result[identity]['reads'].append(task['id'])
            elif step['role'] == 'builder':
                result[identity]['build_steps'] += 1
    return result


def fresh(row, now):
    observed, resets = instant(row.get('observed_at') or ''), row.get('resets_at')
    reset = instant(resets) if isinstance(resets, str) else None
    percent = row.get('used_percent')
    return (row.get('status') == 'measured' and observed is not None and 0 <= now - observed <= FRESH_SECONDS
            and isinstance(percent, (int, float)) and not isinstance(percent, bool)
            and (resets is None or (reset is not None and reset > now)))


def codex_quota(rows, now):
    """Latest row per provider slot; near if any fresh slot reaches the threshold."""
    slots = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        key = (str(row.get('limit')), row.get('window_minutes'))
        if key not in slots or (instant(row.get('observed_at') or '') or 0) > (instant(slots[key].get('observed_at') or '') or 0):
            slots[key] = row
    shown = []
    for (limit, minutes), row in sorted(slots.items(), key=lambda item: (str(item[0][1]), item[0][0])):
        shown.append({'limit': row.get('limit'), 'window_minutes': minutes, 'used_percent': row.get('used_percent'),
                      'resets_at': row.get('resets_at'), 'observed_at': row.get('observed_at'),
                      'status': row.get('status'), 'fresh': fresh(row, now)})
    if any(s['fresh'] and s['used_percent'] >= NEAR_QUOTA for s in shown):
        state = 'near'
    elif shown and all(s['fresh'] for s in shown):
        state = 'clear'
    else:
        state = 'unknown'
    return {'kind': 'measured', 'state': state, 'slots': shown,
            'reason': None if shown else 'no_observation'}


def claude_estimate(windows, reference):
    """Estimate against the declared reference over the exact rolling sums of all Claude windows."""
    sums = {}
    for slot in ('h5', 'd7'):
        rows = [(w.get('rolling') or {}).get(slot) or {} for w in windows]
        known = [r for r in rows if type(r.get('total')) is int]
        complete = [r for r in known if r.get('status') == 'measured']
        status = 'measured' if rows and len(complete) == len(rows) else 'partial' if known else 'not_measured'
        sums[slot] = {'status': status, 'total': sum(r['total'] for r in known) if known else None,
                      'windows': len(rows), 'measured': len(complete),
                      'reasons': sorted({str(r.get('reason')) for r in rows if r not in complete})}
    result = {'kind': 'estimate', 'assumption': ASSUMPTION, 'reset': None, 'sums': sums, 'slots': [],
              'method': 'rolling token sums of all registered Claude windows divided by the declared reference'}
    if not windows:
        return {**result, 'state': 'unknown', 'reason': 'no_claude_window'}
    if reference is None:
        return {**result, 'state': 'unknown', 'reason': 'no_reference'}
    for slot, key in (('h5', 'h5'), ('d7', 'week')):
        total = sums[slot]['total']
        complete = sums[slot]['status'] == 'measured'
        result['slots'].append({'slot': slot, 'reference': reference['claude'][key], 'status': sums[slot]['status'],
                                'percent': 100 * total / reference['claude'][key] if complete else None})
    if any(s['percent'] is not None and s['percent'] >= NEAR_QUOTA for s in result['slots']):
        state = 'near'
    elif all(s['percent'] is not None for s in result['slots']):
        state = 'clear'
    else:
        state = 'unknown'
    return {**result, 'state': state, 'reason': None if state != 'unknown' else 'partial_series'}


def usage_windows(usage):
    if not isinstance(usage, dict):
        return {}
    return {w.get('identity'): w for w in usage.get('windows') or [] if isinstance(w, dict)}


def model_of(group, measured):
    names = sorted({m for m in measured if isinstance(m, str) and m})
    if len(names) == 1:
        return names[0], 'measured'
    if len(group['declared']) == 1:
        return next(iter(group['declared'])), 'declared'
    return None, 'not_declared'


def history(state, events, task_id, result):
    """Per-identity counts over one forecast cohort; figures only from MINIMUM samples."""
    if result is None or result.get('status') not in ('available', 'not_enough_history'):
        return None
    seat = {a['id']: a['identity'] for a in state['agents'].values()}
    rows = {}
    for sample in result.get('samples', []):
        old = state['tasks'].get(sample['task'])
        if old is None:
            continue
        builder = rows.setdefault(seat.get(old['owner']), {'built': [], 'verdicts': 0, 'changes': 0, 'read': 0})
        builder['built'].append(sample.get('seconds', {}).get('build'))
        for review in old.get('reviews', []):
            if review.get('stage') != 'code':
                continue
            changes = review.get('verdict') == 'changes-required'
            builder['verdicts'] += 1
            builder['changes'] += changes
            reader = rows.setdefault(seat.get(review.get('reviewer')), {'built': [], 'verdicts': 0, 'changes': 0, 'read': 0})
            reader['read'] += 1
            reader.setdefault('read_changes', 0)
            reader['read_changes'] += changes
    out = {}
    for identity, row in rows.items():
        builds = [s for s in row['built'] if isinstance(s, (int, float))]
        out[identity] = {
            'built': len(row['built']), 'read': row['read'],
            'median_build_minutes': statistics.median(builds) / 60 if len(builds) >= forecast.MINIMUM else None,
            'changes_required': ({'numerator': row['changes'], 'denominator': row['verdicts']}
                                 if row['verdicts'] >= forecast.MINIMUM else None),
            'read_changes_required': ({'numerator': row.get('read_changes', 0), 'denominator': row['read']}
                                      if row['read'] >= forecast.MINIMUM else None)}
    return out


def window_rows(state, usage, reference, now):
    groups, loads, measured = identities(state), load(state), usage_windows(usage)
    claude_ids = [i for i, g in groups.items() if 'claude' in g['clients']]
    estimate = claude_estimate([measured.get(i, {}) for i in claude_ids], reference) if usage is not None else None
    rows = {}
    for identity, group in sorted(groups.items()):
        seen = measured.get(identity)
        model, source = model_of(group, (seen or {}).get('models') or [])
        recent = (seen or {}).get('recent') or {}
        rate = recent.get('tokens_per_minute') if recent.get('status') == 'measured' else None
        if usage is None:
            quota = {'kind': None, 'state': 'unknown', 'slots': [], 'reason': 'not_collected'}
        elif 'codex' in group['clients'] and seen is not None:
            quota = codex_quota(seen.get('quota'), now)
        elif 'claude' in group['clients']:
            quota = estimate
        else:
            quota = {'kind': None, 'state': 'unknown', 'slots': [], 'reason': 'not_measured'}
        rows[identity] = {
            'identity': identity, 'seats': sorted(group['seats']), 'roles': sorted(group['roles']),
            'clients': sorted(group['clients']), 'model': model, 'model_source': source,
            'available': not group['standby'], **{k: v for k, v in loads[identity].items()},
            'quota': quota,
            'tokens_per_hour': rate * 60 if isinstance(rate, (int, float)) and not isinstance(rate, bool) else None,
            'tokens_status': 'not_collected' if usage is None else recent.get('status', 'not_measured'),
            'hourly': ((seen or {}).get('hourly') or [])[-24:]}
    return rows, groups, estimate


DIMENSIONS = ('quota', 'steps', 'owned', 'history', 'rate')


def rank(candidates, history_rows):
    """Lexicographic order; a dimension missing for any eligible row is omitted for the whole list."""
    omitted = []
    unknown = [r['identity'] for r in candidates if r['quota']['state'] == 'unknown']
    use_quota = not unknown
    if unknown:
        omitted.append({'dimension': 'quota', 'windows': unknown})
    use_history = history_rows is not None
    if not use_history:
        omitted.append({'dimension': 'history', 'windows': [r['identity'] for r in candidates]})
    missing = [r['identity'] for r in candidates if r['tokens_per_hour'] is None]
    use_rate = not missing
    if missing:
        omitted.append({'dimension': 'rate', 'windows': missing})
    used = [d for d, on in zip(DIMENSIONS, (use_quota, True, True, use_history, use_rate)) if on]

    def key(row):
        parts = []
        if use_quota:
            parts.append(row['quota']['state'] == 'near')
        parts += [row['read_steps'] + row['build_steps'], row['owned_open']]
        if use_history:
            parts.append(-row['history_count'])
        if use_rate:
            parts.append(row['tokens_per_hour'])
        return (*parts, row['identity'])
    return sorted(candidates, key=key), used, omitted


def sentence(row, slot):
    held = row['read_steps'] + row['build_steps']
    quota = row['quota']
    parts_it = [f"{held} passi in attesa", f"{row['owned_open']} incarichi aperti"]
    parts_en = [f"{held} next steps held", f"{row['owned_open']} open tasks"]
    if quota['state'] == 'near':
        parts_it.append('vicina al limite di quota' + (' (stima)' if quota['kind'] == 'estimate' else ''))
        parts_en.append('near its quota' + (' (estimate)' if quota['kind'] == 'estimate' else ''))
    elif quota['state'] == 'unknown':
        parts_it.append('quota non misurata')
        parts_en.append('quota not measured')
    if row.get('history_count') is not None:
        word = 'costruiti' if slot == 'build' else 'letti'
        parts_it.append(f"{row['history_count']} lavori simili {word}")
        parts_en.append(f"{row['history_count']} similar tasks {'built' if slot == 'build' else 'read'}")
    return {'it': ', '.join(parts_it) + '.', 'en': ', '.join(parts_en) + '.'}


def slot_list(rows, groups, roles, excluded_identities, history_rows, slot):
    candidates, excluded = [], []
    for identity, row in rows.items():
        group = groups[identity]
        if not group['roles'] & roles:
            continue
        if identity in excluded_identities:
            excluded.append({'identity': identity, 'reason': 'author'})
        elif not row['available']:
            excluded.append({'identity': identity, 'reason': 'stand-by'})
        else:
            stats = (history_rows or {}).get(identity, {'built': 0, 'read': 0})
            candidates.append({**row, 'history': (history_rows or {}).get(identity),
                               'history_count': None if history_rows is None else stats['built' if slot == 'build' else 'read']})
    ordered, used, omitted = rank(candidates, history_rows)
    for position, row in enumerate(ordered, 1):
        row['position'] = position
        row['sentence'] = sentence(row, slot)
    return {'rows': ordered, 'excluded': sorted(excluded, key=lambda e: e['identity']), 'order': used, 'omitted': omitted}


def suggestions(rows, groups, state):
    """S1–S4: advisory rules, each with the numbers it rests on; missing input keeps a rule silent."""
    found = []
    seat = {a['id']: a['identity'] for a in state['agents'].values()}
    readers = [r for i, r in rows.items() if groups[i]['roles'] & READ_ROLES and r['available']]
    for source in sorted(readers, key=lambda r: r['identity']):
        if source['read_steps'] < PENDING_READS:
            continue
        for task_id in sorted(source['reads']):
            task = state['tasks'][task_id]
            authors = {seat[a] for a in task.get('authors', []) if a in seat}
            target = next((r for r in sorted(readers, key=lambda r: r['identity'])
                           if r['read_steps'] == 0 and r['identity'] not in authors and r['identity'] != source['identity']), None)
            if target:
                found.append({'id': 'S1', 'model': source['model'], 'window': source['identity'], 'to': target['identity'],
                              'task': task_id, 'metrics': {'pending_reads': source['read_steps'], 'target_pending_reads': 0}})
                break
    for row in rows.values():
        quota = row['quota']
        if row['available'] and quota['state'] == 'near':
            if quota['kind'] == 'measured':
                hot = [s for s in quota['slots'] if s['fresh'] and s['used_percent'] >= NEAR_QUOTA]
                metrics = {'slots': hot, 'steps': row['read_steps'] + row['build_steps']}
            else:
                metrics = {'slots': [s for s in quota['slots'] if s['percent'] is not None and s['percent'] >= NEAR_QUOTA],
                           'steps': row['read_steps'] + row['build_steps'], 'estimate': True, 'reset': None}
            found.append({'id': 'S2', 'model': row['model'], 'window': row['identity'], 'metrics': metrics})
    builders = [r for i, r in rows.items() if groups[i]['roles'] & BUILD_ROLES and r['available']]
    for row in sorted(builders, key=lambda r: r['identity']):
        if row['owned_open'] < OPEN_TASKS:
            continue
        free = next((r for r in sorted(builders, key=lambda r: r['identity'])
                     if r['owned_open'] == 0 and set(r['clients']) & set(row['clients'])), None)
        if free:
            found.append({'id': 'S3', 'model': row['model'], 'window': row['identity'], 'to': free['identity'],
                          'metrics': {'owned_open': row['owned_open'], 'target_owned_open': 0}})
    busy = {s['model'] for s in found if s['id'] in ('S1', 'S3')}
    if busy:
        by_model = {}
        for identity, row in rows.items():
            if row['model'] and row['available'] and groups[identity]['roles'] & (BUILD_ROLES | {'reviewer'}):
                by_model.setdefault(row['model'], []).append(row)
        for model, members in sorted(by_model.items()):
            if model in busy:
                continue
            if all(r['owned_open'] == 0 and r['read_steps'] + r['build_steps'] == 0 for r in members):
                found.append({'id': 'S4', 'model': model, 'window': members[0]['identity'],
                              'metrics': {'windows': len(members), 'owned_open': 0, 'busy_models': sorted(m or '' for m in busy)}})
    return found


def calculate(state, events, task_id=None, usage=None, reference=None, read_at=None, forecast_result=None):
    """Pure: no event, file or registry change. `usage` is SQ-TOKENS schema 1, or None when not collected."""
    now = datetime.now(timezone.utc).timestamp() if read_at is None else read_at
    if task_id is not None and task_id not in state['tasks']:
        raise Error('Unknown task: ' + task_id)
    rows, groups, estimate = window_rows(state, usage, reference, now)
    models = {}
    for row in rows.values():
        models.setdefault(row['model'] or '', []).append(row['identity'])
    measured_models = {m.get('model'): m for m in (usage or {}).get('models') or [] if isinstance(m, dict)}
    result = {'notice': NOTICE, 'writes': False, 'assigns': False,
              'read_at': datetime.fromtimestamp(now, timezone.utc).isoformat(),
              'constants': {'near_quota_percent': NEAR_QUOTA, 'fresh_seconds': FRESH_SECONDS,
                            'pending_reads': PENDING_READS, 'open_tasks': OPEN_TASKS, 'minimum_samples': forecast.MINIMUM},
              'usage': ({'status': 'not_collected', 'reason': 'run squadra balance for tokens and quota'} if usage is None
                        else {'status': usage.get('status'), 'coverage': usage.get('coverage'),
                              'observed_at': usage.get('observed_at'), 'schema': usage.get('schema')}),
              'claude_estimate': estimate,
              'windows': [rows[i] for i in sorted(rows)],
              'models': [{'model': m or None, 'windows': sorted(ids),
                          'hourly': ((measured_models.get(m) or {}).get('hourly') or [])[-24:]} for m, ids in sorted(models.items())],
              'suggestions': suggestions(rows, groups, state)}
    if task_id is None:
        return result
    task = state['tasks'][task_id]
    if task['status'] == 'done' or task.get('superseded_by'):
        result['task'] = {'id': task_id, 'status': 'task_not_open'}
        return result
    cohort = forecast_result if forecast_result is not None else forecast.calculate(state, events, task_id)
    history_rows = history(state, events, task_id, cohort)
    seat = {a['id']: a['identity'] for a in state['agents'].values()}
    authors = {seat[a] for a in task.get('authors', []) if a in seat}
    result['task'] = {'id': task_id, 'status': 'ranked',
                      'history': {'status': cohort.get('status'), 'samples': [s['task'] for s in cohort.get('samples', [])]},
                      'build': slot_list(rows, groups, BUILD_ROLES, set(), history_rows, 'build'),
                      'read': slot_list(rows, groups, READ_ROLES, authors, history_rows, 'read')}
    return result


def collect_usage(events, state, seconds=CLI_SECONDS):
    """Loop the SQ-TOKENS collector's bounded slices until ready or the deadline."""
    import usage
    end = time.monotonic() + seconds
    while True:
        result = usage.collect(events, state)
        if (result.get('coverage') or {}).get('status') != 'collecting' or time.monotonic() >= end:
            return result
