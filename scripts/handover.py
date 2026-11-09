"""Owner-authorized, two-step seat handovers using the replacement planner."""
import datetime as dt
from pathlib import Path

from registry import (CHANNELS, Error, Store, canonical, control_free, digest,
                      now, plain, require, standby, validate)
from replacement import Plan, deliver_outbox, peers, validate_event as validate_plan

HEADINGS = ('## Server e sessioni aperte', '## Rilasci o invii in corso',
            '## Promesse al proprietario', '## Altro')
OUTSIDE_BYTES = 8192


def safe_text(text):
    import re
    require(not re.search(r'(?i)(?<![\w-])(?:sk-|ghp_|AKIA)[A-Za-z0-9_+/=-]{8,}|'
                          r'-----BEGIN [^-\r\n]*PRIVATE KEY-----|'
                          r'^[ \t]*(?:export[ \t]+)?[\"\']?[\w.-]*(?:password|passwd|token|secret)'
                          r'[\"\']?[ \t]*[=:][ \t]*\S', text, re.M),
            'Outside state contains a recognizable secret; remove its value')


def outside_text(body):
    require(len(body) <= OUTSIDE_BYTES, 'Outside state exceeds 8192 UTF-8 bytes')
    try:
        text = body.decode('utf-8')
    except UnicodeError as exc:
        raise Error('Outside state must be UTF-8') from exc
    safe_text(text)
    sections = {}
    current = None
    for line in text.splitlines():
        if line.strip() in HEADINGS:
            current = line.strip()
            require(current not in sections, 'Duplicate outside-state section')
            sections[current] = []
        elif line.startswith('## '):
            current = None
        elif current:
            sections[current].append(line)
    require(all(''.join(sections.get(h, [])).strip() for h in HEADINGS),
            'Outside state needs all four nonempty sections: ' + ', '.join(HEADINGS))
    return text


def read_outside(root, outside):
    require(isinstance(outside, dict) and set(outside) in ({'artifact'}, {'unknown'}),
            'Declare outside state with a file or an explicit unknown reason')
    if 'unknown' in outside:
        value = outside['unknown']
        require(isinstance(value, str) and 0 < len(value.strip()) <= OUTSIDE_BYTES,
                'Outside-unknown reason required (at most 8192 characters)')
        safe_text(value)
        return {'unknown': value, 'loss_declared': True}
    ref = outside['artifact']
    import re
    require(isinstance(ref, dict) and isinstance(ref.get('sha256'), str) and
            re.fullmatch('[0-9a-f]{64}', ref['sha256']), 'Invalid outside artifact')
    path = Path(root) / 'artifacts' / ref['sha256']
    require(not path.is_symlink(), 'Outside artifact must be a regular file')
    body = path.read_bytes()
    require(digest(body) == ref['sha256'] and len(body) == ref.get('bytes'), 'Outside artifact changed')
    return {'artifact': plain(ref), 'text': outside_text(body)}


def parse_conditions(state, values):
    rows = []
    for n, value in enumerate(values, 1):
        kind, separator, target = value.partition(':')
        require(separator and kind in {'task', 'after', 'confirm'} and target.strip(),
                'Condition must be task:ID, after:ISO8601 or confirm:text')
        if kind == 'task':
            require(target in state['tasks'], 'Unknown condition task: ' + target)
        elif kind == 'after':
            try:
                stamp = dt.datetime.fromisoformat(target)
                require(stamp.tzinfo is not None, 'After condition needs a timezone')
            except ValueError as exc:
                raise Error('Invalid after condition timestamp') from exc
        rows.append({'n': n, 'kind': kind, 'value': target})
    return rows


def pending(state, ident):
    handover = state.get('handovers', {}).get(ident)
    require(handover is not None and handover['status'] == 'pending', 'Pending handover required')
    return handover


def coordinator(state, actor):
    return actor in state['agents'] and state['agents'][actor]['role'] == 'coordinator' and not standby(state, actor)


def decision_for(data):
    result = {'id': data['id'] + '-decision',
              'reason': 'Owner-authorized handover: ' + data['from'] + ' -> ' + data['to'],
              'owner_words': data['owner_words'], 'channel': data['channel']}
    if data.get('answers'):
        result.update(answers=data['answers'], choice=data['choice'])
    return result


