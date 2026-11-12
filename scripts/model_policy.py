"""Opt-in model profiles, recorded per real window through existing registrations."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat
import sys

from registry import Error, plain, require
from usage_cache import EFFORTS

DEFAULT_PROFILES = Path(__file__).resolve().parents[1] / 'references/model-profiles.json'
MODEL = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,127}\Z')
REASONS = {
    'bounded_light_task': ('incarico con contratto leggero; limiti verificati alla consegna',
                           'bounded light task; limits checked at delivery'),
    'verified_small_change': ('modifica piccola verificata', 'verified small change'),
    'complex_task': ('incarico complesso o complessità non verificata', 'complex task or unverified complexity'),
    'independent_review': ('revisione indipendente', 'independent review'),
    'model_unavailable': ('modello rifiutato prima del lavoro', 'model rejected before work'),
    'model_quota': ('quota specifica del modello esaurita prima del lavoro', 'model-specific quota exhausted before work'),
}


def binding(agent):
    return {k: agent.get(k, '') for k in ('identity', 'session', 'client')}


@contextmanager
def window_lock(store):
    """Serialize preference updates and registrations before reading their versions."""
    fd = os.open(store.root / 'models.lock', os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'Unsafe model preference lock')
        if os.name == 'nt':
            import msvcrt
            if not info.st_size:
                os.write(fd, b'0')
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'nt':
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(fd)


def profiles(value, client):
    value = plain(value)
    require(type(value) is dict and set(value) == {'economy', 'quality'}, 'Two model profiles required: economy, quality')
    result = {}
    for key, row in value.items():
        require(type(row) is dict and set(row) <= {'model', 'effort', 'fallback'} and
                {'model', 'effort'} <= set(row), 'Invalid model profile')
        entries = [row, *row.get('fallback', [])] if type(row.get('fallback', [])) is list else []
        require(1 <= len(entries) <= 2, 'At most one fallback per profile')
        for item in entries:
            require(type(item) is dict and isinstance(item.get('model'), str) and
                    MODEL.fullmatch(item['model']) and
                    isinstance(item.get('effort'), str) and item['effort'] in EFFORTS - {'ultra'},
                    'Invalid model or effort in profile')
            require(client == 'codex' or item['effort'] in {'low', 'medium', 'high', 'xhigh', 'max'},
                    'Effort unsupported by this client')
        result[key] = {'model': row['model'], 'effort': row['effort']}
        if len(entries) == 2:
            require(set(entries[1]) == {'model', 'effort'}, 'Fallback needs only model and effort')
            result[key]['fallback'] = [{k: entries[1][k] for k in ('model', 'effort')}]
    return result


def preference(state, actor):
    require(actor in state['agents'], 'Unknown agent: ' + actor)
    seat = binding(state['agents'][actor])
    values = [plain(a['model_policy']) for a in state['agents'].values()
              if binding(a) == seat and isinstance(a.get('model_policy'), dict)
              and a['model_policy'].get('window') == seat]
    if values:
        require(all(type(v.get('seq')) is int for v in values), 'Invalid model preference sequence')
        value = max(values, key=lambda v: v['seq'])
        require(value.get('mode') in {'on', 'off'}, 'Invalid recorded model preference')
        return {**value, 'profiles': profiles(value['profiles'], seat['client'])
                if value['mode'] == 'on' or value.get('profiles') else {}}
    return {'window': seat, 'mode': 'ask', 'profiles': {}}


def configure(store, actor, mode, path=None):
    with window_lock(store):
        return _configure(store, actor, mode, path)


def _configure(store, actor, mode, path):
    state = store.resolved()
    previous = preference(state, actor)
    require(mode in {'on', 'off'}, 'Choose automatic models on or off')
    chosen = previous['profiles']
    if mode == 'on':
        client = state['agents'][actor]['client']
        require(client in {'codex', 'claude'}, 'Automatic models require a supported Codex or Claude runner')
        if path is None and previous['profiles']:
            chosen = previous['profiles']
        else:
            raw = Path(path or DEFAULT_PROFILES).read_bytes()
            require(len(raw) <= 16384, 'Model profiles file too large')
            value = json.loads(raw)
            require(type(value) is dict and value.get('schema') == 1 and
                    type(value.get('clients')) is dict and client in value['clients'], 'Missing client model profiles')
            chosen = profiles(value['clients'][client], client)
    value = {'window': previous['window'], 'mode': mode, 'profiles': chosen}
    if value != {k: previous[k] for k in ('window', 'mode', 'profiles')}:
        value['seq'] = state['event_count'] + 1
        record = plain(state['agents'][actor])
        record.pop('updated', None)
        store.append('agent', actor, record | {'model_policy': value})
        store.publish()
    return value


def require_answer(state, actor, policy):
    pending = any(binding(a) == policy['window'] and a.get('model_choice_required')
                  for a in state['agents'].values())
    require(policy['mode'] != 'ask' or not pending,
            'Choose automatic models before work: squadra models --actor ' + actor + ' --auto on|off')


def accept(state, actor, selection):
    """The runner's initial snapshot accepts one coherent window and preference."""
    policy = preference(state, actor)
    require_answer(state, actor, policy)
    if selection is None:
        return
    require(selection.get('window') == policy['window'] and policy['mode'] == 'on' and
            selection.get('policy_seq') == policy.get('seq'),
            'Automatic model selection changed before turn acceptance; retry the request')
    profile = policy['profiles'].get(selection.get('profile'))
    require(profile is not None and any(
        all(selection.get(key) == candidate[key] for key in ('model', 'effort'))
        for candidate in [profile, *profile.get('fallback', [])]),
        'Automatic model selection changed before turn acceptance; retry the request')


