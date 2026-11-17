"""Explicit dispositions of identified review observations; never acceptance gates."""
import json
from contextlib import closing
import sqlite3
import unicodedata
from pathlib import Path

from registry import require

OUTCOMES = ('fixed', 'rejected', 'deferred')


def identified(review):
    return [f for f in review.get('findings', []) if f.get('id')]


def receipt(review):
    return review.get('receipt') or review.get('request')


def summary(state, task):
    outcomes = {(o['review'], o['finding']): o for o in task.get('finding_outcomes', [])}
    rounds, rejected = [], []
    for review in task.get('reviews', []):
        observations, key = identified(review), receipt(review)
        if not observations:
            continue
        opened = 0
        for finding in observations:
            outcome = outcomes.get((key, finding['id']))
            if outcome is None:
                opened += 1
            elif outcome['outcome'] == 'rejected':
                rejected.append({'review': key, 'id': finding['id'],
                                 'reason': outcome['reason'], 'ref': outcome['ref']})
        rounds.append({'review': key, 'open': opened, 'total': len(observations)})
    return {'rounds': rounds, 'rejected': rejected, 'open': sum(r['open'] for r in rounds)}


def observation(state, task):
    value = summary(state, task)
    if not value['rounds']:
        return None
    return {'id': 'finding_outcomes', 'status': 'unverifiable' if value['open'] else 'ok',
            'detail': str(value['open']) + ' identified findings without an outcome', **value}


def validate_event(state, actor, data):
    require(set(data) == {'task', 'review', 'finding', 'outcome', 'ref', 'reason'}, 'finding_fields_invalid')
    require(isinstance(data.get('task'), str), 'finding_task_unknown')
    task = state['tasks'].get(data['task'])
    require(task is not None, 'finding_task_unknown')
    allowed = [task['owner'], task['coordinator'], task.get('reviewer'), *(task.get('pool') or [])]
    require(actor in allowed and actor in state['agents'], 'finding_actor_refused')
    review = next((r for r in task['reviews'] if receipt(r) == data.get('review')), None)
    require(review is not None, 'finding_review_unknown')
    require(isinstance(data.get('finding'), str) and
            any(f['id'] == data['finding'] for f in identified(review)), 'finding_id_unknown')
    require(data.get('outcome') in OUTCOMES, 'finding_outcome_invalid')
    reason = data.get('reason')
    require(isinstance(reason, str) and 1 <= len(reason) <= 1000 and reason.strip() and
            not any(unicodedata.category(c).startswith('C') for c in reason), 'finding_reason_required')
    ref = data.get('ref')
    require(isinstance(ref, str) and 1 <= len(ref) <= 200 and ref.strip() == ref, 'finding_ref_required')
    if ref.startswith('decision:'):
        require(ref[9:] in state.get('refs', {}).get('decision', {}), 'finding_ref_unknown')
    else:
        # References must already be committed, including when replaying an old outcome.
        path = Path(state['root']) / 'registry.sqlite3'
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as connection:
            row = connection.execute('SELECT seq, data FROM events WHERE id=? OR CAST(seq AS TEXT)=?',
                                     (ref, ref)).fetchone()
        require(row is not None and row[0] <= state['event_count'], 'finding_ref_unknown')
        target = json.loads(row[1])
        run = state.get('runs', {}).get(target.get('id'), {})
        associated = [target.get('task'), target.get('related_task'), target.get('id'), run.get('task')]
        associated.extend(t['task'] for t in (run.get('shared_proof') or {}).get('tasks', []))
        require(task['id'] in associated,
                'finding_ref_wrong_task')
