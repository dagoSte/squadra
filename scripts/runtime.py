"""Run owned processes and record exit status, cancellation and source versions."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import uuid

from registry import (Error, require, snapshot, now, atomic, canonical, run_lease, run_lease_paths,
                      shared_record, validate)

# A measured process gets HOME and TMPDIR in a private folder outside the registry: Unix socket paths stop
# near 104 bytes, and a folder others can write lets them swap what the process reads.
TEMP_LIMIT = 64
TEMP_PREFIX = "sq-"
TEMP_FIX = "set SQUADRA_TMP to a short private folder, for example SQUADRA_TMP=/tmp"


def _open_to_others(path):
    """Whether others may write the folder: group- or other-writable, and not a root-owned sticky one like /tmp."""
    info = os.stat(path)
    return bool(info.st_mode & 0o022) and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)


def _temp_problem(path, outside=None):
    """Every reason a private temp folder at this path is refused, or None; the path need not exist yet.

    outside maps a description to a folder the temp folder must not be in, such as the registry,
    compared both as written and with every link resolved."""
    problems = []
    if len(path) > TEMP_LIMIT:
        problems.append(path + " is " + str(len(path)) + " characters, over " + str(TEMP_LIMIT))
    for label, folder in (outside or {}).items():
        mine, theirs = Path(os.path.abspath(path)), Path(os.path.abspath(folder))
        if (mine.is_relative_to(theirs) or
                Path(os.path.realpath(mine.parent)).is_relative_to(os.path.realpath(theirs))):
            problems.append(path + " is inside " + label + " " + str(folder))
    if os.name != "nt":
        parent = Path(path).parent
        try:
            real = Path(os.path.realpath(parent))
            for folder in dict.fromkeys((parent, *parent.parents, real, *real.parents)):
                if _open_to_others(folder):
                    problems.append(path + " sits under " + str(folder) + ", which others can write")
                    break
        except OSError as exc:
            problems.append(path + " cannot be checked (" + type(exc).__name__ + ": " + str(exc) + ")")
    return "; ".join(problems) or None


def temp_parent(outside=None):
    """The folder that receives a run's private temp folder: SQUADRA_TMP when set, else the system temp, else /tmp.

    Refuses before anything starts, naming the path and the fix, when no candidate is short, safe and
    outside the folders named in outside."""
    explicit = os.environ.get("SQUADRA_TMP")
    candidates = [explicit] if explicit else dict.fromkeys((tempfile.gettempdir(), "/tmp"))
    problems = []
    for candidate in candidates:
        parent = os.path.abspath(candidate)
        problem = (_temp_problem(os.path.join(parent, TEMP_PREFIX + "XXXXXXXX"), outside) if os.path.isdir(parent)
                   else parent + " is not a folder")
        if problem is None:
            return parent
        problems.append(problem)
    raise Error("private_temp_refused: the run did not start; " + "; ".join(problems) +
                ("" if explicit else " (from the system temp folder)") + ". Fix: " + TEMP_FIX)


def private_temp(parent=None, outside=None):
    """Make a run's private temp folder (mode 0700) and return its path, refusing an unsafe or too-long one,
    or one inside the folders named in outside; a parent from temp_parent(outside) was checked against them."""
    folder = tempfile.mkdtemp(prefix=TEMP_PREFIX, dir=parent or temp_parent(outside))
    problem = _temp_problem(folder, outside)
    info = os.stat(folder)
    if problem is None and os.name != "nt" and (stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.geteuid()):
        problem = folder + " is not a private 0700 folder of this user"
    if problem is not None:
        os.rmdir(folder)
        raise Error("private_temp_refused: the run did not start; " + problem + ". Fix: " + TEMP_FIX)
    return folder


def _open_for_owner(folder):
    """Give each folder under folder back to its owner before it is listed; links are never followed."""
    def reopen(path):
        try:
            if not os.path.islink(path):
                os.chmod(path, 0o700)
        except OSError:
            pass
    reopen(folder)
    for path, folders, _ in os.walk(folder):
        for name in folders:
            reopen(os.path.join(path, name))


def remove_temp(folder):
    """Remove a run's private temp folder once its process group is gone; the result says whether it went."""
    for attempt in (1, 2):
        try:
            shutil.rmtree(folder)
            break
        except FileNotFoundError:
            break
        except OSError as exc:
            if attempt == 2:
                return {"temp_removed": False, "temp_error": type(exc).__name__ + ": " + str(exc)}
            # A test may leave read-only or unreadable folders behind; the owner can always open its own.
            _open_for_owner(folder)
    return {"temp_removed": True}


