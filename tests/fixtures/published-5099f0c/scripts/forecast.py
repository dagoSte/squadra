"""Read-only comparisons of recorded task scopes and elapsed intervals."""
from datetime import datetime
from functools import lru_cache
import hashlib
import json
from pathlib import Path, PurePosixPath
import statistics
import subprocess
import sys

from registry import Error, plain

MINIMUM = 3


def working_snapshot(repo):
    """Bound the existing fingerprint reader without leaving a worker behind."""
    program = 'import sys,json;sys.path.insert(0,sys.argv[1]);from registry import snapshot;print(json.dumps(snapshot(sys.argv[2])))'
    result = subprocess.run([sys.executable, '-I', '-B', '-c', program, str(Path(__file__).parent), repo],
                            capture_output=True, check=True, timeout=15)
    return json.loads(result.stdout)


def instant(value):
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return date.timestamp() if date.tzinfo else None
    except (TypeError, ValueError, AttributeError, OverflowError):
        return None


@lru_cache(maxsize=16)
def base_manifest(repo, base):
    def git(*args, data=None):
        return subprocess.run(['git', *args], cwd=repo, input=data, capture_output=True,
                              check=True, timeout=10).stdout
    rows = []
    for raw in git('ls-tree', '-rz', '--full-tree', base).split(b'\0'):
        if not raw:
            continue
        meta, path = raw.split(b'\t', 1)
        mode, kind, oid = meta.decode().split()
        if kind != 'blob' or mode not in ('100644', '100755', '120000'):
            raise ValueError('unsupported base entry')
        rows.append((path.decode('utf-8'), mode, oid))
    bodies = git('cat-file', '--batch', data=''.join(oid + '\n' for _, _, oid in rows).encode())
    result, offset = {}, 0
    for path, mode, oid in rows:
        end = bodies.index(b'\n', offset)
        found, kind, size = bodies[offset:end].decode().split()
        if found != oid or kind != 'blob':
            raise ValueError('unavailable base blob')
        start = end + 1
        offset = start + int(size) + 1
        body = bodies[start:offset - 1]
        result[path] = ('symlink' if mode == '120000' else 'file',
                        hashlib.sha256(body).hexdigest(), mode == '100755')
    return result


def changed_paths(task, current, artifacts_root=None):
    current = plain(current)
    if isinstance(current, dict) and "manifest" in current:
        from source_manifests import resolve
        if artifacts_root is None:
            raise ValueError('snapshot artifact location unavailable')
        current = resolve(current, artifacts_root)
    if not isinstance(current, dict) or not isinstance(current.get('files'), list):
        raise ValueError('snapshot unavailable')
    if not Path(task['repo']).is_dir():
        raise ValueError('repository unavailable')
    before = base_manifest(task['repo'], task['base'])
    after = {}
    for row in current['files']:
        path = row['path']
        if row['kind'] == 'deleted':
            continue
        if path in after:
            raise ValueError('duplicate snapshot path')
        after[path] = (row['kind'], row['sha256'], bool(row.get('executable', False)))
    return sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))


def band(paths):
    return '1-5' if 1 <= len(paths) <= 5 else '6-15' if len(paths) <= 15 and paths else '16+' if paths else None


def overlap(left, right):
    parents = lambda paths: {str(PurePosixPath(p).parent) for p in paths} - {'.'}
    return {'files': sorted(set(left) & set(right)), 'directories': sorted(parents(left) & parents(right))}


