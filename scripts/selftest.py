"""Run offline checks and save their results without contacting models or live teams."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from registry import atomic, digest, now, require
from runtime import private_temp, remove_temp, temp_parent, terminate

TIMEOUT_SECONDS = 1800
GRACE_SECONDS = 30


def stop(proc):
    """Let the runner cancel its own worker sessions, then end its process group."""
    if os.name != "nt" and proc.poll() is None:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
    terminate(proc)


def counts(folder):
    """The runner's summary; an interrupted run counts the tests of the modules that finished."""
    def read(path):
        try:
            value = json.loads(path.read_text())
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}
    summary = read(folder / "summary.json")
    if summary:
        return summary
    return {"collected": len(read(folder / "discovery/result.json").get("ids", [])), "passed": False,
            "executed": sum(read(p).get("executed", 0) for p in folder.glob("*-*/result.json"))}


def handover_scenario(folder):
    """Synthetic coordinator transfer, structurally checked by test_handover.

    The ordinary selftest suite runs that test. This checks recorded coverage,
    never whether a model understands the continuation note.
    """
    from registry import Store, standby
    from context_brief import seat_context
    import handover
    from runtime import execute
    from squadra import deliver
    folder = Path(folder)
    folder.mkdir(mode=0o700, parents=True, exist_ok=False)
    repo = folder / 'repo'
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=repo, stderr=subprocess.PIPE)
    git('init', '-q')
    (repo / 'code.py').write_text('VALUE = 1\n')
    git('add', '.')
    git('-c', 'user.name=Synthetic', '-c', 'user.email=test@example.invalid',
        '-c', 'commit.gpgsign=false', 'commit', '-qm', 'Synthetic handover base')
    store = Store(folder / 'registry', create=True)
    try:
        store.append('init', 'system', {'name': 'Synthetic handover', 'project': str(repo), 'temporary': True,
                     'version': '3.14.0', 'limits': {'plan': 3, 'code': 2, 'build': 3}})
        for name, role, client in [('Old', 'coordinator', 'claude'), ('Worker', 'builder', 'manual'), ('Reader', 'reviewer', 'codex')]:
            store.append('agent', name, {'id': name, 'identity': name, 'session': 'synthetic-' + name,
                         'role': role, 'client': client, 'repo': str(repo), 'skills': []})
        scope = store.artifact_bytes(b'Synthetic task contract.\n', 'scope.md', 'selftest:handover')
        for name, owner, reader in [('OWN', 'Old', 'Reader'), ('READ', 'Worker', 'Old')]:
            store.append('task', 'Old', {'id': name, 'title': 'Synthetic ' + name, 'owner': owner,
                         'repo': str(repo), 'base': git('rev-parse', 'HEAD').decode().strip(), 'scope': scope,
                         'proof': None, 'expected': None, 'full_gate': False, 'depends': [],
                         'delivery_policy': 'frozen-v1', 'reviewer': reader})
            store.append('message', 'Old', {'id': 'assign-' + name, 'to': owner, 'task': name, 'body': 'Synthetic work'})
            store.append('output', owner, {'task': name, 'text': 'Synthetic state', 'next': 'Next step for ' + name})
        store.append('ack', 'Worker', {'message': 'assign-READ'})
        proof = execute(store, 'Worker', 'READ', argv=[sys.executable, '-c', 'pass'])
        require(proof['status'] == 'passed', 'Synthetic handover proof failed')
        delivery = deliver(store, 'Worker', 'READ')
        store.append('resource.acquire', 'Old', {'name': 'synthetic-resource'})
        resource_note = seat_context(store, 'Old', max_chars=None)
        store.append('resource.release', 'Old', {'name': 'synthetic-resource'})
        store.append('message', 'Worker', {'id': 'question', 'to': 'Old', 'body': 'Synthetic pending question'})
        store.append('decision', 'Old', {'id': 'synthetic-contract', 'tasks': ['OWN'], 'reason': 'Preserve the contract'})
        ident = handover.prepare(store, 'Old', 'Old', 'New', outside_unknown='Synthetic session, no outside processes',
                                 owner_words='Synthetic owner authorizes the session change', channel='typed-here')['id']
        before = store.state()
        result = handover.accept(store, 'New', ident, 'synthetic-new-session', 'claude')
        state = store.state()
        note = json.loads(state['handovers'][ident]['accepted']['seat_note'])
        expected_tasks = {'OWN', 'READ'}
        expected_messages = {m['id'] for m in before['messages'].values() if not m.get('ack') and
                             not m.get('info') and not m.get('replaced_by') and (m['to'] == 'Old' or m['from'] == 'Old')}
        checks = {'tasks': {t['id'] for t in note['tasks']} == expected_tasks,
                  'next_steps': {t['next'] for t in note['tasks']} == {'Next step for OWN', 'Next step for READ'},
                  'pending_messages': {m['id'] for m in note['pending_messages']} == expected_messages,
                  'resources': note['resources'] == [] and resource_note['resources'][0]['name'] == 'synthetic-resource',
                  'decisions': 'synthetic-contract' in {d['id'] for d in note['decisions']},
                  'one_coordinator': [n for n, a in state['agents'].items() if a['role'] == 'coordinator' and not standby(state, n)] == ['New'],
                  'read_preserved': state['tasks']['READ']['reviewer'] == 'New' and
                                    state['tasks']['READ']['delivery']['frozen'] == before['tasks']['READ']['delivery']['frozen'],
                  'fresh_read_ack': state['messages'][state['tasks']['READ']['delivery']['handoff']]['ack'] is None,
                  'successor': 'OWN' in result['successors'],
                  'history': before['decisions'][0] in state['decisions']}
        return {'status': 'passed' if all(checks.values()) else 'failed', 'checks': checks,
                'scope': 'Structural recorded coverage; no model-comprehension claim', 'id': ident,
                'read_before': delivery['handoff']}
    finally:
        store.close()


