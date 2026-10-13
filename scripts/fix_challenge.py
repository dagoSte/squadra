"""Bind defect demonstrations to the task, its original base and its candidate."""
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import time
import uuid

from registry import (canonical, digest, require, snapshot, fix_binding as binding,
                      fix_disposition as disposition, fix_matched as matched)

RUNNER = Path(__file__).with_name('challenge_runner.py')
ARGV = ['{python}', '-I', '-B', '{wrapper}', '{config}', '{result}']


def read_artifact(root, artifact):
    require(isinstance(artifact, dict) and {'sha256', 'bytes', 'name', 'original'} <= artifact.keys(),
            'fix_artifact_required')
    sha = artifact['sha256']
    require(isinstance(sha, str) and len(sha) == 64 and all(c in '0123456789abcdef' for c in sha),
            'fix_artifact_invalid')
    path = Path(root) / 'artifacts' / sha
    require(path.is_file() and not path.is_symlink(), 'fix_artifact_missing')
    body = path.read_bytes()
    require(digest(body) == sha and len(body) == artifact['bytes'], 'fix_artifact_changed')
    return body


def test_path(name):
    require(isinstance(name, str) and name and '\\' not in name, 'fix_test_path_required')
    path = PurePosixPath(name)
    require(not path.is_absolute() and str(path) == name and '..' not in path.parts and
            (path.parts[0] == 'tests' or (len(path.parts) == 1 and name.startswith('test_'))) and
            path.suffix == '.py', 'fix_test_path_required')
    return name


def validate_fix(fix, root):
    require(isinstance(fix, dict) and set(fix) == {'v', 'agreement'} and
            type(fix['v']) is int and fix['v'] == 1, 'fix_policy_unsupported')
    a = fix['agreement']
    if a is None:
        return
    require(isinstance(a, dict) and set(a) == {'runner', 'ids', 'files', 'oracle', 'argv', 'python', 'wrapper'},
            'fix_agreement_invalid')
    require(a['runner'] in ('python-unittest-v1', 'python-check-v1') and a['argv'] == ARGV and
            a['python'] == str(Path(sys.executable).resolve()), 'fix_runner_unsupported')
    require(read_artifact(root, a['wrapper']) == RUNNER.read_bytes(), 'fix_runner_changed')
    require(isinstance(a['files'], dict) and isinstance(a['ids'], list), 'fix_agreement_invalid')
    if a['runner'] == 'python-unittest-v1':
        require(a['oracle'] is None and a['ids'] and
                all(isinstance(i, str) and len(i.split('.')) >= 3 and
                    all(p.isidentifier() for p in i.split('.')) for i in a['ids']) and
                len(a['ids']) == len(set(a['ids'])) and a['files'],
                'fix_test_ids_required')
        for name, artifact in a['files'].items():
            test_path(name)
            read_artifact(root, artifact)
    else:
        require(not a['ids'] and not a['files'], 'fix_agreement_invalid')
        read_artifact(root, a['oracle'])


def prepare(store, task, filename=None):
    fix = {'v': 1, 'agreement': None}
    if filename is None:
        return fix
    config = json.loads(Path(filename).read_text())
    require(isinstance(config, dict) and set(config) <= {'runner', 'ids', 'files', 'oracle'},
            'fix_agreement_invalid')
    runner = config.get('runner', 'python-unittest-v1')
    a = {'runner': runner, 'ids': config.get('ids', []), 'files': {}, 'oracle': None,
         'argv': list(ARGV), 'python': str(Path(sys.executable).resolve()), 'wrapper': store.artifact(RUNNER)}
    repo = Path(task['repo']).resolve()
    for name in config.get('files', []):
        test_path(name)
        path = repo / name
        require(path.resolve().is_relative_to(repo) and
                not any(p.is_symlink() for p in [path, *path.parents] if p != repo and repo in p.parents),
                'fix_test_symlink_refused')
        a['files'][name] = store.artifact(path)
    if config.get('oracle'):
        a['oracle'] = store.artifact(config['oracle'])
    fix['agreement'] = a
    validate_fix(fix, store.root)
    return fix


def manifest(files):
    return [{'path': name, 'kind': 'file', 'sha256': digest(body), 'executable': executable}
            for name, (body, executable) in sorted(files.items())]


