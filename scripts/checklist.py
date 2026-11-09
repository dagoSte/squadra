"""Read-only checks of recorded workflow facts and local Git history."""
from pathlib import Path
import re
import subprocess

from registry import Error, candidate_checks, decision_id, git_process, now, require, standby
from ripresa import readiness

# A round scope is the latest decision whose reason starts with exactly this text.
ROUND_SCOPE = 'Round scope:'


def _git(repo, *args):
    result = git_process(repo, *args, timeout=10)
    require(result.returncode == 0, result.stderr.decode(errors='replace').strip() or 'Git command failed')
    return result.stdout.decode(errors='replace').strip()


def _expect(condition, detail):
    require(condition, detail)
    return detail


def _add(items, name, check):
    try:
        value = check()
        status, detail = value if isinstance(value, tuple) else ('ok', value)
    except Error as exc:
        status, detail = 'fail', str(exc)
    except (OSError, subprocess.SubprocessError) as exc:
        status, detail = 'unverifiable', f'{type(exc).__name__}: {exc}'
    items.append({'id': name, 'status': status, 'detail': detail})


def _resume(state, actor, items):
    seat = state['agents'].get(actor)
    _add(items, 'actor', lambda: _expect(seat is not None, f'Registered actor required: {actor}'))
    if seat is not None:
        _add(items, 'coordinator', lambda: _expect(seat['role'] == 'coordinator', f'{actor}: coordinator role required'))
        _add(items, 'active', lambda: _expect(not standby(state, actor), f'{actor}: must be outside stand-by'))

    def check_readiness():
        result = readiness(state, now())
        require(result['ready'], '; '.join(result['blockers']))
        return 'No checkout blockers. Notices: ' + ('; '.join(result['notices']) or 'none')

    _add(items, 'readiness', check_readiness)
    items.append({'id': 'mail_listener', 'status': 'unverifiable',
                  'detail': 'The registry cannot observe whether this window is listening for mail.'})


def _readers(state, task):
    names = list(dict.fromkeys([r for r in [task.get('reviewer'), *(task.get('pool') or [])] if r]))
    require(names, 'No reviewer or reviewer pool recorded')
    require(any(name in state['agents'] and not standby(state, name) for name in names),
            'No registered reader outside stand-by')
    owner = state['agents'][task['owner']]
    authors = {state['agents'][a]['identity'] for a in task.get('authors', [task['owner']])}
    for name in names:
        reader = state['agents'].get(name)
        require(reader is not None, 'Unregistered reviewer: ' + name)
        require(reader['identity'] not in authors, 'Author alias is not an independent reviewer: ' + name)
        require(reader['client'] != owner['client'] or bool((task.get('same_client_reason') or '').strip()),
                'Same-client reviewer without a recorded reason: ' + name)
    return 'Registered independent readers: ' + ', '.join(names)


def _checkout_overlap(task, others):
    path = Path(task['repo']).resolve()
    conflicts = [t['id'] for t in others if Path(t['repo']).resolve() == path]
    require(not conflicts, 'Checkout also belongs to open tasks: ' + ', '.join(conflicts))
    return str(path) + ': no other open task uses this checkout'


def _branch_info(repo):
    branch = _git(repo, 'branch', '--show-current')
    require(branch, 'Detached HEAD: no branch to compare')
    common = str((Path(repo) / _git(repo, 'rev-parse', '--git-common-dir')).resolve())
    origin = git_process(repo, 'config', '--get', 'remote.origin.url', timeout=10)
    require(origin.returncode in (0, 1), 'Cannot read the local origin configuration')
    url = origin.stdout.decode(errors='replace').strip()
    if url.startswith(('/', './', '../')):
        url = str((Path(repo) / url).resolve())
    return branch, common, url