def run(output=None, timeout=TIMEOUT_SECONDS):
    source = Path(__file__).resolve().parents[1]
    folder = Path(output).resolve() if output else Path.home() / ".local/state/squadra/selftests" / uuid.uuid4().hex
    require(timeout > 0, "Positive --timeout required")
    require(not folder.is_relative_to(source), "Selftest output must be outside the Squadra source")
    parent = temp_parent({"the selftest output": folder, "the Squadra source": source})
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    # The parallel runner fails a run whose executed tests differ from the collected ones.
    argv = [sys.executable, "-B", "tools/parallel_tests.py", "--timeout", str(timeout), "--output", str(folder / "parallel")]
    result = {"folder": str(folder), "argv": argv, "started": now(), "python": sys.version,
              "status": "failed", "native_rc": None, "timeout_seconds": timeout,
              "scope": "Synthetic toolkit checks; no live model or product gate"}
    temp = result["temp"] = private_temp(parent)
    env = os.environ.copy()
    env.update(HOME=temp, TMPDIR=temp, PYTHONDONTWRITEBYTECODE="1")
    start = time.monotonic()
    try:
        with (folder / "stdout.log").open("wb") as out, (folder / "stderr.log").open("wb") as err:
            options = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            proc = subprocess.Popen(argv, cwd=source, env=env, stdout=out, stderr=err, **options)
            try:
                result["native_rc"] = proc.wait(timeout=timeout)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                stop(proc)
                result.update(status="interrupted", native_rc=proc.returncode)
    finally:
        result.update(remove_temp(temp))
    summary = counts(folder / "parallel")
    result.update(collected=summary.get("collected", 0), tests=summary.get("executed", 0))
    if (result["status"] != "interrupted" and result["native_rc"] == 0 and summary.get("passed") is True
            and result["tests"] > 0 and result["tests"] == result["collected"]):
        result["status"] = "passed"
    result.update(ended=now(), seconds=round(time.monotonic() - start, 3),
                  hashes={name: digest((folder / name).read_bytes()) for name in ["stdout.log", "stderr.log"]})
    atomic(folder / "RESULT.json", (json.dumps(result, indent=2) + "\n").encode())
    return result