def tree_manifest(folder):
    rows = []
    for path in sorted(folder.rglob('*')):
        if path.is_symlink():
            rows.append({'path': str(path.relative_to(folder)), 'kind': 'symlink', 'sha256': digest(os.fsencode(os.readlink(path)))})
        elif path.is_file():
            rows.append({'path': str(path.relative_to(folder)), 'kind': 'file',
                         'sha256': digest(path.read_bytes()), 'executable': bool(path.stat().st_mode & 0o111)})
    return rows


def overlaid(rows, agreement, remedy):
    result = {r['path']: r for r in rows}
    modes = {r['path']: r['executable'] for r in remedy}
    for name, artifact in agreement['files'].items():
        result[name] = {'path': name, 'kind': 'file', 'sha256': artifact['sha256'], 'executable': modes[name]}
    return [result[k] for k in sorted(result)]


def inputs(task, current):
    from light_route import base_files
    _, original = base_files(task['repo'], task['base'])
    remedy = {}
    repo = Path(task['repo'])
    for row in current['files']:
        if row['kind'] == 'deleted':
            continue
        require(row['kind'] == 'file', 'fix_source_unsupported')
        path = repo / row['path']
        require(path.resolve().is_relative_to(repo.resolve()) and
                not any(p.is_symlink() for p in [path, *path.parents] if p != repo and repo in p.parents),
                'fix_source_unsupported')
        body = path.read_bytes()
        require(digest(body) == row['sha256'], 'fix_candidate_drift')
        remedy[row['path']] = body, row['executable']
    require(snapshot(repo) == current, 'fix_candidate_drift')
    return original, remedy


def eligible(state, task, actor):
    require(not state['paused'] and task['status'] != 'done' and not task.get('superseded_by'),
            'fix_task_not_open')
    require(actor in (task['owner'], task.get('reviewer'), task['coordinator']), 'fix_actor_refused')
    require(state['messages'].get('assign-' + task['id'], {}).get('ack'), 'Assignment has no recipient ACK')
    require(not any(r['task'] == task['id'] and r['status'] in ('running', 'cleanup-unknown')
                    for r in state['runs'].values()), 'fix_run_active')
    require(all(state['tasks'][d]['status'] == 'done' for d in task.get('depends', [])), 'Dependency not closed')


def verify_result(task, record, root):
    agreement = task['fix']['agreement']
    require(agreement is not None, 'fix_agreement_required')
    result = json.loads(read_artifact(root, record['result']))
    require(result == {k: v for k, v in record.items() if k != 'result'}, 'fix_result_binding_changed')
    require(read_artifact(root, result['wrapper']) == read_artifact(root, agreement['wrapper']), 'fix_runner_changed')
    for r in result['runs']:
        read_artifact(root, r['stdout']); read_artifact(root, r['stderr'])
        observed = json.loads(read_artifact(root, r['result']))
        keys = {'outcome', 'ids', 'executed', 'assertions', 'tests', 'failures', 'errors', 'skips', 'xfail', 'xpass', 'error'}
        require(isinstance(observed, dict) and all(r.get(k) == v for k, v in observed.items()) and
                {k: r[k] for k in keys if k in r} == observed, 'fix_observation_changed')
        config = json.loads(read_artifact(root, r['config']))
        require(config == {'runner': agreement['runner'], 'ids': agreement['ids'], 'files': list(agreement['files']),
                           'oracle': result.get('oracle', {}).get('original', '')}, 'fix_oracle_changed')
        substitutions = {'{python}': agreement['python'], '{wrapper}': result['wrapper']['original'],
                         '{config}': r['config']['original'], '{result}': r['result']['original']}
        require(r['argv'] == [substitutions.get(s, s) for s in agreement['argv']], 'fix_argv_changed')
        require(r.get('label') in ('original', 'remedy'), 'fix_result_binding_changed')
        expected = overlaid(result[r['label'] + '_files'], agreement, result['remedy_files'])
        require(r.get('executed_sha256') == digest(canonical(expected).encode()), 'fix_executed_input_changed')
    if agreement['oracle']:
        require(read_artifact(root, result['oracle']) == read_artifact(root, agreement['oracle']), 'fix_oracle_changed')
    return result