def _branch_overlap(task, others):
    try:
        branch, common, origin = _branch_info(task['repo'])
    except Error as exc:
        return 'unverifiable', 'Branch not inspected: ' + str(exc)
    conflicts, unknown = [], []
    for other in others:
        try:
            name, directory, remote = _branch_info(other['repo'])
        except (Error, OSError, subprocess.SubprocessError) as exc:
            unknown.append(other['id'] + ': ' + str(exc))
            continue
        if name == branch and (directory == common or origin and remote == origin):
            conflicts.append(other['id'])
    require(not conflicts, 'Branch ' + branch + ' also belongs to open tasks in the same repository: ' + ', '.join(conflicts))
    if unknown:
        return 'unverifiable', '; '.join(unknown)
    return branch + ': no branch overlap in shared Git storage or matching local origin configuration'


def _assignment(state, task):
    message = state['messages'].get('assign-' + task['id'])
    require(message and message.get('task') == task['id'] and message.get('to') == task['owner'],
            'Assignment message missing for this task and owner')
    require(message.get('ack') or message.get('transport'), 'Assignment has neither recorded transport nor ACK')
    if message.get('ack'):
        return 'Assignment ACK recorded'
    return 'Transport recorded; delivery observation: ' + str(message['transport'].get('delivered')) + '; this is not an ACK'


def _named(task_id, text):
    """The exact task ID as a whole token: R1 is not named by R1-B, XR1 or R10."""
    return re.search(r'(?<![\w-])' + re.escape(task_id) + r'(?![\w-])', text) is not None


def _round_scope(store, task):
    """Never fail: the registry cannot tell a blocking fix or the owner's word from prose."""
    created = scope = None
    for row in store.db.execute("SELECT * FROM events WHERE kind IN ('task', 'decision') ORDER BY seq"):
        event = store.decode_event(row)
        data = event['data']
        if event['kind'] == 'task' and data.get('id') == task['id'] and created is None:
            created = event['seq']
        elif event['kind'] == 'decision' and isinstance(data.get('reason'), str) \
                and (data.get('round_scope') is True or data['reason'].startswith(ROUND_SCOPE)):
            scope = (event['seq'], decision_id(data, event['seq']), data['reason'])
    if scope is None:
        return 'No round scope recorded (a decision whose reason starts with "' + ROUND_SCOPE + '")'
    seq, name, reason = scope
    if created is None:
        return 'unverifiable', task['id'] + ': task event not found; round scope ' + name + ' not compared'
    if _named(task['id'], reason):
        return task['id'] + ': named in round scope ' + name
    if created < seq:
        return task['id'] + ': created before round scope ' + name
    return ('unverifiable', task['id'] + ': task created after the round scope and not named in it: '
            "owner's word or a blocking fix needed (round scope " + name + ')')


def _assign(store, state, task, items):
    _add(items, 'task_open', lambda: _expect(task['status'] != 'done' and not task.get('superseded_by'),
                                           task['id'] + ': must be open and not superseded'))
    _add(items, 'brief', lambda: _expect(bool(task.get('brief')), task['id'] + ': recorded brief required'))
    _add(items, 'readers', lambda: _readers(state, task))
    others = [t for t in state['tasks'].values() if t['id'] != task['id'] and
              t['status'] != 'done' and not t.get('superseded_by')]
    _add(items, 'checkout', lambda: _checkout_overlap(task, others))
    _add(items, 'branch', lambda: _branch_overlap(task, others))
    _add(items, 'assignment', lambda: _assignment(state, task))
    _add(items, 'round_scope', lambda: _round_scope(store, task))


def _deliver(store, state, task, items):
    def task_state():
        require(task['status'] != 'done', task['id'] + ': task already closed: nothing to deliver')
        return task['id'] + ': not closed'

    _add(items, 'task_state', task_state)
    _add(items, 'delivery', lambda: _expect(bool(task.get('delivery')), task['id'] + ': recorded delivery required'))

    def proof():
        from frozen_candidate import current
        candidate = current(task, store.root)
        store.verify_artifacts(task)
        candidate_checks(task, candidate, require_review=False)
        return 'Current native proof bound to candidate ' + candidate['sha256']

    _add(items, 'proof', proof)
    delivery = task.get('delivery') or {}
    handoffs = [h for h in [delivery.get('handoff'), *(delivery.get('handoffs') or [])] if h]
    _add(items, 'handoff_ack', lambda: _expect(any(state['messages'].get(h, {}).get('ack') for h in handoffs),
                                             'Current review handoff must have its recipient ACK'))


