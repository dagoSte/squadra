"""Read-only evaluation of the recorded conditions of a pending session handover."""
import datetime as dt
from collections.abc import Mapping


def _timestamp(value):
    try:
        stamp = dt.datetime.fromisoformat(value)
        return stamp if stamp.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def evaluate(state, handover, now):
    """Return one n/kind/value/met/detail row per condition, preserving their order."""
    clock = _timestamp(now)
    english = state.get('workspace', {}).get('lang', 'it') == 'en'
    result = []
    for condition in handover.get('conditions', []):
        kind, value = condition['kind'], condition['value']
        met = False
        if kind == 'task':
            task = state.get('tasks', {}).get(value)
            met = task is not None and task.get('status') == 'done'
            detail = (('Task closed: ' if met else 'Wait for task closure: ') if english else
                      ('Incarico chiuso: ' if met else "Attendere la chiusura dell'incarico: ")) + value
        elif kind == 'after':
            target = _timestamp(value)
            met = clock is not None and target is not None and clock >= target
            detail = (('Time reached: ' if met else 'Wait until: ') if english else
                      ('Orario raggiunto: ' if met else 'Attendere fino a: ')) + value
            if clock is None or target is None:
                detail = ('Timezone-aware time required: ' if english else 'Orario con fuso richiesto: ') + value
        elif kind == 'confirm':
            confirmation = condition.get('confirmed')
            met = isinstance(confirmation, Mapping) and bool(confirmation)
            detail = (('Confirmation recorded: ' if met else 'Wait for confirmation: ') if english else
                      ('Conferma registrata: ' if met else 'Attendere la conferma: ')) + value
        else:
            detail = ('Unknown condition: ' if english else 'Condizione sconosciuta: ') + str(kind)
        result.append({'n': condition['n'], 'kind': kind, 'value': value, 'met': met, 'detail': detail})
    return result
