"""Plan a window handover, commit it once, then deliver its durable outbox."""
import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

from registry import (Error, TASK_FIELDS, WAIT_SECONDS, apply, canonical, digest, git,
                      now, plain, require, run_count, run_lease_paths, standby,
                      validate, validate_cross_client, _owned_process_gone, _private_run_file)


LIVE = {'running', 'cleanup-unknown'}


def peers(state, name):
    seat = state['agents'][name]
    return {key for key, other in state['agents'].items()
            if other['identity'] == seat['identity'] or
            (seat.get('session') and other.get('session') == seat['session'])}


def request(args):
    require(bool(args.map) != bool(args.old or args.new), 'Use --old/--new or --map')
    pairs = [part.split('=') for part in args.map.split(',')] if args.map else [[args.old, args.new]]
    require(pairs and all(len(p) == 2 and all(p) and p[0] != p[1] for p in pairs), 'Invalid replacement map')
    mapping = dict(pairs)
    require(len(mapping) == len(pairs) and not set(mapping).intersection(mapping.values()),
            'Duplicate sources, replacement chains and cycles are refused')
    require(args.reason.strip(), 'Replacement reason required')
    sessions = {}
    for value in args.session or []:
        if '=' in value:
            target, value = value.split('=', 1)
        else:
            require(len(set(mapping.values())) == 1, 'Use repeated --session NEW=ID for multiple replacements')
            target = next(iter(mapping.values()))
        require(target in mapping.values() and value and target not in sessions, 'Invalid or duplicate --session')
        sessions[target] = value
    return {'map': dict(sorted(mapping.items())), 'sessions': sessions, 'client': args.client,
            'reason': args.reason, 'carry': args.carry, 'force': args.force}


def key_for(actor, req):
    return 'replace-' + digest(canonical({'actor': actor, 'request': req}).encode())


def expansion(state, req):
    mapping = {}
    for old, new in req['map'].items():
        require(old in state['agents'], 'Unknown old agent: ' + old)
        require(state['agents'][old]['role'] != 'coordinator', 'Coordinator replacement needs an explicit role handover')
        for alias in peers(state, old):
            require(alias not in mapping or mapping[alias] == new, 'One old session maps to conflicting replacements')
            mapping[alias] = new
    require(not set(mapping).intersection(mapping.values()), 'Replacement cannot reuse an old session identity')
    return mapping


def check_safety(state, req, mapping, stamp):
    require(state['agents'].get(req['actor'], {}).get('role') == 'coordinator', 'Coordinator role required')
    if req['force']:
        return
    threshold = state['workspace'].get('wait_seconds', {}).get('alive', WAIT_SECONDS['alive'])
    clock = dt.datetime.fromisoformat(stamp)
    answers = [m['ack']['utc'] for m in state['messages'].values() if m.get('ack') and m['to'] in mapping]
    require(not any((clock - dt.datetime.fromisoformat(t)).total_seconds() < threshold for t in answers),
            'Old window answered recently; use --force with the reason')


def run_observation(root, run):
    """Absent metadata is uncertainty, never evidence that a process ended."""
    _, _, metadata, terminal = run_lease_paths(root, run['id'])
    if not metadata.exists() or terminal.exists():
        return False
    try:
        _owned_process_gone(SimpleNamespace(root=root), run, 'replacement')
        record = json.loads(_private_run_file(metadata))
        require(record.get('phase') in {'not-started', 'disposed'} or record.get('pgid'), 'Unobserved launch')
        folder = root / 'runs' / run['id']
        require(folder.parent == root / 'runs', 'Unsafe run path')
        for name in ('stdout.log', 'stderr.log', 'reply.txt'):
            path = folder / name
            require(not path.is_symlink() and (not path.exists() or path.stat().st_size == 0), 'Run has results')
        return True
    except (Error, OSError, ValueError):
        return False


def useful(message):
    if message['id'].lower().startswith(('stop-', 'resume-', 'pause-', 'review-')):
        return False
    if message.get('ping') or message.get('replaced_by') or message.get('task'):
        return False
    command = message['body'].lstrip().split(None, 1)[0].lower().rstrip(':.,') if message['body'].strip() else ''
    return command not in {'stop', 'resume', 'pause', 'standby'}


