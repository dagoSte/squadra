"""Run offline checks and save their results without contacting models or live teams."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from registry import atomic, digest, now
from runtime import terminate

TIMEOUT_SECONDS = 900


def run(output=None):
    source = Path(__file__).resolve().parents[1]
    folder = Path(output).resolve() if output else Path.home() / ".local/state/squadra/selftests" / uuid.uuid4().hex
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    argv = [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"]
    result = {"folder": str(folder), "argv": argv, "started": now(), "python": sys.version,
              "status": "failed", "native_rc": None, "timeout_seconds": TIMEOUT_SECONDS,
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
            result["native_rc"] = proc.wait(timeout=TIMEOUT_SECONDS)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            terminate(proc)
            result.update(status="interrupted", native_rc=proc.returncode)
    text = (folder / "stderr.log").read_text(errors="replace")
    count = re.findall(r"Ran (\d+) tests? in", text)
    result["tests"] = int(count[-1]) if count else 0
    if (result["status"] != "interrupted" and result["native_rc"] == 0 and result["tests"] > 0
            and re.search(r"^OK(?: \(.*\))?$", text, re.M)):
        result["status"] = "passed"
    result.update(ended=now(), seconds=round(time.monotonic() - start, 3),
                  hashes={name: digest((folder / name).read_bytes()) for name in ["stdout.log", "stderr.log"]})
    atomic(folder / "RESULT.json", (json.dumps(result, indent=2) + "\n").encode())
    return result
