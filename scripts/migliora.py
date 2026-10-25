"""Turn observed development friction into a bounded, checked project rule."""
from collections import Counter
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from registry import Error, canonical, digest, git, require

START = '<!-- squadra:migliora:start -->'
END = '<!-- squadra:migliora:end -->'
RULE_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,63}\Z')
COMMIT_SECONDS = 120


def run_process(argv, repo, env, timeout):
    """Bound the command and clean up only the process group created here."""
    from runtime import terminate
    options = {'start_new_session': True} if os.name != 'nt' else {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
    proc = subprocess.Popen(argv, cwd=repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
    timed_out = False
    try:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            terminate(proc)
            out, err = proc.communicate(timeout=10)
    finally:
        terminate(proc)
    result = subprocess.CompletedProcess(argv, proc.returncode, out, err)
    result.timed_out = timed_out
    return result


def inspect(store, task_id=None):
    state = store.resolved()
    tasks = [state['tasks'][task_id]] if task_id else list(state['tasks'].values())
    findings = []
    for task in tasks:
        failed = [p for p in task.get('proofs', []) if p['status'] != 'passed']
        if failed:
            findings.append({'task': task['id'], 'kind': 'failed-proof', 'count': len(failed),
                             'evidence': [p['id'] for p in failed],
                             'next': 'Classify the failures and inspect their logs before adding a rule.'})
        commands = Counter((p.get('after', {}).get('sha256'), canonical(p.get('argv')))
                           for p in task.get('proofs', []) if p['status'] == 'passed')
        repeated = [key for key, count in commands.items() if count > 1]
        if repeated:
            findings.append({'task': task['id'], 'kind': 'repeated-proof',
                             'count': sum(commands[key] - 1 for key in repeated),
                             'next': 'Check whether unchanged source, contract and environment justified reusing the proof.'})
    return {'task': task_id, 'findings': findings, 'automatic_changes': False,
            'next': 'Apply only an evidence-backed rule within the authorized project scope; otherwise record no change.'}


def rule_file(path):
    require(Path(path).stat().st_size <= 16384, 'Migliora rule exceeds 16 KiB')
    rule = json.loads(Path(path).read_text())
    fields = {'id', 'evidence', 'trigger', 'action', 'check', 'retire_when'}
    require(isinstance(rule, dict) and set(rule) == fields, 'Rule needs id, evidence, trigger, action, check and retire_when')
    require(isinstance(rule['id'], str) and RULE_ID.fullmatch(rule['id']), 'Invalid migliora rule ID')
    for key in ('trigger', 'action', 'retire_when'):
        require(isinstance(rule[key], str) and 1 <= len(rule[key]) <= 2000 and
                '<!--' not in rule[key] and '-->' not in rule[key], 'Invalid rule ' + key)
    require(isinstance(rule['evidence'], list) and 1 <= len(rule['evidence']) <= 16 and
            all(isinstance(e, str) and e for e in rule['evidence']), 'Rule needs bounded event or run evidence')
    require(isinstance(rule['check'], list) and rule['check'] and
            all(isinstance(a, str) and a and '\0' not in a for a in rule['check']), 'Rule check must be argv, never shell text')
    require(all(marker not in canonical(rule) for marker in (START, END, '<!-- rule:')),
            'Managed markers are forbidden in every rendered rule field')
    return rule


def render(text, rule):
    require(text.count(START) == text.count(END) and text.count(START) <= 1, 'Invalid managed migliora block')
    key = '<!-- rule:' + rule['id'] + ' -->'
    entry = (key + '\n' + 'Trigger: ' + rule['trigger'] + '\n\n' + rule['action'] + '\n\n' +
             'Evidence: ' + ', '.join(rule['evidence']) + '\n\n' +
             'Check argv: ' + canonical(rule['check']) + '\n\n' + 'Retire when: ' + rule['retire_when'] + '\n')
    if START not in text:
        return text.rstrip() + ('\n\n' if text.strip() else '') + START + '\n' + entry + END + '\n'
    left, middle = text.split(START, 1)
    managed, right = middle.split(END, 1)
    require(key not in managed, 'Rule already exists; inspect its effectiveness before changing it')
    return left + START + managed.rstrip() + '\n\n' + entry + END + right


def apply(store, actor, task_id, path, *, defer_commit=False, timeout=120):
    require(type(timeout) is int and 1 <= timeout <= 1800, 'Migliora check timeout must be 1..1800 seconds')
    task = store.task(task_id)
    require(actor == task['owner'] or store.view()['agents'][actor]['role'] == 'coordinator',
            'Migliora needs the task owner or coordinator')
    rule = rule_file(path)
    events = store.events()
    evidence = {e['id'] for e in events if e['data'].get('task') == task_id}
    evidence.update(e['data']['id'] for e in events if e['kind'] == 'run.start' and e['data'].get('task') == task_id)
    require(set(rule['evidence']) <= evidence, 'Rule evidence must belong to this task')
    repo = Path(task['repo'])
    target = repo / 'AGENTS.md'
    require(not target.is_symlink(), 'Migliora cannot replace a linked instruction file')
    require(not git(repo, 'status', '--porcelain', '--', 'AGENTS.md').strip(),
            'AGENTS.md has existing changes; preserve them and reconcile first')
    original = target.read_bytes() if target.exists() else None
    updated = render((original or b'').decode('utf-8'), rule).encode()
    head = git(repo, 'rev-parse', 'HEAD').strip()
    target.write_bytes(updated)
    committing = False
    try:
        with tempfile.TemporaryDirectory(prefix='squadra-migliora-') as home:
            env = os.environ | {'HOME': home, 'TMPDIR': home, 'PYTHONDONTWRITEBYTECODE': '1'}
            done = run_process(rule['check'], repo, env, timeout)
        check = {'argv': rule['check'], 'native_rc': done.returncode,
                 'status': 'timeout' if done.timed_out else 'passed' if done.returncode == 0 else 'failed',
                 'stdout': store.artifact_bytes(done.stdout, 'migliora-stdout.log', 'migliora:' + rule['id']),
                 'stderr': store.artifact_bytes(done.stderr, 'migliora-stderr.log', 'migliora:' + rule['id'])}
        if done.timed_out or done.returncode != 0:
            store.append('output', actor, {'task': task_id, 'text': 'Migliora rule check failed',
                         'next': 'Inspect the retained native check status and logs before retrying.',
                         'artifacts': [check['stdout'], check['stderr']],
                         'migliora': {'rule': rule['id'], 'status': 'check-failed', 'check': check,
                                      'committed': False, 'applied': False}})
            raise Error('Migliora rule check failed (' + check['status'] + ', native rc ' +
                        str(done.returncode) + '); rule not committed; logs saved in the task output')
        require(target.read_bytes() == updated, 'Rule changed during its check; reconcile before committing')
        require(git(repo, 'rev-parse', 'HEAD').strip() == head, 'HEAD changed during rule check; reconcile before committing')
        result = {'rule': rule['id'], 'path': 'AGENTS.md', 'sha256': digest(updated),
                  'check': check, 'committed': False, 'base_head': head.decode()}
        store.append('output', actor, {'task': task_id, 'text': 'Migliora: prepared checked rule ' + rule['id'],
                     'next': 'Complete required review and commit the checked instruction.',
                     'artifacts': [store.artifact(target)], 'migliora': result})
        if defer_commit:
            return result
        committing = True
        return recorded_commit(store, actor, task_id, repo, rule['id'], result)
    except BaseException:
        # Restore only our own unchanged bytes, never edits made by another writer or by a hook.
        if not committing and target.is_file() and target.read_bytes() == updated:
            if original is None:
                target.unlink()
            else:
                target.write_bytes(original)
        raise


def commit(repo, rule_id, expected_sha, base_head):
    """Hooks use a private index; reconcile only formerly clean entries after HEAD moves."""
    require(git(repo, 'rev-parse', 'HEAD').decode().strip() == base_head, 'HEAD changed before rule commit')
    require(digest((Path(repo) / 'AGENTS.md').read_bytes()) == expected_sha, 'Rule changed before commit')
    index = Path(git(repo, 'rev-parse', '--path-format=absolute', '--git-path', 'index').decode().strip())
    lock = Path(str(index) + '.lock')
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise Error('Git index is busy; retry after its writer finishes') from exc
    os.close(fd)
    try:
        with tempfile.TemporaryDirectory(prefix='squadra-rule-index-') as folder:
            private = Path(folder) / 'index'
            env = os.environ | {'GIT_INDEX_FILE': str(private)}
            def run(*args):
                result = subprocess.run(['git', *args], cwd=repo, env=env, capture_output=True)
                require(result.returncode == 0, 'Cannot prepare or reconcile private rule index')
                return result.stdout
            run('read-tree', base_head)
            run('add', '--', 'AGENTS.md')
            done = run_process(['git', 'commit', '-m', 'Improve development workflow: ' + rule_id],
                               repo, env, COMMIT_SECONDS)
            actual = git(repo, 'rev-parse', 'HEAD').decode().strip()
            changed = actual != base_head
            files = git(repo, 'diff', '--name-only', '-z', base_head, actual).decode().split('\0')[:-1] if changed else []
            mismatch = []
            if changed:
                instruction = subprocess.run(['git', 'show', 'HEAD:AGENTS.md'], cwd=repo, capture_output=True)
                if (files != ['AGENTS.md'] or git(repo, 'rev-parse', 'HEAD^').decode().strip() != base_head or
                        instruction.returncode != 0 or digest(instruction.stdout) != expected_sha):
                    mismatch.append('Git committed unexpected files, bytes or base')
                # The real index remains locked and untouched by hooks. Preserve preexisting staged entries.
                if index.exists():
                    shutil.copyfile(index, private)
                else:
                    private.unlink(missing_ok=True)
                    run('read-tree', base_head)
                for name in files:
                    entry = run('ls-files', '--stage', '-z', '--', name)
                    before = run('ls-tree', '-z', base_head, '--', name)
                    clean = (not entry and not before)
                    if entry and before:
                        clean = entry.split(b'\t')[0].split()[:2] == before.split(b'\t')[0].split()[::2]
                    if clean:
                        run('reset', '-q', actual, '--', name)
                os.replace(private, index)
                target = Path(repo) / 'AGENTS.md'
                if not target.is_file() or target.is_symlink() or digest(target.read_bytes()) != expected_sha:
                    mismatch.append('Working instruction changed during commit')
            status = 'reconcile' if mismatch or (changed and (done.timed_out or done.returncode != 0)) else (
                     'committed' if changed else 'timeout' if done.timed_out else 'refused')
            return {'commit': actual if changed else None, 'files': files, 'committed': changed,
                    'applied': status == 'committed', 'status': status, 'mismatch': mismatch,
                    'native_rc': done.returncode, 'stdout_bytes': done.stdout, 'stderr_bytes': done.stderr}
    finally:
        lock.unlink(missing_ok=True)


def recorded_commit(store, actor, task_id, repo, rule_id, pending):
    outcome = commit(repo, rule_id, pending['sha256'], pending['base_head'])
    logs = [store.artifact_bytes(outcome.pop(key), 'migliora-commit-' + key + '.log', 'migliora:' + rule_id)
            for key in ('stdout_bytes', 'stderr_bytes')]
    result = {**pending, **outcome, 'commit_logs': logs}
    store.append('output', actor, {'task': task_id, 'text': 'Migliora commit: ' + result['status'],
                 'next': 'Measure effectiveness.' if result['applied'] else 'Inspect the recorded commit, files and native logs before continuing.',
                 'artifacts': logs, 'migliora': result})
    if not result['applied']:
        label = 'timed out' if result['status'] == 'timeout' else 'refused' if not result['committed'] else 'needs reconciliation'
        raise Error('Migliora commit ' + label + '; actual commit ' + str(result['commit']) +
                    '; files ' + canonical(result['files']) + '; task output retains native status and logs')
    return result


def finish(store, actor, task_id, path):
    task = store.task(task_id)
    require(actor == task['owner'] or store.view()['agents'][actor]['role'] == 'coordinator',
            'Migliora needs the task owner or coordinator')
    rule = rule_file(path)
    pending = next((o['migliora'] for o in reversed(store.view()['outputs'])
                    if o.get('task') == task_id and o.get('migliora', {}).get('rule') == rule['id']), None)
    require(pending is not None, 'No prepared migliora rule')
    if pending.get('committed'):
        return {**pending, 'already_committed': True}
    require(pending.get('sha256') and pending.get('base_head') and
            pending.get('check', {}).get('status') == 'passed', 'No successfully checked instruction to commit')
    repo = Path(task['repo'])
    target = repo / 'AGENTS.md'
    require(not target.is_symlink() and digest(target.read_bytes()) == pending['sha256'],
            'Prepared rule changed; check and review the new bytes before committing')
    require(git(repo, 'rev-parse', 'HEAD').decode().strip() == pending['base_head'],
            'HEAD changed since the rule was checked; reconcile before committing')
    return recorded_commit(store, actor, task_id, repo, rule['id'], pending)