def independent(state, task, reader):
    require(reader in state['agents'], 'Unknown replacement reader')
    authors = set().union(*(peers(state, author) for author in task['authors']))
    require(reader not in authors, 'Author cannot independently review own changes (including session aliases)')
    require(state['agents'][reader]['role'] in {'reviewer', 'coordinator'}, 'Replacement reader needs a reviewer seat')


def reader_policy(state, task):
    if task.get('cross_client'):
        from registry import same_client_readers
        readers = [r for r in [task.get('reviewer'), *(task.get('pool') or [])] if r]
        if not same_client_readers(state, task['owner'], readers):
            task['same_client_reason'] = None
        validate_cross_client(state, task)
    return task.get('same_client_reason')


class Plan:
    def __init__(self, root, state, actor, req, stamp=None):
        self.root, self.state, self.actor, self.req = Path(root), plain(state), actor, req
        self.stamp = stamp or now()
        self.key = key_for(actor, req)
        self.ops, self.blobs = [], {}
        self.mapping = expansion(state, req)
        check_safety(state, {**req, 'actor': actor}, self.mapping, self.stamp)

    def artifact(self, body, name):
        ref = {'sha256': digest(body), 'name': name, 'original': 'replacement:' + self.key, 'bytes': len(body)}
        self.blobs[ref['sha256']] = (body, ref)
        return ref

    def add(self, kind, data, actor=None):
        op = {'kind': kind, 'actor': actor or self.actor, 'data': data}
        self.ops.append(op)
        apply(self.state, {**op, 'id': self.key, 'utc': self.stamp})

    def build(self):
        original = plain(self.state)
        for new in sorted(set(self.mapping.values())):
            old = next(old for old, target in self.req['map'].items() if target == new)
            prior = original['agents'][old]
            if new in self.state['agents']:
                seat = self.state['agents'][new]
                require(not peers(self.state, new).intersection(self.mapping), 'New seat shares the old session')
                require(not standby(self.state, new), 'New agent is in stand-by')
                require(not self.req['sessions'].get(new) or self.req['sessions'][new] == seat.get('session'), 'Session differs from registered replacement')
                require(not self.req['client'] or self.req['client'] == seat['client'], 'Client differs from registered replacement')
            else:
                session = self.req['sessions'].get(new, '')
                client = self.req['client'] or prior['client']
                require(session or client in {'manual', 'cursor'}, 'Choose a candidate with --session for ' + new)
                require(not any(session and a.get('session') == session and a['client'] == client
                                for a in self.state['agents'].values()), 'Session already registered; name its existing seat')
                self.add('agent', {'id': new, 'identity': session or new, 'role': ('builder' if any(t['owner'] in self.mapping and self.mapping[t['owner']] == new for t in original['tasks'].values()) else prior['role']), 'client': client,
                                  'session': session, 'repo': prior['repo'], 'skills': []})

        unresolved = []
        for run in original['runs'].values():
            if run['agent'] not in self.mapping or run['status'] not in LIVE:
                continue
            if run_observation(self.root, run) and run['status'] == 'running':
                observation = {'form': 'squadra-run-abandon/1', 'run': run['id'], 'observed_at': self.stamp,
                               'processes': 'none', 'results': 'none',
                               'observation': 'Replacement verified the recorded process group absent and no terminal receipt or results.'}
                evidence = self.artifact((canonical(observation) + '\n').encode(), 'abandonment.json')
                self.add('run.abandon', {'id': run['id'], 'starter': original['agents'][run['agent']]['identity'],
                         'reason': self.req['reason'], 'evidence': evidence, 'observation': observation,
                         'legacy': not run.get('lease_version')}, run['agent'])
            else:
                unresolved.append(run['id'])
        require(not unresolved or self.req['force'], 'Old window has a live or unverified run; use --force with the reason')
        held_owners = set().union(*(peers(original, original['runs'][run]['agent']) for run in unresolved))
        for name, resource in original['resources'].items():
            if resource['owner'] in self.mapping and resource['owner'] not in held_owners:
                self.add('resource.release', {'name': name}, resource['owner'])

        # Keep a distinct reader seat when one new window takes over both roles.
        readers = {}
        for old, new in self.mapping.items():
            if any(old == t.get('reviewer') or old in (t.get('pool') or []) for t in original['tasks'].values()
                   if t['status'] != 'done' and not t.get('superseded_by')):
                seat = self.state['agents'][new]
                reader = new if seat['role'] in {'reviewer', 'coordinator'} else new + '-read'
                if reader not in self.state['agents']:
                    self.add('agent', {k: v for k, v in {**seat, 'id': reader, 'role': 'reviewer'}.items()
                                       if k not in {'updated', 'availability', 'replaces', 'service_wait'}})
                require(self.state['agents'][reader]['identity'] == seat['identity'], 'Reader seat name already belongs to another identity')
                readers[old] = reader

        successors, changed = {}, set()
        for old_task in original['tasks'].values():
            if old_task['status'] == 'done' or old_task.get('superseded_by') or old_task['owner'] not in self.mapping:
                continue
            new = self.mapping[old_task['owner']]
            task = {k: plain(v) for k, v in old_task.items() if k in TASK_FIELDS}
            ident = old_task['id'] + '-takeover-' + self.key[-12:]
            task.update(id=ident, owner=new, supersedes=old_task['id'], delivery_policy='frozen-v1',
                        base=git(old_task['repo'], 'rev-parse', 'HEAD').decode().strip())
            raw = (self.root / 'artifacts' / old_task['scope']['sha256']).read_bytes()
            require(digest(raw) == old_task['scope']['sha256'], 'Scope artifact changed')
            task['scope'] = self.artifact(('taken over from ' + old_task['owner'] + '\n').encode() + raw, 'takeover-scope.md')
            task['reviewer'] = readers.get(task.get('reviewer'), task.get('reviewer'))
            if task.get('pool'):
                task['pool'] = list(dict.fromkeys(readers.get(r, r) for r in task['pool']))
                if len(task['pool']) == 1:
                    task['reviewer'], task['pool'] = task['pool'][0], None
            reader_policy(self.state, task)
            if task.get('route') == 'light':
                from light_route import contract
                task['contract'] = contract(task)
            self.add('task', task)
            inheritance = {'task': ident, 'from': old_task['id'],
                           'authors': list(dict.fromkeys([*old_task['authors'], new])),
                           'rounds': old_task['rounds'], 'builds': run_count(original, old_task['id'], 'build'),
                           'reads': {stage: run_count(original, old_task['id'], 'read', stage) for stage in ('plan', 'code')},
                           'runs': list(dict.fromkeys([*old_task.get('inherited_runs', []),
                               *[r for r in unresolved if original['runs'][r]['task'] == old_task['id']]]))}
            if old_task.get('plan'):
                plan = old_task['plan']
                require(digest((self.root / 'artifacts' / plan['sha256']).read_bytes()) == plan['sha256'],
                        'Required plan artifact changed')
                inheritance['plan'] = plan
            self.add('replacement.inherit', inheritance)
            changed.add(ident)
            for reader in [task.get('reviewer'), *(task.get('pool') or [])]:
                if reader:
                    independent(self.state, self.state['tasks'][ident], reader)
            self.add('message', {'id': 'assign-' + ident, 'to': new, 'task': ident,
                                'body': 'Assignment ' + ident + ': ' + task['title'] + '\nACK required before starting.'})
            successors[old_task['id']] = ident

        for old_task in original['tasks'].values():
            if old_task['id'] in successors or old_task['status'] == 'done' or old_task.get('superseded_by'):
                continue
            selected = [r for r in [old_task.get('reviewer'), *(old_task.get('pool') or [])] if r in readers]
            if not selected:
                continue
            changed.add(old_task['id'])
            candidates = list(dict.fromkeys(readers.get(r, r) for r in (old_task.get('pool') or [old_task['reviewer']])))
            policy = reader_policy(self.state, {**old_task, 'reviewer': candidates[0] if len(candidates) == 1 else None,
                                                'pool': candidates if len(candidates) > 1 else None})
            for reader in candidates:
                independent(self.state, old_task, reader)
            held = [r for r in unresolved if original['runs'][r]['task'] == old_task['id']]
            if held:
                self.add('replacement.hold', {'task': old_task['id'], 'runs': list(dict.fromkeys([*old_task.get('inherited_runs', []), *held]))})
            if old_task['status'] == 'delivered':
                from source_manifests import transform_packet, resolve
                previous = old_task['delivery']
                packet = transform_packet(previous['packet_data'], lambda v: resolve(v, self.root / 'artifacts'))
                packet.pop('pool', None)
                packet.update(reviewer=candidates[0], reassignment={'previous': previous.get('handoff'),
                              'previous_pool': previous.get('handoffs', []), 'reason': self.req['reason']})
                if old_task.get('cross_client'):
                    packet['reassignment']['same_client_reason'] = policy
                handoff = 'review-' + digest(canonical(packet).encode())
                data = {'task': old_task['id'], 'reviewer': candidates[0], 'reason': self.req['reason'],
                        'previous': previous.get('handoff'), 'previous_pool': previous.get('handoffs', []),
                        'packet_data': packet, 'handoff': handoff,
                        'packet': self.artifact((canonical(packet) + '\n').encode(), handoff + '.json')}
                if old_task.get('cross_client'):
                    data['same_client_reason'] = policy
                from squadra import frozen_copy
                where = frozen_copy(self.root, previous['frozen'], old_task['repo'])[0] if previous.get('frozen') else old_task['repo']
                if len(candidates) == 1:
                    self.add('reassign', data)
                    self.add('message', {'id': handoff, 'to': candidates[0], 'related_task': old_task['id'],
                                        'body': 'Read reassigned: ' + where + '. ACK this handoff.\n' + canonical(packet)})
                else:
                    require(not any(r['task'] == old_task['id'] and r['status'] in LIVE and r.get('mode') == 'read' and r['id'] not in held
                                    for r in original['runs'].values()), 'Reconcile active reader before reassignment')
                    packet.pop('reviewer', None)
                    packet['pool'] = candidates
                    handoff = 'review-' + digest(canonical(packet).encode())
                    handoffs = [handoff + '-' + r for r in candidates if not standby(self.state, r)]
                    require(handoffs, 'No available replacement pool reader')
                    data.update(packet_data=packet, handoffs=handoffs, pool=candidates, reviewer=None,
                                packet=self.artifact((canonical(packet) + '\n').encode(), handoff + '.json'))
                    data.pop('handoff')
                    self.add('replacement.pool', data)
                    for reader in candidates:
                        ident = handoff + '-' + reader
                        if ident in handoffs:
                            self.add('message', {'id': ident, 'to': reader, 'related_task': old_task['id'],
                                                'pool': handoffs, 'body': 'Read reassigned: ' + where + '. First ACK takes the read.\n' + canonical(packet)})
            else:
                self.add('replacement.reader', {'task': old_task['id'], 'reviewer': candidates[0] if len(candidates) == 1 else None,
                                                'pool': candidates if len(candidates) > 1 else None, 'same_client_reason': policy})

        if successors:
            changed.update(t['id'] for t in self.state['tasks'].values()
                           if t['status'] != 'done' and not t.get('superseded_by') and
                           any(dep in successors for dep in t.get('depends', [])))
            self.add('replacement.dependencies', {'successors': successors})
        predecessors = {new: old for old, new in successors.items()}
        for ident in sorted(changed):
            task = self.state['tasks'][ident]
            if task.get('route') == 'light':
                from light_route import check_contract, contract
                check_contract(original['tasks'][predecessors.get(ident, ident)])
                self.add('replacement.contract', {'task': ident, 'contract': contract(task)})
        for old in sorted(self.mapping):
            if not standby(self.state, old):
                self.add('availability', {'agent': old, 'status': 'stand-by', 'reason': 'replaced by ' + self.mapping[old]})
        pending = [m for m in original['messages'].values() if m['to'] in self.mapping and
                   not m.get('ack') and not m.get('info') and not m.get('replaced_by')]
        self.add('replacement.retire', {'messages': [m['id'] for m in pending], 'by': self.key})
        outbox = []
        for new in sorted(set(self.mapping.values())):
            olds = sorted(old for old, target in self.req['map'].items() if target == new)
            self.add('replacement.provenance', {'agent': new, 'olds': olds})
            assignments = [t['id'] for t in self.state['tasks'].values() if t['status'] != 'done' and not t.get('superseded_by')
                           and (t['owner'] in peers(self.state, new) or t.get('reviewer') in peers(self.state, new) or
                                any(r in peers(self.state, new) for r in t.get('pool') or []))]
            messages = [m for m in pending if self.mapping[m['to']] == new and useful(m)
                        and self.req['carry'] != 'none' and (self.req['carry'] == 'all' or not m.get('related_task') or
                            not original['tasks'].get(m['related_task'], {}).get('superseded_by'))]
            template = self.state['workspace'].get('onboarding_template', 'Read the project rules and each assignment; ACK before starting.')
            body = '\n'.join(['Replacement onboarding', 'Registry: ' + str(self.root), 'Identity: ' + new,
                              'Replaces: ' + ', '.join(olds), template,
                              'Assignments: ' + ', '.join(assignments), 'Useful pending messages:',
                              *[m['id'] + ': ' + m['body'] for m in messages]])
            ident = self.key + '-onboard-' + new
            self.add('message', {'id': ident, 'to': new, 'body': body, 'delivery_intent': True})
            outbox.append(ident)
        return {'request': self.req, 'expected_seq': original['event_count'], 'planned_at': self.stamp,
                'operations': self.ops, 'outbox': outbox, 'successors': successors, 'unresolved_runs': unresolved}