def _commit(repo, ref):
    return _git(repo, 'rev-parse', '--verify', '--end-of-options', ref + '^{commit}')


def _commit_emails(repo, *revisions):
    value = _git(repo, 'log', '--format=%H%x00%ae%x00%ce%x00', *revisions, '--')
    if not value:
        return []
    fields = value.split('\0')
    require(fields[-1] == '' and len(fields) % 3 == 1, 'Cannot parse commit email records')
    return [(fields[i].strip(), fields[i + 1], fields[i + 2]) for i in range(0, len(fields) - 1, 3)]


def _emails(repo, revision_range):
    interval = revision_range or '@{upstream}..HEAD'
    parts = interval.split('..')
    require(len(parts) == 2 and all(parts) and not any(p.startswith('.') for p in parts),
            'Commit range must be A..B: ' + interval)
    try:
        start, end = (_commit(repo, ref) for ref in parts)
    except Error as exc:
        if revision_range is None:
            return 'unverifiable', 'Default upstream range is unavailable: ' + str(exc)
        raise
    if revision_range is not None:
        ancestry = git_process(repo, 'merge-base', '--is-ancestor', start, end, timeout=10)
        require(ancestry.returncode in (0, 1),
                ancestry.stderr.decode(errors='replace').strip() or 'Git ancestry check failed')
        require(ancestry.returncode == 0, 'range start is not an ancestor of its end: ' + interval)
    previous = {email for _, author, committer in _commit_emails(repo, start) for email in (author, committer)}
    commits = _commit_emails(repo, end, '^' + start)
    new = sorted({email for _, author, committer in commits for email in (author, committer)} - previous)
    require(not new, 'Emails not present before this interval: ' + ', '.join(new))
    return f'All {len(commits)} commits use emails already present in the ancestry of {parts[0]}'


def _fast_forward(repo, revision_range=None):
    if revision_range is not None:
        parts = revision_range.split('..')
        require(len(parts) == 2 and all(parts) and not any(p.startswith('.') for p in parts),
                'Commit range must be A..B: ' + revision_range)
        start, end = (_commit(repo, ref) for ref in parts)
        _git(repo, 'merge-base', '--is-ancestor', start, end)
        return 'Range start is an ancestor of its end: ' + revision_range
    try:
        upstream = _commit(repo, '@{upstream}')
    except Error as exc:
        return 'unverifiable', 'Local upstream unavailable; no fetch performed: ' + str(exc)
    _git(repo, 'merge-base', '--is-ancestor', upstream, _commit(repo, 'HEAD'))
    return 'Local upstream at the last fetch is an ancestor of HEAD'


def _push(state, repo, revision_range, task_id, items):
    def repository():
        require(repo is not None and Path(repo).is_dir(), 'An existing --repo directory is required')
        require(_git(repo, 'rev-parse', '--is-inside-work-tree') == 'true', '--repo must be a Git checkout')
        return str(Path(repo).resolve())

    _add(items, 'repository', repository)
    if items[-1]['status'] != 'ok':
        return
    _add(items, 'emails', lambda: _emails(repo, revision_range))
    _add(items, 'fast_forward', lambda: _fast_forward(repo, revision_range))
    if task_id is not None:
        task = state['tasks'].get(task_id)
        _add(items, 'task_closed', lambda: _expect(task is not None and task['status'] == 'done',
                                                 f'{task_id}: a closed registered task is required'))

        def contains_candidate():
            head = (task or {}).get('candidate', {}).get('head')
            require(head, 'No candidate commit recorded for task ' + task_id)
            _git(repo, 'merge-base', '--is-ancestor', _commit(repo, head), _commit(repo, 'HEAD'))
            return 'Candidate commit of ' + task_id + ' is contained in HEAD'

        _add(items, 'candidate_ancestor', contains_candidate)