def evidence(task, current, root, require_review=False):
    if not task.get('fix'):
        return None
    validate_fix(task['fix'], root)
    record = task.get('fix_result')
    require(record and record.get('binding') == binding(task) and record.get('candidate') == current and
            record.get('task') == task['id'] and record.get('base') == task['base'], 'fix_challenge_required_or_stale')
    if record.get('exception'):
        require(isinstance(record.get('reason'), str) and record['reason'].strip(), 'fix_exception_reason_required')
    else:
        result = verify_result(task, record, root)
        require(matched(task['fix']['agreement'], result['runs']), 'fix_challenge_not_matched')
    if require_review:
        reads = [r for r in task['reviews'] if r['stage'] == 'code']
        require(reads and reads[-1].get('fix_binding') == binding(task) and
                reads[-1].get('fix_disposition') == disposition(task), 'fix_current_read_required')
    return record


def validate_event(state, kind, actor, data):
    task = state['tasks'].get(data.get('task'))
    root = state['root']
    if kind == 'task':
        if 'fix' in data:
            validate_fix(data['fix'], root)
        return
    if kind == 'fix':
        require(task is not None, 'Unknown task')
        require(state['agents'][actor]['role'] == 'coordinator', 'Coordinator role required')
        eligible(state, task, actor)
        require(isinstance(data.get('reason'), str) and data['reason'].strip(), 'fix_reason_required')
        require(data.get('previous') == (binding(task) if task.get('fix') else None), 'fix_contract_changed')
        validate_fix(data['fix'], root)
        if task.get('contract'):
            from light_route import contract
            require(data.get('contract') == contract({**task, 'fix': data['fix']}), 'fix_contract_changed')
        else:
            require('contract' not in data, 'fix_contract_changed')
        return
    if task is None or not task.get('fix'):
        require(kind != 'challenge', 'fix_agreement_required')
        return
    validate_fix(task['fix'], root)
    if kind == 'challenge':
        eligible(state, task, actor)
        current = snapshot(task['repo'])
        require(data.get('candidate') == current and data.get('binding') == binding(task) and
                data.get('base') == task['base'], 'fix_challenge_binding_changed')
        if data.get('exception'):
            require(state['agents'][actor]['role'] == 'coordinator', 'Coordinator role required')
            require(data.get('exception') is True and isinstance(data.get('reason'), str) and
                    data['reason'].strip(), 'fix_exception_reason_required')
        else:
            require(task['fix']['agreement'] is not None, 'fix_agreement_required')
            verify_result(task, data, root)
            original, remedy = inputs(task, current)
            require(data.get('original_files') == manifest(original) and data.get('remedy_files') == manifest(remedy),
                    'fix_source_binding_changed')
        return
    if kind in ('run.start', 'deliver', 'review'):
        require(data.get('fix_binding') == binding(task), 'fix_contract_changed')
    if kind == 'deliver':
        require(data.get('fix_disposition') == disposition(task), 'fix_disposition_changed')
        if task.get('reviewer'):
            require(data['packet_data'].get('fix') == task['fix'] and
                    data['packet_data'].get('fix_result') == task.get('fix_result'), 'fix_handoff_changed')
    if kind == 'review' and data['stage'] == 'code':
        require(data.get('fix_disposition') == disposition(task) and
                task.get('delivery', {}).get('fix_disposition') == disposition(task), 'fix_redelivery_required')
        if data['verdict'] == 'accepted':
            evidence(task, snapshot(task['repo']), root)
    if kind == 'close':
        from registry import candidate_checks
        current = snapshot(task['repo'])
        candidate_checks(task, current)
        evidence(task, current, root, require_review=True)


def register(store, actor, task_id, filename, reason):
    task = store.state()['tasks'][task_id]
    data = {'task': task_id, 'fix': prepare(store, task, filename), 'reason': reason,
            'previous': binding(task) if task.get('fix') else None}
    if task.get('contract'):
        from light_route import contract
        data['contract'] = contract({**task, 'fix': data['fix']})
    store.append('fix', actor, data)


