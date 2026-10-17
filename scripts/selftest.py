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
from runtime import terminate

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


def run(output=None, timeout=TIMEOUT_SECONDS):
    source = Path(__file__).resolve().parents[1]
    folder = Path(output).resolve() if output else Path.home() / ".local/state/squadra/selftests" / uuid.uuid4().hex
    require(timeout > 0, "Positive --timeout required")
    require(not folder.is_relative_to(source), "Selftest output must be outside the Squadra source")
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    # The parallel runner fails a run whose executed tests differ from the collected ones.
    argv = [sys.executable, "-B", "tools/parallel_tests.py", "--timeout", str(timeout), "--output", str(folder / "parallel")]
    result = {"folder": str(folder), "argv": argv, "started": now(), "python": sys.version,
              "status": "failed", "native_rc": None, "timeout_seconds": timeout,
              "scope": "Synthetic toolkit checks; no live model or product gate"}
    env = os.environ.copy()
    for name in ["home", "tmp"]:
        (folder / name).mkdir(mode=0o700)
    env.update(HOME=str(folder / "home"), TMPDIR=str(folder / "tmp"), PYTHONDONTWRITEBYTECODE="1")
    start = time.monotonic()
    with (folder / "stdout.log").open("wb") as out, (folder / "stderr.log").open("wb") as err:
        options = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        proc = subprocess.Popen(argv, cwd=source, env=env, stdout=out, stderr=err, **options)
        try:
            result["native_rc"] = proc.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            stop(proc)
            result.update(status="interrupted", native_rc=proc.returncode)
    summary = counts(folder / "parallel")
    result.update(collected=summary.get("collected", 0), tests=summary.get("executed", 0))
    if (result["status"] != "interrupted" and result["native_rc"] == 0 and summary.get("passed") is True
            and result["tests"] > 0 and result["tests"] == result["collected"]):
        result["status"] = "passed"
    result.update(ended=now(), seconds=round(time.monotonic() - start, 3),
                  hashes={name: digest((folder / name).read_bytes()) for name in ["stdout.log", "stderr.log"]})
    atomic(folder / "RESULT.json", (json.dumps(result, indent=2) + "\n").encode())
    return result