def _handover_seats(state, actor):
    """Aliases share a nonempty identity or session, including transitive links."""
    require(actor in state['agents'], 'Registered actor required: ' + str(actor))
    names, identities, sessions = {actor}, set(), set()
    while True:
        for name in names:
            seat = state['agents'][name]
            if seat.get('identity'):
                identities.add(seat['identity'])
            if seat.get('session'):
                sessions.add(seat['session'])
        aliases = {name for name, seat in state['agents'].items()
                   if seat.get('identity') in identities or seat.get('session') in sessions}
        if aliases <= names:
            return names
        names.update(aliases)


def _handover_reading(state, seats):
    pending = []
    for task in state['tasks'].values():
        if task['status'] in {'done', 'withdrawn'} or task.get('superseded_by'):
            continue
        delivery = task.get('delivery') or {}
        for handoff in [delivery.get('handoff'), *(delivery.get('handoffs') or [])]:
            message = state['messages'].get(handoff, {})
            ack = message.get('ack') or {}
            if ack.get('agent') not in seats:
                continue
            verdicts = [r for r in task.get('reviews', []) if r.get('stage') == 'code'
                        and r.get('reviewer') in seats and r.get('handoff') == handoff]
            if not verdicts:
                pending.append(task['id'])
    return sorted(set(pending))


def _handover_worktrees(tasks, english):
    dirty, unknown = [], []
    for repo in sorted({t['repo'] for t in tasks}):
        try:
            if not Path(repo).is_dir():
                unknown.append(repo)
            elif _git(repo, 'status', '--porcelain', '--untracked-files=all'):
                dirty.append(repo)
        except (Error, OSError, subprocess.SubprocessError):
            unknown.append(repo)
    require(not dirty, ('Wait for a WIP commit in ' if english else 'Attendere il commit del WIP in ') + ', '.join(dirty))
    if unknown:
        return 'unverifiable', ('Checkout not inspected: ' if english else 'Checkout non verificato: ') + ', '.join(unknown)
    return 'Worktrees clean' if english else 'Checkout puliti'


def _tiredness_notice(items, values, english):
    reasons = [str(reason) for value in values if value and value.get('tired') for reason in value.get('reasons', [])]
    if reasons or any(value and value.get('tired') for value in values):
        items.append({'id': 'tiredness', 'status': 'notice',
                      'detail': (('Consider restarting: ' if english else 'Conviene ripartire: ')
                                 + '; '.join(reasons) + '. squadra handover prepare …')})


def _aware(value):
    """A timezone-aware instant, or None: the gate depends only on evaluate() from handover_conditions."""
    import datetime as dt
    try:
        stamp = dt.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return stamp if stamp.tzinfo is not None else None


