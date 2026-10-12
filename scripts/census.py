"""List local client processes; a running process does not prove a reply."""
from pathlib import Path
import subprocess
import sys

from registry import now, presence


def parse_processes(text):
    result = {}
    for line in text.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2 or not fields[0].isdigit():
            continue
        client = Path(fields[1]).name.lower()
        if client in {"codex", "claude", "cursor-agent"}:
            result[int(fields[0])] = "cursor" if client == "cursor-agent" else client
    return result


def parse_lsof(text):
    paths, pid = {}, None
    for field in text.split("\0"):
        field = field.lstrip("\n")
        if field.startswith("p"):
            pid = int(field[1:]) if field[1:].isdigit() else None
        elif field.startswith("n") and pid is not None:
            paths[pid] = field[1:]
    return paths


def collect(project, state=None, at=None):
    project = Path(project).resolve(strict=True)
    roots = {project}
    if state:
        roots.update(Path(a["repo"]).resolve() for a in state["agents"].values())
    limitations = [
        "A PID or registration does not prove that a window reads messages; only its actual ACK is an answer.",
        "Executable-name and cwd scan only: wrappers, remote clients, GUI sessions and inaccessible/exited processes may be absent.",
        "Cursor is recognised by the name cursor-agent; a session started as `agent`, or listed under its node runtime, may be missed.",
        "Cwd overlap does not establish a process-to-session identity. Use Claude peer lists and Codex session files as complementary evidence; verify session IDs before transport.",
    ]
    errors, processes, paths = [], {}, {}
    if sys.platform not in {"darwin", "linux"}:
        errors.append("Process census supports macOS and Linux only")
    else:
        try:
            p = subprocess.run(["ps", "-axo", "pid=,comm="], capture_output=True, text=True, timeout=5)
            if p.returncode:
                errors.append("ps failed (exit " + str(p.returncode) + ")")
            else:
                processes = parse_processes(p.stdout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append("ps unavailable: " + type(exc).__name__)
        if sys.platform == "linux":
            for pid in processes:
                try:
                    paths[pid] = str((Path("/proc") / str(pid) / "cwd").readlink())
                except OSError as exc:
                    errors.append("cwd unavailable for PID " + str(pid) + ": " + type(exc).__name__)
        elif processes:
            try:
                p = subprocess.run(["lsof", "-a", "-p", ",".join(map(str, processes)), "-d", "cwd", "-Fpn0"],
                                   capture_output=True, text=True, timeout=5)
                paths = parse_lsof(p.stdout)
                if p.returncode:
                    errors.append("lsof partial/failed (exit " + str(p.returncode) + ")")
            except (OSError, subprocess.TimeoutExpired) as exc:
                errors.append("lsof unavailable: " + type(exc).__name__)
            errors.extend("cwd unavailable for PID " + str(pid) for pid in processes if pid not in paths)
    found = []
    for pid, raw in paths.items():
        if pid not in processes or not Path(raw).is_absolute():
            continue
        path = Path(raw).resolve()
        if any(path == root or root in path.parents for root in roots):
            found.append({"pid": pid, "client": processes[pid], "cwd": str(path)})
    stamp = at or now()
    seats = []
    if state:
        for row in presence(state, stamp):
            agent = state["agents"][row["agent"]]
            seats.append({**row, "role": agent["role"], "client": agent["client"],
                          "repo": agent["repo"], "session": agent.get("session", "")})
    return {"project": str(project), "roots": sorted(map(str, roots)), "as_of": stamp,
            "processes": sorted(found, key=lambda p: p["pid"]), "agents": seats,
            "scan": "partial" if errors else "observed", "errors": errors, "limitations": limitations}