REVIEW_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "verdict": {"type": "string", "enum": ["accepted", "changes-required", "blocked"]},
    "summary": {"type": "string"}, "findings": {"type": "array", "items": {"type": "string"}},
    "coverage": {"type": "array", "items": {"type": "string"}},
    "limitations": {"type": "array", "items": {"type": "string"}}},
    "required": ["verdict", "summary", "findings", "coverage", "limitations"]}


# Cursor runs unsandboxed user and organization hooks, so it is interactive only.
CURSOR_REFUSAL = ("Squadra does not launch cursor-agent: Cursor runs team, user and enterprise hooks unsandboxed. "
                  "Cursor seats work interactively / Squadra non avvia cursor-agent: Cursor esegue gli hook di team, "
                  "utente e azienda senza sandbox. Le finestre Cursor lavorano in modo interattivo")


def cli_command(client, mode, folder, model=None, executable=None):
    if client == "cursor":
        raise Error(CURSOR_REFUSAL)
    executable = executable or client
    if client == "codex":
        # Ignore ordinary configuration for read turns; preserve authentication in CODEX_HOME.
        args = [executable, "exec", "--json", "-s", "read-only" if mode == "read" else "workspace-write",
                "-c", 'approval_policy="never"', "-o", str(folder / "reply.txt")]
        if mode == "read":
            args += ["--ignore-user-config", "--output-schema", str(folder / "schema.json")]
        if model:
            args += ["-m", model]
        return args + ["-"]
    if client == "claude":
        args = [executable, "-p", "--output-format", "json", "--permission-prompts", "none"]
        if mode == "read":
            args += ["--safe-mode", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                     "--tools", "Read,Glob,Grep", "--allowedTools", "Read,Glob,Grep",
                     "--permission-mode", "dontAsk", "--no-chrome", "--json-schema", json.dumps(REVIEW_SCHEMA)]
        else:
            args += ["--permission-mode", "acceptEdits"]
        if model:
            args += ["--model", model]
        return args
    raise Error("Only registered codex/claude clients can run; manual seats use shared inbox")


def validate_reply(client, folder, structured=True):
    text = (folder / "stdout.log").read_text(errors="replace")
    if client == "codex":
        events = [json.loads(line) for line in text.splitlines() if line.strip()]
        require(all(isinstance(e, dict) for e in events), "Invalid event stream")
        require(not any(e.get("type") in {"turn.failed", "error"} for e in events), "Failed model turn")
        completed = [e for e in events if e.get("type") == "turn.completed"]
        sessions = [e.get("thread_id") for e in events if e.get("type") == "thread.started"]
        require(len(completed) == 1 and len(sessions) == 1, "Missing completed turn/session")
        body = (folder / "reply.txt").read_text()
        response = json.loads(body) if structured else body
        session, usage = sessions[0], completed[0].get("usage")
        models = sorted({e["model"] for e in events if isinstance(e.get("model"), str)})
    else:
        value = json.loads(text)
        if isinstance(value, list):
            values = [e for e in value if isinstance(e, dict) and e.get("type") == "result"]
            require(len(values) == 1, "Missing unique Claude result")
            value = values[0]
        require(isinstance(value, dict) and value.get("type") == "result" and
                value.get("subtype") == "success" and not value.get("is_error"), "Failed Claude turn")
        response = value.get("structured_output") if structured else value.get("result")
        session, usage = value.get("session_id"), value.get("usage")
        models = [value["model"]] if isinstance(value.get("model"), str) else []
    try:
        uuid.UUID(session)
    except (ValueError, TypeError, AttributeError) as exc:
        raise Error("Missing valid actual session UUID") from exc
    if not structured:
        require(isinstance(response, str) and bool(response.strip()), "Empty build result")
        return {"response": response, "actual_session": session, "usage": usage, "observed_models": models}
    require(isinstance(response, dict) and set(response) == set(REVIEW_SCHEMA["required"]), "Malformed structured review")
    require(response["verdict"] in {"accepted", "changes-required", "blocked"}, "Invalid verdict")
    require(isinstance(response["summary"], str) and bool(response["summary"].strip()), "Empty summary")
    for field in ["findings", "coverage", "limitations"]:
        require(isinstance(response[field], list) and all(isinstance(x, str) and x.strip() for x in response[field]), "Invalid " + field)
    require(response["verdict"] == "blocked" or response["coverage"], "Missing review coverage")
    require(response["verdict"] != "accepted" or not response["findings"], "Accepted verdict with unresolved findings")
    require(response["verdict"] != "changes-required" or response["findings"], "Missing concrete findings")
    require(response["verdict"] != "blocked" or response["limitations"], "Missing blocker")
    return {"response": response, "actual_session": session, "usage": usage, "observed_models": models}