def choose(state, actor, task_id, mode, stage='code', *, root=None):
    policy = preference(state, actor)
    if policy['mode'] != 'on':
        require_answer(state, actor, policy)
        return None
    require(task_id in state['tasks'], 'Unknown task')
    task = state['tasks'][task_id]
    profile, reason, evidence = 'quality', 'complex_task', {}
    if mode == 'read':
        reason = 'independent_review'
    elif (stage == 'code' and task.get('route') == 'light' and not task.get('fix') and
          not task.get('full_gate') and not task.get('release_gate') and not task.get('depends')):
        from light_route import measure
        try:
            measured = measure(task, root=root)
            profile, reason = 'economy', 'verified_small_change'
            evidence = {k: measured[k] for k in ('files', 'lines', 'contract')}
        except Error as exc:
            if str(exc).startswith('light_empty_change'):
                # A registered light contract bounds the planned change; delivery still verifies it.
                profile, reason = 'economy', 'bounded_light_task'
    chosen = policy['profiles'][profile]
    return {k: chosen[k] for k in ('model', 'effort')} | {
        'automatic': True, 'window': policy['window'], 'policy_seq': policy['seq'],
        'profile': profile, 'reason': reason, 'evidence': evidence}


def message(selection, lang='it'):
    reason = REASONS[selection['reason']][lang == 'en']
    prefix = 'Automatic model selection' if lang == 'en' else 'Scelta automatica del modello'
    return f"{prefix}: {selection['model']} / {selection['effort']}; {'reason' if lang == 'en' else 'motivo'}: {reason}."


def failure_reason(run):
    """Retry only a structured model rejection before any model/tool output."""
    if run.get('status') != 'failed' or run.get('after') != run.get('before') or not run.get('after'):
        return None
    path = Path(run['folder']) / 'stdout.log'
    if not path.is_file() or path.stat().st_size > 262144:
        return None
    try:
        raw = path.read_text()
        try:
            parsed = json.loads(raw)
            rows = parsed if isinstance(parsed, list) else [parsed]
        except ValueError:
            rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
        if not rows or any(not isinstance(row, dict) or row.get('type') not in
                           {'thread.started', 'turn.failed', 'error', 'result'} for row in rows):
            return None
        errors = []
        for row in rows:
            if row.get('type') == 'result' and not (row.get('is_error') and row.get('num_turns') == 0):
                return None
            error = row.get('error')
            if isinstance(error, dict):
                errors.append(error)
            elif row.get('type') == 'error':
                errors.append(row)
        for error in errors:
            if error.get('code') in {'model_not_found', 'model_not_available', 'model_not_supported'}:
                return 'model_unavailable'
            if (error.get('code') in {'rate_limit_exceeded', 'quota_exceeded'} and
                    error.get('scope') == 'model' and error.get('model') == run.get('requested_model')):
                return 'model_quota'
    except (OSError, ValueError, UnicodeError):
        pass
    return None