def validate_event(state, actor, data):
    require(data['expected_seq'] == state['event_count'], 'Registry changed; rerun replace to replan')
    plan = Plan(state['root'], state, actor, data['request'], data['planned_at'])
    expected = plan.build()
    require(canonical(data) == canonical(expected), 'Replacement plan differs from current sources or registry')
    # A forced handover preserves unresolved runs and blocks successor execution;
    # it never invents a terminal receipt to satisfy ordinary supersession checks.
    working = plain(state)
    for op in data['operations']:
        kind, value = op['kind'], op['data']
        if not kind.startswith('replacement.'):
            checked = working
            if kind in {'task', 'reassign'} and data['request']['force']:
                checked = plain(working)
                held = set().union(*(peers(working, working['runs'][run]['agent'])
                                     for run in data['unresolved_runs']))
                for run in data['unresolved_runs']:
                    checked['runs'][run]['status'] = 'held-for-reconciliation'
                if kind == 'task':
                    # Validate the successor while the real reservations remain held in working.
                    checked['resources'] = {name: resource for name, resource in checked['resources'].items()
                                            if resource['owner'] not in held}
            validate(checked, kind, op['actor'], value)
        apply(working, {**op, 'id': plan.key, 'utc': data['planned_at']})


def execute(store, actor, req, dry_run=False):
    state = store.resolved()
    key = key_for(actor, req)
    recorded = state['workspace'].get('replacements', {}).get(key)
    if recorded:
        if not dry_run:
            deliver_outbox(store, actor, recorded['outbox'])
        return {'id': key, 'already_replaced': True, **plain(recorded)}
    unknown = [new for old, new in req['map'].items() if new not in state['agents'] and not req['sessions'].get(new)
               and (req['client'] or state['agents'].get(old, {}).get('client')) in {'codex', 'claude'}]
    if unknown:
        from census import unregistered
        return {'selection_required': sorted(set(unknown)), 'candidates': unregistered(state), 'changed': False}
    from squadra import bindings
    leaving = expansion(state, req)
    for task in state['tasks'].values():
        if (task['status'] == 'delivered' and not task.get('superseded_by') and task['owner'] not in leaving
                and any(r in leaving for r in [task.get('reviewer'), *(task.get('pool') or [])])):
            bindings(store, task['id'], require_review=False)
    planner = Plan(store.root, state, actor, req)
    data = planner.build()
    validate_event(state, actor, data)
    if dry_run:
        return {'dry_run': True, 'id': key, 'plan': data, 'artifacts': [ref for _, ref in planner.blobs.values()]}
    for body, ref in planner.blobs.values():
        store.artifact_bytes(body, ref['name'], ref['original'])
    store.append('replace', actor, data, key)
    # Queue attempts have their own receipt. A crash after the attempt is ambiguous:
    # rerunning replace never blindly resends an old onboarding message.
    deliver_outbox(store, actor, data['outbox'])
    return {'id': key, 'successors': data['successors'], 'outbox': data['outbox'], 'unresolved_runs': data['unresolved_runs']}


def deliver_outbox(store, actor, outbox):
    from squadra import dispatch
    for ident in outbox:
        message = store.view()['messages'][ident]
        if message.get('transport', {}).get('attempted') or message.get('ack') or message.get('replaced_by'):
            continue
        agent = store.view()['agents'][message['to']]
        via = 'codex-queue' if agent['client'] == 'codex' and agent.get('session') else 'manual'
        dispatch(SimpleNamespace(command='transport', actor=actor, message=ident, via=via, force=False), store)