def validate_prepare(state, actor, data):
    require(set(data) == {'id', 'from', 'to', 'owner_words', 'channel', 'answers', 'choice', 'conditions', 'outside', 'decision'},
            'Invalid handover preparation fields')
    old, new = data['from'], data['to']
    require(old in state['agents'], 'Unknown origin seat')
    require(actor in peers(state, old) or coordinator(state, actor), 'Origin seat or active coordinator required')
    require(isinstance(new, str) and new.strip() and control_free(new) and new not in state['agents'],
            'Choose a new, unregistered seat name')
    leaving = peers(state, old)
    require(not any(h['status'] == 'pending' and (h['from'] in leaving or h['to'] == new)
                    for h in state.get('handovers', {}).values()), 'A handover is already pending for this seat or target')
    require(data['conditions'] == parse_conditions(state, [c['kind'] + ':' + c['value'] for c in data['conditions']]),
            'Invalid handover conditions')
    read_outside(state['root'], data['outside'])
    require(data['channel'] in CHANNELS, 'Owner instruction channel required')
    if not data['answers']:
        require(isinstance(data['owner_words'], str) and data['owner_words'].strip() and not data['choice'],
                'Owner words and channel are required, or answer a recorded ask')
    require(data['decision'] == decision_for(data), 'Handover decision differs from owner authorization')
    # Reuse the decision validator, including open-ask/choice and link checks. This
    # authorization belongs to the preparation actor; it grants no coordinator role.
    authority = next((name for name in state['agents'] if coordinator(state, name)), None)
    require(authority is not None, 'Reconcile an active coordinator before preparing a handover')
    validate(state, 'decision', authority, data['decision'])
    expected = 'handover-' + digest(canonical({k: v for k, v in data.items() if k not in {'id', 'decision'}}).encode())
    require(data['id'] == expected and data['id'] not in state.get('handovers', {}), 'Invalid or reused handover ID')


def evaluate(state, handover, stamp):
    try:
        from handover_conditions import evaluate as conditions
    except ImportError as exc:
        raise Error('Handover conditions component unavailable; install the complete coordinated version') from exc
    rows = conditions(state, handover, stamp)
    expected = handover['conditions']
    require(isinstance(rows, list) and len(rows) == len(expected) and all(
        isinstance(row, dict) and all(row.get(k) == condition[k] for k in ('n', 'kind', 'value'))
        and type(row.get('met')) is bool for row, condition in zip(rows, expected)),
        'Invalid handover condition evaluation')
    return rows


def safe_moment(store, handover):
    import checklist
    try:
        gate = checklist.run(store, 'handover', actor=handover['from'])
    except (ValueError, KeyError) as exc:
        raise Error('Handover gate unavailable; install the complete coordinated version') from exc
    require(isinstance(gate, dict) and type(gate.get('ready')) is bool, 'Invalid handover gate result')
    return gate


def check_accept(store, state, handover, actor, session, client):
    require(not state['paused'], 'Workspace paused; reconcile before accepting a handover')
    require(actor == handover['to'] and actor not in state['agents'], 'New handover seat must accept for itself')
    require(isinstance(session, str) and session.strip() and control_free(session), 'Actual new session ID required')
    require(client in {'codex', 'claude', 'cursor'}, 'Native handover client required')
    require(not any(a.get('session') == session or a['identity'] == session for a in state['agents'].values()),
            'Session already registered; reconcile its identity first')
    leaving = peers(state, handover['from'])
    if any(state['agents'][name]['role'] == 'coordinator' for name in leaving):
        require(not any(name not in leaving and coordinator(state, name) for name in state['agents']),
                'Reconcile competing active coordinators before handover')
    gate = safe_moment(store, handover)
    require(gate['ready'] and not any(i.get('status') == 'fail' for i in gate.get('items', [])),
            'Handover safe-moment gate failed: ' + canonical(gate))
    rows = evaluate(state, handover, now())
    require(all(row['met'] is True for row in rows), 'Handover conditions are not met: ' + canonical(rows))


def request_for(store, state, handover, actor, session, client):
    from context_brief import seat_context
    packet = seat_context(store, handover['from'], max_chars=None, state=state)
    return {'map': {handover['from']: actor}, 'sessions': {actor: session}, 'client': client,
            'carry': 'useful', 'force': False, 'reason': handover['decision']['reason'],
            'mode': 'handover', 'handover': handover['id'], 'seat_note': canonical(packet)}


def validate_event(state, kind, actor, data):
    if kind == 'handover.prepare':
        validate_prepare(state, actor, data)
        return
    ident = data.get('request', {}).get('handover') if kind == 'handover' else data.get('id')
    handover = pending(state, ident)
    if kind == 'handover.confirm':
        require(set(data) == {'id', 'condition', 'reason'} and coordinator(state, actor), 'Active coordinator confirmation required')
        require(isinstance(data['reason'], str) and data['reason'].strip(), 'Confirmation reason required')
        condition = next((c for c in handover['conditions'] if c['n'] == data['condition']), None)
        require(type(data['condition']) is int and condition and condition['kind'] == 'confirm' and not condition.get('confirmed'),
                'Only an unconfirmed confirm: condition can be confirmed')
    elif kind == 'handover.cancel':
        require(set(data) == {'id', 'reason'} and (actor in peers(state, handover['from']) or
                actor == handover['to'] or coordinator(state, actor)), 'Origin, new seat or active coordinator required')
        require(isinstance(data['reason'], str) and data['reason'].strip(), 'Cancellation reason required')
    elif kind == 'handover':
        req = data['request']
        require(set(req) == {'map', 'sessions', 'client', 'carry', 'force', 'reason', 'mode', 'handover', 'seat_note'},
                'Invalid handover request')
        session = req['sessions'].get(actor)
        check = Store(state['root'], read_only=True)
        try:
            check_accept(check, state, handover, actor, session, req['client'])
            require(req == request_for(check, state, handover, actor, session, req['client']),
                    'Handover request or complete seat note changed')
            validate_plan(state, actor, data, handover_mode=True)
        finally:
            check.close()


