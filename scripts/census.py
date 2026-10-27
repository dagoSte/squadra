"""List local client processes; a running process does not prove a reply."""
import os
from pathlib import Path
import subprocess
import sys

from registry import now, presence


SANDBOX_ADVICE = ("Run census from a Claude window: inside a sandbox, such as a Codex window, "
                  "the process list hides the other windows, so this census cannot see them.")


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
    # A Codex sandbox hides other processes: ps is refused, or lists only the sandbox's own.
    denied = False
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
            denied = isinstance(exc, PermissionError)
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
    if denied or os.environ.get("CODEX_SANDBOX"):
        errors.append(SANDBOX_ADVICE)
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


def unregistered(state, sessions=None, peers=()):
    """Read only session metadata, never conversation bodies; peers are an explicit snapshot."""
    import json
    from registry import git, Error
    roots = {Path(state['workspace']['project']).resolve()}
    roots.update(Path(a['repo']).resolve() for a in state['agents'].values())
    roots.update(Path(t['repo']).resolve() for t in state.get('tasks', {}).values())
    limitations = []
    try:
        for line in git(state['workspace']['project'], 'worktree', 'list', '--porcelain').decode().splitlines():
            if line.startswith('worktree '):
                roots.add(Path(line[9:]).resolve())
    except (Error, OSError):
        limitations.append('Git worktree discovery unavailable; registered roots are still checked.')
    registered = {(a['client'], a.get('session')) for a in state['agents'].values() if a.get('session')}
    found = {}
    folder = Path(sessions) if sessions is not None else Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'sessions'
    for number, path in enumerate(folder.rglob('*.jsonl')):
        if number >= 10000:
            limitations.append('Session discovery stopped at 10000 files.')
            break
        if path.is_symlink():
            continue
        try:
            with path.open('rb') as stream:
                line = stream.readline(1024 * 1024)
            record = json.loads(line)
            data = record.get('payload', {})
            session, cwd = data.get('id'), data.get('cwd')
            if record.get('type') != 'session_meta' or not isinstance(session, str) or not isinstance(cwd, str):
                continue
            if Path(cwd).resolve() in roots and ('codex', session) not in registered:
                found['codex', session] = {'client': 'codex', 'session': session, 'cwd': cwd, 'source': str(path)}
        except (OSError, ValueError, TypeError):
            limitations.append('Unreadable session metadata: ' + str(path))
    for peer in peers:
        if not isinstance(peer, dict):
            continue
        session, cwd = peer.get('session'), peer.get('cwd')
        if (isinstance(session, str) and isinstance(cwd, str) and Path(cwd).resolve() in roots
                and ('claude', session) not in registered):
            found['claude', session] = {'client': 'claude', 'session': session, 'cwd': cwd, 'source': 'supplied Claude peer list'}
    if not peers:
        limitations.append('Claude peers are available through the coordinator client; supply --peers-file to census when exported.')
    return {'candidates': sorted(found.values(), key=lambda row: (row['client'], row['session'])),
            'limitations': limitations, 'notice': 'Candidates require verification; discovery does not register or wake a window.'}
