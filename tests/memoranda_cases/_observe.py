"""Test instrument: runs the collector under a recording audit hook.

argv: <record.json> <collector arguments...>. Every write-intent open, folder creation and SQLite connection is
recorded with its resolved path. With SQMEM_OBSERVE_READY set, the first write of a snapshot database writes that path
into the named file and waits there, so a test can interrupt the collector in the middle of its copy.
"""
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import unquote, urlparse

import fx
sys.path.insert(0, str(fx.ROOT))
WRITE = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
record, ready = sys.argv[1], os.environ.get("SQMEM_OBSERVE_READY")
events, state = [], {"paused": False}


def _path(value):
    return os.path.realpath(os.fsdecode(value)) if isinstance(value, (str, bytes, os.PathLike)) else None


def hook(name, args):
    if name == "open":
        path, mode, flags = args
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(flags, int) and flags & WRITE)
        resolved = _path(path)
        if writing and resolved:
            events.append(["write", resolved])
            if ready and not state["paused"] and resolved.endswith(os.sep + os.path.join("snapshot", "registry.sqlite3")):
                state["paused"] = True
                Path(ready).write_text(resolved)
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    time.sleep(0.05)
    elif name == "os.mkdir" and _path(args[0]):
        events.append(["mkdir", _path(args[0])])
    elif name == "sqlite3.connect":
        text = os.fsdecode(args[0]) if isinstance(args[0], (str, bytes, os.PathLike)) else str(args[0])
        events.append(["sqlite3.connect", os.path.realpath(unquote(urlparse(text).path) if text.startswith("file:") else text)])


sys.addaudithook(hook)
from squadra_memories.cli import main  # noqa: E402

code = 4
try:
    code = main(sys.argv[2:])
finally:
    Path(record).write_text(json.dumps(events))
sys.exit(code)
