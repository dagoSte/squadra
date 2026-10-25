"""Turn observed development friction into a bounded, checked project rule."""
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from registry import Error, canonical, digest, git, require

START = '<!-- squadra:migliora:start -->'
END = '<!-- squadra:migliora:end -->'
RULE_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,63}\Z')


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
            done = subprocess.run(rule['check'], cwd=repo, env=env, capture_output=True, timeout=timeout)
        check = {'argv': rule['check'], 'native_rc': done.returncode,
                 'stdout': store.artifact_bytes(done.stdout, 'migliora-stdout.log', 'migliora:' + rule['id']),
                 'stderr': store.artifact_bytes(done.stderr, 'migliora-stderr.log', 'migliora:' + rule['id'])}
        require(done.returncode == 0, 'Migliora rule check failed; rule not committed')
        require(target.read_bytes() == updated, 'Rule changed during its check; reconcile before committing')
        require(git(repo, 'rev-parse', 'HEAD').strip() == head, 'HEAD changed during rule check; reconcile before committing')
        result = {'rule': rule['id'], 'path': 'AGENTS.md', 'sha256': digest(updated),
                  'check': check, 'committed': False, 'base_head': head.decode()}
        store.append('output', actor, {'task': task_id, 'text': 'Migliora: prepared checked rule ' + rule['id'],
                     'next': 'Complete required review and commit the checked instruction.',
                     'artifacts': [store.artifact(target)], 'migliora': result})
        if defer_commit:
            return result
        if not defer_commit:
            committing = True
            result['commit'] = commit(repo, rule['id'], digest(updated), head.decode())
            result['committed'] = True
        store.append('output', actor, {'task': task_id, 'text': 'Migliora: applied rule ' + rule['id'],
                     'next': 'Measure effectiveness at the next matching trigger.',
                 'artifacts': [], 'migliora': result})
        return result
    except BaseException:
        # Restore only our own unchanged bytes, never edits made by another writer or by a hook.
        if not committing and target.is_file() and target.read_bytes() == updated and git(repo, 'rev-parse', 'HEAD').strip() == head:
            if original is None:
                target.unlink()
            else:
                target.write_bytes(original)
        raise


def commit(repo, rule_id, expected_sha, base_head):
    # A pathspec commit preserves unrelated staged changes and runs the repository's native hooks.
    staged = git(repo, 'diff', '--cached', '--binary', '--', '.', ':!AGENTS.md')
    require(git(repo, 'rev-parse', 'HEAD').decode().strip() == base_head, 'HEAD changed before rule commit')
    require(digest((Path(repo) / 'AGENTS.md').read_bytes()) == expected_sha, 'Rule changed before commit')
    git(repo, 'add', '--', 'AGENTS.md')
    result = subprocess.run(['git', 'commit', '--only', '-m', 'Improve development workflow: ' + rule_id,
                             '--', 'AGENTS.md'], cwd=repo, env=os.environ | {'MEMORANDA_PATHS': 'AGENTS.md'},
                            capture_output=True, timeout=120)
    require(result.returncode == 0, 'Migliora commit refused by Git or its hooks; inspect the retained changes')
    require(git(repo, 'diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD').decode().splitlines() == ['AGENTS.md'] and
            digest(git(repo, 'show', 'HEAD:AGENTS.md')) == expected_sha and
            digest((Path(repo) / 'AGENTS.md').read_bytes()) == expected_sha and
            git(repo, 'rev-parse', 'HEAD^').decode().strip() == base_head,
            'Git committed unexpected bytes or a changed base; retain the commit and reconcile before continuing')
    require(git(repo, 'diff', '--cached', '--binary', '--', '.', ':!AGENTS.md') == staged,
            'Unrelated staged changes moved during commit; reconcile the shared index')
    return git(repo, 'rev-parse', 'HEAD').decode().strip()


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
    repo = Path(task['repo'])
    target = repo / 'AGENTS.md'
    require(not target.is_symlink() and digest(target.read_bytes()) == pending['sha256'],
            'Prepared rule changed; check and review the new bytes before committing')
    require(git(repo, 'rev-parse', 'HEAD').decode().strip() == pending['base_head'],
            'HEAD changed since the rule was checked; reconcile before committing')
    result = {**pending, 'commit': commit(repo, rule['id'], pending['sha256'], pending['base_head']), 'committed': True}
    store.append('output', actor, {'task': task_id, 'text': 'Migliora: committed rule ' + rule['id'],
                 'next': 'Measure effectiveness at the next matching trigger.', 'artifacts': [], 'migliora': result})
    return result
