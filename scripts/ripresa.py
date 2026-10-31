"""Read-only resume readiness: interpreter, registered checkouts, release drift and silent windows."""
import datetime as dt
from pathlib import Path
import sys

from registry import git_process, presence, standby

# A window with no recorded answer for this long is listed for explicit reconciliation, never changed.
SILENT_DAYS = 2


def _git(repo, *args):
    try:
        done = git_process(repo, *args, timeout=10)
    except Exception:
        return None
    return done.stdout.decode(errors='replace').strip() if done.returncode == 0 else None


def checkout(path):
    """What Git says about one registered checkout, from local refs only (no fetch)."""
    folder = Path(path)
    if not folder.is_dir():
        return {'repo': path, 'exists': False}
    if _git(path, 'rev-parse', '--is-inside-work-tree') != 'true':
        return {'repo': path, 'exists': True, 'git': False}
    row = {'repo': path, 'exists': True, 'git': True,
           'branch': _git(path, 'branch', '--show-current') or None,
           'head': _git(path, 'rev-parse', '--short', 'HEAD'),
           'dirty': bool(_git(path, 'status', '--porcelain', '--untracked-files=no'))}
    tag = _git(path, 'describe', '--tags', '--abbrev=0')
    if tag:
        row['last_tag'] = tag
        row['commits_since_tag'] = int(_git(path, 'rev-list', '--count', tag + '..HEAD') or 0)
    counts = _git(path, 'rev-list', '--left-right', '--count', '@{upstream}...HEAD')
    if counts:
        behind, ahead = (int(n) for n in counts.split())
        row['upstream'] = {'behind': behind, 'ahead': ahead, 'as_of': 'last fetch'}
    return row


def readiness(state, at):
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
    silent = []
    for row in presence(state, clock):
        if row['availability'] != 'active':
            continue
        last = row['last_answer']
        days = None if last is None else (clock - dt.datetime.fromisoformat(last)).days
        if days is None or days >= SILENT_DAYS:
            silent.append({'agent': row['agent'], 'last_answer': last, 'days': days})
    if silent:
        notices.append(f"{len(silent)} active windows silent for {SILENT_DAYS}+ days: ping them or record stand-by")
    return {'ready': not blockers, 'blockers': blockers, 'notices': notices,
            'python': {'executable': sys.executable, 'version': '%d.%d.%d' % sys.version_info[:3]},
            'checkouts': checkouts, 'silent_windows': silent,
            'limits': 'Local refs only, no fetch; windows are only listed, never changed.'}