def execute(store, actor, task_id, destination, timeout=30, exception=None):
    state = store.state(); task = state['tasks'][task_id]
    eligible(state, task, actor)
    require(task.get('fix'), 'fix_agreement_required')
    validate_fix(task['fix'], store.root)
    current = snapshot(task['repo'])
    record = {'task': task_id, 'base': task['base'], 'binding': binding(task), 'candidate': current}
    if exception is not None:
        record.update(exception=True, reason=exception)
        store.append('challenge', actor, record)
        return {'exception': True, 'reason': exception}
    agreement = task['fix']['agreement']
    require(agreement is not None, 'fix_agreement_required')
    require(os.name != 'nt' and type(timeout) is int and 1 <= timeout <= 3600, 'fix_bounded_timeout_required')
    folder = Path(destination).resolve()
    require(not folder.exists() and not folder.is_relative_to(Path(task['repo']).resolve()), 'fix_output_must_be_new_external')
    original, remedy = inputs(task, current)
    for name, artifact in agreement['files'].items():
        require(name in remedy and digest(remedy[name][0]) == artifact['sha256'], 'fix_oracle_drift')
    folder.mkdir(parents=True, mode=0o700)
    wrapper = folder / 'runner.py'; wrapper.write_bytes(read_artifact(store.root, agreement['wrapper']))
    oracle = folder / 'oracle.py'
    if agreement['oracle']:
        oracle.write_bytes(read_artifact(store.root, agreement['oracle']))
    record.update(id=uuid.uuid4().hex, original_files=manifest(original), remedy_files=manifest(remedy),
                  wrapper=store.artifact(wrapper), runs=[])
    if agreement['oracle']:
        record['oracle'] = store.artifact(oracle)
    from runtime import terminate
    for label, files in [('original', original), ('remedy', remedy)]:
        work = folder / label; work.mkdir()
        for name, (body, executable) in files.items():
            path = work / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body); path.chmod(0o755 if executable else 0o644)
        for name, artifact in agreement['files'].items():
            path = work / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(read_artifact(store.root, artifact))
            path.chmod(0o755 if remedy[name][1] else 0o644)
        before = tree_manifest(work)
        require(before == overlaid(manifest(files), agreement, manifest(remedy)), 'fix_copy_changed')
        config = folder / (label + '.config.json'); result = folder / (label + '.result.json')
        config_body = canonical({'runner': agreement['runner'], 'ids': agreement['ids'], 'files': list(agreement['files']),
                                 'oracle': str(oracle) if agreement['oracle'] else ''})
        config.write_text(config_body)
        substitutions = {'{python}': agreement['python'], '{wrapper}': str(wrapper),
                         '{config}': str(config), '{result}': str(result)}
        argv = [substitutions.get(s, s) for s in agreement['argv']]
        home = folder / (label + '-home'); home.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith('PYTHON')}
        env.update({k: str(home) for k in ('HOME', 'TMPDIR', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR')})
        out, err = folder / (label + '.stdout.log'), folder / (label + '.stderr.log')
        started = time.monotonic(); status = 'completed'; rc = None
        try:
            with out.open('wb') as stdout, err.open('wb') as stderr:
                proc = subprocess.Popen(argv, cwd=work, env=env, stdout=stdout, stderr=stderr, start_new_session=True)
                try:
                    rc = proc.wait(timeout=timeout)
                except (subprocess.TimeoutExpired, KeyboardInterrupt):
                    status = 'interrupted'
                finally:
                    try:
                        terminate(proc)
                        rc = proc.poll()
                    except (OSError, subprocess.SubprocessError):
                        status = 'cleanup-unknown'
            observed = json.loads(result.read_text()) if result.is_file() else {'outcome': 'instrument-error'}
            if not isinstance(observed, dict):
                raise ValueError('Invalid runner result')
        except (OSError, ValueError, subprocess.SubprocessError):
            observed = {'outcome': 'instrument-error'}
            status = 'instrument-error'
        if (tree_manifest(work) != before or snapshot(task['repo']) != current or
                wrapper.read_bytes() != read_artifact(store.root, agreement['wrapper']) or config.read_text() != config_body or
                (agreement['oracle'] and oracle.read_bytes() != read_artifact(store.root, agreement['oracle']))):
            status = 'source-changed'
        if not result.is_file():
            result.write_text(canonical(observed))
        record['runs'].append({**observed, 'label': label, 'status': status, 'native_rc': rc, 'argv': argv,
                               'seconds': round(time.monotonic() - started, 3), 'executed_sha256': digest(canonical(before).encode()),
                               'stdout': store.artifact(out), 'stderr': store.artifact(err), 'result': store.artifact(result),
                               'config': store.artifact(config)})
    receipt = folder / 'RESULT.json'; receipt.write_text(canonical(record) + '\n')
    store.append('challenge', actor, {**record, 'result': store.artifact(receipt)})
    return {'id': record['id'], 'status': 'matched' if matched(agreement, record['runs']) else 'failed',
            'base': task['base'], 'candidate': current['sha256'], 'runs': record['runs']}
