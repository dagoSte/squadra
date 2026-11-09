"""Compose the existing CLI primitives without new events, roles or acknowledgements."""
from __future__ import annotations

import argparse
import contextlib
import io
import json

from registry import Store, composition, latest_ping, now, plain, require, standby


def add_parsers(sub):
    # Reuse the primitive argument definitions, including their mutually exclusive groups.
    assign = sub.add_parser('assign', parents=[sub.choices['task']], add_help=False,
                            help='Create, brief, transport and check an assignment')
    assign.add_argument('--brief-file', required=True)
    assign.add_argument('--via', choices=['manual', 'codex-queue'], required=True)
    join = sub.add_parser('join', parents=[sub.choices['agent']], add_help=False,
                          help='Register, ping and introduce this seat to an existing coordinator')
    join.add_argument('--coordinator', required=True)
    join.add_argument('--body-file', required=True)
    join.add_argument('--via', choices=['manual', 'codex-queue'], required=True)
    pickup = sub.add_parser('pickup', help='Read-only resume checks, census, board and pending mail')
    pickup.add_argument('--actor', required=True)


def _argv(cli, root, command, values):
    """Serialize only options accepted by the primitive; proof argv always goes last."""
    primitive = cli.parser()._subparsers._group_actions[0].choices[command]
    argv, tail = ['--root', str(root), command], []
    for action in primitive._actions:
        if action.dest in {'help', 'root'} or not action.option_strings:
            continue
        value = values.get(action.dest, action.default)
        if value is None or value == argparse.SUPPRESS:
            continue
        option = action.option_strings[0]
        if isinstance(value, bool):
            if value:
                argv.append(option)
        elif action.nargs == argparse.REMAINDER:
            tail = [option, *value]
        elif isinstance(value, list):
            for item in value:
                argv.extend([option, str(item)])
        else:
            argv.extend([option, str(value)])
    return argv + tail


def _state(root):
    store = Store(root, read_only=True)
    try:
        state = store.view()
        return {key: plain(state[key]) for key in ('agents', 'messages')}
    finally:
        store.close()


def _step(cli, root, command, values, completed):
    output, errors = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        rc = cli.main(_argv(cli, root, command, values))
    if rc:
        print(json.dumps({'step': command, 'completed': completed, 'rc': rc}))
        # Preserve the primitive's actual refusal/transport receipt; never retry a committed step.
        print(output.getvalue(), end='')
        print(errors.getvalue(), end='', file=cli.sys.stderr)
    else:
        completed.append(command)
        if command == 'checklist':
            for item in json.loads(output.getvalue())['items']:
                if item['status'] != 'ok':
                    print(json.dumps({'checklist_notice': item}, ensure_ascii=False), file=cli.sys.stderr)
    return rc


def run(a, root):
    import squadra as cli

    if a.command == 'pickup':
        return pickup(a, root, cli)
    values, completed = vars(a).copy(), []
    if a.command == 'assign':
        # Automatic lanes create one assignment per lane; assign transports only assign-<id>.
        require(not getattr(a, 'paths', None), 'assign does not create automatic lanes, whose assignments it would '
                'not transport; use task --paths, then transport each lane assignment')
        steps = [('task', values), ('brief', {**values, 'task': a.id, 'project': False, 'file': a.brief_file}),
                 ('transport', {**values, 'message': 'assign-' + a.id, 'force': False}),
                 ('checklist', {'moment': 'assign', 'actor': a.actor, 'task': a.id})]
        for command, options in steps:
            rc = _step(cli, root, command, options, completed)
            if rc:
                return rc
        print(json.dumps({'command': 'assign', 'task': a.id, 'ready': True}))
        return 0

    state = _state(root)
    coordinator = state['agents'].get(a.coordinator)
    require(coordinator and coordinator['role'] == 'coordinator' and not standby(state, a.coordinator),
            'join requires an existing active coordinator; it does not elect one')
    rc = _step(cli, root, 'agent', values, completed)
    if rc:
        return rc
    rc = _step(cli, root, 'ping', {'actor': a.id, 'to': a.coordinator}, completed)
    if rc:
        return rc
    ping = latest_ping(_state(root), a.coordinator)
    rc = _step(cli, root, 'transport', {'actor': a.id, 'message': ping['id'], 'via': a.via}, completed)
    if rc:
        return rc
    before = set(_state(root)['messages'])
    rc = _step(cli, root, 'send', {'actor': a.id, 'to': a.coordinator, 'body_file': a.body_file}, completed)
    if rc:
        return rc
    messages = _state(root)['messages']
    added = {key for key in set(messages) - before
             if messages[key]['from'] == a.id and messages[key]['to'] == a.coordinator and not messages[key].get('ping')}
    require(len(added) == 1, 'Introduction receipt is ambiguous; inspect the inbox before retrying')
    introduction = added.pop()
    rc = _step(cli, root, 'transport', {'actor': a.id, 'message': introduction, 'via': a.via}, completed)
    if rc:
        return rc
    print(json.dumps({'command': 'join', 'agent': a.id, 'coordinator': a.coordinator, 'ack_required': True}))
    return 0


def pickup(a, root, cli):
    """Use a read-only Store directly, including for compact checkpoints: never restore projections."""
    from census import collect

    store = Store(root, read_only=True)
    try:
        state = store.resolved()
        require(a.actor in state['agents'], 'Registered pickup actor required')
        doctor = cli.dispatch(cli.parser().parse_args(['doctor', '--resume']), store)
        found = collect(state['workspace']['project'], state)
        found['composition'] = composition(state, found['processes'], state['workspace'].get('lang', 'it'), found['scan'])
        messages = [m for m in cli.inbox(state, [a.actor]) if not m.get('ack') and not m.get('info')]
        print(json.dumps({'doctor --resume': plain(doctor), 'census': found}, ensure_ascii=False, indent=2))
        print(cli.board(state, read_at=now(), events=store.events()))
        print(json.dumps({'pending_mail': plain(messages)}, ensure_ascii=False, indent=2))
        return 0 if doctor.get('resume', {}).get('ready', True) else 1
    finally:
        store.close()
