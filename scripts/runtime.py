"""Run owned processes and record exit status, cancellation and source versions."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
import uuid

from registry import Error, require, snapshot, now

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
            *, checked=None):
    state = store.state()
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
    before = snapshot(task["repo"], store.root)
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
    run_id = run_id or uuid.uuid4().hex
    folder = store.root / "runs" / run_id
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    if mode in {"read", "build"}:
        require(prompt is not None, "Model run requires a prompt file")
        (folder / "schema.json").write_text(json.dumps(REVIEW_SCHEMA))
        if mode == "read":
            prompt = ("Read-only independent examination. Do not edit, publish, send messages or claim tests ran. "
                      "Return the requested structured verdict, actual coverage and limitations.\n" + prompt)
        else:
            prompt = ("Build only your scoped assignment. Do not commit, push or publish. "
                      "Report changed files, meaningful tests and any denied operation.\n" + prompt)
        argv = cli_command(agent["client"], mode, folder, agent.get("model") or None, executable)
        if agent["client"] == "codex" and mode == "read":
            config = Path(task["repo"]) / ".codex/config.toml"
            require(not config.exists(), "Review repo contains Codex config; inspect in a clean isolated copy without external MCP configuration")
    else:
        require(argv and all(isinstance(x, str) and x for x in argv), "Proof requires an argv list")
        require(mode == "probe" or not task.get("proof") or argv == task["proof"], "Proof command differs from agreed task contract")
    record = {"id": run_id, "task": task_id, "mode": mode, "argv": argv,
              "before": before, "gate": gate, "timeout": timeout, "folder": str(folder), "stage": stage,
              "expected_rc": expected_rc, "requested_model": agent.get("model"), "covers": covers or [], **route_record}
    record.update(fix_record)
    store.append("run.start", actor, record, "run-start:" + run_id)
    store.project()
    result = {"id": run_id, "status": "failed", "native_rc": None, "started": now()}
    env = os.environ.copy()
    if mode in {"proof", "probe"}:
        home = folder / "home"; home.mkdir(mode=0o700)
        tmp = folder / "tmp"; tmp.mkdir(mode=0o700)
        env.update(HOME=str(home), TMPDIR=str(tmp), PYTHONDONTWRITEBYTECODE="1")
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
            proc = subprocess.Popen(argv, cwd=task["repo"], env=env, stdout=stdout, stderr=stderr,
                                    stdin=stdin if prompt is not None else subprocess.DEVNULL, **options)
            while proc.poll() is None:
                current = store.state()
                if current["paused"] or current["runs"][run_id].get("cancel_requested"):
                    terminate(proc); result["status"] = "interrupted"; break
                if time.monotonic() - start > timeout:
                    terminate(proc); result["status"] = "timeout"; break
                if time.monotonic() - last_heartbeat >= 10:
                    store.append("run.progress", actor, {"id": run_id})
                    last_heartbeat = time.monotonic()
                time.sleep(0.1)
            result["native_rc"] = proc.wait()
        after = snapshot(task["repo"], store.root)
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
        result["seconds"] = round(time.monotonic() - start, 3)
        result["ended"] = now()
        result["artifacts"] = []
        for name in ["stdout.log", "stderr.log", "reply.txt", "schema.json", "input.txt"]:
            if (folder / name).is_file():
                try:
                    result["artifacts"].append(store.artifact(folder / name))
                except (Error, OSError) as exc:
                    result.update(status="failed", error="Evidence capture failed: " + str(exc))
        store.append("run.end", actor, result, "run-end:" + run_id)
        store.project()
    return result