def prepare(store, actor, old, new, *, outside_file=None, outside_unknown=None,
            owner_words=None, channel=None, answers=None, choice=None, when=()):
    require(bool(outside_file) != bool(outside_unknown), 'Declare --outside-file or --outside-unknown')
    require(bool(owner_words) != bool(answers), 'Use owner words or a recorded ask answer')
    state = store.resolved()
    if outside_file:
        body = Path(outside_file).read_bytes()
        outside_text(body)  # Refuse recognizable secrets before storing any bytes.
        outside = {'artifact': store.artifact_bytes(body, 'outside-state.md', str(Path(outside_file).resolve()))}
    else:
        outside = {'unknown': outside_unknown}
    data = {'from': old, 'to': new, 'owner_words': owner_words, 'channel': channel or ('relayed' if answers else None),
            'answers': answers, 'choice': choice, 'conditions': parse_conditions(state, when), 'outside': outside}
    data['id'] = 'handover-' + digest(canonical(data).encode())
    data['decision'] = decision_for(data)
    recorded = state.get('handovers', {}).get(data['id'])
    require(recorded is None or recorded['status'] == 'pending', 'Handover already completed or cancelled; choose a new target')
    store.append('handover.prepare', actor, data, data['id'])
    return {'id': data['id'], 'status': 'pending', 'changed_roles': False}


def accept(store, actor, ident, session, client, dry_run=False):
    state = store.resolved()
    record = state.get('handovers', {}).get(ident)
    require(record is not None, 'Unknown handover')
    if record['status'] == 'accepted':
        receipt = record['accepted']
        require(actor == record['to'] and session == receipt['session'] and client == receipt['client'],
                'Handover already accepted by a different session')
        if not dry_run:
            deliver_outbox(store, actor, receipt['outbox'])
        return {'id': ident, 'already_accepted': True, **plain(receipt)}
    record = pending(state, ident)
    check_accept(store, state, record, actor, session, client)
    req = request_for(store, state, record, actor, session, client)
    # Keep the ordinary frozen-source verification for any reassigned delivery.
    from squadra import bindings
    leaving = peers(state, record['from'])
    for task in state['tasks'].values():
        if task['status'] == 'delivered' and not task.get('superseded_by') and task['owner'] not in leaving and any(
                r in leaving for r in [task.get('reviewer'), *(task.get('pool') or [])]):
            bindings(store, task['id'], require_review=False)
    plan = Plan(store.root, state, actor, req)
    data = plan.build()
    validate_event(state, 'handover', actor, data)
    if dry_run:
        return {'id': ident, 'dry_run': True, 'plan': data, 'artifacts': [ref for _, ref in plan.blobs.values()]}
    for body, ref in plan.blobs.values():
        store.artifact_bytes(body, ref['name'], ref['original'])
    store.append('handover', actor, data, plan.key)
    deliver_outbox(store, actor, data['outbox'])
    return {'id': ident, 'successors': data['successors'], 'outbox': data['outbox']}


def status(store, old=None):
    from context_brief import seat_context
    state = store.resolved()
    require(old is None or old in state['agents'], 'Unknown seat')
    return {'handovers': [{**plain(h), 'conditions_evaluated': evaluate(state, h, now()),
                           'gate': safe_moment(store, h), 'note': seat_context(store, h['from'], state=state)}
                          for h in state.get('handovers', {}).values()
                          if h['status'] == 'pending' and (old is None or h['from'] in peers(state, old))]}


def dispatch(store, args):
    if args.action == 'prepare':
        return prepare(store, args.actor, args.origin, args.target, outside_file=args.outside_file,
                       outside_unknown=args.outside_unknown, owner_words=args.owner_words, channel=args.channel,
                       answers=args.answers, choice=args.choice, when=args.when)
    if args.action == 'accept':
        return accept(store, args.actor, args.id, args.session, args.client, args.dry_run)
    if args.action == 'status':
        return status(store, args.origin)
    if args.action == 'note':
        from context_brief import seat_context
        return seat_context(store, args.origin, max_chars=args.max_chars)
    data = {'id': args.id, 'reason': args.reason}
    if args.action == 'confirm':
        data['condition'] = args.condition
    store.append('handover.' + args.action, args.actor, data)
    return {'id': args.id, 'action': args.action}