def handover_checks(state, actor, at=None):
    """Shared gate for the explicit checklist and views; never reads transcripts or writes."""
    from handover_conditions import evaluate
    _timestamp = _aware
    clock, items = at or now(), []
    english = state.get('workspace', {}).get('lang', 'it') == 'en'
    _add(items, 'actor', lambda: _expect(actor in state['agents'], 'Registered actor required: ' + str(actor)))
    if items[-1]['status'] == 'fail':
        return {'moment': 'handover', 'items': items, 'ready': False}
    seats = _handover_seats(state, actor)
    tasks = [t for t in state['tasks'].values() if t.get('owner') in seats
             and t['status'] not in {'done', 'withdrawn'} and not t.get('superseded_by')]
    runs = sorted(r['id'] for r in state.get('runs', {}).values()
                  if r.get('agent') in seats and r['status'] in {'running', 'cleanup-unknown'})
    resources = sorted(name for name, r in state.get('resources', {}).items() if r.get('owner') in seats)
    reading = _handover_reading(state, seats)
    releases = sorted(t['id'] for t in tasks if t.get('release_gate') is True)
    for name, blocked, message, clear in [
        ('runs', runs, 'Wait for terminal receipt of run ' if english else 'Attendere la ricevuta terminale del run ',
         'No live runs' if english else 'Nessun run in corso'),
        ('resources', resources, 'Wait for release of ' if english else 'Attendere il rilascio di ',
         'No resources held' if english else 'Nessuna risorsa prenotata'),
        ('reading', reading, 'Wait for verdict on task ' if english else "Attendere il verdetto sull'incarico ",
         'No unfinished reads' if english else 'Nessuna lettura incompleta'),
        ('release', releases, 'Wait for release closure: ' if english else 'Attendere la chiusura del rilascio ',
         'No open release gates' if english else 'Nessun rilascio aperto'),
    ]:
        _add(items, name, lambda blocked=blocked, message=message, clear=clear:
             _expect(not blocked, message + ', '.join(blocked)) if blocked else clear)
    _add(items, 'worktree', lambda: _handover_worktrees(tasks, english))
    missing = [row['detail'] for handover in state.get('handovers', {}).values()
               if handover.get('status') == 'pending' and handover.get('from') in seats
               for row in evaluate(state, handover, clock) if not row['met']]
    _add(items, 'conditions', lambda: _expect(not missing, '; '.join(missing)) if missing else
         ('Conditions met' if english else 'Condizioni raggiunte'))
    outgoing = sorted(m['id'] for m in state.get('messages', {}).values()
                      if m.get('from') in seats and m.get('to') not in seats
                      and not m.get('ack') and not m.get('info') and not m.get('replaced_by'))
    items.append({'id': 'pending_acks', 'status': 'notice',
                  'detail': (('Pending acknowledgements will transfer: ' if english else 'Conferme pendenti da trasferire: ')
                             + ', '.join(outgoing) if outgoing else
                             ('No pending outgoing acknowledgements' if english else 'Nessuna conferma altrui in attesa'))})
    waits = []
    for seat in sorted(seats):
        wait = state['agents'][seat].get('service_wait') or {}
        until = _timestamp(wait.get('until'))
        if until is not None and _timestamp(clock) is not None and _timestamp(clock) < until:
            waits.append(seat + ': ' + str(wait.get('cause', '')) + ' → ' + wait['until'])
    items.append({'id': 'service_wait', 'status': 'notice',
                  'detail': '; '.join(waits) or ('No active service waits' if english else 'Nessuna attesa di servizio attiva')})
    import usage
    cached = getattr(usage, 'cached_tiredness', None)
    if callable(cached):
        values = cached(state, clock)
        _tiredness_notice(items, [values.get(seat) for seat in sorted(seats)], english)
    return {'moment': 'handover', 'items': items, 'ready': not any(i['status'] == 'fail' for i in items)}


def run(store, moment, *, actor=None, task_id=None, repo=None, revision_range=None):
    """Ready means no failed item; every unverifiable observation remains explicit."""
    state, items = store.resolved(), []
    if moment == 'handover':
        result = handover_checks(state, actor)
        if actor in state['agents']:
            import usage
            measure = getattr(usage, 'window_tiredness', None)
            if callable(measure):
                value = measure(store.events(), state, actor, now=now())
                if value is not None:
                    result['items'] = [i for i in result['items'] if i['id'] != 'tiredness']
                    _tiredness_notice(result['items'], [value], state.get('workspace', {}).get('lang', 'it') == 'en')
        return result
    if moment == 'resume':
        _resume(state, actor, items)
    elif moment == 'push':
        _push(state, repo, revision_range, task_id, items)
    else:
        task = state['tasks'].get(task_id)
        _add(items, 'task', lambda: _expect(task is not None, f'Registered --task required: {task_id}'))
        if task is not None:
            if moment == 'assign':
                _assign(store, state, task, items)
            else:
                _deliver(store, state, task, items)
    return {'moment': moment, 'items': items, 'ready': not any(item['status'] == 'fail' for item in items)}