def execute(store, actor, task_id, **kwargs):
    from runtime import execute as run, DEFAULT_TIMEOUT
    state = store.resolved()
    if state['agents'].get(actor, {}).get('client') == 'cursor':
        # Keep the native runner's pre-process refusal, including for a new unanswered window.
        return run(store, actor, task_id, **kwargs)
    timeout = kwargs.setdefault('timeout', DEFAULT_TIMEOUT)
    selection = choose(state, actor, task_id, kwargs['mode'], kwargs.get('stage', 'code'), root=store.root)
    lang = state.get('workspace', {}).get('lang', 'it')
    if selection:
        print(message(selection, lang), file=sys.stderr, flush=True)
    result = run(store, actor, task_id, model_selection=selection, **kwargs)
    if not selection or result.get('status') != 'failed':
        return result
    recorded = store.resolved()['runs'][result['id']]
    reason = failure_reason(recorded)
    if reason is None:
        return result
    policy = preference(store.resolved(), actor)
    if policy['mode'] != 'on' or policy['window'] != selection['window']:
        return result
    alternatives = policy['profiles'][selection['profile']].get('fallback', [])
    target_profile = selection['profile']
    if not alternatives and selection['profile'] == 'economy':
        alternatives = [policy['profiles']['quality']]
        target_profile = 'quality'
    if not alternatives or alternatives[0]['model'] == selection['model']:
        return result
    remaining = int(timeout - result.get('seconds', 0))
    if remaining < 1:
        return result
    fallback = {k: alternatives[0][k] for k in ('model', 'effort')} | {
        'automatic': True, 'window': policy['window'], 'policy_seq': policy['seq'],
        'profile': target_profile, 'reason': reason,
        'previous': {k: selection[k] for k in ('model', 'effort')}, 'previous_run': result['id'], 'evidence': {}}
    print(message(fallback, lang), file=sys.stderr, flush=True)
    # A second, separately recorded run consumes the ordinary task budget. Never loop or replay tool work.
    return run(store, actor, task_id, model_selection=fallback, **(kwargs | {'timeout': remaining}))


def report(state, actor, task_id=None, mode='build', stage='code', *, root=None):
    policy = preference(state, actor)
    lang = state.get('workspace', {}).get('lang', 'it')
    question = ('Enable automatic model and effort selection for this window before work? [on/off]'
                if lang == 'en' else 'Attivare la scelta automatica di modello ed effort per questa finestra prima di lavorare? [on/off]')
    result = {'schema': 1, 'actor': actor, **policy}
    if policy['mode'] == 'ask':
        result['question'] = question
    elif task_id:
        result['selection'] = choose(state, actor, task_id, mode, stage, root=root)
    runs = [r for r in state.get('runs', {}).values()
            if r.get('model_selection') and plain(r['model_selection'].get('window')) == policy['window']]
    if runs:
        last = max(runs, key=lambda r: (r['utc'], r['id']))
        result['last_selection'] = {**last['model_selection'], 'run': last['id'], 'utc': last['utc']}
    return result


def text_report(value, lang='it'):
    if value['mode'] == 'ask':
        return value['question'] + '\n' + 'squadra models --actor ' + value['actor'] + ' --auto on|off'
    lines = [('Scelta automatica' if lang != 'en' else 'Automatic selection') + ': ' + value['mode']]
    for key, row in value['profiles'].items():
        lines.append(f"  {key}: {row['model']} / {row['effort']}")
    for key in ('selection', 'last_selection'):
        if value.get(key):
            lines.append(message(value[key], lang))
    return '\n'.join(lines)
