"""Read-only resume readiness: interpreter, registered checkouts, release drift and silent windows."""
import datetime as dt
from pathlib import Path
import sys

from registry import git_process, presence, require, standby

# A window with no recorded answer for this long is listed for explicit reconciliation, never changed.
SILENT_DAYS = 2
IDLE_MINUTES = 30
# Git could not be asked (timeout, missing binary): unknown, never reported as "not a Git checkout".
UNREADABLE = object()


def _git(repo, *args):
    try:
        done = git_process(repo, *args, timeout=10)
    except Exception:
        return UNREADABLE
    return done.stdout.decode(errors='replace').strip() if done.returncode == 0 else None


def checkout(path):
    """What Git says about one registered checkout, from local refs only (no fetch)."""
    folder = Path(path)
    if not folder.is_dir():
        return {'repo': path, 'exists': False}
    inside = _git(path, 'rev-parse', '--is-inside-work-tree')
    if inside is UNREADABLE:
        return {'repo': path, 'exists': True, 'git': None}
    if inside != 'true':
        return {'repo': path, 'exists': True, 'git': False}
    unreadable = {'repo': path, 'exists': True, 'git': None}
    answers = [_git(path, 'branch', '--show-current'), _git(path, 'rev-parse', '--short', 'HEAD'),
               _git(path, 'status', '--porcelain', '--untracked-files=no'), _git(path, 'describe', '--tags', '--abbrev=0')]
    if UNREADABLE in answers:
        return unreadable
    branch, head, dirty, tag = answers
    row = {'repo': path, 'exists': True, 'git': True, 'branch': branch or None, 'head': head, 'dirty': bool(dirty)}
    if tag:
        since = _git(path, 'rev-list', '--count', tag + '..HEAD')
        if since is UNREADABLE:
            return unreadable
        row['last_tag'] = tag
        row['commits_since_tag'] = int(since) if since else None
    counts = _git(path, 'rev-list', '--left-right', '--count', '@{upstream}...HEAD')
    if counts is UNREADABLE:
        return unreadable
    if counts:
        behind, ahead = (int(n) for n in counts.split())
        row['upstream'] = {'behind': behind, 'ahead': ahead, 'as_of': 'last fetch'}
    return row


def holds(state, agent, open_tasks):
    """What keeps a silent window from being archived: it must be reconciled first, never parked.

    Stand-by applies to every seat of an identity, so every seat sharing it is checked.
    """
    identity = state['agents'][agent]['identity']
    keeps = []
    for seat in sorted(name for name, row in state['agents'].items() if row['identity'] == identity):
        prefix = '' if seat == agent else f'same identity as {seat}: '
        if state['agents'][seat]['role'] == 'coordinator':
            keeps.append(prefix + 'coordinator: reconcile it with Rule 0')
        owned = [t['id'] for t in open_tasks if t.get('owner') == seat]
        if owned:
            keeps.append(prefix + 'owns open tasks ' + ', '.join(owned))
        readers = [t['id'] for t in open_tasks if seat in (t.get('reviewer'), *(t.get('pool') or []))]
        if readers:
            keeps.append(prefix + 'reads open tasks ' + ', '.join(readers))
        held = [name for name, resource in state.get('resources', {}).items() if resource.get('owner') == seat]
        if held:
            keeps.append(prefix + 'holds resources ' + ', '.join(held))
    return keeps


def silent_windows(state, at):
    """Identity-wide silence from the latest ACK or registration, without inspecting Git."""
    clock = dt.datetime.fromisoformat(at) if isinstance(at, str) else at
    open_tasks = [t for t in state['tasks'].values() if t['status'] != 'done' and not t.get('superseded_by')]
    registrations = {}
    for agent in state['agents'].values():
        if stamp := agent.get('updated'):
            identity = agent['identity']
            registrations[identity] = max(registrations.get(identity, stamp), stamp, key=dt.datetime.fromisoformat)
    silent, parked_by = [], {}
    for row in presence(state, clock):
        if row['availability'] != 'active':
            continue
        last = row['last_answer']
        signs = [stamp for stamp in (last, registrations.get(row['identity'])) if stamp]
        days = (clock - max(dt.datetime.fromisoformat(stamp) for stamp in signs)).days if signs else None
        if days is None or days >= SILENT_DAYS:
            keeps = holds(state, row['agent'], open_tasks)
            entry = {'agent': row['agent'], 'identity': row['identity'], 'last_answer': last, 'days': days,
                     'archivable': not keeps, 'keeps': keeps}
            if not keeps:
                first = parked_by.setdefault(row['identity'], row['agent'])
                if first == row['agent']:
                    entry['command'] = (f"squadra standby --actor <coordinator> --agent {row['agent']} "
                                        f"--reason \"<owner's words>\"")
                else:
                    entry['parked_with'] = first
            silent.append(entry)
    return silent


def _open_lane_parents(state):
    """Use the parent/children decision recorded atomically by lanes_apply."""
    parents = set()
    for decision in state.get('decisions', []):
        assigned = decision.get('tasks', [])
        if len(assigned) < 2:
            continue
        parent, *children = assigned
        if (decision.get('id') != 'lanes-' + parent
                or decision.get('plan', {}).get('original') != 'generated-lanes:' + parent):
            continue
        if any(child in state['tasks'] and state['tasks'][child]['status'] != 'done'
               and not state['tasks'][child].get('superseded_by') for child in children):
            parents.add(parent)
    return parents