def terminate(proc):
    if os.name == "nt":
        if proc.poll() is not None:
            return
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        def kill_group(sig):
            # Poll and wait briefly through macOS exit-window EPERM; persistent denial still raises.
            limit = time.monotonic() + 2
            while True:
                try:
                    os.killpg(proc.pid, sig)
                    return
                except PermissionError:
                    proc.poll()
                    if time.monotonic() >= limit:
                        raise
                    time.sleep(0.005)

        try:
            kill_group(signal.SIGTERM)
        except ProcessLookupError:
            proc.wait(timeout=10); return
        # A leader can exit while a descendant ignores TERM. Wait for the group,
        # not just the leader, then kill remaining owned descendants.
        deadline = time.monotonic() + 2
        while True:
            proc.poll()  # Reap the leader if already exited.
            try:
                kill_group(0)
            except ProcessLookupError:
                break
            if time.monotonic() >= deadline:
                try:
                    kill_group(signal.SIGKILL)
                except ProcessLookupError:
                    pass
                break
            time.sleep(0.05)
    proc.wait(timeout=10)


def execute(store, actor, task_id, argv=None, mode="proof", prompt=None,
            timeout=120, gate=False, executable=None, run_id=None, stage="code", expected_rc=0, covers=None,
            *, checked=None, also=None):
    run_id = run_id or uuid.uuid4().hex
    with run_lease(store.root, run_id) as lease_fd:
        return _execute_owned(store, actor, task_id, argv, mode, prompt, timeout, gate, executable,
                              run_id, stage, expected_rc, covers, checked=checked, also=also, lease_fd=lease_fd)


