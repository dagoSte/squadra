"""Candidate-bound assertion evidence for explicitly declared requirement tests."""
from pathlib import Path
import sys

from registry import Error, canonical, digest, require, snapshot


def validate_task(task):
    if 'red_first' not in task:
        return
    require(type(task['red_first']) is bool, 'red_first_invalid')
    if not task['red_first']:
        return
    require(bool(task.get('requirements')), 'requirement_tests_required')
    for requirement in task['requirements']:
        require(('tests' in requirement) != ('exception' in requirement), 'requirement_tests_or_exception')
        if 'exception' in requirement:
            reason = requirement['exception']
            require(isinstance(reason, str) and reason.strip() and len(reason) <= 1000 and reason.isprintable(),
                    'requirement_exception_reason_required')
        else:
            ids = requirement['tests']
            require(isinstance(ids, list) and ids and all(isinstance(i, str) and
                    len(i.split('.')) >= 3 and all(p.isidentifier() for p in i.split('.')) for i in ids)
                    and len(ids) == len(set(ids)), 'requirement_test_ids_invalid')


def test_ids(task):
    return list(dict.fromkeys(i for r in task.get('requirements', []) for i in r.get('tests', [])))


def exceptions(task):
    return {r['id']: r['exception'] for r in task['requirements'] if 'exception' in r}


def policy(task):
    return digest(canonical({'requirements': task['requirements'], 'red_first': task['red_first']}).encode())


def prepare(store, task):
    from fix_challenge import ARGV, RUNNER, test_path, validate_fix
    validate_task(task)
    require(task.get('red_first') is True, 'red_first_required')
    ids = test_ids(task)
    if not ids:
        return {'v': 1, 'agreement': None}
    repo, files = Path(task['repo']).resolve(), {}
    for name in ids:
        module = '/'.join(name.split('.')[:-2]) + '.py'
        candidates = [repo / module, repo / 'tests' / module]
        found = [p for p in candidates if p.is_file()]
        require(len(found) == 1, 'requirement_test_file_missing_or_ambiguous')
        path = found[0]
        require(path.resolve().is_relative_to(repo) and
                not any(p.is_symlink() for p in [path, *path.parents] if p != repo and repo in p.parents),
                'fix_test_symlink_refused')
        relative = test_path(str(path.relative_to(repo)))
        files[relative] = store.artifact(path)
    fix = {'v': 1, 'agreement': {'runner': 'python-unittest-v1', 'ids': ids, 'files': files,
           'oracle': None, 'argv': list(ARGV), 'python': str(Path(sys.executable).resolve()),
           'wrapper': store.artifact(RUNNER)}}
    validate_fix(fix, store.root)
    return fix


def validate_record(task, record, current, root):
    from fix_challenge import validate_fix, verify_result, matched
    validate_task(task)
    require(task.get('red_first') is True and record.get('requirements') is True,
            'red_first_required')
    require(record.get('task') == task['id'] and record.get('base') == task['base'] and
            record.get('candidate') == current and record.get('requirements_binding') == policy(task),
            'requirement_challenge_required_or_stale')
    require(record.get('exceptions') == exceptions(task) and not record.get('exception'),
            'requirement_exceptions_changed')
    agreement = record.get('agreement')
    if not test_ids(task):
        require(agreement is None and record.get('runs') == [], 'requirement_challenge_invalid')
        return
    view = {**task, 'fix': {'v': 1, 'agreement': agreement}}
    validate_fix(view['fix'], root)
    require(agreement is not None and agreement['runner'] == 'python-unittest-v1' and
            agreement['ids'] == test_ids(task), 'requirement_test_ids_changed')
    from fix_challenge import binding
    require(record.get('binding') == binding(view), 'requirement_challenge_binding_changed')
    result = verify_result(view, record, root)
    require(matched(agreement, result['runs']), 'requirement_challenge_not_matched')


def validate_event(state, actor, data):
    from fix_challenge import eligible, inputs, manifest
    task = state['tasks'].get(data.get('task'))
    require(task is not None, 'Unknown task')
    eligible(state, task, actor)
    current = snapshot(task['repo'])
    # Failed experiments remain recorded with their native observations for diagnosis.
    if data.get('agreement'):
        original, remedy = inputs(task, current)
        require(data.get('original_files') == manifest(original) and data.get('remedy_files') == manifest(remedy),
                'fix_source_binding_changed')
    try:
        validate_record(task, data, current, state['root'])
    except Error as exc:
        if str(exc) != 'requirement_challenge_not_matched':
            raise


def evidence(task, current, root):
    if not task.get('red_first'):
        return None
    validate_task(task)
    if not test_ids(task):
        return {'exceptions': exceptions(task)}
    record = task.get('requirement_result')
    require(record is not None, 'requirement_challenge_required_or_stale')
    validate_record(task, record, current, root)
    return record
