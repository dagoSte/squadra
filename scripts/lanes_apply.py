"""Assign a partition to available windows, then register every lane in one batch."""
import argparse
import copy
from pathlib import Path

import balance
from registry import Error, canonical, control_free, git, plain, require


def parent(store, task):
    state = store.view()
    task_id = task if isinstance(task, str) else task['id']
    result = plain(state['tasks'].get(task_id, task if isinstance(task, dict) else None))
    require(isinstance(result, dict), 'Unknown parent task: ' + task_id)
    return result


def roots(repo):
    repo = Path(repo).resolve(strict=True)
    require(git(repo, 'rev-parse', '--show-toplevel').decode().strip() == str(repo),
            'Registered repository must be a Git checkout root')
    return tuple(sorted(git(repo, 'rev-list', '--max-parents=0', 'HEAD').decode().split()))


def groups(partition):
    require(partition.get('form') == 'squadra-lanes/1', 'Unknown lanes partition format')
    rows = copy.deepcopy(partition['lanes'])
    shared = list(partition['shared'])
    owned = [*shared, *(path for row in rows for path in row['files'])]
    require(all(isinstance(p, str) and p and control_free(p) and not Path(p).is_absolute()
                and str(Path(p)) == p and Path(p).parts and '..' not in Path(p).parts
                and Path(p).parts[:2] != ('tests', 'fixtures') for p in owned), 'Invalid lane path or fixture')
    require(len(owned) == len(set(owned)), 'Lane files must not overlap')
    for row in rows:
        require(row['files'] and type(row['lines']) is int and row['lines'] >= 0, 'Invalid lane size')
        row['files'] = sorted(row['files'])
        row['tests'] = sorted(row.get('tests', []))
        require(set(row['tests']) <= set(row['files']), 'Lane tests must also be owned files')
    return rows, sorted(shared)


def plan(store, parent_task, partition_result):
    """Read-only, deterministic assignment; balance ranks windows, identity joins aliases."""
    task = parent(store, parent_task)
    require(Path(partition_result['repo']).resolve() == Path(task['repo']).resolve()
            and partition_result['base'] == task['base'], 'Lane analysis must match the parent repository and base')
    state = plain(store.view())
    store.release_views()
    rows, shared = groups(partition_result)
    reasons = list(partition_result.get('reasons', []))
    result = {'form': 'squadra-lanes-assignment/1', 'parent': task['id'],
              'partition': copy.deepcopy(partition_result), 'lanes': [], 'shared': shared, 'reasons': reasons}
    owner = state['agents'][task['owner']]
    if owner['role'] != 'coordinator':
        reasons.append('Parent is not owned by the coordinator; only the parent is created')
        return result
    if task.get('status') == 'done' or task.get('superseded_by'):
        reasons.append('Parent task is no longer open')
        return result
    if len(rows) < 2:
        reasons.append('Fewer than two independent groups; no automatic lanes')
        return result
    virtual = {**state, 'tasks': {**state['tasks'], task['id']: {
               **task, 'status': task.get('status', 'ready'), 'authors': []}}}
    ranked = balance.calculate(virtual, store.events(), task['id'], read_at=0,
                               forecast_result={'status': 'scope_unavailable', 'samples': []})['task']
    reasons.extend(row['identity'] + ': ' + row['reason'] for row in ranked['build']['excluded'])
    agents = state['agents']

    def model(window):
        clients = set(window['clients']) & {'codex', 'claude'}
        return next(iter(clients)) if len(clients) == 1 else None

    def seat(window, roles):
        return next((agents[name] for name in window['seats'] if agents[name]['role'] in roles
                     and agents[name].get('client') == model(window)), None)

    def reader(builder):
        opposite = {'codex': 'claude', 'claude': 'codex'}.get(builder.get('client'))
        return next((seat(w, balance.READ_ROLES) for w in ranked['read']['rows']
                     if model(w) == opposite and w['identity'] != builder['identity']
                     and seat(w, balance.READ_ROLES)), None)

    project_roots = roots(task['repo'])
    checkouts = {Path(task['repo']).resolve()}
    builders = []
    for window in ranked['build']['rows']:
        builder = seat(window, balance.BUILD_ROLES)
        if builder is None:
            reasons.append('Unknown or ambiguous model for ' + window['identity'])
            continue
        if window['owned_open']:
            reasons.append(builder['id'] + ': already owns an open task')
            continue
        reviewer = reader(builder)
        if reviewer is None:
            reasons.append(builder['id'] + ': no independent reader of the other model')
            continue
        try:
            repo = Path(builder['repo']).resolve(strict=True)
            require(roots(repo) == project_roots, 'Checkout belongs to another project')
            git(repo, 'cat-file', '-e', task['base'] + '^{commit}')
            require(repo not in checkouts, 'Checkout already used by the parent or another builder')
        except (OSError, Error) as exc:
            reasons.append(builder['id'] + ': ' + str(exc))
            continue
        checkouts.add(repo)
        builders.append((builder, reviewer, str(repo)))
    if len(builders) < 2:
        reasons.append('Fewer than two available builders with separate checkouts and valid readers')
        return result
    while len(rows) > len(builders):
        rows.sort(key=lambda r: (r['lines'], r['files']))
        left, right, *rows = rows
        rows.append({'files': sorted(left['files'] + right['files']),
                     'tests': sorted(left['tests'] + right['tests']), 'lines': left['lines'] + right['lines']})
        reasons.append('Merged the two smallest groups to fit the available builders')
    dependencies = list(task.get('depends', []))
    if shared:
        reviewer = reader(owner) if owner.get('client') == 'claude' else None
        if reviewer is None:
            reasons.append('Shared contract has no independent reader of the other model; no lanes created')
            return result
        result['lanes'].append({'id': task['id'] + '.L0', 'files': shared, 'tests': [], 'lines': 0,
                                'owner': owner['id'], 'reviewer': reviewer['id'], 'repo': task['repo'],
                                'depends': dependencies})
        dependencies = [*dependencies, task['id'] + '.L0']
    for number, (row, (builder, reviewer, repo)) in enumerate(
            zip(sorted(rows, key=lambda r: r['files']), builders), 1):
        result['lanes'].append({**row, 'id': task['id'] + '.L' + str(number), 'owner': builder['id'],
                                'reviewer': reviewer['id'], 'repo': repo, 'depends': dependencies})
    return result


