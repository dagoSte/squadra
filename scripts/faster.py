"""Measured closing advice; an ineffective remedy is a warning, never a gate."""
import math
import sys

from forecast import timings
from registry import require

PHASES = ('ack', 'build', 'read', 'fix')


def measure(events, task):
    measured = timings(events, task)
    if measured is None:
        return None
    seconds = measured['seconds']
    phase = max(PHASES, key=lambda key: seconds[key])
    return {'phase': phase if seconds[phase] > 0 else None,
            'minutes': seconds[phase] / 60, 'plan_minutes': seconds['plan'] / 60,
            'seconds': seconds}


def validate_event(state, actor, data):
    require(actor in state['agents'] and state['agents'][actor]['role'] == 'coordinator',
            'faster_coordinator_required')
    require(isinstance(data, dict) and set(data) == {'task', 'phase', 'minutes', 'remedy'},
            'faster_fields_invalid')
    require(isinstance(data['task'], str) and data['task'] in state['tasks']
            and state['tasks'][data['task']]['status'] == 'done', 'faster_closed_task_required')
    require(isinstance(data['phase'], str) and data['phase'] in PHASES, 'faster_phase_invalid')
    require(type(data['minutes']) in (int, float) and data['minutes'] >= 0
            and (type(data['minutes']) is int or math.isfinite(data['minutes'])), 'faster_minutes_invalid')
    remedy = data['remedy']
    require(isinstance(remedy, str) and 0 < len(remedy.strip()) <= 1000 and remedy.isprintable(),
            'faster_remedy_required')


def record(store, actor, task, phase, remedy):
    _, histories = _context(store)
    measured = measure(histories.get(task, []), task)
    require(measured is not None, 'faster_timing_unavailable')
    data = {'task': task, 'phase': phase, 'minutes': measured['seconds'][phase] / 60,
            'remedy': remedy}
    store.append('faster', actor, data)
    return data


def ineffective(state, histories, closing_task):
    """Judge the first three closes of tasks assigned after the remedy was recorded."""
    warnings = []
    for data in state.get('faster', []):
        following = data['following']
        if len(following) < 3 or following[-1] != closing_task:
            continue
        measured = [measure(histories.get(task, []), task) for task in following]
        repeated = sum(value is not None and value['phase'] == data['phase'] for value in measured)
        if repeated >= 2:
            warnings.append({'event': data['seq'], 'task': data['task'], 'phase': data['phase'],
                             'remedy': data['remedy'], 'repeated': repeated,
                             'tasks': list(following)})
    return warnings


def _context(store):
    state = store.resolved(compact=True)
    return state, (state.current or {}).get('forecast_events', {})


def closing(store, task):
    state, histories = _context(store)
    return {'measured': measure(histories.get(task, []), task),
            'ineffective': ineffective(state, histories, task)}


def announce(report, lang):
    en = lang == 'en'
    labels = ({'ack': 'ACK wait', 'build': 'construction', 'read': 'review wait', 'fix': 'corrections'}
              if en else {'ack': 'attesa ACK', 'build': 'costruzione', 'read': 'attesa lettura', 'fix': 'correzioni'})
    measured = report['measured']
    prefix = 'Faster next time: ' if en else 'Più veloce la prossima volta: '
    if measured is None or measured['phase'] is None:
        text = 'no measured longest phase; no estimate.' if en else 'fase più lunga non misurabile; nessuna stima.'
    else:
        text = ('largest phase: ' if en else 'fase più lunga: ') + labels[measured['phase']]
        text += ' (%.2f min).' % measured['minutes']
        text += (' Plan wait: ' if en else ' Attesa piano: ') + '%.2f min.' % measured['plan_minutes']
    print(prefix + text, file=sys.stderr)
    for warning in report['ineffective']:
        text = ('ineffective remedy' if en else 'rimedio inefficace')
        text += ' (%s, %d/3): %s' % (labels[warning['phase']], warning['repeated'], warning['remedy'])
        print(prefix + text, file=sys.stderr)