def _execute_owned(store, actor, task_id, argv, mode, prompt, timeout, gate, executable,
                   run_id, stage, expected_rc, covers, *, checked, also, lease_fd):
    state = store.resolved()
    require(task_id in state["tasks"], "Unknown task")
    task = state["tasks"][task_id]
    agent = state["agents"][actor]
    # Refuse automated Cursor runs before creating files or processes.
    require(not (mode in {"read", "build"} and agent["client"] == "cursor"), CURSOR_REFUSAL)
    require(1 <= timeout <= 86400, "Timeout must be explicit and bounded")
    if task.get("plan"):
        from registry import digest
        path = Path(task["plan"]["original"])
        require(path.is_file() and digest(path.read_bytes()) == task["plan"]["sha256"], "Original plan changed; re-register/review it")
    read_frozen = mode == 'read' and stage == 'code' and task.get('delivery', {}).get('frozen')
    from frozen_candidate import current as delivered_current
    from frozen_candidate import repository as frozen_repository
    working_repo = str(frozen_repository(task, store.root)) if read_frozen else task['repo']
    before = delivered_current(task, store.root) if read_frozen else snapshot(task['repo'], store.root)
    # A passed precheck binds the proof to the exact bytes it checked, before any record or process.
    require(checked is None or before == checked,
            "precheck_failed: source changed after the checks (checked " + str((checked or {}).get("sha256", ""))[:12] +
            ", now " + before["sha256"][:12] + "); prove again")
    if task.get("fix"):
        from fix_challenge import binding
        fix_record = {"fix_binding": binding(task)}
    else:
        fix_record = {}
    route_record = {}
    if task.get("contract") or task.get("route") == "light":
        from light_route import check_contract, measure
        check_contract(task)
        route_record["contract"] = task["contract"]
        if mode == "proof" and task["route"] == "light":
            route_record["light"] = measure(task, before)
    shared = None
    if also:
        require(mode == 'proof', 'Only proof runs may serve additional tasks')
        shared, covers = shared_record(state, task_id, also, before, argv, gate, expected_rc, covers or [])
    folder = store.root / "runs" / run_id
    if mode in {"read", "build"}:
        require(prompt is not None, "Model run requires a prompt file")
        if mode == "read":
            prompt = ("Read-only independent examination. Do not edit, publish, send messages or claim tests ran. "
                      "Return the requested structured verdict, actual coverage and limitations.\n" + prompt)
        else:
            prompt = ("Build only your scoped assignment. Do not commit, push or publish. "
                      "Report changed files, meaningful tests and any denied operation.\n" + prompt)
        argv = cli_command(agent["client"], mode, folder, agent.get("model") or None, executable)
        if agent["client"] == "codex" and mode == "read":
            config = Path(working_repo) / ".codex/config.toml"
            require(not config.exists(), "Review repo contains Codex config; inspect in a clean isolated copy without external MCP configuration")
    else:
        require(argv and all(isinstance(x, str) and x for x in argv), "Proof requires an argv list")
        require(mode == "probe" or not task.get("proof") or argv == task["proof"], "Proof command differs from agreed task contract")
    record = {"id": run_id, "task": task_id, "mode": mode, "argv": argv,
              "before": before, "gate": gate, "timeout": timeout, "folder": str(folder), "stage": stage,
              "expected_rc": expected_rc, "requested_model": agent.get("model"), "covers": covers or [], **route_record}
    record.update(fix_record)
    record["lease_version"] = 1
    if shared is not None:
        record['shared_proof'] = shared
        validate(state, 'run.start', actor, record)
    # Model turns keep the caller's HOME, where the client keeps its sign-in.
    temp = private_temp(outside={"the registry": store.root}) if mode in {"proof", "probe"} else None
    try:
        if temp:
            record["temp"] = temp
        folder.mkdir(parents=True, mode=0o700, exist_ok=False)
        if mode in {"read", "build"}:
            (folder / "schema.json").write_text(json.dumps(REVIEW_SCHEMA))
        _, _, lease_metadata, terminal_receipt = run_lease_paths(store.root, run_id)
        atomic(lease_metadata, canonical({"run": run_id, "phase": "not-started", "pgid": None}).encode())
        store.append("run.start", actor, record, "run-start:" + run_id)
        store.publish()
    except BaseException:
        if temp:
            remove_temp(temp)
        raise
    result = {"id": run_id, "status": "failed", "native_rc": None, "started": now()}
    env = os.environ.copy()
    if temp:
        env.update(HOME=temp, TMPDIR=temp, PYTHONDONTWRITEBYTECODE="1")
    proc = None
    start = time.monotonic()
    last_heartbeat = start
    try:
        if mode in {"read", "build"}:
            version = subprocess.run([argv[0], "--version"], capture_output=True, text=True, timeout=15)
            require(version.returncode == 0, "CLI version preflight failed")
            result["cli_version"] = version.stdout.strip()
        (folder / "input.txt").write_text(prompt or "")
        with (folder / "stdout.log").open("wb") as stdout, (folder / "stderr.log").open("wb") as stderr, (folder / "input.txt").open("rb") as stdin:
            options = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            atomic(lease_metadata, canonical({"run": run_id, "phase": "launching", "pgid": None}).encode())
            if os.name != "nt":
                options["pass_fds"] = (lease_fd,)
            proc = subprocess.Popen(argv, cwd=working_repo, env=env, stdout=stdout, stderr=stderr,
                                    stdin=stdin if prompt is not None else subprocess.DEVNULL, **options)
            atomic(lease_metadata, canonical({"run": run_id, "phase": "spawned", "pgid": proc.pid}).encode())
            while proc.poll() is None:
                current = store.view()
                try:
                    cancelled = current["paused"] or current["runs"][run_id].get("cancel_requested")
                finally:
                    store.release_views()
                if cancelled:
                    terminate(proc); result["status"] = "interrupted"; break
                if time.monotonic() - start > timeout:
                    terminate(proc); result["status"] = "timeout"; break
                if time.monotonic() - last_heartbeat >= 10:
                    store.append("run.progress", actor, {"id": run_id})
                    last_heartbeat = time.monotonic()
                time.sleep(0.1)
            result["native_rc"] = proc.wait()
        after = delivered_current(task, store.root) if read_frozen else snapshot(task["repo"], store.root)
        result["after"] = after
        if result["status"] not in {"interrupted", "timeout"}:
            require(result["native_rc"] == expected_rc, "Native command exit differs from expected result")
            if mode != "build":
                require(after == before, "Source changed during verification/read")
            if mode == "read":
                result.update(validate_reply(agent["client"], folder))
            elif mode == "build":
                require(after["head"] == before["head"], "Builder committed despite the no-commit contract")
                result.update(validate_reply(agent["client"], folder, structured=False))
            if mode == "proof" and task.get("expected") is not None:
                text = (folder / "stdout.log").read_text(errors="replace") + (folder / "stderr.log").read_text(errors="replace")
                summaries = [line for line in text.splitlines() if re.search(r"\d+ passed", line)]
                counts = {name: int(n) for n, name in re.findall(r"(\d+) (passed|failed|skipped|xfailed|xpassed|errors?)", summaries[-1] if summaries else "")}
                unit = re.findall(r"Ran (\d+) tests? in", text)
                if unit and re.search(r"^OK(?: \(.*\))?$", text, re.M):
                    counts = {"passed": int(unit[-1])}
                require(sum(counts.values()) == task["expected"] and not counts.get("failed") and
                        not counts.get("error") and not counts.get("errors"), "Executed test counts do not match agreed collection")
                result["counts"] = counts
            if shared is not None:
                current = store.resolved()
                all_covers = [*record['covers'], *[key for entry in shared['tasks'] for key in entry['covers']]]
                final, _ = shared_record(current, task_id, also, after, argv, gate, expected_rc, all_covers)
                require(final == shared, 'Shared task contract changed during proof')
            result["status"] = "matched" if mode == "probe" else "passed"
    except KeyboardInterrupt:
        result.update(status="interrupted", error="Owner interruption")
    except Exception as exc:
        result.update(status="failed", error=str(exc))
    finally:
        if proc:
            try:
                terminate(proc)
                result["cleanup"] = "owned group disposed"
            except Exception as exc:
                result.update(status="cleanup-unknown", cleanup="not verified", error="Cleanup failed: " + str(exc))
            result["native_rc"] = proc.poll()
        if temp:
            result.update(remove_temp(temp))
        result["seconds"] = round(time.monotonic() - start, 3)
        result["ended"] = now()
        result["artifacts"] = []
        for name in ["stdout.log", "stderr.log", "reply.txt", "schema.json", "input.txt"]:
            if (folder / name).is_file():
                try:
                    result["artifacts"].append(store.artifact(folder / name))
                except (Error, OSError) as exc:
                    result.update(status="failed", error="Evidence capture failed: " + str(exc))
        atomic(terminal_receipt, canonical({"run": run_id, "status": result["status"],
                                           "native_rc": result["native_rc"], "ended": result["ended"]}).encode())
        try:
            store.append("run.end", actor, result, "run-end:" + run_id)
        except Error as exc:
            if shared is None or result['status'] != 'passed':
                raise
            result.update(status='failed', error='Shared terminal binding refused: ' + str(exc))
            atomic(terminal_receipt, canonical({"run": run_id, "status": result['status'],
                                               "native_rc": result['native_rc'], "ended": result['ended']}).encode())
            store.append("run.end", actor, result, "run-end:" + run_id)
        store.publish()
    return result