def timings(events, task_id):
    """Match the dashboard's fix > plan > read > ACK > initial-build partition."""
    relevant = [e for e in events if e['data'].get('task', e['data'].get('id')) == task_id]
    assignments = {e['data']['id'] for e in relevant if e['kind'] == 'message' and e['data'].get('task') == task_id
                   and e['data']['id'] == 'assign-' + task_id}
    acks = [e for e in events if e['kind'] == 'ack' and e['data'].get('message') in assignments]
    created = next((instant(e['utc']) for e in relevant if e['kind'] == 'task'), None)
    closed = next((instant(e['utc']) for e in reversed(relevant) if e['kind'] == 'close'), None)
    ack = next((instant(e['utc']) for e in acks), None)
    if any(x is None for x in (created, closed, ack)) or not created <= ack <= closed:
        return None
    steps = [e for e in relevant if e['kind'] in ('plan', 'deliver', 'review')]
    if any(instant(e['utc']) is None or not created <= instant(e['utc']) <= closed for e in steps):
        return None
    deliveries = [e for e in steps if e['kind'] == 'deliver']
    if not deliveries:
        return None
    intervals = [('ack', created, ack), ('build', ack, instant(deliveries[0]['utc']))]
    for index, event in enumerate(steps):
        kind, data = event['kind'], event['data']
        later = steps[index + 1:]
        if kind in ('plan', 'deliver'):
            phase, stage = ('plan', 'plan') if kind == 'plan' else ('read', 'code')
            following = next((e for e in later if e['kind'] == 'review' and e['data']['stage'] == stage), None)
        elif data.get('verdict') == 'changes-required':
            phase = 'fix'
            following = next((e for e in later if e['kind'] in ('plan', 'deliver')), None)
        else:
            continue
        if following is None:
            return None
        intervals.append((phase, instant(event['utc']), instant(following['utc'])))
    if any(b < a for _, a, b in intervals):
        return None
    values = dict.fromkeys(('ack', 'plan', 'build', 'read', 'fix', 'other'), 0)
    points = sorted({created, closed, *(x for _, a, b in intervals for x in (a, b))})
    for a, b in zip(points, points[1:]):
        key = next((key for key in ('fix', 'plan', 'read', 'ack', 'build')
                    if any(k == key and low <= a and high >= b for k, low, high in intervals)), 'other')
        values[key] += b - a
    code = [e for e in steps if e['kind'] == 'review' and e['data'].get('stage') == 'code']
    return {'seconds': values, 'code_reads': len(code),
            'changes_required': sum(e['data'].get('verdict') == 'changes-required' for e in code)}


def calculate(state, events, task_id):
    if task_id not in state['tasks']:
        raise Error('Unknown task: ' + task_id)
    task = state['tasks'][task_id]
    artifacts = Path(state['root']) / 'artifacts' if state.get('root') else None
    indexed = getattr(state, 'current', None)
    indexed_events = indexed.get('forecast_events') if indexed is not None else None
    route = task.get('route', 'full')
    result = {'task': task_id, 'minimum': MINIMUM, 'status': 'scope_unavailable', 'samples': [], 'excluded': [],
              'similarity': 'Same route, changed-file band, and shared file or non-root parent directory',
              'suggested_route': 'full', 'route_reason': 'Insufficient evidence; coordinator triage required',
              'notice': 'Historical elapsed intervals, not active effort or a completion guarantee', 'writes': False}
    if task.get('status') == 'done' or task.get('superseded_by'):
        result['status'] = 'task_not_open'
        return result
    try:
        current = task.get('candidate') or working_snapshot(task['repo'])
        paths = changed_paths(task, current, artifacts)
    except (Error, OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        return result
    result['scope'] = {'source': 'recorded_candidate' if task.get('candidate') else 'working_tree',
                       'fingerprint': current['sha256'], 'paths': paths, 'band': band(paths), 'route': route}
    if not paths:
        return result
    for old in state['tasks'].values():
        if old['id'] == task_id or old.get('status') != 'done' or old.get('superseded_by'):
            continue
        reason = None
        try:
            delivered = old.get('delivery', {}).get('snapshot')
            previous = changed_paths(old, delivered, artifacts)
            shared = overlap(paths, previous)
            if old.get('route', 'full') != route or band(previous) != band(paths) or not any(shared.values()):
                reason = 'not_comparable'
            measured = timings(indexed_events.get(old['id'], []) if indexed_events is not None else events, old['id']) if reason is None else None
            if reason is None and (measured is None or measured['code_reads'] == 0):
                reason = 'incomplete_timing'
            if reason is None:
                result['samples'].append({'task': old['id'], 'band': band(previous), 'shared': shared,
                                          'fingerprint': delivered['sha256'], **measured})
        except (Error, OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
            reason = 'historical_scope_unavailable'
        if reason:
            result['excluded'].append({'task': old['id'], 'reason': reason})
    result['status'] = 'not_enough_history'
    if len(result['samples']) >= MINIMUM:
        samples = result['samples']
        numerator = sum(s['changes_required'] for s in samples)
        denominator = sum(s['code_reads'] for s in samples)
        result.update(status='available', suggested_route=route,
                      route_reason='Retain the registered route; historical results do not override triage or light eligibility',
                      median_build_minutes=statistics.median(s['seconds']['build'] for s in samples) / 60,
                      median_read_minutes=statistics.median(s['seconds']['read'] for s in samples) / 60,
                      changes_required={'numerator': numerator, 'denominator': denominator,
                                        'percent': 100 * numerator / denominator})
    else:
        for sample in result['samples']:
            for key in ('seconds', 'code_reads', 'changes_required'):
                sample.pop(key)
    return result
