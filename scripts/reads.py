"""Recorded reads and subsequent revisions; chronology does not prove causation."""
from collections import Counter
import json
from pathlib import Path

from registry import FINDINGS_BYTES, SEVERITIES, validate_findings

VERDICTS = ('accepted', 'changes-required', 'blocked')

def load_findings(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate review findings key')
            result[key] = value
        return result
    with Path(path).open('rb') as stream:
        body = stream.read(FINDINGS_BYTES + 1)
    if len(body) > FINDINGS_BYTES:
        raise ValueError('Review findings exceed 16 KiB')
    try:
        value = json.loads(body.decode('utf-8'), object_pairs_hook=unique)
    except (UnicodeError, ValueError):
        raise ValueError('Invalid review findings JSON') from None
    return validate_findings(value)


def totals(reads, revisions):
    verdicts = Counter(r['verdict'] if r['verdict'] in VERDICTS else 'unsupported' for r in reads)
    reporting = [r for r in reads if r['findings_status'] == 'recorded']
    findings = [f for r in reporting for f in r['findings']]
    severity = Counter(f['severity'] for f in findings)
    return {'reads': len(reads), 'verdicts': {k: verdicts[k] for k in (*VERDICTS, 'unsupported')},
            'accepted_with_conditions': None,
            'code_revisions': sum(r['stage'] == 'code' for r in revisions),
            'plan_revisions': sum(r['stage'] == 'plan' for r in revisions),
            'observations': len(findings) if reporting else None,
            'reporting_reads': len(reporting),
            'unrecorded_reads': sum(r['findings_status'] == 'unrecorded' for r in reads),
            'unsupported_reads': sum(r['findings_status'] == 'unsupported' for r in reads),
            'severities': {k: severity[k] for k in SEVERITIES}}


def read_value(events):
    versions, latest, tasks, reads, revisions = {}, {}, {}, [], []
    for event in events:
        data, kind = event['data'], event['kind']
        task = data.get('task')
        if kind == 'task':
            tasks[data['id']] = {'id': data['id'], 'title': data['title']}
        elif kind in ('plan', 'deliver'):
            stage = 'plan' if kind == 'plan' else 'code'
            key = (task, stage)
            version = (data.get('artifact') if stage == 'plan' else data.get('snapshot')) or {}
            sha = version.get('sha256')
            previous, review = versions.get(key), latest.get(key)
            if (sha and previous and previous['sha256'] and sha != previous['sha256'] and review
                    and review['binding'] == previous['sha256'] and review['verdict'] == 'changes-required'):
                revisions.append({'task': task, 'stage': stage, 'read_id': review['id'],
                                  'read_seq': review['seq'], 'from_seq': previous['seq'],
                                  'to_seq': event['seq'], 'from_sha256': previous['sha256'],
                                  'to_sha256': sha, 'report': review['report']})
            if not previous or sha != previous['sha256']:
                latest.pop(key, None)
            versions[key] = {'sha256': sha, 'seq': event['seq']}
        elif kind == 'review':
            status, findings = 'unrecorded', []
            if 'findings' in data:
                try:
                    findings = validate_findings(data['findings'])
                    status = 'recorded'
                except (ValueError, TypeError):
                    status = 'unsupported'
            read = {'id': event['id'], 'seq': event['seq'], 'utc': event['utc'], 'task': task,
                    'stage': data.get('stage'), 'verdict': data.get('verdict'), 'reader': event['actor'],
                    'binding': data.get('binding'), 'covers': data.get('covers', []),
                    'report': data.get('report'), 'findings_status': status,
                    'findings': [{**f, 'id': event['id'] + ':' + str(i)} for i, f in enumerate(findings)]}
            reads.append(read)
            key = (task, read['stage'])
            if read['binding'] and versions.get(key, {}).get('sha256') == read['binding']:
                latest[key] = read
    per_task = []
    for task in tasks.values():
        per_task.append({**task, 'totals': totals([r for r in reads if r['task'] == task['id']],
                                                 [r for r in revisions if r['task'] == task['id']])})
    head = {k: events[-1][k] for k in ('seq', 'hash')} if events else {'seq': 0, 'hash': None}
    return {'head': head, 'totals': totals(reads, revisions), 'tasks': per_task, 'reads': reads,
            'revisions': revisions, 'availability': {'accepted_with_conditions': 'not-recorded',
                                                    'findings': 'optional', 'causation': 'not-recorded'}}