def idle_tasks(state, at, *, minutes=IDLE_MINUTES):
    """Accepted assignments without recorded work; the coordinator decides any handoff."""
    require(type(minutes) is int and minutes >= 0, 'Nonnegative integer idle minutes required')
    clock = dt.datetime.fromisoformat(at) if isinstance(at, str) else at
    running = {run['task'] for run in state['runs'].values()}
    # A parent of automatic lanes waits for them by design: its own idleness is integration, not a stall.
    lane_parents = _open_lane_parents(state)
    plan_events = getattr(state, 'wait_context', {}).get('tasks', {})
    english = state.get('workspace', {}).get('lang', 'it') == 'en'
    result = []
    for task in sorted(state['tasks'].values(), key=lambda t: t['id']):
        # A plan carried over by a takeover is the previous owner's work, not activity of the successor.
        origin = task.get('taken_over_from')
        previous = (state['tasks'].get(origin) if isinstance(origin, str) else None) or {}
        # A plan event on this task counts even when it reuses the inherited artifact.
        registered_plan = task.get('plan_utc') or plan_events.get(task['id'], {}).get('plan_utc')
        own_plan = task.get('plan') and (registered_plan or not (previous and task['plan'] == previous.get('plan')))
        if (task['status'] != 'ready' or task.get('superseded_by') or task.get('candidate')
                or own_plan or task.get('delivery') or task.get('proofs') or task['id'] in running
                or task['id'] in lane_parents):
            continue
        acknowledgements = [m['ack']['utc'] for m in state['messages'].values()
                            if m.get('task') == task['id'] and m['to'] == task['owner']
                            and m.get('ack') and m['ack']['agent'] == task['owner']]
        if not acknowledgements:
            continue
        accepted = max(acknowledgements, key=dt.datetime.fromisoformat)
        elapsed = (clock - dt.datetime.fromisoformat(accepted)).total_seconds()
        if elapsed < minutes * 60:
            continue
        blocked = [dep for dep in task.get('depends', []) if state['tasks'][dep]['status'] != 'done']
        result.append({'task': task['id'], 'owner': task['owner'], 'ack': accepted,
                       'minutes': int(elapsed // 60), 'threshold_minutes': minutes, 'blocked_by': blocked,
                       'next': (('Wait for the dependencies: ' if english else 'Attendere le dipendenze: ')
                                + ', '.join(blocked) if blocked else
                                ('Check the inactivity; if confirmed, the coordinator records the handoff of the '
                                 "build to another window under the owner's rule, keeping the history."
                                 if english else
                                 "Verificare l'inattività; se confermata, il coordinatore registra il passaggio "
                                 "della costruzione a un'altra finestra secondo la regola del proprietario, "
                                 'conservando lo storico.'))})
    return result


def readiness(state, at, *, idle_minutes=IDLE_MINUTES):
    """Blockers stop the resume; notices ask for reconciliation. Nothing here writes or reassigns."""
    blockers, notices = [], []
    open_tasks = [t for t in state['tasks'].values() if t['status'] != 'done' and not t.get('superseded_by')]
    active = [a for a in state['agents'].values() if not standby(state, a['id'])]
    repos = {}
    for task in open_tasks:
        repos.setdefault(task['repo'], {'tasks': [], 'agents': []})['tasks'].append(task['id'])
    for agent in active:
        if agent.get('repo'):
            repos.setdefault(agent['repo'], {'tasks': [], 'agents': []})['agents'].append(agent['id'])
    checkouts = []
    for path in sorted(repos):
        row = {**checkout(path), **repos[path]}
        checkouts.append(row)
        if row.get('git', True) is None:
            notices.append(f"{path}: Git did not answer; checkout not inspected, check it by hand")
            continue
        if not row['exists'] or not row.get('git', True):
            problem = 'missing' if not row['exists'] else 'not a Git checkout'
            if row['tasks']:
                blockers.append(f"{path}: {problem}, used by open tasks {', '.join(row['tasks'])}")
            else:
                notices.append(f"{path}: {problem}, registered by {', '.join(row['agents'])}")
            continue
        if row.get('upstream', {}).get('behind'):
            notices.append(f"{path}: {row['upstream']['behind']} commits behind upstream at the last fetch")
        if row.get('commits_since_tag'):
            notices.append(f"{path}: {row['commits_since_tag']} commits after {row['last_tag']}")
    clock = dt.datetime.fromisoformat(at) if isinstance(at, str) else at
    silent = silent_windows(state, clock)
    if silent:
        archivable = sum(entry['archivable'] for entry in silent)
        notices.append(f"{len(silent)} active windows silent for {SILENT_DAYS}+ days, {archivable} archivable "
                       f"after the owner's confirmation: ping them or record stand-by")
    idle = idle_tasks(state, clock, minutes=idle_minutes)
    english = state.get('workspace', {}).get('lang', 'it') == 'en'
    notices.extend((f"{row['task']}: no recorded activity for {row['minutes']} minutes after ACK. {row['next']}"
                    if english else
                    f"{row['task']}: nessuna attività registrata da {row['minutes']} minuti dopo l'ACK. {row['next']}")
                   for row in idle)
    return {'ready': not blockers, 'blockers': blockers, 'notices': notices,
            'python': {'executable': sys.executable, 'version': '%d.%d.%d' % sys.version_info[:3]},
            'checkouts': checkouts, 'silent_windows': silent, 'idle_tasks': idle,
            'limits': 'Local refs only, no fetch; windows are only listed, never changed.'}