def propose(store, parent_task, paths, *, max_lanes=4):
    """Load lane A only when a caller actually requests code analysis."""
    try:
        from lanes import analyze, partition
    except ModuleNotFoundError as exc:
        if exc.name != 'lanes':
            raise
        raise Error('Automatic lanes require the lanes analysis module from the integrated release') from None
    task = parent(store, parent_task)
    require(type(max_lanes) is int and max_lanes > 0, 'Positive --max-lanes required')
    return plan(store, task, partition(analyze(task['repo'], paths), max_lanes=max_lanes))


def assignment(task):
    return {'id': 'assign-' + task['id'], 'to': task['owner'], 'task': task['id'],
            'body': f"Assignment {task['id']}: {task['title']}\nBase {task['base']}\n"
                    f"Scope {task['scope']['sha256']}\nACK required before starting."}


def apply(store, actor, parent_task, planned):
    """Commit tasks/assignments/decision together; external delivery follows the commit."""
    task = parent(store, parent_task)
    state = store.view()
    require(state['agents'][actor]['role'] == 'coordinator', 'Coordinator role required')
    existing = task['id'] in state['tasks']
    if existing:
        require(task['owner'] == actor, 'Parent must be owned by the coordinator applying lanes')
    require(not state['paused'], 'Workspace paused')
    require(plan(store, task, planned['partition']) == planned, 'Lane plan changed; inspect a new dry-run')
    require(not planned['lanes'] or task['owner'] == actor, 'Parent must be owned by the coordinator applying lanes')
    operations, messages = [], []
    if not existing:
        operations.append(('task', actor, task, None))
        messages.append(assignment(task))
        operations.append(('message', actor, messages[-1], None))
    for lane in planned['lanes']:
        require(lane['id'] not in store.view()['tasks'], 'Lanes already exist for this parent')
        scope = (f"{lane['id']}: {task['title']}\nParent scope: {task['scope']['sha256']}\n"
                 "Owned files (each path belongs only to this lane):\n" +
                 ''.join(path + '\n' for path in lane['files']))
        child = {'id': lane['id'], 'title': task['title'] + ' / ' + lane['id'].rsplit('.', 1)[1],
                 'owner': lane['owner'], 'reviewer': lane['reviewer'], 'repo': lane['repo'], 'base': task['base'],
                 'proof': task.get('proof'), 'expected': task.get('expected'),
                 'full_gate': task.get('full_gate', False), 'depends': lane['depends'],
                 'requirements': task.get('requirements', []), 'delivery_policy': 'frozen-v1', 'cross_client': True,
                 'scope': store.artifact_bytes(scope.encode(), lane['id'] + '-scope.txt', 'generated-lane:' + lane['id'])}
        operations.append(('task', actor, child, None))
        messages.append(assignment(child))
        operations.append(('message', actor, messages[-1], None))
    if planned['lanes']:
        artifact = store.artifact_bytes((canonical(planned) + '\n').encode(), 'lanes-' + task['id'] + '.json',
                                        'generated-lanes:' + task['id'])
        operations.append(('decision', actor, {'id': 'lanes-' + task['id'],
                          'reason': 'Automatic lanes: ' + '; '.join(map(str, planned['reasons'])),
                          'tasks': [task['id'], *(r['id'] for r in planned['lanes'])], 'plan': artifact}, None))
    if operations:
        store.batch(operations)
    from squadra import dispatch
    transports = []
    for message in messages:
        agent = store.view()['agents'][message['to']]
        via = 'codex-queue' if agent.get('client') == 'codex' else 'manual'
        try:
            receipt = dispatch(argparse.Namespace(command='transport', actor=actor,
                               message=message['id'], via=via, force=False), store)
        except (Error, OSError) as exc:
            receipt = {'message': message['id'], 'via': via, 'delivered': False, 'reason': str(exc)}
            store.append('transport', actor, receipt)
        transports.append(receipt)
    return {**planned, 'dry_run': False, 'created': [r['id'] for r in planned['lanes']], 'transport': transports}
