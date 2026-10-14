"""Run a repository's declared quick checks on the exact candidate before proof and delivery."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

from registry import Error, digest, git, now, snapshot

NAME = ".squadra-precheck.json"
DECLARATION_BYTES = 16 * 1024
CHECK_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
LOGS = ("stdout.log", "stderr.log")


def invalid(reason):
    raise Error("precheck_invalid: " + reason)


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            invalid("duplicate key " + json.dumps(key))
        value[key] = item
    return value


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def parse(body):
    """Validate declaration bytes and return the declaration."""
    if len(body) > DECLARATION_BYTES:
        invalid(NAME + " exceeds 16 KiB")
    try:
        value = json.loads(body.decode("utf-8"), object_pairs_hook=_unique)
    except (ValueError, RecursionError) as exc:
        invalid(NAME + " is not UTF-8 JSON (" + type(exc).__name__ + ")")
    if not isinstance(value, dict) or set(value) != {"schema", "total_seconds", "checks"}:
        invalid("the declaration needs exactly schema, total_seconds and checks")
    if not (type(value["schema"]) is int and value["schema"] == 1):
        invalid("schema must be 1")
    total = value["total_seconds"]
    if not _integer(total, 1, 120):
        invalid("total_seconds must be an integer from 1 to 120")
    checks = value["checks"]
    if not isinstance(checks, list) or not 1 <= len(checks) <= 32:
        invalid("checks must be a list of 1 to 32 items")
    names = set()
    for index, check in enumerate(checks, 1):
        if not isinstance(check, dict) or set(check) != {"name", "argv", "timeout_seconds"}:
            invalid("check " + str(index) + " needs exactly name, argv and timeout_seconds")
        name = check["name"]
        if not isinstance(name, str) or not CHECK_NAME.fullmatch(name):
            invalid("check " + str(index) + " has an invalid name")
        if name in names:
            invalid("duplicate check name " + name)
        names.add(name)
        argv = check["argv"]
        if not (isinstance(argv, list) and 1 <= len(argv) <= 64 and
                all(isinstance(arg, str) and arg and "\0" not in arg for arg in argv)):
            invalid(name + ": argv must be a list of 1 to 64 nonempty strings, never a shell string")
        if not _integer(check["timeout_seconds"], 1, 60) or check["timeout_seconds"] > total:
            invalid(name + ": timeout_seconds must be an integer from 1 to 60 and at most total_seconds")
    return value


def declaration(repo):
    """Return the tracked declaration bytes, or None when the candidate has none."""
    path = Path(repo) / NAME
    if not os.path.lexists(path):
        return None
    if path.is_symlink():
        invalid(NAME + " is a symlink")
    if NAME.encode() not in git(repo, "ls-files", "-z", "--", NAME).split(b"\0"):
        invalid(NAME + " is not tracked by Git (untracked or ignored)")
    try:
        return path.read_bytes()
    except OSError as exc:
        invalid(NAME + " is unreadable (" + type(exc).__name__ + ")")


def _mark(source):
    return {"head": source["head"], "sha256": source["sha256"]}


def _check(store, repo, folder, argv, wait, before):
    """Run one check in its own process group; never raises an ordinary exception."""
    from runtime import terminate
    result = {"status": "failed", "native_rc": None, "started": now()}
    start = time.monotonic()
    proc = None
    try:
        folder.mkdir(parents=True, mode=0o700, exist_ok=False)
        home = folder / "home"; home.mkdir(mode=0o700)
        tmp = folder / "tmp"; tmp.mkdir(mode=0o700)
        env = os.environ.copy()
        env.update(HOME=str(home), TMPDIR=str(tmp), PYTHONDONTWRITEBYTECODE="1")
        options = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        with (folder / "stdout.log").open("wb") as stdout, (folder / "stderr.log").open("wb") as stderr:
            try:
                proc = subprocess.Popen(argv, cwd=repo, env=env, stdout=stdout, stderr=stderr,
                                        stdin=subprocess.DEVNULL, **options)
            except OSError as exc:
                result["error"] = "unrunnable: " + type(exc).__name__ + ": " + str(exc)
            else:
                try:
                    result["native_rc"] = proc.wait(timeout=wait)
                    result["status"] = "passed" if result["native_rc"] == 0 else "failed"
                except subprocess.TimeoutExpired:
                    result.update(status="timeout", error="timed out after " + str(round(wait, 3)) + " s")
    except Exception as exc:
        result.update(status="failed", error="check could not run: " + type(exc).__name__ + ": " + str(exc))
    finally:
        if proc:
            try:
                terminate(proc)
            except Exception as exc:
                result.update(status="failed", error="cleanup not verified: " + str(exc))
            if result["native_rc"] is None:
                result["native_rc"] = proc.poll()
        result["seconds"] = round(time.monotonic() - start, 3)
        result["ended"] = now()
    try:
        after = snapshot(repo, store.root)
        result["after"] = _mark(after)
        if after != before:
            result.update(status="failed", error="source changed during the check")
            result["changed"] = True
    except Exception as exc:
        result.update(status="failed", error="snapshot failed: " + type(exc).__name__ + ": " + str(exc))
        result["changed"] = True
    result["artifacts"] = []
    for name in LOGS:
        if (folder / name).is_file():
            try:
                result["artifacts"].append(store.artifact(folder / name))
            except Exception as exc:
                result.update(status="failed", error="evidence capture failed: " + type(exc).__name__ + ": " + str(exc))
    return result


def _line(folder, result):
    for name in ("stderr.log", "stdout.log"):
        try:
            text = (folder / name).read_text(errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if line.strip():
                return line.strip()[:200]
    return str(result.get("error", ""))[:200]


def run(store, actor, task, current=None):
    """Run the declaration on the exact candidate; None without one, else (checked snapshot, summary)."""
    t0 = time.monotonic()
    repo = task["repo"]
    body = declaration(repo)
    if body is None:
        return None
    spec = parse(body)
    deadline = t0 + spec["total_seconds"]
    before = snapshot(repo, store.root)
    if current is not None and before != current:
        raise Error("precheck_failed: source changed before the checks; deliver again")
    row = next((r for r in before["files"] if r["path"] == NAME), None)
    if not row or row.get("kind") != "file" or row["sha256"] != digest(body):
        invalid(NAME + " changed while it was read")
    bound = {}
    if task.get("fix"):
        from fix_challenge import binding
        bound["fix_binding"] = binding(task)
    if task.get("contract"):
        bound["contract"] = task["contract"]
    declared = {"path": NAME, "sha256": row["sha256"]}
    runs = [{"id": uuid.uuid4().hex, "check": check} for check in spec["checks"]]
    for item in runs:
        item["folder"] = store.root / "runs" / item["id"]
    store.batch([("run.start", actor, {
        "id": item["id"], "task": task["id"], "mode": "precheck", "check": item["check"]["name"],
        "argv": item["check"]["argv"], "timeout": item["check"]["timeout_seconds"], "folder": str(item["folder"]),
        "declaration": declared, "before": _mark(before), "gate": False, "covers": [], "expected_rc": 0, **bound},
        "run-start:" + item["id"]) for item in runs])
    results = {}
    stop = None
    try:
        for item in runs:
            remaining = deadline - time.monotonic()
            if stop or remaining <= 0:
                results[item["id"]] = {"status": "interrupted", "native_rc": None, "seconds": 0, "ended": now(),
                                       "error": stop or "deadline reached before start; not started"}
                continue
            result = _check(store, repo, item["folder"], item["check"]["argv"],
                            min(item["check"]["timeout_seconds"], remaining), before)
            if result.pop("changed", False):
                stop = "not started: the source changed during an earlier check"
            results[item["id"]] = result
    except BaseException as exc:
        stop = "not started: precheck interrupted (" + type(exc).__name__ + ")"
        for item in runs:
            results.setdefault(item["id"], {"status": "interrupted", "native_rc": None, "seconds": 0,
                                            "ended": now(), "error": stop})
    finally:
        ends = [("run.end", actor, {"id": item["id"], **results.get(item["id"], {
                    "status": "interrupted", "native_rc": None, "seconds": 0, "ended": now(), "error": stop or "not started"})},
                 "run-end:" + item["id"]) for item in runs]
        try:
            store.batch(ends)
        except Exception as exc:
            raise Error("precheck_failed: check results could not be recorded (" + type(exc).__name__ +
                        "); runs left running: " + ", ".join(item["id"] for item in runs)) from exc
    seconds = round(time.monotonic() - t0, 3)
    failed = [(item, results[item["id"]]) for item in runs if results[item["id"]]["status"] != "passed"]
    if failed:
        raise Error("precheck_failed: " + "; ".join(
            item["check"]["name"] + " (" + result["status"] + ", rc " + str(result.get("native_rc")) + ", run " +
            item["id"] + ": " + _line(item["folder"], result) + ")" for item, result in failed))
    return before, {"declaration": declared["sha256"], "seconds": seconds,
                    "checks": [{"name": item["check"]["name"], "run": item["id"], "status": results[item["id"]]["status"],
                                "native_rc": results[item["id"]]["native_rc"], "seconds": results[item["id"]]["seconds"]}
                               for item in runs]}
