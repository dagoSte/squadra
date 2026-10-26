"""Store squad records in SQLite and generate Markdown views."""
from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from collections import ChainMap
from contextlib import contextmanager, ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
import unicodedata
import weakref


VERSION = "3.11.0"
WAIT_SECONDS = {"ack": 900, "delivered": 3600, "reviewed": 1800, "heartbeat": 1800, "alive": 300,
                "to-forward": 120, "plan": 3600}
MESSAGE_WAIT_KEYS = {"to-forward", "plan"}
CHANNELS = ("typed-here", "relayed")
REFERENCE_KINDS = ("ask", "decision")
# Decisions recorded without --id are named dec-e<event sequence>; explicit IDs cannot take that form.
GENERATED_DECISION_ID = re.compile(r"^dec-e[0-9]+$")
BRIEF_BYTES = 8192
# A decision's optional "scrub" field names one artifact digest whose file may be absent.
ARTIFACT_DIGEST = re.compile(r"[0-9a-f]{64}")
SCRUB_NOTE = ("Scrubbed artifacts were removed on purpose by a coordinator decision; the record keeps their digests. "
              "A file still present may now be deleted. Published Squadra 3.10.1 fails its full audit on this "
              "registry until it updates.")

TASK_STATUSES = ("waiting-ack", "ready", "working", "delivered", "changes-required", "reviewed", "done")
# The fields a task event declares; status, authors, reads, proofs, gate reads and the rest are recorded by the
# reducer from their own events, so a declaration never presets them (M-8).
TASK_FIELDS = frozenset({"id", "title", "owner", "repo", "base", "scope", "proof", "expected", "full_gate", "depends",
                         "reviewer", "pool", "requirements", "delivery_policy", "read_at_gate", "release_gate", "route",
                         "light_policy", "contract", "fix", "supersedes", "cross_client", "same_client_reason"})
NEXT_STEPS = {
    "waiting-ack": "ACK dell'esecutore",
    "ready": "Avvio dell'esecutore",
    "working": "Prova e consegna dell'esecutore",
    "delivered": "Lettura non autore",
    "changes-required": "Correzione dell'esecutore",
    "reviewed": "Check e chiusura del coordinatore",
    "done": "Nessun passo pendente",
}
# English text views for an English workspace. Italian sentences stay the recorded values; these
# maps are keyed by step phase, wait kind and owner item kind, never by the Italian text.
STEP_TEXT_EN = {
    "waiting-ack": "Executor ACK",
    "ready": "Executor start",
    "working": "Executor proof and delivery",
    "delivered": "Non-author read",
    "changes-required": "Executor correction",
    "reviewed": "Coordinator check and close",
    "done": "No pending step",
    "gate-read": "Closed without a read: to be read at the gate",
    "plan-read": "Plan waiting for a non-author read",
    "plan-fix": "Plan correction",
    "plan-blocked": "Plan read blocked",
}
WAIT_TEXT_EN = {
    "ack": "Receipt to be confirmed",
    "pool": "Pool read: the first ACK takes it",
    "cleanup-unknown": "Stop to verify; receipt present",
    "heartbeat": "Old heartbeat; process presence not inferred",
    "alive": "Ping without a reply; stand-by needs a coordinator sweep",
}
OWNER_TEXT_EN = {
    "budget": "Budget used up: decide whether to extend the scope or stop",
    "paused": "Area paused: resume only at the owner's request",
}


FINDINGS_BYTES = 16 * 1024
SEVERITIES = ('grave', 'minor', 'info')
# SQLite's write-ahead log is cut back to this size once its frames are in the database file.
WAL_LIMIT = 64 * 1024 * 1024
# After a commit the writer never waits for readers: a held log is left for a later commit or doctor --repair.
TRIM_BUSY_MS = 0
BUSY_MS = 30000


def validate_findings(value):
    valid = isinstance(value, list) and len(value) <= 32
    if valid:
        for item in value:
            if not isinstance(item, dict) or set(item) != {'severity', 'title'}:
                valid = False
                break
            title = item['title']
            if (item['severity'] not in SEVERITIES or not isinstance(title, str)
                    or not 1 <= len(title) <= 200 or not title.strip()
                    or any(unicodedata.category(c).startswith('C') or c in '\u2028\u2029' for c in title)):
                valid = False
                break
    if not valid:
        raise ValueError('Invalid review findings')
    if len(json.dumps(value, ensure_ascii=False).encode('utf-8')) > FINDINGS_BYTES:
        raise ValueError('Review findings exceed 16 KiB')
    return value



class Error(Exception):
    pass


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def plain(value):
    if isinstance(value, Mapping):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def canonical(value):
    return json.dumps(plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, data):
    path = Path(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        temporary = Path(f.name)
        try:
            os.chmod(temporary, 0o600)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
        if os.name != "nt":
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def git(repo, *args, timeout=30):
    try:
        p = subprocess.run(["git", *args], cwd=repo, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Error("git " + str(args[0]) + " timed out after " + str(timeout) + " s") from None
    if p.returncode:
        raise Error(p.stderr.decode(errors="replace").strip())
    return p.stdout


def git_probe(*args, timeout=30):
    """Run git for its exit status; a hung git becomes a plain refusal."""
    try:
        return subprocess.run(["git", *args], capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Error("git timed out after " + str(timeout) + " s") from None


def snapshot(repo, exclude=None):
    repo = Path(repo).resolve()
    head = git(repo, "rev-parse", "HEAD").decode().strip()
    tracked = git(repo, "ls-files", "-z").split(b"\0")
    untracked = git(repo, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
    rows = []
    for raw in sorted(set(tracked + untracked) - {b""}):
        rel = os.fsdecode(raw)
        path = repo / rel
        if raw not in tracked and exclude and Path(exclude).resolve() in path.resolve().parents:
            continue
        if path.is_symlink():
            body = os.fsencode(os.readlink(path))
            row = {"path": rel, "kind": "symlink", "sha256": digest(body)}
        elif path.is_file():
            h = hashlib.sha256()
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            row = {"path": rel, "kind": "file", "sha256": h.hexdigest(),
                   "executable": bool(path.stat().st_mode & 0o111)}
        elif not path.exists():
            row = {"path": rel, "kind": "deleted"}
        else:
            raise Error("Submodule/directory requires explicit verification: " + rel)
        rows.append(row)
    result = {"repo": str(repo), "head": head, "files": rows}
    result["sha256"] = digest(canonical(result).encode())
    return result


def initial():
    return {"workspace": {}, "agents": {}, "tasks": {}, "messages": {},
            "runs": {}, "resources": {}, "outputs": [], "legacy": {},
            "decisions": [], "asks": {}, "saves": [], "restores": [], "challenges": [], "benchmarks": [],
            "paused": False, "event_count": 0, "refs": {"ask": {}, "decision": {}}}


def wait_facts(events):
    """Transient facts: keep the shared reducer and its schema-5 cache unchanged."""
    facts = {"messages": {}, "tasks": {}, "settings": {}}
    for event in events:
        data = event["data"]
        if event["kind"] == "transport":
            message = facts["messages"].setdefault(data["message"], {})
            message.setdefault("transport_first_utc", event["utc"])
            message["transport_all_failed"] = message.get("transport_all_failed", True) and data.get("delivered") is not True
        elif event["kind"] == "plan":
            facts["tasks"][data["task"]] = {"plan_utc": event["utc"]}
        elif event["kind"] == "settings":
            facts["settings"] = data.get("message_wait_seconds", {})
    return facts


class ReadEntities(Mapping):
    def __init__(self, values, resolver):
        self.values_source, self.resolver, self.memo = values, resolver, {}

    def __getitem__(self, key):
        if key not in self.memo:
            self.memo[key] = self.resolver(key, self.values_source[key])
        return self.memo[key]

    def __iter__(self):
        return iter(self.values_source)

    def __len__(self):
        return len(self.values_source)


class ReadState(dict):
    """Canonical fields with transient read context that is never serialized."""


def wait_view(source, facts):
    class View(dict):
        pass
    result = View(source.items())
    result.current = current_index(source)
    for kind in ("messages", "tasks"):
        additions = facts[kind]
        result[kind] = ReadEntities(source[kind], lambda name, row, additions=additions:
                                   ChainMap(dict(additions.get(name, {})), row))
    if facts["settings"]:
        result["workspace"] = {**source["workspace"], "message_wait_seconds": facts["settings"]}
    return result


def wait_settings(value):
    """Older writers/readers understand only the original wait_seconds keys."""
    require(isinstance(value, dict) and set(value) <= set(WAIT_SECONDS), "Unknown wait threshold")
    return {"wait_seconds": {k: v for k, v in value.items() if k not in MESSAGE_WAIT_KEYS},
            "message_wait_seconds": {k: v for k, v in value.items() if k in MESSAGE_WAIT_KEYS}}


def decision_id(data, seq):
    """Use the supplied decision ID or a stable name from its event number."""
    return data.get("id") or (None if seq is None else "dec-e" + str(seq))


def read_at_gate(task):
    """A light task registered to be read at a release gate instead of on its own."""
    return task.get("read_at_gate") is True and task.get("route") == "light"


def format3_started(state):
    """Whether the state shows journal format 3 begun: a frozen-delivery task and a reassignment are always written in it."""
    return any(t.get("delivery_policy") == "frozen-v1" or t.get("reassignments") for t in state["tasks"].values())


def gate_pending(task):
    """A read-at-gate task closed without a read and not yet covered by a release gate."""
    return read_at_gate(task) and task["status"] == "done" and not task.get("gate_read")


def gate_deferred(task, gate):
    return any(x.get("gate") == gate for x in task.get("gate_deferrals", []))


def pool_claim(state, task):
    """The pool member whose ACK took the current delivery packet's read, or None."""
    for handoff in task.get("delivery", {}).get("handoffs") or []:
        message = state["messages"].get(handoff)
        if message and message["ack"]:
            return {"agent": message["ack"]["agent"], "handoff": handoff, "utc": message["ack"]["utc"]}
    return None


def active_pool(state, task):
    """Pool members not in stand-by, in pool order: the members a new delivery hands the read to."""
    return [member for member in task["pool"] if not standby(state, member)]


def pool_readers(task):
    """Pool members that hold a handoff of the current delivery, in pool order.

    A member in stand-by when the packet was delivered has no handoff, so it is not a reader of it.
    """
    handoffs = task.get("delivery", {}).get("handoffs") or []
    size = len("review-") + 64 + 1
    if not handoffs or any(not h.startswith("review-") or len(h) <= size for h in handoffs):
        return list(task["pool"])
    prefix = handoffs[0][:size]
    return [member for member in task["pool"] if prefix + member in handoffs]


def last_code_read(task):
    reads = [r for r in task["reviews"] if r["stage"] == "code"]
    return reads[-1] if reads else {}


def apply(state, event):
    d, kind = event["data"], event["kind"]
    refs = state.setdefault("refs", {"ask": {}, "decision": {}})
    if kind == "init":
        state["workspace"] = dict(d)
    elif kind == "agent":
        previous = next((a.get("availability") for a in state["agents"].values()
                         if a["identity"] == d["identity"] and a.get("availability")), None)
        state["agents"][d["id"]] = {**d, "updated": event["utc"]}
        if previous:
            state["agents"][d["id"]]["availability"] = previous
    elif kind == "availability":
        identity = state["agents"][d["agent"]]["identity"]
        for agent in state["agents"].values():
            if agent["identity"] == identity:
                agent["availability"] = {**d, "utc": event["utc"], "event": event["id"]}
    elif kind == "task":
        state["tasks"][d["id"]] = {**d, "status": "waiting-ack", "authors": [d["owner"]],
                                  "status_utc": event["utc"],
                                  "coordinator": event["actor"],
                                  "reviews": [], "proofs": [], "rounds": {"plan": 0, "code": 0}}
        if d.get("supersedes"):
            # Keep the replaced task and its history, but remove its next steps and waits.
            state["tasks"][d["supersedes"]].update(superseded_by=d["id"], superseded_utc=event["utc"])
    elif kind == "route":
        task = state["tasks"][d["task"]]
        task.setdefault("route_history", []).append({**d, "utc": event["utc"]})
        task.update({k: d[k] for k in ("route", "contract", "scope", "proof", "expected", "full_gate")})
        for key in ("candidate", "delivery", "plan", "claim"):
            task.pop(key, None)
        task.update(status="working", status_utc=event["utc"])
    elif kind == "fix":
        task = state["tasks"][d["task"]]
        task["fix"] = d["fix"]
        if "contract" in d:
            task["contract"] = d["contract"]
        task.setdefault("fix_history", []).append({**d, "actor": event["actor"], "utc": event["utc"]})
        for key in ("candidate", "delivery", "claim"):
            task.pop(key, None)
        if task["status"] != "waiting-ack":
            task.update(status="working", status_utc=event["utc"])
    elif kind == "supersede":
        # Record replacement after the new task has already been created.
        state["tasks"][d["task"]].update(superseded_by=d["by"], superseded_utc=event["utc"])
    elif kind == "reassign":
        task = state['tasks'][d['task']]
        for handoff in [d.get('previous'), *d.get('previous_pool', [])]:
            if handoff in state['messages']:
                state['messages'][handoff]['replaced_by'] = d['handoff']
        task.setdefault('reassignments', []).append({**d, 'utc': event['utc'], 'event': event['id']})
        task.update(reviewer=d['reviewer'], pool=None, status='delivered', status_utc=event['utc'])
        if task.get('cross_client'):
            task['same_client_reason'] = d.get('same_client_reason')
        task['delivery'] = {**task['delivery'], 'handoff': d['handoff'],
                            'packet': d['packet'], 'packet_data': d['packet_data']}
        task['delivery'].pop('handoffs', None)
        task.pop('claim', None)
    elif kind == "brief":
        record = {"text": d["text"], "artifact": d["artifact"], "actor": event["actor"], "utc": event["utc"]}
        if d.get("project"):
            state["workspace"]["brief"] = record
        else:
            state["tasks"][d["task"]]["brief"] = record
    elif kind == "ack":
        state["messages"][d["message"]]["ack"] = {"agent": event["actor"], "utc": event["utc"]}
        task = state["messages"][d["message"]].get("task")
        if task:
            state["tasks"][task]["status"] = "ready"
            state["tasks"][task]["status_utc"] = event["utc"]
        related = state["tasks"].get(state["messages"][d["message"]].get("related_task"))
        if state["messages"][d["message"]].get("pool") and related and pool_claim(state, related):
            related["claim"] = pool_claim(state, related)
    elif kind == "message":
        state["messages"][d["id"]] = {**d, "from": event["actor"], "utc": event["utc"], "ack": None}
    elif kind == "transport":
        message = state["messages"][d["message"]]
        message["transport"] = d
    elif kind == "plan":
        state["tasks"][d["task"]]["plan"] = d["artifact"]
    elif kind == "start":
        state["tasks"][d["task"]]["status"] = "working"
        state["tasks"][d["task"]]["status_utc"] = event["utc"]
    elif kind == "deliver":
        task = state["tasks"][d["task"]]
        task.update(status="delivered", candidate=d["snapshot"], delivery=d,
                    status_utc=event["utc"], delivered_utc=event["utc"])
        # A claim belongs to the packet: an identical redelivery keeps it, a new packet starts unclaimed.
        claim = pool_claim(state, task)
        if claim:
            task["claim"] = claim
        else:
            task.pop("claim", None)
    elif kind == "review":
        task = state["tasks"][d["task"]]
        task["reviews"].append({**d, "reviewer": event["actor"], "utc": event["utc"]})
        task["rounds"][d["stage"]] += 1
        if d["stage"] == "code":
            task["status"] = "reviewed" if d["verdict"] == "accepted" else "changes-required"
            task["status_utc"] = event["utc"]
    elif kind == "close":
        task = state["tasks"][d["task"]]
        task["status"] = "done"
        task["status_utc"] = event["utc"]
        if d.get('note'):
            task['close_note'] = d['note']
        if task.get("release_gate") is True:
            read = last_code_read(task)
            for other in state["tasks"].values():
                if (gate_pending(other) and not gate_deferred(other, task["id"])
                        and other["id"] in read.get("gate_covers", [])):
                    other["gate_read"] = {"gate": task["id"], "reviewer": read["reviewer"], "utc": event["utc"]}
    elif kind == "output":
        state["outputs"].append({**d, "agent": event["actor"], "utc": event["utc"], "event": event["id"]})
    elif kind == "run.start":
        state["runs"][d["id"]] = {**d, "agent": event["actor"], "utc": event["utc"], "status": "running"}
    elif kind == "run.end":
        run = state["runs"][d["id"]]
        run.update(d)
        if run["mode"] == "proof":
            state["tasks"][run["task"]]["proofs"].append(run.copy())
    elif kind == "run.abandon":
        run = state["runs"][d["id"]]
        run.update(status="abandoned", native_rc=None, ended=event["utc"], abandonment=d)
    elif kind == "run.cancel":
        state["runs"][d["id"]]["cancel_requested"] = True
    elif kind == "run.progress":
        state["runs"][d["id"]]["heartbeat"] = event["utc"]
    elif kind == "resource.acquire":
        state["resources"][d["name"]] = {**d, "owner": event["actor"], "utc": event["utc"]}
    elif kind == "resource.release":
        state["resources"].pop(d["name"], None)
    elif kind in {"pause", "restore.freeze"}:
        state["paused"] = True
    elif kind == "resume":
        state["paused"] = False
    elif kind == "legacy":
        state["legacy"][d["path"]] = {**d, "utc": event["utc"], "trust": "reported-unverified"}
    elif kind == "ask":
        state["asks"][d["id"]] = {**d, "actor": event["actor"], "utc": event["utc"], "status": "open"}
        refs["ask"][d["id"]] = event.get("seq")
    elif kind == "decision":
        state["decisions"].append({**d, "actor": event["actor"], "utc": event["utc"]})
        name = decision_id(d, event.get("seq"))
        if name:
            refs["decision"][name] = event.get("seq")
        target = d.get("answers") or d.get("withdraws")
        if target:
            state["asks"][target].update(status="answered" if d.get("answers") else "withdrawn",
                                         resolution=state["decisions"][-1])
        if d.get("defer_read"):
            state["tasks"][d["defer_read"]["task"]].setdefault("gate_deferrals", []).append(
                {"gate": d["defer_read"]["gate"], "reason": d["reason"], "actor": event["actor"], "utc": event["utc"]})
    elif kind == "settings":
        state["workspace"]["wait_seconds"] = d["wait_seconds"]
    elif kind in {"save", "restore.check", "challenge", "benchmark"}:
        state[{"save": "saves", "restore.check": "restores", "challenge": "challenges", "benchmark":"benchmarks"}[kind]].append(
            {**d, "actor": event["actor"], "utc": event["utc"]})
        if kind == "challenge" and d.get("task"):
            state["tasks"][d["task"]]["fix_result"] = dict(d)
    elif kind == "lesson":
        state.setdefault("lessons", {})[d["id"]] = {**d, "author": event["actor"], "utc": event["utc"],
                                                   "seq": event.get("seq"), "retired": None, "recalls": [],
                                                   "recalled_tasks": [], "ineffective": []}
    elif kind == "lesson.retire":
        state["lessons"][d["lesson"]]["retired"] = {"reason": d["reason"], "actor": event["actor"],
                                                    "utc": event["utc"], "seq": event.get("seq")}
    elif kind == "lesson.recall":
        state.setdefault("lesson_recalls", []).append({**d, "actor": event["actor"], "utc": event["utc"],
                                                       "seq": event.get("seq")})
        for name in d["lessons"]:
            lesson = state["lessons"][name]
            lesson["recalls"].append(event.get("seq"))
            if d["task"] not in lesson["recalled_tasks"]:
                lesson["recalled_tasks"].append(d["task"])
    elif kind == "lesson.ineffective":
        state["lessons"][d["lesson"]]["ineffective"].append({"recall": d["recall"], "reason": d["reason"],
                                                             "actor": event["actor"], "utc": event["utc"],
                                                             "seq": event.get("seq")})
    state["event_count"] += 1


def require(ok, message):
    if not ok:
        raise Error(message)


def standby(state, agent):
    return state["agents"][agent].get("availability", {}).get("status") == "stand-by"


def current_index(state):
    return getattr(state, "current", None)


def pending_messages(state, recipient=None):
    index = current_index(state)
    if index is not None:
        from registry_current import pending_rows
        return pending_rows(index, state, recipient)
    return (m for m in state["messages"].values() if not m["ack"] and m.get("info") is not True and not m.get("replaced_by") and
            (recipient is None or m["to"] == recipient))


def information_count(state):
    index = current_index(state)
    return index["information_count"] if index is not None else sum(
        message.get("info") is True for message in state["messages"].values())


def live_runs(state, status=None):
    index = current_index(state)
    if index is not None:
        from registry_current import live_rows
        return live_rows(index, state, status)
    return (r for r in state["runs"].values() if r["status"] in
            ({status} if status else {"running", "cleanup-unknown"}))


def run_count(state, task, mode, stage=None):
    index = current_index(state)
    if index is not None:
        from registry_current import run_count as count
        return count(index, task, mode, stage)
    return sum(r["task"] == task and r["mode"] == mode and
               (stage is None or r.get("stage", "code") == stage) for r in state["runs"].values())


def last_run(state, task, mode):
    index = current_index(state)
    if index is not None:
        from registry_current import latest_run
        return latest_run(index, state, task, mode)
    return next((r for r in reversed(list(state["runs"].values())) if r["task"] == task and r["mode"] == mode), None)


def latest_ping(state, agent):
    index = current_index(state)
    if index is not None:
        from registry_current import latest_ping as lookup
        return lookup(index, state, agent)
    identity = state["agents"][agent]["identity"]
    return next((m for m in reversed(list(state["messages"].values()))
                 if m.get("ping") and state["agents"][m["to"]]["identity"] == identity), None)


def transport_delivered(state, message):
    """True once any recorded transport of the message reported delivered:true; read-time facts only."""
    facts = getattr(state, 'wait_context', {}).get('messages', {})
    return facts.get(message["id"], message).get("transport_all_failed") is False


def presence(state, at, settings=None):
    """Read-time observations; neither missing replies nor elapsed time change availability.

    A ping is due only once a transport reported delivered:true: a window cannot answer a
    challenge that never reached it, so an undelivered ping never qualifies a sweep.
    """
    clock = dt.datetime.fromisoformat(at) if isinstance(at, str) else at
    require(clock.tzinfo is not None, "Timezone-aware clock required")
    threshold = (WAIT_SECONDS | state["workspace"].get("wait_seconds", {}) | (settings or {}))["alive"]
    require(type(threshold) is int and threshold >= 0, "Nonnegative integer alive seconds required")
    result = []
    for agent in state["agents"].values():
        index = current_index(state)
        if index is not None:
            from registry_current import last_answer
            answer = last_answer(index, state, agent["id"])
        else:
            answer = max((m["ack"]["utc"] for m in state["messages"].values()
                          if m["ack"] and state["agents"][m["to"]]["identity"] == agent["identity"]), default=None)
        ping = latest_ping(state, agent["id"])
        row = {"agent": agent["id"], "identity": agent["identity"],
               "availability": "stand-by" if standby(state, agent["id"]) else "active",
               "last_answer": answer, "threshold": threshold,
               "as_of": clock.isoformat(), "ping": None, "due": False}
        if ping:
            seconds = max(0, int((clock - dt.datetime.fromisoformat(ping["utc"])).total_seconds()))
            current = agent.get("availability", {})
            delivered = transport_delivered(state, ping)
            row.update(ping=ping["id"], seconds=seconds, answer=ping["ack"], delivered=delivered,
                       due=delivered and not ping["ack"] and seconds >= threshold and
                       (standby(state, agent["id"]) or ping.get("availability_event") == current.get("event")))
        result.append(row)
    return result


def coordinator_ping(state, message, actor):
    """Any registered seat may record a handoff of a ping to a coordinator; its fixed body is only relayed."""
    return (message.get("ping") is True and actor in state["agents"] and
            state["agents"][message["to"]]["role"] == "coordinator")


def return_ping(state, agent):
    """Only a challenge issued after the recorded stand-by can qualify a return."""
    current = state["agents"][agent].get("availability", {})
    ping = latest_ping(state, agent)
    return ping if (current.get("status") == "stand-by" and ping and ping["ack"]
                    and ping.get("availability_event") == current["event"]) else None


def current_plan_review(task):
    review = next((r for r in reversed(task["reviews"]) if r["stage"] == "plan"), None)
    return review if review and review["binding"] == task.get("plan", {}).get("sha256") else None


def next_step(task):
    """Find who holds a task's next step for both the board and attention view."""
    status = task["status"]
    phase, text = status, NEXT_STEPS[status]
    role = "coordinator" if status == "reviewed" else "reviewer" if status == "delivered" else "builder"
    who = task.get("coordinator") if status == "reviewed" else task.get("reviewer") if status == "delivered" else task["owner"]
    if status == "delivered" and task.get("pool"):
        who = [task["claim"]["agent"]] if task.get("claim") else pool_readers(task)
    if status == "done":
        who = None
        if gate_pending(task):
            phase, text = "gate-read", "Chiuso senza lettura: da leggere al gate"
            deferral = (task.get("gate_deferrals") or [None])[-1]
            if deferral:
                text += " · rinviato da " + deferral["gate"] + " (" + deferral["actor"] + "): " + deferral["reason"]
    elif task.get("plan"):
        review = current_plan_review(task)
        if not review:
            phase, text, who = "plan-read", "Piano in attesa di lettura non autore", task.get("reviewer") or task.get("pool")
            role = "reviewer"
        elif review["verdict"] != "accepted":
            phase = "plan-fix" if review["verdict"] == "changes-required" else "plan-blocked"
            text = "Correzione del piano" if phase == "plan-fix" else "Lettura del piano bloccata"
            role, who = "builder", task["owner"]
    who = (list(who) if isinstance(who, list) else [who]) if who else []
    return {"task": task["id"], "status": status, "phase": phase, "role": role, "text": text, "who": who}


def attention(state):
    """List recorded decisions and waits without inferring intent from prose."""
    open_asks = [a for a in state.get("asks", {}).values() if a["status"] == "open"]
    owners = [{"kind": "ask", **a} for a in open_asks]
    waiting, steps = [], []
    superseded = {t["id"]: t for t in state["tasks"].values() if t.get("superseded_by")}
    inactive = set(superseded) | {t["id"] for t in state["tasks"].values() if t["status"] == "done"}
    coordinators = [a["id"] for a in state["agents"].values() if a["role"] == "coordinator"]
    facts = getattr(state, 'wait_context', {})
    for message in pending_messages(state):
        linked = message.get("task") or message.get("related_task")
        if linked in inactive:
            continue
        task = state["tasks"].get(linked, {})
        if message.get("pool") and (task.get("claim") or message["id"] not in task.get("delivery", {}).get("handoffs", [])):
            continue
        # Pool handoffs wait once per delivery, below; a claimed pool's siblings stay unacknowledged.
        if not message.get("pool"):
            waiting.append({"kind": "ack", "id": message["id"], "who": [message["to"]],
                            "utc": message["utc"], "text": "Presa in carico da confermare"})
        metadata = facts.get('messages', {}).get(message['id'], message)
        # A Codex window has no follower of its own: until a transport reports delivered:true, with
        # failed, unconfirmed or no attempts at all, its mail waits on the coordinator. Every other
        # window follows its own mail, so a transport cannot end or advance its wait.
        recipient = state["agents"].get(message["to"], {})
        if recipient.get("client") == "codex" and metadata.get("transport_all_failed") is not False:
            item = {"kind": "to-forward", "id": message["id"], "to": message["to"], "who": coordinators,
                    "utc": metadata.get("transport_first_utc") or message["utc"],
                    "text": "Da inoltrare a " + message["to"] + " · inoltra: " + ", ".join(coordinators),
                    "text_en": "To forward to " + message["to"] + " · forwarder: " + ", ".join(coordinators)}
            if linked:
                item["task"] = linked
            if state.get("root"):
                item["ack_command"] = shlex.join(["squadra", "ack", "--actor", message["to"],
                                                 "--message", message["id"], "--root", state["root"]])
            waiting.append(item)
    for task in state["tasks"].values():
        if task["id"] in superseded:
            continue
        status = task["status"]
        step = next_step(task)
        steps.append(step)
        if status == "done":
            continue
        common = {"task": task["id"], "utc": task.get("status_utc")}
        author_ids = {state["agents"][a]["identity"] for a in task["authors"]}
        readers = [a["id"] for a in state["agents"].values()
                   if a["role"] in {"reviewer", "coordinator"} and a["identity"] not in author_ids
                   and not standby(state, a["id"])]
        if task.get("pool"):
            readers = [task["claim"]["agent"]] if task.get("claim") else pool_readers(task)
        handoffs = task.get("delivery", {}).get("handoffs")
        if handoffs and not task.get("claim"):
            waiting.append({**common, "kind": "ack", "id": handoffs[0], "handoffs": list(handoffs),
                            "who": pool_readers(task), "utc": task.get("delivered_utc"),
                            "text": "Lettura in pool: il primo ACK la prende"})
        if status in {"ready", "delivered", "changes-required", "reviewed"}:
            waiting.append({**common, "kind": status, "text": NEXT_STEPS[status],
                            "who": readers if status == "delivered" else [task.get("coordinator")]
                            if status == "reviewed" else [task["owner"]],
                            "rounds": task["rounds"], "limits": state["workspace"]["limits"]})
        dependencies = [d for d in task.get("depends", []) if state["tasks"][d]["status"] != "done"]
        if dependencies:
            waiting.append({**common, "kind": "dependencies", "who": [], "depends": dependencies,
                            "text": "Dipendenze ancora aperte"})
        plan_review = current_plan_review(task)
        if task.get("plan") and not (plan_review and plan_review["verdict"] == "accepted"):
            metadata = facts.get('tasks', {}).get(task['id'], task)
            waiting.append({**common, "kind": "plan", "who": readers, "utc": metadata.get("plan_utc"),
                            "text": "Piano in attesa di lettura non autore", "text_en": "Plan waiting for a non-author read"})
        exhausted = []
        for stage in ("plan", "code"):
            reads = [r for r in task["reviews"] if r["stage"] == stage]
            if task["rounds"][stage] >= state["workspace"]["limits"][stage] and reads and reads[-1]["verdict"] != "accepted":
                exhausted.append(stage)
        builds = run_count(state, task["id"], "build")
        last_build = last_run(state, task["id"], "build")
        code_reviews = [r for r in task["reviews"] if r["stage"] == "code"]
        accepted_code = (status == "reviewed" and code_reviews and code_reviews[-1]["verdict"] == "accepted"
                         and code_reviews[-1]["binding"] == task.get("candidate", {}).get("sha256"))
        if not accepted_code and builds >= state["workspace"]["limits"]["build"] and last_build["status"] not in {"running", "passed"}:
            exhausted.append("build")
        if exhausted and not any(a.get("task") == task["id"] for a in open_asks):
            owners.append({**common, "kind": "budget", "stages": exhausted,
                           "question": "Budget esaurito: decidere se estendere il perimetro o fermare"})
    for run in live_runs(state):
        if run["status"] in {"running", "cleanup-unknown"}:
            waiting.append({"kind": run["status"], "id": run["id"], "task": run["task"],
                            "who": [run["agent"]], "utc": run.get("ended", run["utc"]),
                            "text": "Stop da verificare; ricevuta presente" if run["status"] == "cleanup-unknown"
                            else "Esito dell'esecuzione non ancora registrato"})
    if state["paused"]:
        owners.append({"kind": "paused", "question": "Area in pausa: ripresa solo su richiesta del proprietario"})
    decisions = []
    for decision in state["decisions"][-20:]:
        target = decision.get("answers") or decision.get("withdraws")
        decisions.append({**decision, "ask": state.get("asks", {}).get(target),
                          "legacy": not bool(target) and not decision.get("channel")})
    replaced = [{"task": t["id"], "by": t["superseded_by"], "utc": t.get("superseded_utc"), "status": t["status"]}
                for t in superseded.values()]
    waiting.sort(key=lambda w: w["kind"] != "to-forward")
    return {"for_owner": owners, "decided": decisions, "waiting": waiting, "next_steps": steps,
            "superseded": replaced}


def waits(state, at, settings=None, *, attention_value=None, fold=True):
    """Calculate ages on a caller-supplied UTC clock; alerts never perform actions.

    Rows waiting on a window in stand-by come back as one 'stand-by' row per window; fold=False keeps them
    apart, each with its own threshold."""
    supplied = (state["workspace"].get("wait_seconds", {}) | state["workspace"].get("message_wait_seconds", {})
                | getattr(state, 'wait_context', {}).get('settings', {}) | (settings or {}))
    thresholds = WAIT_SECONDS | {k: v for k, v in supplied.items() if k in WAIT_SECONDS}
    require(all(type(v) is int and v >= 0 for v in thresholds.values()),
            "Nonnegative wait thresholds required")
    clock = dt.datetime.fromisoformat(at) if isinstance(at, str) else at
    require(clock.tzinfo is not None, "Timezone-aware clock required")
    result = []
    view = attention(state) if attention_value is None else attention_value
    items = [w for w in view["waiting"] if w["kind"] in {"ack", "delivered", "reviewed", "cleanup-unknown", "to-forward"}
             or w["kind"] == "plan" and current_plan_review(state["tasks"][w["task"]]) is None]
    items += [{"kind": "heartbeat", "id": r["id"], "task": r["task"], "who": [r["agent"]],
               "utc": r.get("heartbeat", r["utc"]), "text": "Heartbeat vecchio; presenza del processo non dedotta"}
              for r in live_runs(state, "running")]
    aged = []
    for item in items:
        if not item.get("utc"):
            continue
        stamp = dt.datetime.fromisoformat(item["utc"])
        require(stamp.tzinfo is not None, "Timezone-aware event required")
        seconds = max(0, int((clock - stamp).total_seconds()))
        threshold = thresholds.get(item["kind"], 0)
        # Mail to a window in stand-by is counted at any age; a run only once its heartbeat is old.
        if seconds >= threshold or item["kind"] != "heartbeat" and held_window(state, item):
            aged.append({**item, "seconds": seconds, "threshold": threshold, "as_of": clock.isoformat()})
    for item in fold_standby(state, aged) if fold else aged:
        if item["kind"] == "stand-by":
            item = {**item, "seconds": max(0, int((clock - dt.datetime.fromisoformat(item["utc"])).total_seconds())),
                    "threshold": 0, "as_of": clock.isoformat()}
        result.append(item)
    for row in presence(state, clock, thresholds):
        if row["due"] and row["availability"] == "active" and row["agent"] == state["messages"][row["ping"]]["to"]:
            result.append({"kind": "alive", "id": row["ping"], "who": [row["agent"]],
                           "seconds": row["seconds"], "threshold": thresholds["alive"],
                           "as_of": clock.isoformat(), "text": "Ping senza risposta; stand-by richiede sweep del coordinatore"})
    return sorted(result, key=lambda w: w["kind"] != "to-forward")


def pending_ack_ids(state, *, attention_value=None):
    """The attention ACK rows are the operational definition in every consumer."""
    view = attention(state) if attention_value is None else attention_value
    return [name for row in view["waiting"] if row["kind"] == "ack"
            for name in row.get("handoffs", [row["id"]])]


def fold_forward_waits(rows):
    """Compact presentation: an ACK row folds into its original message's forwarding row."""
    forwarded = {w['id'] for w in rows if w['kind'] == 'to-forward'}
    return [w for w in rows if not (w['kind'] == 'ack' and
            (w['id'] in forwarded or forwarded.intersection(w.get('handoffs', []))))]


def held_window(state, row):
    """The window in stand-by a wait row waits on: the recipient of mail without ACK or forwarding, or the owner
    of a run with an old heartbeat. Pool handoffs and task steps wait on no single window."""
    if row['kind'] == 'to-forward':
        window = row.get('to')
    elif row['kind'] in {'ack', 'heartbeat'} and not row.get('handoffs') and len(row.get('who', [])) == 1:
        window = row['who'][0]
    else:
        return None
    return window if window in state['agents'] and standby(state, window) else None


def standby_text(window, messages, runs, reason, en):
    if en:
        head = (f"{messages} message{'s' * (messages != 1)} to {window}" if messages
                else f"{runs} run{'s' * (runs != 1)} of {window}")
        text = head + ' on stand-by' + (' (' + reason + ')' if reason else '')
        return text + (f" · {runs} run{'s' * (runs != 1)} of {window}, state uncertain" if messages and runs
                       else ', state uncertain' if runs else '')
    head = (f"{messages} {'messaggio' if messages == 1 else 'messaggi'} a {window}" if messages
            else f"{runs} {'esecuzione' if runs == 1 else 'esecuzioni'} di {window}")
    text = head + ' in stand-by' + (' (' + reason + ')' if reason else '')
    return text + (f" · {runs} {'esecuzione' if runs == 1 else 'esecuzioni'} di {window}, stato incerto" if messages and runs
                   else ', stato incerto' if runs else '')


def fold_standby(state, rows):
    """Rows waiting on a window in stand-by leave the list: one counted row per window, with the recorded reason.

    Read-time only. The messages stay without ACK and the runs uncertain: an ACK still closes a message, and a
    recorded return (standby --resume or a sweep) brings the rows back."""
    kept, held = [], {}
    for row in rows:
        window = held_window(state, row)
        if window is None:
            kept.append(row)
            continue
        group = held.setdefault(window, {'messages': [], 'runs': [], 'utc': None})
        names = group['runs' if row['kind'] == 'heartbeat' else 'messages']
        if row['id'] not in names:
            names.append(row['id'])
        if row.get('utc') and (group['utc'] is None or
                               dt.datetime.fromisoformat(row['utc']) < dt.datetime.fromisoformat(group['utc'])):
            group['utc'] = row['utc']
    for window, group in held.items():
        availability = state['agents'][window]['availability']
        counts, reason = (len(group['messages']), len(group['runs'])), availability.get('reason', '')
        kept.append({'kind': 'stand-by', 'id': window, 'who': [window], **group, 'count': sum(counts),
                     'reason': reason, 'since': availability.get('utc'),
                     'text': standby_text(window, *counts, reason, False),
                     'text_en': standby_text(window, *counts, reason, True)})
    return kept


def fold_recorded(state, rows, timed):
    """fold_standby over the recorded waits, also counting the old runs the timed waits fold for each window."""
    runs = [{'kind': 'heartbeat', 'id': run, 'who': [w['id']], 'utc': w['utc']}
            for w in timed if w['kind'] == 'stand-by' for run in w['runs']]
    return fold_standby(state, list(rows) + runs)


def standby_lines(rows, lang='it'):
    rows = [w for w in rows if w['kind'] == 'stand-by']
    if not rows:
        return []
    en = lang == 'en'
    return ['', 'On stand-by, not counted among the waits (an ACK still closes a message; standby --resume '
                'brings the waits back):' if en else
                'In stand-by, fuori dalle attese (un ACK chiude comunque un messaggio; standby --resume '
                'riporta le attese):'] + ['- ' + w['text_en' if en else 'text'] for w in rows]


def forwarding_lines(waiting, lang='it'):
    """Forwarding owed by the coordinator, then the windows in stand-by; callers pass fold_standby rows."""
    rows = [w for w in waiting if w['kind'] == 'to-forward']
    lines = ['', 'To forward:' if lang == 'en' else 'Da inoltrare:'] + [
        '- ' + w['id'] + ' · ' + w['utc'] + ' · ' + w.get('text_en' if lang == 'en' else 'text', w['text'])
        + (' · ' + w['ack_command'] if w.get('ack_command') else '') for w in rows] if rows else []
    return lines + standby_lines(waiting, lang)


def fix_binding(task):
    return digest(canonical({k: task.get(k) for k in
                             ('id', 'base', 'scope', 'proof', 'contract', 'fix')}).encode())


def fix_disposition(task):
    return digest(canonical(task.get('fix_result')).encode()) if task.get('fix_result') else None


def fix_matched(agreement, runs):
    if not isinstance(runs, list) or not all(isinstance(r, dict) for r in runs) or [r.get('label') for r in runs] != ['original', 'remedy']:
        return False
    for r, outcome, rc in zip(runs, ('assertion', 'passed'), (1, 0)):
        if (r.get('status') != 'completed' or r.get('outcome') != outcome or
                type(r.get('native_rc')) is not int or r['native_rc'] != rc):
            return False
        if agreement['runner'] == 'python-unittest-v1':
            ids = agreement['ids']
            if (r.get('ids') != ids or r.get('executed') != ids or r.get('tests') != len(ids) or
                    any(type(r.get(k)) is not int or r[k] != 0 for k in ('errors', 'skips', 'xfail', 'xpass'))):
                return False
            if r.get('assertions') != (sorted(ids) if rc else []) or (r.get('failures', 0) > 0) != bool(rc):
                return False
    return True


def fix_status(task):
    if not task.get('fix'):
        return 'not-required'
    if task['fix'].get('v') != 1:
        return 'unsupported'
    record = task.get('fix_result')
    if not record:
        return 'pending'
    if record.get('binding') != fix_binding(task) or record.get('candidate') != task.get('candidate'):
        return 'outdated'
    if record.get('exception'):
        return 'exception'
    agreement = task['fix'].get('agreement')
    return 'matched' if agreement and fix_matched(agreement, record.get('runs')) else 'failed'


def contract_matches(task, record):
    if task.get("fix"):
        if record.get("fix_binding") != fix_binding(task):
            return False
    return not task.get("contract") or record.get("contract") == task["contract"]


SHARED_START_PREDICATE = "kind='run.start' AND instr(data, '\"shared_proof\":')>0"
SHARED_RUN_FIELDS = ('contract', 'fix_binding', 'light')
SHARED_IMMUTABLE_FIELDS = ('task', 'mode', 'argv', 'before', 'gate', 'expected_rc', 'covers', 'stage',
                           'folder', 'timeout', 'lease_version', 'shared_proof', *SHARED_RUN_FIELDS)


def proof_binding(task):
    """The task contract a shared execution agreed before it started."""
    fields = ('repo', 'base', 'proof', 'expected', 'full_gate', 'scope', 'plan', 'requirements',
              'route', 'contract', 'light_policy')
    value = {key: plain(task.get(key)) for key in fields}
    if task.get('fix'):
        value['fix_binding'] = fix_binding(task)
    return digest(canonical(value).encode())


def shared_coverage(state, task_ids, covers):
    require(len(task_ids) == len(set(task_ids)), 'shared_proof_invalid: duplicate task')
    require(all(name in state['tasks'] for name in task_ids), 'shared_proof_invalid: unknown task')
    ids = {name: [r['id'] for r in state['tasks'][name].get('requirements', [])] for name in task_ids}
    require(set(covers) <= {key for values in ids.values() for key in values},
            'shared_proof_invalid: unknown covered requirement')
    for name, values in ids.items():
        require(set(values) <= set(covers), 'shared_proof_invalid: missing coverage for ' + name + ': ' +
                ', '.join(key for key in values if key not in covers))
    return ids


def shared_record(state, task_id, also, before, argv, gate, expected_rc, covers):
    """Bind each served task; this changes no reducer record."""
    require(isinstance(also, (list, tuple)) and also and all(isinstance(name, str) for name in also),
            'shared_proof_invalid: additional task IDs required')
    names = [task_id, *also]
    coverage = shared_coverage(state, names, covers)
    tasks = [state['tasks'][name] for name in names]
    require(expected_rc == 0, 'shared_proof_invalid: a proof must expect exit zero')
    require(isinstance(argv, list) and argv and all(isinstance(arg, str) and arg for arg in argv),
            'shared_proof_invalid: proof argv required')
    for task in tasks:
        require(str(Path(task['repo']).resolve()) == before['repo'],
                'shared_proof_invalid: repository/source differs: ' + task['id'])
    require(snapshot(before['repo']) == before, 'shared_proof_invalid: source changed before start')
    entries = []
    for task in tasks:
        require(task['status'] not in {'done', 'waiting-ack'} and not task.get('superseded_by'),
                'shared_proof_invalid: task is closed, superseded or unacknowledged: ' + task['id'])
        require(not task.get('proof') or task['proof'] == argv,
                'shared_proof_invalid: proof argv differs: ' + task['id'])
        require(task.get('expected') == tasks[0].get('expected'),
                'shared_proof_invalid: expected test count differs: ' + task['id'])
        require(not task.get('full_gate') or gate, 'shared_proof_invalid: task requires the gate: ' + task['id'])
        ids = coverage[task['id']]
        plan = task.get('plan')
        if plan:
            path = Path(plan['original'])
            require(path.is_file() and digest(path.read_bytes()) == plan['sha256'],
                    'Original plan changed; re-register/review it: ' + task['id'])
        entry = {'task': task['id'], 'repo': before['repo'], 'source': before['sha256'],
                 'binding': proof_binding(task), 'expected': task.get('expected'), 'covers': ids}
        if task.get('fix'):
            entry['fix_binding'] = fix_binding(task)
        if task.get('contract') or task.get('route') == 'light':
            from light_route import check_contract, measure
            check_contract(task)
            entry['contract'] = task['contract']
            if task['route'] == 'light':
                entry['light'] = measure(task, before)
        entries.append(entry)
    return {'v': 1, 'primary_binding': entries[0]['binding'], 'tasks': entries[1:]}, entries[0]['covers']


def shared_members(run):
    """Task IDs associated with one physical execution, primary first."""
    metadata = plain(run.get('shared_proof'))
    if metadata is None:
        return [run['task']]
    require(isinstance(metadata, dict) and set(metadata) == {'v', 'primary_binding', 'tasks'} and
            type(metadata['v']) is int and metadata['v'] == 1 and
            isinstance(metadata['primary_binding'], str) and ARTIFACT_DIGEST.fullmatch(metadata['primary_binding']) and
            isinstance(metadata['tasks'], list) and metadata['tasks'],
            'shared_proof_invalid: unsupported shared proof')
    require(all(isinstance(item, dict) and isinstance(item.get('task'), str) and
                isinstance(item.get('covers'), list) and all(isinstance(key, str) for key in item['covers'])
                for item in metadata['tasks']),
            'shared_proof_invalid: invalid member')
    names = [run['task'], *[item['task'] for item in metadata['tasks']]]
    require(len(names) == len(set(names)), 'shared_proof_invalid: duplicate member')
    return names


def completed_members(start, end):
    # Old writers can end a run, but cannot change the contract bound at its start.
    if any(key in end for key in (*SHARED_IMMUTABLE_FIELDS, 'agent', 'utc')):
        return [start['task']]
    try:
        return shared_members(start)
    except Error:
        return [start['task']]


def validate_shared_start(state, actor, data):
    names = shared_members(data)
    covers = list(data.get('covers', [])) + [key for item in data['shared_proof']['tasks'] for key in item.get('covers', [])]
    expected, primary_covers = shared_record(state, names[0], names[1:], data['before'], data['argv'],
                                             data.get('gate', False), data.get('expected_rc', 0), covers)
    require(data['mode'] == 'proof' and data['shared_proof'] == expected and data.get('covers', []) == primary_covers,
            'shared_proof_invalid: shared binding changed')
    for entry in expected['tasks']:
        member = {key: value for key, value in data.items() if key not in (*SHARED_RUN_FIELDS, 'shared_proof')}
        member.update(task=entry['task'], covers=entry['covers'])
        member.update({key: entry[key] for key in SHARED_RUN_FIELDS if key in entry})
        validate(state, 'run.start', actor, member)


def effective_proof(task, run, entry):
    """An attributed copy; the physical run and raw task remain unchanged."""
    if not (run['mode'] == 'proof' and run['status'] == 'passed' and run.get('native_rc') == 0 and
            run.get('expected_rc', 0) == 0 and run.get('after') == run['before'] and
            entry.get('binding') == proof_binding(task) and entry.get('repo') == run['before']['repo'] and
            entry.get('source') == run['before']['sha256'] and
            str(Path(task['repo']).resolve()) == entry['repo'] and
            (not task.get('proof') or run['argv'] == task['proof']) and
            (not task.get('full_gate') or run.get('gate')) and
            entry.get('expected') == task.get('expected') and
            set(entry.get('covers', [])) == {r['id'] for r in task.get('requirements', [])}):
        return None
    if task.get('expected') is not None:
        counts = run.get('counts', {})
        if sum(counts.values()) != task['expected'] or any(counts.get(key) for key in ('failed', 'error', 'errors')):
            return None
    proof = {key: value for key, value in run.items() if key not in SHARED_RUN_FIELDS}
    proof.update(task=task['id'], shared_from=run['task'], covers=entry['covers'])
    proof.update({key: entry[key] for key in SHARED_RUN_FIELDS if key in entry})
    return proof


def current_proofs(task, current):
    return [p for p in task["proofs"] if p["status"] == "passed" and p["after"] == current
            and contract_matches(task, p)]


DRIFT_PATHS = 20


def drift(candidate, current):
    """What moved since the delivery fingerprint, in a sentence: up to 20 paths, then a count."""
    if not candidate:
        return " (nothing was delivered yet)"
    old = {row["path"]: row for row in candidate.get("files", [])}
    new = {row["path"]: row for row in current.get("files", [])}
    moved = []
    for path in sorted(old.keys() | new.keys()):
        before, after = old.get(path), new.get(path)
        if before == after:
            continue
        if before is None or before.get("kind") == "deleted":
            moved.append("added " + path)
        elif after is None or after.get("kind") == "deleted":
            moved.append("removed " + path)
        else:
            moved.append("changed " + path)
    parts = []
    if candidate.get("repo") != current.get("repo"):
        parts.append("repository " + str(candidate.get("repo")) + " -> " + str(current.get("repo")))
    if candidate.get("head") != current.get("head"):
        parts.append("HEAD " + str(candidate.get("head"))[:12] + " -> " + str(current.get("head"))[:12])
    parts += moved[:DRIFT_PATHS]
    if len(moved) > DRIFT_PATHS:
        parts.append("and " + str(len(moved) - DRIFT_PATHS) + " more paths")
    return ". Moved since the delivery fingerprint: " + ", ".join(parts) if parts else ""


def require_candidate(task, current, hint):
    """Refuse a source that is not the delivered candidate, naming what moved."""
    if current != task.get("candidate"):
        raise Error("Candidate drift; " + hint + drift(task.get("candidate"), current))


CROSS_CLIENT_RULE = ("the read that counts comes from another client: Codex writes and Claude reads, Claude writes "
                     "and Codex reads; a same-client read only adds to it")
# The rule names models; a manual or Cursor seat may run any model, so its work is not judged by it.
MODEL_CLIENTS = ("claude", "codex")
REASON_BYTES = 2000


def same_client_readers(state, owner, readers):
    """The named readers whose registered client is the owner's Claude or Codex client, as «reader (client)»."""
    own = state["agents"][owner]["client"]
    return [r + " (" + own + ")" for r in readers if own in MODEL_CLIENTS and state["agents"][r]["client"] == own]


def validate_cross_client(state, d):
    """A task recorded under the cross-client rule names readers of another client, or records why not."""
    require(d.get("cross_client") is True, "cross_client_invalid: cross_client is true when present")
    reason = d.get("same_client_reason")
    require(reason is None or (isinstance(reason, str) and reason.strip() and len(reason.encode()) <= REASON_BYTES),
            "same_client_reason_invalid: a nonempty reason of at most " + str(REASON_BYTES) + " bytes")
    readers = [r for r in [d.get("reviewer"), *(d.get("pool") or [])] if r]
    same = same_client_readers(state, d["owner"], readers)
    owner = d["owner"] + " (" + state["agents"][d["owner"]]["client"] + ")"
    if reason is None:
        require(not same, "same_client_reader: " + ", ".join(same) + " would read the work of " + owner + "; " +
                CROSS_CLIENT_RULE + ". Name a reader of another client, or record why with --same-client-reason")
    else:
        require(bool(same) or not readers, "same_client_reason_unneeded: every reader named for " + owner +
                " is of another client")


def cross_client_judged(state, task):
    """The Claude or Codex clients that wrote a task the cross-client rule judges, or an empty set."""
    authors = {state["agents"][a]["client"] for a in task["authors"]}
    if not task.get("cross_client") or task.get("same_client_reason") is not None or not authors <= set(MODEL_CLIENTS):
        return set()
    return authors


def gate_same_client(state, task, reader):
    """Why a release gate's reader cannot be the read that counts for a covered task, or None."""
    authors, client = cross_client_judged(state, task), state["agents"][reader]["client"]
    if client not in authors:
        return None
    return (task["id"] + " was written by " + "/".join(sorted(authors)) + " and the gate reader " + reader +
            " is " + client + " too")


def cross_client_read(state, task, current):
    """A task recorded under the cross-client rule closes only on an accepted current read from another client.

    A task read at a release gate is judged on the gate's reader instead (gate_same_client)."""
    authors = cross_client_judged(state, task)
    if not authors or read_at_gate(task):
        return
    readers = {state["agents"][r["reviewer"]]["client"] for r in task["reviews"]
               if r["stage"] == "code" and r["verdict"] == "accepted" and r["binding"] == (current or {}).get("sha256")}
    require(bool(readers - authors), "cross_client_read_missing: " + task["id"] + " was written by " +
            "/".join(sorted(authors)) + " and its accepted current reads come from " +
            ("/".join(sorted(readers)) or "no reader") + "; " + CROSS_CLIENT_RULE)


def candidate_checks(task, current, require_review=True, delivering=False):
    if not delivering:
        require_candidate(task, current, "deliver/prove the new exact source")
    proof = current_proofs(task, current)
    require(bool(proof), "No complete native proof on current candidate")
    if task.get("full_gate"):
        require(any(p.get("gate") for p in proof), "Required full gate not complete")
    ids = {r["id"] for r in task.get("requirements", [])}
    require(ids <= {i for p in proof for i in p.get("covers", [])}, "Requirement lacks native current proof")
    if require_review:
        reviews = [r for r in task["reviews"] if r["stage"] == "code"]
        require(bool(reviews) and reviews[-1]["verdict"] == "accepted" and
                reviews[-1]["binding"] == current["sha256"] and contract_matches(task, reviews[-1]),
                "Independent current code review pending")
        require(ids <= set(reviews[-1].get("covers", [])), "Requirement lacks independent current review")
        if reviews[-1].get('request'):
            delivery = task.get('delivery', {})
            # A review with no handoff counts only on a delivery with none, which has no configured reader (H-5).
            handoffs = [h for h in [*delivery.get('handoffs', []), delivery.get('handoff')] if h] or [None]
            require(reviews[-1].get('handoff') in handoffs, 'Independent review belongs to an older handoff')


def review_handoff(state, task, actor):
    delivery = task.get('delivery', {})
    return delivery.get('handoff') or next((h for h in delivery.get('handoffs', [])
                                         if state['messages'].get(h, {}).get('to') == actor), None)


def review_request(actor, data):
    identity = {k: v for k, v in data.items() if k != 'request'}
    identity['report'] = data['report']['sha256']
    return 'review-request:' + digest(canonical({'actor': actor, **identity}).encode())


def check_pool(state, d):
    """Refuse the whole registration if the pool's size or any member is invalid."""
    pool = d["pool"]
    require(isinstance(pool, list) and 2 <= len(pool) <= 4 and d.get("reviewer") is None,
            "review_pool_size: a pool names 2 to 4 readers; one reader uses a single --reviewer")
    owner = state["agents"][d["owner"]]["identity"]
    for member in pool:
        agent = state["agents"].get(member) if isinstance(member, str) else None
        problem = ("named twice" if pool.count(member) > 1 else
                   "registered reviewer required" if not agent or agent["role"] not in {"reviewer", "coordinator"} else
                   "in stand-by" if standby(state, member) else
                   "author alias is not independent" if agent["identity"] == owner else None)
        require(problem is None, "review_pool_member: " + str(member) + ": " + str(problem))


def require_handoff(state, task, actor, action):
    """Only the configured reader, after its own ACK of the delivery's handoff about this task, reads it (H-5)."""
    require(actor == task.get("reviewer"), "Configured reviewer must ACK exact handoff before " + action)
    handoff = task.get("delivery", {}).get("handoff")
    message = state["messages"].get(handoff) if isinstance(handoff, str) else None
    require(message is not None and message["to"] == actor and message.get("related_task") == task["id"],
            "handoff_missing: " + task["id"] + " was delivered without a handoff message to " + actor)
    require((message["ack"] or {}).get("agent") == actor, "Configured reviewer must ACK exact handoff before " + action)


def require_claim(state, task, actor, action):
    """Only the member whose ACK claimed the current pool packet may read it."""
    claim = pool_claim(state, task)
    require(claim is None or claim["agent"] == actor,
            "review_claimed: " + str(claim and claim["agent"]) + " holds the read of " + task["id"])
    require(claim is not None, "Configured reviewer must ACK exact handoff before " + action)


def check_gate_covers(state, gate, actor, d):
    covers = d["gate_covers"]
    require(gate.get("release_gate") is True and d.get("stage") == "code" and isinstance(covers, list) and bool(covers),
            "gate_covers_invalid: only a release gate's code read lists the tasks it covers")
    reader = state["agents"][actor]["identity"]
    for position, name in enumerate(covers):
        task = state["tasks"].get(name) if isinstance(name, str) else None
        require(task is not None and name not in covers[:position] and gate_pending(task),
                "gate_covers_invalid: " + str(name) + ": not an outstanding read-at-gate task")
        require(reader not in {state["agents"][a]["identity"] for a in task["authors"]},
                "gate_covers_invalid: " + name + ": the reader is its author")
        same = gate_same_client(state, task, actor)
        require(same is None, "gate_covers_invalid: " + str(same) + "; " + CROSS_CLIENT_RULE +
                ". Have the gate read by another client, or defer the task with decision --defer-read")


def check_deferral(state, d):
    target = d["defer_read"]
    require(isinstance(target, dict) and set(target) == {"task", "gate"} and
            all(isinstance(target[k], str) for k in target) and
            not (d.get("answers") or d.get("withdraws") or d.get("choice")),
            "gate_defer_invalid: a deferral names one task and one gate, outside an answer or withdrawal")
    task, gate = state["tasks"].get(target["task"]), state["tasks"].get(target["gate"])
    require(task is not None and gate_pending(task),
            "gate_defer_invalid: " + target["task"] + " is not an outstanding read-at-gate task")
    require(gate is not None and gate.get("release_gate") is True and gate["status"] != "done",
            "gate_defer_invalid: " + target["gate"] + " is not an open release gate")
    require(not gate_deferred(task, gate["id"]),
            "gate_defer_invalid: " + task["id"] + " is already deferred from " + gate["id"])


def gate_checks(state, gate, current):
    """Refuse a release gate while an outstanding read-at-gate task is neither covered nor deferred.

    Covered means: the gate's current accepted code read lists the task, from a reader who is
    not its author and, for a task under the cross-client rule, not of its author's client, and
    every path the task changed has its exact delivered bytes in the gate.
    """
    read = last_code_read(gate)
    if read.get("verdict") != "accepted" or read.get("binding") != current["sha256"]:
        read = {}
    reader = state["agents"].get(read.get("reviewer"), {}).get("identity")
    rows = {r["path"]: r for r in current["files"] if r["kind"] != "deleted"}
    unread, same, missing = [], [], []
    for task in state["tasks"].values():
        if not gate_pending(task) or gate_deferred(task, gate["id"]):
            continue
        if (task["id"] not in read.get("gate_covers", []) or reader is None or
                reader in {state["agents"][a]["identity"] for a in task["authors"]}):
            unread.append(task["id"])
        elif gate_same_client(state, task, read["reviewer"]):
            same.append(gate_same_client(state, task, read["reviewer"]))
        delivered = {r["path"]: r for r in task["candidate"]["files"] if r["kind"] != "deleted"}
        missing += [task["id"] + " (" + c["path"] + ")" for c in task["delivery"]["light"]["changes"]
                    if delivered.get(c["path"]) != rows.get(c["path"])]
    problems = (["gate_read_missing: " + ", ".join(unread)] if unread else []) + (
        ["cross_client_read_missing: " + "; ".join(same) + "; " + CROSS_CLIENT_RULE] if same else []) + (
        ["gate_bytes_missing: " + ", ".join(missing)] if missing else [])
    require(not problems, "; ".join(problems))


def requirement_matrix(task):
    current = task.get("candidate", {}).get("sha256")
    proofs = [p for p in task["proofs"] if p["status"] == "passed" and current and p["after"]["sha256"] == current and contract_matches(task, p)]
    code = [r for r in task["reviews"] if r["stage"] == "code"]
    accepted = code[-1] if code and code[-1]["verdict"] == "accepted" and code[-1]["binding"] == current and contract_matches(task, code[-1]) else {}
    return [{**r, "proofs": [{"id": p["id"], "native_rc": p["native_rc"], "snapshot": current}
                             for p in proofs if r["id"] in p.get("covers", [])],
             "reviewed": r["id"] in accepted.get("covers", [])} for r in task.get("requirements", [])]


def validate_decision_links(state, d):
    """Resolve explicit decision IDs, task links and --follows targets when recording."""
    refs = state.get("refs", {"ask": {}, "decision": {}})
    if "id" in d:
        name = d["id"]
        require(isinstance(name, str) and bool(name) and name == name.strip(),
                "Decision ID must be nonblank text without surrounding spaces")
        require(not GENERATED_DECISION_ID.match(name), "dec-e<number> is reserved for decisions recorded without --id")
        require(name not in refs["decision"], "Decision ID already exists")
    tasks = d.get("tasks", [])
    require(isinstance(tasks, list) and all(isinstance(t, str) for t in tasks), "Decision tasks must be task IDs")
    require(len(set(tasks)) == len(tasks), "Duplicate decision task link")
    for task in tasks:
        require(task in state["tasks"], "Unknown decision task: " + task)
    follows = d.get("follows", [])
    require(isinstance(follows, list), "Follows must be a list of qualified targets")
    seen = set()
    for target in follows:
        require(isinstance(target, dict) and set(target) == {"kind", "id", "seq"} and target["kind"] in REFERENCE_KINDS,
                "--follows needs a qualified target: indica ask: o decision:")
        kind, name, seq = target["kind"], target["id"], target["seq"]
        require(isinstance(name, str) and name in refs[kind], "Unknown " + kind + ": " + str(name))
        require(type(seq) is int and refs[kind][name] == seq, "Follows target is not the recorded " + kind + " event")
        require((kind, name) not in seen, "Duplicate follows target")
        seen.add((kind, name))


def validate_supersession(state, d):
    """Refuse replacement while the old task has unresolved obligations."""
    old = d["supersedes"]
    require(isinstance(old, str) and old in state["tasks"], "Superseded task must already exist")
    task = state["tasks"][old]
    require(not task.get("fix") or d.get("fix"),
            "fix_supersession_requires_defect_fix: replacing a defect fix requires --defect-fix on the replacement")
    require(task["status"] != "done", "A closed task cannot be superseded")
    require(not task.get("superseded_by"), "Task " + old + " is already superseded by " + str(task.get("superseded_by")))
    require(old not in d.get("depends", []), "Una attività non può dipendere da quella che sostituisce")
    runs = sorted(r["id"] for r in state["runs"].values()
                  if r.get("task") == old and r["status"] in {"running", "cleanup-unknown"})
    require(not runs, "Reconcile first: run " + ", ".join(runs) + " of " + old + " is running or its stop is unverified")
    identity = state["agents"].get(task["owner"], {}).get("identity")
    held = sorted(name for name, r in state['resources'].items()
                  if r.get('task') == old or (r.get('task') is None and
                     (r['owner'] == task['owner'] or
                      (identity and state['agents'].get(r['owner'], {}).get('identity') == identity))))
    require(not held, "Reconcile first: reservation " + ", ".join(held) + " is held by the owner of " + old)
    asks = sorted(a["id"] for a in state["asks"].values() if a.get("task") == old and a["status"] == "open")
    require(not asks, "Reconcile first: open question " + ", ".join(asks) + " about " + old + " needs an answer or a withdrawal")


def validate_supersede(state, d):
    """Apply task replacement checks when the replacement already exists."""
    old, new = d.get("task"), d.get("by")
    require(isinstance(d.get("reason"), str) and bool(d["reason"].strip()), "Supersede reason required")
    require(isinstance(new, str) and new in state["tasks"], "Replacement task must already exist")
    require(new != old, "A task cannot supersede itself")
    # A replacement that depends on the old task, even indirectly, cannot proceed.
    reached, pending = [], list(state["tasks"][new].get("depends", []))
    while pending:
        name = pending.pop()
        if name not in reached:
            reached.append(name)
            pending.extend(state["tasks"][name].get("depends", []))
    validate_supersession(state, {"supersedes": old, "depends": reached, "fix": state["tasks"][new].get("fix")})
    # Dictionary order follows task creation because IDs are never removed.
    order = list(state["tasks"])
    require(order.index(old) < order.index(new), "The replacement must be a task recorded after " + old)


def validate_brief(state, d):
    require((d.get("project") is True) != (d.get("task") is not None), "Brief needs exactly one of --task or --project")
    if d.get("task") is not None:
        require(d["task"] in state["tasks"], "Unknown task")
    text = d.get("text")
    require(isinstance(text, str) and bool(text.strip()), "Brief text required")
    body = text.encode("utf-8")
    require(len(body) <= BRIEF_BYTES, "Brief exceeds " + str(BRIEF_BYTES) + " bytes")
    artifact = d.get("artifact")
    require(isinstance(artifact, dict) and {"sha256", "name", "bytes", "original"} <= artifact.keys()
            and artifact["sha256"] == digest(body), "Brief text must equal its registered artifact")


def artifact_refs(value, found=None):
    """Find registered artifacts in nested data, once per content hash."""
    found = {} if found is None else found
    if isinstance(value, dict):
        if {"sha256", "name", "bytes", "original"} <= value.keys() and isinstance(value["sha256"], str):
            found.setdefault(value["sha256"], {k: value[k] for k in ("sha256", "name", "bytes", "original")})
        for v in value.values():
            artifact_refs(v, found)
    elif isinstance(value, list):
        for v in value:
            artifact_refs(v, found)
    return list(found.values())


def _names_manifest(value, sha):
    """Whether nested data uses the digest as a source-file manifest reference."""
    if isinstance(value, dict):
        manifest = value.get("manifest")
        if isinstance(manifest, dict) and manifest.get("sha256") == sha:
            return True
        return any(_names_manifest(v, sha) for v in value.values())
    if isinstance(value, list):
        return any(_names_manifest(v, sha) for v in value)
    return False


def artifact_uses(events, sha):
    """Whether recorded events register the digest, use it as a source manifest, and which tasks reference it."""
    registered = manifest = False
    tasks, known, runs = [], set(), {}
    for event in events:
        kind, d = event["kind"], event.get("data") or {}
        if kind == "task" and isinstance(d.get("id"), str):
            known.add(d["id"])
        if kind == "run.start" and isinstance(d.get("id"), str):
            runs[d["id"]] = d.get("task")
        if not any(ref["sha256"] == sha for ref in artifact_refs(d)):
            continue
        registered = True
        manifest = manifest or _names_manifest(d, sha)
        task = d.get("id") if kind == "task" else runs.get(d.get("id")) if kind.startswith("run.") else d.get("task")
        if isinstance(task, str) and task in known and task not in tasks:
            tasks.append(task)
    return {"registered": registered, "manifest": manifest, "tasks": tasks}


def validate_scrub_artifact(events, scrub):
    """A scrub names a registered artifact other than a source manifest, with exactly the tasks that reference it."""
    uses = artifact_uses(events, scrub["sha256"])
    require(uses["registered"], "Scrub needs a registered artifact: no recorded event references " + scrub["sha256"])
    require(not uses["manifest"], "A source manifest cannot be scrubbed: the registry resolves recorded snapshots from it")
    require(scrub["tasks"] == uses["tasks"],
            "Scrub tasks must be the tasks that reference the artifact: " + (", ".join(uses["tasks"]) or "none"))


def scrub_index(events):
    """Artifact digests named by a recorded coordinator scrub decision, each with the first decision naming it."""
    roles, found = {}, {}
    for event in events:
        d = event.get("data") or {}
        if event["kind"] == "agent" and isinstance(d.get("id"), str):
            roles.setdefault(d["id"], d.get("role"))
        elif event["kind"] == "decision" and roles.get(event["actor"]) == "coordinator":
            scrub = d.get("scrub")
            if (isinstance(scrub, dict) and isinstance(scrub.get("sha256"), str)
                    and ARTIFACT_DIGEST.fullmatch(scrub["sha256"])):
                found.setdefault(scrub["sha256"], {
                    "decision": decision_id(d, event.get("seq")), "seq": event.get("seq"),
                    "tasks": [t for t in scrub.get("tasks") or [] if isinstance(t, str)]})
    return found


def scrub_report(root, events, index=None):
    """Each scrubbed artifact with its decision, recorded names and tasks, and whether its file is still present."""
    index = scrub_index(events) if index is None else index
    names = {}
    if index:
        for event in events:
            for ref in artifact_refs(event.get("data")):
                if ref["sha256"] in index and ref["name"] not in names.setdefault(ref["sha256"], []):
                    names[ref["sha256"]].append(ref["name"])
    folder = Path(root) / "artifacts"
    return [{"sha256": sha, "names": names.get(sha, []), "decision": entry["decision"], "tasks": entry["tasks"],
             "file": "present" if os.path.lexists(folder / sha) else "absent"} for sha, entry in index.items()]


DECISION_NODE_KINDS = ("owner-answer", "withdrawal", "owner-instruction", "coordinator-decision")
# Recorded links point forward in history. Dashed "cronologia" links show only
# the order of steps in one task; they do not imply a reason or dependency.
EDGE_KINDS = ("answers", "withdraws", "follows", "concerns", "depends", "supersedes",
              "of-task", "plan-read", "code-read", "proof-delivered", "cronologia")


def graph_cycles(nodes, edges):
    """Return nodes involved in cycles, or an empty set for valid CLI history."""
    indegree = {n["id"]: 0 for n in nodes}
    outgoing = {n["id"]: [] for n in nodes}
    for e in edges:
        if e["from"] in indegree and e["to"] in indegree:
            indegree[e["to"]] += 1
            outgoing[e["from"]].append(e["to"])
    ready = [i for i, n in indegree.items() if n == 0]
    placed = set(ready)
    while ready:
        for target in outgoing[ready.pop()]:
            indegree[target] -= 1
            if indegree[target] == 0:
                placed.add(target)
                ready.append(target)
    return [n["id"] for n in nodes if n["id"] not in placed]


def decision_graph(events):
    """Build graph nodes and links from recorded fields and each task's event order.

    Message text, owner words and timing do not create links.
    """
    nodes, edges, unresolved = [], [], []
    by_id, by_seq = {}, {}
    names = {"task": {}, "ask": {}, "decision": {}}
    runs, chains = {}, {}

    def add(node_id, kind, event, seq, **fields):
        if node_id in by_id:
            node_id = "event:" + str(seq)
        node = {"id": node_id, "kind": kind, "seq": seq, "hash": event.get("hash"), "actor": event.get("actor"),
                "utc": event.get("utc"), "artifacts": artifact_refs(event.get("data")), "tasks": [], **fields}
        nodes.append(node)
        by_id[node_id] = by_seq[seq] = node
        return node

    def link(source, target, kind, recorded=True):
        if source in by_id and target in by_id:
            edges.append({"from": source, "to": target, "kind": kind, "recorded": recorded})
            return True
        return False

    def step(task, node, bindings):
        node.update(task=task, tasks=[task] if task else [])
        chain = chains.setdefault(task, [])
        if bindings:
            for source, kind in bindings:
                link(source, node["id"], kind)
        elif chain:
            link(chain[-1]["id"], node["id"], "cronologia", recorded=False)
        else:
            link(names["task"].get(task), node["id"], "of-task")
        chain.append(node)

    for position, event in enumerate(events):
        kind, d = event.get("kind"), event.get("data") or {}
        seq = event.get("seq", position + 1)
        task = d.get("task") if isinstance(d.get("task"), str) else None
        if kind == "task":
            node = add("task:" + d["id"], "task", event, seq, task=d["id"], tasks=[d["id"]], title=d.get("title"),
                       owner=d.get("owner"), depends=list(d.get("depends") or []), supersedes=d.get("supersedes"))
            names["task"][d["id"]] = node["id"]
            for dependency in d.get("depends") or []:
                link(names["task"].get(dependency), node["id"], "depends")
            if d.get("supersedes"):
                link(names["task"].get(d["supersedes"]), node["id"], "supersedes")
        elif kind == "supersede":
            # Link this replacement event to its task, without interpreting the reason text.
            by = d.get("by") if isinstance(d.get("by"), str) else None
            link(names["task"].get(task), names["task"].get(by), "supersedes")
        elif kind == "ask":
            node = add("ask:" + d["id"], "ask", event, seq, ask=d["id"], question=d.get("question"), task=task,
                       tasks=[task] if task else [], recommended=d.get("recommended"),
                       options=[{"id": o.get("id"), "label": o.get("label")} for o in d.get("options") or [] if isinstance(o, dict)])
            names["ask"][d["id"]] = node["id"]
            if task:
                link(names["task"].get(task), node["id"], "concerns")
        elif kind == "decision":
            name = decision_id(d, seq)
            words = d["owner_words"] if isinstance(d.get("owner_words"), str) and d["owner_words"].strip() else None
            node_kind = ("owner-answer" if d.get("answers") else "withdrawal" if d.get("withdraws")
                         else "owner-instruction" if words else "coordinator-decision")
            asked = by_id.get(names["ask"].get(d.get("answers") or d.get("withdraws")))
            tasks = list(d.get("tasks") or [])
            if asked and asked.get("task") and asked["task"] not in tasks:
                tasks.append(asked["task"])
            node = add("decision:" + name, node_kind, event, seq, decision=name, explicit_id=bool(d.get("id")),
                       reason=d.get("reason"), words=words, channel=d.get("channel"), choice=d.get("choice"),
                       answers=d.get("answers"), withdraws=d.get("withdraws"), tasks=tasks,
                       follows=[dict(f) for f in d.get("follows") or [] if isinstance(f, dict)])
            names["decision"][name] = node["id"]
            if d.get("answers"):
                link(names["ask"].get(d["answers"]), node["id"], "answers")
            if d.get("withdraws"):
                link(names["ask"].get(d["withdraws"]), node["id"], "withdraws")
            for linked in d.get("tasks") or []:
                link(names["task"].get(linked), node["id"], "concerns")
            for target in d.get("follows") or []:
                source = by_seq.get(target.get("seq")) if isinstance(target, dict) else None
                family = "ask" if source and source["kind"] == "ask" else "decision" if source and source["kind"] in DECISION_NODE_KINDS else None
                if not (family and family == target.get("kind") and source.get(family) == target.get("id")
                        and link(source["id"], node["id"], "follows")):
                    unresolved.append({"node": node["id"], "kind": "follows", "target": target})
        elif kind == "plan":
            node = add("event:" + str(seq), "plan", event, seq, sha256=(d.get("artifact") or {}).get("sha256"))
            step(task, node, [])
        elif kind == "review":
            stage = "plan" if d.get("stage") == "plan" else "code"
            node = add("event:" + str(seq), stage + "-verdict", event, seq, verdict=d.get("verdict"),
                       binding=d.get("binding"), covers=list(d.get("covers") or []))
            wanted = "plan" if stage == "plan" else "delivery"
            source = next((n for n in reversed(chains.get(task, [])) if n["kind"] == wanted and n.get("sha256") == d.get("binding")), None)
            step(task, node, [(source["id"], stage + "-read")] if source else [])
        elif kind == "run.start":
            runs[d.get("id")] = d
        elif kind == "run.end" and runs.get(d.get("id"), {}).get("mode") == "proof":
            run, after = runs[d["id"]], d.get("after") or {}
            node = add("event:" + str(seq), "proof", event, seq, run=d["id"], status=d.get("status"),
                       native_rc=d.get("native_rc"), counts=d.get("counts"), error=d.get("error"),
                       after_sha=after.get("sha256"), after_head=after.get("head"), gate=bool(run.get("gate")))
            step(run["task"], node, [])
            for member in completed_members(run, d)[1:]:
                chain = chains.setdefault(member, [])
                link(chain[-1]['id'] if chain else names['task'].get(member), node['id'],
                     'cronologia' if chain else 'of-task', recorded=not bool(chain))
                chain.append(node)
                node['tasks'].append(member)
        elif kind == "deliver":
            snapshot, packet = d.get("snapshot") or {}, d.get("packet_data")
            node = add("event:" + str(seq), "delivery", event, seq, sha256=snapshot.get("sha256"),
                       head=snapshot.get("head"), handoff=d.get("handoff"))
            proofs = [n for n in chains.get(task, []) if n["kind"] == "proof"]
            if isinstance(packet, dict) and isinstance(packet.get("proofs"), list):
                carried = {p.get("id") for p in packet["proofs"] if isinstance(p, dict)}
                sources = [n for n in proofs if n["run"] in carried]
            else:
                sources = [n for n in proofs if n["status"] == "passed" and n["after_sha"] == snapshot.get("sha256")]
            step(task, node, [(n["id"], "proof-delivered") for n in sources])
        elif kind == "close":
            step(task, add("event:" + str(seq), "close", event, seq), [])
        elif kind in {"pause", "restore.freeze", "resume"}:
            add("event:" + str(seq), "resume" if kind == "resume" else "pause", event, seq,
                reason=d.get("reason"), restore=kind == "restore.freeze")
    return {"nodes": nodes, "edges": edges, "cycles": graph_cycles(nodes, edges), "unresolved": unresolved}


def _proof_note(proof):
    """Return the latest proof outcome and recorded facts for display."""
    good = proof.get("status") == "passed"
    note = {"tone": "good" if good else "bad", "code": "proof", "status": proof.get("status"),
            "native_rc": proof.get("native_rc"), "utc": proof["utc"]}
    if isinstance(proof.get("counts"), dict):
        note["counts"] = proof["counts"]
    if proof.get("error") and not good:
        note["error"] = str(proof["error"]).strip().splitlines()[0]
    return note


def task_progress(events):
    """Build task progress from recorded events.

    Reaching a step and succeeding at it are separate facts; only close ends a task.
    """
    state, facts, runs = initial(), {}, {}
    for position, event in enumerate(events):
        kind, d, utc = event["kind"], event["data"], event["utc"]
        mark = {"utc": utc, "seq": event.get("seq", position + 1)}
        if kind == "task":
            facts[d["id"]] = {"created": utc, "plans": [], "plan_reads": [], "starts": [], "proofs": [],
                              "deliveries": [], "refusals": [], "code_reads": [], "closes": []}
        fact = facts.get(d.get("task")) if isinstance(d.get("task"), str) else None
        if fact is not None and kind == "plan":
            fact["plans"].append({**mark, "sha": d["artifact"]["sha256"]})
        elif fact is not None and kind == "review":
            fact["plan_reads" if d["stage"] == "plan" else "code_reads"].append({**mark, "verdict": d["verdict"], "binding": d.get("binding")})
        elif fact is not None and kind == "start":
            fact["starts"].append(mark)
        elif kind == "run.start":
            runs[d["id"]] = d
        elif kind == "run.end" and runs.get(d["id"], {}).get("mode") == "proof":
            for member in completed_members(runs[d['id']], d):
                if member in facts:
                    facts[member]['proofs'].append({**d, **mark})
        elif fact is not None and kind == "deliver":
            fact["deliveries"].append({**mark, "sha": d["snapshot"].get("sha256")})
        elif fact is not None and kind == "diagnostic" and d.get("command") in {"deliver", "advance"}:
            fact["refusals"].append({**mark, "error": d.get("error")})
        elif fact is not None and kind == "close":
            fact["closes"].append(mark)
        apply(state, event)
    result = {}
    for task_id, task in state["tasks"].items():
        f = facts[task_id]
        plans, plan_reads, code_reads = f["plans"], f["plan_reads"], f["code_reads"]
        proofs, deliveries = f["proofs"], f["deliveries"]
        steps = []

        def put(step_id, mark, notes):
            steps.append({"id": step_id, "reached": mark is not None, "utc": mark["utc"] if mark else None, "notes": notes})

        def note(tone, code, mark, **fields):
            return {"tone": tone, "code": code, "utc": mark["utc"], **fields}

        put("plan", plans[-1] if plans else None,
            [note("info", "plan-versions", plans[-1], count=len(plans))] if len(plans) > 1 else [])
        notes, current_plan = [], plans[-1]["sha"] if plans else None
        if plan_reads:
            last = plan_reads[-1]
            if last["verdict"] == "accepted":
                notes.append(note("good", "plan-accepted", last) if last["binding"] == current_plan
                             else note("bad", "plan-accepted-old", last))
            elif last["binding"] == current_plan:
                notes.append(note("bad", "plan-to-fix" if last["verdict"] == "changes-required" else "plan-blocked", last))
            else:
                notes.append(note("info", "plan-new-version", plans[-1]))
        put("plan-read", plan_reads[-1] if plan_reads else None, notes)
        put("build", f["starts"][0] if f["starts"] else None, [])
        notes = []
        if proofs:
            notes.append(_proof_note(proofs[-1]))
            if (proofs[-1].get("status") == "passed" and deliveries and deliveries[-1]["seq"] > proofs[-1]["seq"]
                    and deliveries[-1]["sha"] != (proofs[-1].get("after") or {}).get("sha256")):
                notes.append(note("bad", "proof-other-version", deliveries[-1]))
        if deliveries:
            notes.append(note("info", "delivered", deliveries[-1]))
        if f["refusals"] and (not deliveries or f["refusals"][-1]["seq"] > deliveries[-1]["seq"]):
            refusal = f["refusals"][-1]
            notes.append(note("bad", "delivery-refused", refusal, error=str(refusal["error"] or "").strip().splitlines()[0]
                              if str(refusal["error"] or "").strip() else ""))
        put("proof", proofs[-1] if proofs else None, notes)
        notes, current = [], deliveries[-1]["sha"] if deliveries else None
        if code_reads:
            last = code_reads[-1]
            if last["verdict"] == "accepted":
                notes.append(note("good", "code-accepted", last) if last["binding"] == current
                             else note("bad", "code-accepted-old", last))
            else:
                notes.append(note("bad", "code-to-fix" if last["verdict"] == "changes-required" else "code-blocked", last))
        gate = task.get("gate_read")
        if gate and not code_reads:
            notes.append({"tone": "good", "code": "gate-read", "utc": gate["utc"], "gate": gate["gate"]})
        put("code-read", code_reads[-1] if code_reads else {"utc": gate["utc"]} if gate else None, notes)
        put("closed", f["closes"][-1] if f["closes"] else None, [])
        plan_to_fix = bool(plan_reads) and plan_reads[-1]["verdict"] != "accepted" and plan_reads[-1]["binding"] == current_plan
        if task.get("superseded_by"):
            situation = {"id": "superseded", "by": task["superseded_by"], "utc": task.get("superseded_utc")}
        elif gate_pending(task):
            situation = {"id": "closed-unread", "utc": task.get("status_utc"), "deferrals": task.get("gate_deferrals", [])}
        elif task["status"] == "done":
            situation = {"id": "closed", "utc": task.get("status_utc")}
        elif task["status"] == "changes-required":
            situation = {"id": "to-fix", "utc": code_reads[-1]["utc"] if code_reads else task.get("status_utc")}
        elif plan_to_fix:
            situation = {"id": "to-fix", "utc": plan_reads[-1]["utc"]}
        else:
            situation = {"id": "open", "utc": task.get("status_utc")}
        result[task_id] = {"task": task_id, "created": f["created"], "status": task["status"],
                           "status_utc": task.get("status_utc"), "state": situation, "steps": steps}
    return result


def validate(state, kind, actor, d, stored_packet=None):
    require(kind in {"init", "agent", "task", "ack", "message", "transport", "plan", "start", "deliver",
                    "review", "close", "output", "run.start", "run.end", "run.cancel", "run.progress", "run.abandon",
                    "resource.acquire", "resource.release", "pause", "resume", "legacy", "decision", "ask", "diagnostic",
                    "settings", "save", "restore.check", "restore.freeze", "challenge", "benchmark", "brief",
                    "availability", "supersede", "reassign", "route", "fix",
                    "lesson", "lesson.retire", "lesson.recall", "lesson.ineffective"},
            "Unknown event kind")
    if kind == "init":
        require(not state["workspace"], "Workspace already initialized")
        return
    require(bool(state["workspace"]), "Run squadra init first")
    if kind == "restore.freeze":
        require(actor == "system" and d.get("reason") == "Historical restore; explicit reconciliation and owner resume required",
                "Recovery freeze is a tool receipt, not an agent authorization")
        return
    if kind == "diagnostic":
        return
    if kind == "agent":
        require("availability" not in d, "Availability requires a recorded coordinator action")
        old = state["agents"].get(d["id"])
        require(not old or (old["identity"] == d["identity"] and old["role"] == d["role"]),
                "Agent identity/role cannot be rewritten; register a new seat")
        return
    require(actor in state["agents"], "Unknown agent: " + actor)
    coordinator = state["agents"][actor]["role"] == "coordinator"
    if kind in {"task", "close", "pause", "resume", "decision", "ask", "settings", "save", "restore.check", "brief",
                "availability", "supersede", "reassign", "route", "fix"}:
        require(coordinator, "Coordinator role required")
    if kind == 'reassign':
        task = state['tasks'].get(d.get('task'))
        require(task is not None and task['status'] == 'delivered' and not task.get('superseded_by'),
                'Reassign requires a current delivered task without a recorded verdict')
        require(isinstance(d.get('reason'), str) and bool(d['reason'].strip()), 'Reassign reason required')
        reader = state['agents'].get(d.get('reviewer'))
        require(reader is not None and reader['role'] in {'reviewer', 'coordinator'} and
                not standby(state, reader['id']), 'Available registered reviewer required')
        require(reader['identity'] not in {state['agents'][a]['identity'] for a in task['authors']},
                'Author cannot independently review own changes')
        require(reader['id'] != task.get('reviewer'), 'Reviewer is already assigned')
        if task.get('cross_client'):
            validate_cross_client(state, {**task, 'reviewer': reader['id'], 'pool': None,
                                         'same_client_reason': d.get('same_client_reason')})
        require(not any(r.get('task') == task['id'] and r.get('mode') == 'read' and
                        r['status'] in {'running', 'cleanup-unknown'} for r in state['runs'].values()),
                'Reconcile active reader before reassignment')
        require(d.get('previous') == task.get('delivery', {}).get('handoff') and
                d.get('previous_pool', []) == task.get('delivery', {}).get('handoffs', []),
                'Delivery changed before reassignment')
        from source_manifests import transform_packet, resolve
        packet = d.get('packet_data', {})
        expected = transform_packet(task['delivery'].get('packet_data', {}),
                                    lambda value: resolve(value, Path(state['root']) / 'artifacts'))
        expected.pop('pool', None)
        expected.update(reviewer=reader['id'], reassignment={'previous': d.get('previous'),
                        'previous_pool': d.get('previous_pool', []), 'reason': d['reason']})
        if task.get('cross_client'):
            expected['reassignment']['same_client_reason'] = d.get('same_client_reason')
        require(packet == expected and packet.get('proofs') == current_proofs(task, task['candidate']),
                'Reassignment must preserve the complete delivery contract and proofs')
        raw_packet = stored_packet if stored_packet is not None else packet
        require(d.get('packet', {}).get('sha256') == digest((canonical(raw_packet) + '\n').encode()),
                'Reassignment artifact must contain its exact packet')
        require(d.get('handoff') not in state['messages'] and
                d.get('handoff') == 'review-' + digest(canonical(stored_packet if stored_packet is not None else packet).encode()),
                'Reassignment handoff must bind its packet')
    if kind == 'output' and d.get('candidate_action'):
        require(coordinator, 'Candidate storage actions require coordinator')
        action = d['candidate_action']
        require(action.get('action') in {'limits', 'repair', 'retire'} and
                isinstance(action.get('reason'), str) and bool(action['reason'].strip()), 'Invalid candidate storage receipt')
        if action['action'] == 'limits':
            require(type(action.get('max_copy_bytes')) is int and type(action.get('max_total_bytes')) is int and
                    1024 * 1024 <= action['max_copy_bytes'] <= action['max_total_bytes'] <= 102400 * 1024 * 1024,
                    'Invalid frozen storage limits')
        else:
            task = state['tasks'].get(d.get('task'), {})
            require(action.get('sha256') == task.get('delivery', {}).get('frozen', {}).get('sha256') and action.get('sha256'),
                    'Candidate action must name its delivered source')
            if action['action'] == 'retire':
                require(not any(t.get('delivery', {}).get('frozen', {}).get('sha256') == action['sha256'] and
                                t['status'] != 'done' and not t.get('superseded_by') for t in state['tasks'].values()),
                        'Candidate still needed by active tasks')
    if kind == "route":
        require(d.get("task") in state["tasks"], "Unknown task")
        from light_route import promotion
        promotion(state, state["tasks"][d["task"]], d)
    if kind == "brief":
        validate_brief(state, d)
    if kind == "availability":
        require(d.get("agent") in state["agents"], "Registered agent required")
        require(d.get("status") in {"active", "stand-by"}, "Availability must be active or stand-by")
        require(isinstance(d.get("reason"), str) and d["reason"].strip(), "Availability reason required")
        require(standby(state, d["agent"]) != (d["status"] == "stand-by"), "Availability already recorded")
        if d.get("ping"):
            ping = latest_ping(state, d["agent"])
            require(ping and ping["id"] == d["ping"], "Sweep challenge changed; read again")
            if d["status"] == "stand-by":
                require(state["agents"][d["agent"]]["identity"] != state["agents"].get(actor, {}).get("identity"),
                        "A sweep never stands its own coordinator by")
                observed = next(r for r in presence(state, now()) if r["agent"] == d["agent"])
                require(observed["due"], "Ping no longer overdue; read current answer and alive threshold")
            else:
                require(return_ping(state, d["agent"]), "Return needs ACK of a post-stand-by ping")
    if kind == "settings":
        thresholds = d.get("wait_seconds")
        require(isinstance(thresholds, dict) and set(thresholds) <= set(WAIT_SECONDS) - MESSAGE_WAIT_KEYS, "Unknown wait threshold")
        require(all(type(v) is int and v >= 0 for v in thresholds.values()), "Nonnegative integer seconds required")
        extra = d.get("message_wait_seconds", {})
        require(isinstance(extra, dict) and set(extra) <= MESSAGE_WAIT_KEYS, "Unknown message wait threshold")
        require(all(type(v) is int and v >= 0 for v in extra.values()), "Nonnegative integer seconds required")
    if kind == "ask":
        require(isinstance(d.get("id"), str) and d["id"].strip() and d["id"] not in state["asks"], "Unique nonempty ask ID required")
        require(isinstance(d.get("question"), str) and d["question"].strip(), "Nonempty question required")
        options = d.get("options")
        require(isinstance(options, list) and len(options) >= 2, "At least two options required")
        for option in options:
            require(isinstance(option, dict) and all(isinstance(option.get(k), str) and option[k].strip()
                    for k in ("id", "label", "say")), "Options need nonempty id, label and say")
        ids = [option["id"] for option in options]
        require(len(set(ids)) == len(ids), "Option IDs must be unique")
        require(d.get("recommended") is None or d["recommended"] in ids, "Recommended choice is not an option")
        require(isinstance(d.get("waiting", []), list) and all(x in state["agents"] for x in d.get("waiting", [])), "Waiting seats must be registered")
        require(d.get("task") is None or d["task"] in state["tasks"], "Unknown ask task")
        require(isinstance(d.get("evidence", []), list), "Evidence must be an artifact list")
        for artifact in d.get("evidence", []):
            require(isinstance(artifact, dict) and {"sha256", "name", "bytes", "original"} <= artifact.keys(), "Evidence must be a registered artifact")
    if kind == "decision":
        require(isinstance(d.get("reason"), str) and d["reason"].strip(), "Decision reason required")
        require(not (d.get("answers") and d.get("withdraws")), "Answer and withdrawal are exclusive")
        target = d.get("answers") or d.get("withdraws")
        if target:
            require(target in state["asks"] and state["asks"][target]["status"] == "open", "Ask must exist and be open")
        if d.get("answers"):
            require(d.get("choice") in [x["id"] for x in state["asks"][target]["options"]], "Choice is not an option")
            require(d.get("channel") in CHANNELS, "Answer channel must be typed-here or relayed")
        else:
            require(not d.get("choice"), "Choice requires an answer (--answers)")
            if d.get("channel") is not None:
                # Record the instruction's source without treating it as an answer.
                require(d["channel"] in CHANNELS, "Channel must be typed-here or relayed")
                require(isinstance(d.get("owner_words"), str) and bool(d["owner_words"].strip()),
                        "A channel without an answer needs nonblank owner words")
        require(d.get("owner_words") is None or isinstance(d["owner_words"], str), "Owner words must be text")
        if "defer_read" in d:
            check_deferral(state, d)
        if "scrub" in d:
            scrub = d["scrub"]
            require(isinstance(scrub, dict) and set(scrub) == {"sha256", "tasks"} and isinstance(scrub["sha256"], str)
                    and ARTIFACT_DIGEST.fullmatch(scrub["sha256"]) is not None,
                    "Scrub needs one artifact SHA-256 digest (64 lowercase hex characters)")
            tasks = scrub["tasks"]
            require(isinstance(tasks, list) and len(set(map(str, tasks))) == len(tasks)
                    and all(isinstance(t, str) and t in state["tasks"] for t in tasks), "Scrub tasks must be recorded task IDs")
            require(not any(isinstance(x.get("scrub"), Mapping) and x["scrub"].get("sha256") == scrub["sha256"]
                            for x in state["decisions"]), "Artifact already scrubbed by an earlier decision")
        validate_decision_links(state, d)
    if kind in {"start", "deliver", "run.start", "resource.acquire", "review"}:
        require(not state["paused"], "Workspace paused; explicit coordinator resume required")
    if kind == "task":
        unknown = sorted(set(d) - TASK_FIELDS)
        require(not unknown, "task_field_unknown: " + ", ".join(unknown) + "; a task declaration never presets "
                "what its own events record")
        # From journal format 3 on, new tasks use frozen delivery, as README's update note says (L-19).
        require(d.get("delivery_policy") in (("frozen-v1",) if format3_started(state) else (None, "frozen-v1")),
                "delivery_policy_required: a task recorded in journal format 3 declares delivery_policy frozen-v1")
        require(d["id"] not in state["tasks"], "Task ID already exists")
        require(d["owner"] in state["agents"], "Task owner not registered")
        require(not standby(state, d["owner"]), "Task owner is in stand-by; coordinator must record return first")
        require(all(dep in state["tasks"] for dep in d.get("depends", [])), "Dependencies must already exist")
        requirements = d.get("requirements", [])
        require(isinstance(requirements, list) and all(isinstance(r, dict) and
                all(isinstance(r.get(k), str) and r[k].strip() for k in ("id", "text")) for r in requirements),
                "Requirements need nonempty id/text")
        require(len({r["id"] for r in requirements}) == len(requirements), "Duplicate requirement ID")
        if d.get("supersedes") is not None:
            validate_supersession(state, d)
        reader = d.get("reviewer")
        if reader:
            require(reader in state["agents"] and state["agents"][reader]["role"] in {"reviewer", "coordinator"},
                    "Registered reviewer required")
            require(not standby(state, reader), "Reviewer is in stand-by; coordinator must record return first")
            require(state["agents"][reader]["identity"] != state["agents"][d["owner"]]["identity"], "Author alias is not independent")
        if d.get("pool") is not None:
            check_pool(state, d)
        if "cross_client" in d or "same_client_reason" in d:
            validate_cross_client(state, d)
        if d.get("read_at_gate") is True:
            require(d.get("route") == "light", "read_at_gate_requires_light: a full-route task always has its own read")
            require(not d.get("reviewer") and not d.get("pool") and d.get("release_gate") is not True,
                    "read_at_gate_no_own_read: a read-at-gate task names no reader and is not a release gate")
        require(d.get("route", "full") in {"full", "light"}, "Unknown task route")
        if d.get("route") == "light":
            from light_route import register
            register(d, state)
        else:
            require(not d.get("contract") and not d.get("light_policy"), "Full task cannot forge route metadata")
    if (kind == "task" and "fix" in d) or kind == "fix" or (kind == "challenge" and d.get("task")) or (
            kind in {"run.start", "deliver", "review", "close"} and state["tasks"].get(d.get("task"), {}).get("fix")):
        from fix_challenge import validate_event as validate_fix_event
        validate_fix_event(state, kind, actor, d)
    if kind.startswith("lesson"):
        from errata import validate_event as validate_lesson_event
        validate_lesson_event(state, kind, actor, d)
    if kind == "supersede":
        validate_supersede(state, d)
    if kind in {"plan", "start", "deliver", "review", "close", "run.start"} or (kind == "output" and d.get("task")):
        require(d["task"] in state["tasks"], "Unknown task")
        task = state["tasks"][d["task"]]
        if kind in {"run.start", "review"}:
            require(set(d.get("covers", [])) <= {r["id"] for r in task.get("requirements", [])}, "Unknown covered requirement")
        require(task["status"] != "done" or kind == "output", "Task already closed")
        if kind in {"start", "deliver"}:
            require(actor == task["owner"], "Only assigned owner can start/deliver")
            require(task["status"] != "waiting-ack", "Assignment has no recipient ACK")
        if kind in {"start", "run.start"}:
            require(all(state["tasks"][dep]["status"] == "done" for dep in task.get("depends", [])),
                    "Dependency not independently closed")
        if kind == "plan":
            require(coordinator or actor == task["owner"], "Plan owner/coordinator required")
        if kind == "deliver":
            if task.get('delivery_policy') == 'frozen-v1':
                from frozen_candidate import current as frozen_current
                require(d.get('frozen', {}).get('sha256') == d['snapshot']['sha256'],
                        'Frozen delivery required for this task')
                require(frozen_current({**task, 'delivery': d}, state.get('root')) == d['snapshot'],
                        'Frozen delivery differs from the proved source')
            handoffs = d.get("handoffs", []) + ([d["handoff"]] if d.get("handoff") else [])
            require(not any(state["messages"].get(h, {}).get("info") is True for h in handoffs),
                    "Information cannot be a review handoff")
            require(not task.get("reviewer") or not standby(state, task["reviewer"]),
                    "Reviewer is in stand-by; no new review handoff")
            if task.get("pool"):
                held = [member for member in task["pool"] if standby(state, member)]
                require(len(held) < len(task["pool"]), "Reviewer is in stand-by; no new review handoff: " + ", ".join(held))
            require(any(p["status"] == "passed" and p["after"]["sha256"] == d["snapshot"]["sha256"]
                        and contract_matches(task, p) and (not task.get("full_gate") or p.get("gate"))
                        for p in task["proofs"]), "No passing proof on this exact source")
            require({r["id"] for r in task.get("requirements", [])} <=
                    {c for p in task["proofs"] if p["status"] == "passed" and p["after"] == d["snapshot"] and contract_matches(task, p)
                     for c in p.get("covers", [])}, "Requirement lacks native current proof")
            if task.get("reviewer") or task.get("pool"):
                packet = d.get("packet_data", {})
                require(packet.get("candidate") == d["snapshot"] and packet.get("scope") == task["scope"] and
                        packet.get("plan") == task.get("plan") and packet.get("reviewer") == task["reviewer"] and
                        packet.get("pool") == task.get("pool"),
                        "Handoff packet changed during delivery")
                key = "review-" + digest(canonical(stored_packet if stored_packet is not None else packet).encode())
                if task.get("pool"):
                    # One handoff per active member, each named after this exact packet; stand-by members get none.
                    require(d.get("handoffs") == [key + "-" + member for member in active_pool(state, task)] and
                            "handoff" not in d, "Pool handoffs must name every active member for this exact packet")
                else:
                    # The single reader's handoff is named after this exact packet too, so new bytes need a new ACK (H-5).
                    require(d.get("handoff") == key and "handoffs" not in d,
                            "handoff_unbound: a delivery to " + str(task["reviewer"]) + " names the handoff "
                            "review-<digest of its packet>")
                require(packet.get("proofs") == [p for p in task["proofs"] if p["status"] == "passed" and
                        p["after"] == d["snapshot"] and contract_matches(task, p)], "Handoff proofs changed during delivery")
        if kind == "run.start":
            require(d["id"] not in state["runs"], "Run ID already exists; do not replay")
            require(task["status"] != "waiting-ack", "Assignment has no recipient ACK")
            if d["mode"] == "build":
                require(actor == task["owner"], "Only assigned builder can build")
                require(state["agents"][actor]["role"] != "reviewer", "Reviewer cannot become a builder silently")
                require(run_count(state, d["task"], "build")
                        < state["workspace"]["limits"]["build"], "Build/fix budget exhausted")
                plan = task.get("plan")
                if plan:
                    plans = [x for x in task["reviews"] if x["stage"] == "plan"]
                    require(plans and plans[-1]["verdict"] == "accepted" and plans[-1]["binding"] == plan["sha256"],
                            "Current plan has no independent approval")
            if d["mode"] == "read":
                require(state["agents"][actor]["identity"] not in
                        {state["agents"][a]["identity"] for a in task["authors"]}, "Author cannot be the independent CLI reader")
                stage = d.get("stage", "code")
                require(run_count(state, d["task"], "read", stage) < state["workspace"]["limits"][stage],
                        "Independent CLI read budget exhausted")
                delivery = task.get("delivery", {})
                if (task.get("reviewer") or delivery.get("handoff")) and stage == "code":
                    require_handoff(state, task, actor, "read")
                elif delivery.get("handoffs") and stage == "code":
                    require_claim(state, task, actor, "read")
            if d.get("gate"):
                require(state["resources"].get("gate", {}).get("owner") == actor,
                        "Acquire exclusive gate resource first")
                require(not any(r.get("gate") for r in live_runs(state, "running")),
                        "A whole gate is already running, including under the same owner")
            if 'shared_proof' in d:
                validate_shared_start(state, actor, d)
        if kind in {'review', 'close'} and task.get('delivery', {}).get('frozen'):
            from frozen_candidate import current as frozen_current
            require(frozen_current(task, state.get('root')) == task['candidate'], 'Frozen candidate differs')
        if kind == "review":
            if d.get('request'):
                require(d.get('handoff') == review_handoff(state, task, actor), 'Review belongs to an older handoff')
                prior = [r for r in task['reviews'] if r['reviewer'] == actor and r['stage'] == d['stage'] and
                         r['binding'] == d['binding'] and r.get('handoff') == d.get('handoff')]
                require(not prior or d.get('amends') == prior[-1].get('request'),
                        'review_already_recorded: use --amend with the last review receipt')
                require(not d.get('amends') or (prior and d['amends'] == prior[-1].get('request')),
                        'Review amendment must name the current review receipt')
            if 'findings' in d:
                validate_findings(d['findings'])
            if 'net_lines' in d:
                require('lens' in d, "net_lines_requires_lens")
                require(type(d['net_lines']) is int and 0 <= d['net_lines'] <= 1000000, "net_lines_invalid")
            if 'lens' in d:
                require(d['lens'] == "rasoio", "lens_unknown")
            author_identities = {state["agents"][a]["identity"] for a in task["authors"]}
            require(state["agents"][actor]["identity"] not in author_identities,
                    "Author cannot independently review own changes, even under another seat ID")
            require(state["agents"][actor]["role"] in {"reviewer", "coordinator"}, "Reviewer role required")
            limit = state["workspace"]["limits"][d["stage"]]
            require(task["rounds"][d["stage"]] < limit, "Review round budget exhausted; record a decision")
            if d["stage"] == "plan":
                require(task.get("plan", {}).get("sha256") == d["binding"], "Plan approval refers to old bytes")
            else:
                require(task.get("candidate", {}).get("sha256") == d["binding"], "Review refers to another candidate")
                require(task["status"] in {"delivered", "reviewed", "changes-required"}, "No delivered candidate")
                delivery = task.get("delivery", {})
                if task.get("reviewer") or delivery.get("handoff"):
                    require_handoff(state, task, actor, "review")
                elif delivery.get("handoffs"):
                    require_claim(state, task, actor, "review")
                if d["verdict"] == "accepted":
                    require(set(d.get("covers", [])) == {r["id"] for r in task.get("requirements", [])},
                            "Accepted review must cover every requirement explicitly")
            if "gate_covers" in d:
                check_gate_covers(state, task, actor, d)
        if kind == "close":
            require(task["status"] == "reviewed" or (read_at_gate(task) and task["status"] == "delivered"),
                    "Exact candidate independent review still pending")
            cross_client_read(state, task, task.get("candidate"))
            if task.get("release_gate") is True:
                from frozen_candidate import current as frozen_current
                gate_checks(state, task, frozen_current(task, state.get('root')))
        if task.get("route") == "light" or task.get("contract"):
            from light_route import validate_event
            validate_event(state, kind, task, d)
    if kind == "ack":
        require(d["message"] in state["messages"], "Unknown message")
        require(state["messages"][d["message"]]["to"] == actor, "ACK must come from actual recipient seat")
        require(state["messages"][d["message"]]["ack"] is None, "Message already acknowledged")
        name = state["messages"][d["message"]].get("task")
        if name:
            # The reducer readies the task of any acknowledged message carrying task, as recorded history
            # replays; so only its owner's ACK of the task's own assignment, before it closes, is recorded (G-1).
            assigned = state["tasks"].get(name)
            require(d["message"] == "assign-" + name and assigned is not None and actor == assigned["owner"] and
                    assigned["status"] != "done",
                    "assignment_ack_invalid: " + d["message"] + " is not the assignment of an open task to " + actor)
        siblings = state["messages"][d["message"]].get("pool") or []
        winner = next((state["messages"][m]["ack"]["agent"] for m in siblings
                       if m != d["message"] and state["messages"].get(m, {}).get("ack")), None)
        require(winner is None, "review_claimed: " + str(winner) + " holds the read of "
                + str(state["messages"][d["message"]].get("related_task")))
    if kind == "message":
        require("info" not in d or type(d["info"]) is bool, "info must be boolean")
        if d.get("info") is True:
            handoffs = [h for t in state["tasks"].values() for h in
                        t.get("delivery", {}).get("handoffs", []) +
                        ([t["delivery"]["handoff"]] if t.get("delivery", {}).get("handoff") else [])]
            require(not any(k in d for k in ("task", "ping", "pool", "awaited")) and d["id"] not in handoffs,
                    "Information cannot replace a mandatory ACK")
        require(isinstance(d.get("to"), str) and d["to"].strip(), "Nonempty recipient required")
        require(d["to"] not in state["agents"] if d.get("awaited") else d["to"] in state["agents"],
                "Awaited recipient already registered" if d.get("awaited") else "Recipient not registered")
        require(d["id"] not in state["messages"], "Message ID already exists")
        if "task" in d:
            # task marks the assignment, whose owner's ACK readies the task; any other link is related_task (G-1).
            assigned = state["tasks"].get(d["task"]) if isinstance(d["task"], str) else None
            require(coordinator and assigned is not None and d["id"] == "assign-" + d["task"] and
                    d["to"] == assigned["owner"] and assigned["status"] == "waiting-ack",
                    "assignment_invalid: only the coordinator's assign-<task> message to the owner of a task waiting "
                    "for its ACK carries task; other messages about a task name it as related_task")
        if d.get("pool") is not None:
            related = state["tasks"].get(d.get("related_task"), {})
            require(d["pool"] == related.get("delivery", {}).get("handoffs") and d["id"] in d["pool"],
                    "Pool handoff must belong to the current pool delivery")
            # A slot is review-<packet digest>-<member>: its message goes to that member only (H-5).
            require(d["id"][len("review-") + 64 + 1:] == d["to"] and d["to"] in (related.get("pool") or []),
                    "pool_slot_invalid: the pool handoff " + d["id"] + " is addressed to the member its name carries")
        if d.get("ping"):
            require(d["ping"] is True and not d.get("awaited") and not d.get("task") and not d.get("related_task"),
                    "Ping is a registered presence challenge, not an assignment")
            require(coordinator or state["agents"][d["to"]]["role"] == "coordinator",
                    "Only coordinator may ping workers; entrants may ping a coordinator")
            previous = latest_ping(state, d["to"])
            current = state["agents"][d["to"]].get("availability", {})
            after = current.get("event")
            require(d.get("availability_event") == after, "Availability changed; read before pinging")
            require(not previous or previous["ack"] or previous.get("availability_event") != after,
                    "Reuse the outstanding ping; sending again cannot postpone its deadline")
    if kind == "transport":
        require(d["message"] in state["messages"], "Unknown message")
        message = state["messages"][d["message"]]
        sender = message["from"] == actor
        require(sender or coordinator_ping(state, message, actor) or
                state["agents"].get(actor, {}).get("role") == "coordinator", "Sender or coordinator required")
        require(d.get("relayed_by") == (None if sender else actor), "relayed_by names the relaying actor, and only a relay")
    if kind in {"run.end", "run.cancel", "run.progress", "run.abandon"}:
        require(d["id"] in state["runs"], "Unknown run")
        run = state["runs"][d["id"]]
        require(actor == run["agent"] or coordinator, "Run owner/coordinator required")
        require(run["status"] == "running", "Run already ended")
        task = state["tasks"][run["task"]]
        if kind == 'run.end' and run.get('shared_proof') is not None:
            require(not set((*SHARED_IMMUTABLE_FIELDS, 'agent', 'utc')).intersection(d), 'Shared run contract is immutable')
            if d.get('status') == 'passed':
                require(d.get('native_rc') == 0 and run.get('expected_rc', 0) == 0 and d.get('after') == run['before'],
                        'Shared proof does not keep its native source binding')
                if task.get('expected') is not None:
                    counts = d.get('counts', {})
                    require(sum(counts.values()) == task['expected'] and
                            not any(counts.get(key) for key in ('failed', 'error', 'errors')),
                            'Shared proof test counts do not match the agreed collection')
                require(run['shared_proof']['primary_binding'] == proof_binding(task) and
                        all(item['binding'] == proof_binding(state['tasks'][item['task']])
                            for item in run['shared_proof']['tasks']), 'Shared task contract changed during proof')
        if kind == "run.end" and d.get("reconciled") is not None:
            require(actor == run["agent"], "Only the original run starter may reconcile its run")
            require(bool(run.get("lease_version")), "Only a leased run has a receipt or logs to reconcile from")
            require(isinstance(d["reconciled"], dict) and d["reconciled"].get("source") in {"receipt", "logs"},
                    "Reconciliation names its source: the terminal receipt or the logs")
        if kind == "run.abandon":
            require(actor == run["agent"], "Only the original run starter may declare abandonment")
            require(set(d) == {"id", "starter", "reason", "evidence", "observation", "legacy"} and
                    d["starter"] == state["agents"][run["agent"]]["identity"] and
                    type(d["legacy"]) is bool and d["legacy"] == (not run.get("lease_version")),
                    "Invalid abandonment identity or legacy declaration")
            require(isinstance(d["reason"], str) and 1 <= len(d["reason"].strip()) <= 8192,
                    "Abandonment reason required")
            abandonment_observation(d["observation"], d["id"])
        if kind == "run.end" and (task.get("contract") or task.get("fix")):
            require(not {"mode", "argv", "before", "task", "contract", "fix_binding", "light", "covers", "gate", "expected_rc"}.intersection(d),
                    "Run contract is immutable")
            if d.get("status") == "passed" and run["mode"] == "proof":
                require(d.get("native_rc") == 0 and run.get("expected_rc") == 0 and
                        d.get("after") == run["before"] and contract_matches(task, run),
                        "Native proof does not keep its source/contract")
                require(d["after"] == snapshot(task["repo"]), "Source changed before proof completion")
                if task.get("contract"):
                    from light_route import check_contract, measure
                    check_contract(task)
                    if task["route"] == "light":
                        require(run.get("light") == measure(task, d["after"]), "Light proof measurement changed")
    if kind == "resource.acquire":
        require(d["name"] not in state["resources"], "Resource already reserved; expiry is not release")
        if d.get('task') is not None:
            require(d['task'] in state['tasks'] and state['tasks'][d['task']]['status'] != 'done' and
                    not state['tasks'][d['task']].get('superseded_by'), 'Resource task must be open')
    if kind == "resource.release":
        require(state["resources"].get(d["name"], {}).get("owner") == actor, "Only resource owner can release")


ABANDON_FORM = "squadra-run-abandon/1"
RUN_OUTPUTS = ("stdout.log", "stderr.log", "reply.txt")
RECONCILE_FILES = RUN_OUTPUTS + ("schema.json", "input.txt")


def abandonment_observation(value, run_id):
    require(isinstance(value, dict) and set(value) ==
            {"form", "run", "observed_at", "processes", "results", "observation"},
            "Abandonment evidence needs the explicit no-process/no-results declaration")
    require(value["form"] == ABANDON_FORM and value["run"] == run_id and
            value["processes"] == "none" and value["results"] == "none",
            "Abandonment evidence does not declare this run without process or results")
    require(isinstance(value["observation"], str) and 1 <= len(value["observation"].strip()) <= 8192,
            "Abandonment requires a concrete observation")
    try:
        observed = dt.datetime.fromisoformat(value["observed_at"].replace("Z", "+00:00"))
        require(observed.tzinfo is not None, "Observation time requires a timezone")
    except (AttributeError, TypeError, ValueError) as exc:
        raise Error("Invalid abandonment observation time") from exc
    return value


def run_lease_paths(root, run_id):
    key = digest(run_id.encode())
    folder = Path(root) / "run-leases"
    return folder, folder / (key + ".lock"), folder / (key + ".json"), folder / (key + ".result.json")


def _private_run_file(path, limit=64 * 1024):
    import stat
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        require(stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and
                not st.st_mode & 0o077 and st.st_nlink == 1 and st.st_size <= limit,
                "Unsafe run lease/evidence file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            body = stream.read(limit + 1)
        require(len(body) <= limit, "Run lease/evidence file exceeds its bound")
        return body
    finally:
        os.close(fd)


@contextmanager
def run_lease(root, run_id):
    """Hold the same exclusive lease for execution and abandonment; never infer liveness from age."""
    import fcntl
    import stat
    folder, path, _, _ = run_lease_paths(root, run_id)
    folder.mkdir(mode=0o700, exist_ok=True)
    st = folder.lstat()
    require(stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and not st.st_mode & 0o077,
            "Unsafe run lease directory")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        st = os.fstat(fd)
        require(stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and
                not st.st_mode & 0o077 and st.st_nlink == 1, "Unsafe run lease file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Error("Run lease is held; execution or cleanup is still owned") from exc
        yield fd
    finally:
        os.close(fd)


def validate_abandonment_files(store, run, data):
    """Check local observations under the run lease and the journal write transaction."""
    evidence = data.get("evidence", {})
    require(isinstance(evidence, dict) and set(evidence) == {"sha256", "name", "original", "bytes"} and
            isinstance(evidence["sha256"], str) and re.fullmatch("[0-9a-f]{64}", evidence["sha256"]),
            "Registered abandonment evidence required")
    body = _private_run_file(store.root / "artifacts" / evidence["sha256"])
    require(len(body) == evidence["bytes"] and digest(body) == evidence["sha256"],
            "Abandonment evidence artifact changed")
    require(abandonment_observation(json.loads(body), run["id"]) == data.get("observation"),
            "Abandonment declaration differs from evidence")
    _, _, _, terminal = run_lease_paths(store.root, run["id"])
    require(not os.path.lexists(terminal), "A terminal result receipt exists; abandonment cannot erase it")
    _owned_process_gone(store, run, "abandonment")
    folder = store.root / "runs" / run["id"]
    require(folder.parent == store.root / "runs", "Unsafe run folder")
    if os.path.lexists(folder):
        import stat
        require(stat.S_ISDIR(folder.lstat().st_mode), "Unsafe run folder")
        for name in RUN_OUTPUTS:
            path = folder / name
            if os.path.lexists(path):
                st = path.lstat()
                require(stat.S_ISREG(st.st_mode) and st.st_size == 0,
                        "Run output exists; abandonment cannot declare no results")


def _owned_process_gone(store, run, action):
    """The launch phase is settled and no process group the run recorded is still live."""
    _, _, metadata, _ = run_lease_paths(store.root, run["id"])
    if os.path.lexists(metadata):
        owned = json.loads(_private_run_file(metadata))
        require(owned.get("run") == run["id"] and owned.get("phase") in {"not-started", "spawned", "disposed"},
                "Process launch is uncertain; " + action + " refused")
        pgid = owned.get("pgid")
        if pgid is not None:
            require(type(pgid) is int and pgid > 0, "Invalid owned process group")
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                pass
            except PermissionError as exc:
                raise Error("Owned process group existence is uncertain") from exc
            else:
                raise Error("A recorded process group is still live")
    else:
        require(not run.get("lease_version"), "New runtime lease observation is missing")


def _run_files(store, run):
    """Regular files a run left in its folder, by name, among RECONCILE_FILES."""
    import stat
    folder = store.root / "runs" / run["id"]
    require(folder.parent == store.root / "runs", "Unsafe run folder")
    if not os.path.lexists(folder):
        return {}
    require(stat.S_ISDIR(folder.lstat().st_mode), "Unsafe run folder")
    found = {}
    for name in RECONCILE_FILES:
        path = folder / name
        if os.path.lexists(path):
            require(stat.S_ISREG(path.lstat().st_mode), "Unsafe run file: " + name)
            found[name] = path
    return found


def _terminal_receipt(store, run):
    """The run's private terminal result receipt, or None when the run wrote none."""
    terminal = run_lease_paths(store.root, run["id"])[3]
    if not os.path.lexists(terminal):
        return None, terminal
    body = _private_run_file(terminal)
    value = json.loads(body)
    require(isinstance(value, dict) and value.get("run") == run["id"] and isinstance(value.get("status"), str),
            "Terminal result receipt does not name this run")
    return value, terminal


def reconciled_status(run, receipt_status):
    """A precheck keeps its receipt's status; a passed proof, probe or model run is not rebuilt without its evidence."""
    if run["mode"] != "precheck" and receipt_status in {"passed", "matched"}:
        return "interrupted"
    return receipt_status


def reconcile_end(store, run):
    """The run.end the starter records for an orphaned run: from its terminal receipt, else interrupted with its logs."""
    receipt, terminal = _terminal_receipt(store, run)
    files = _run_files(store, run)
    artifacts = [store.artifact(files[name]) for name in RECONCILE_FILES if name in files]
    if receipt is None:
        require(any(files[name].stat().st_size for name in RUN_OUTPUTS if name in files),
                "Nothing to reconcile: no terminal receipt and no output; declare the run abandoned instead")
        return {"id": run["id"], "status": "interrupted", "native_rc": None, "ended": now(),
                "error": "Reconciled by the starter: the run's process ended without a recorded result",
                "artifacts": artifacts, "reconciled": {"source": "logs"}}
    status = reconciled_status(run, receipt["status"])
    data = {"id": run["id"], "status": status, "native_rc": receipt.get("native_rc"), "ended": receipt.get("ended"),
            "artifacts": artifacts,
            "reconciled": {"source": "receipt", "receipt_status": receipt["status"], "receipt": store.artifact(terminal)}}
    if status != receipt["status"]:
        data["error"] = "Reconciled from a receipt without its recorded evidence; a passed result is not rebuilt"
    return data


def validate_reconcile_files(store, run, data):
    """Under the run lease: the record repeats the receipt, or the settled process and its logs, exactly."""
    reconciled = data["reconciled"]
    receipt, terminal = _terminal_receipt(store, run)
    files = _run_files(store, run)
    listed = [(a.get("name"), a.get("sha256")) for a in data.get("artifacts") or [] if isinstance(a, dict)]
    require(len(listed) == len(data.get("artifacts") or []) and
            listed == [(name, digest(files[name].read_bytes())) for name in RECONCILE_FILES if name in files],
            "Reconciliation must list every file the run left, with its current bytes")
    if receipt is None:
        require(reconciled.get("source") == "logs", "No terminal result receipt exists for this run")
        _owned_process_gone(store, run, "reconciliation")
        require(any(files[name].stat().st_size for name in RUN_OUTPUTS if name in files),
                "Nothing to reconcile: no terminal receipt and no output; declare the run abandoned instead")
        require(data.get("status") == "interrupted" and data.get("native_rc") is None,
                "A run without a receipt is reconciled as interrupted, with no native result")
        return
    body = _private_run_file(terminal)
    kept = reconciled.get("receipt")
    require(reconciled.get("source") == "receipt" and reconciled.get("receipt_status") == receipt["status"] and
            isinstance(kept, dict) and kept.get("sha256") == digest(body) and kept.get("bytes") == len(body),
            "A terminal result receipt exists; reconcile from it")
    require(data.get("status") == reconciled_status(run, receipt["status"]) and
            data.get("native_rc") == receipt.get("native_rc") and data.get("ended") == receipt.get("ended"),
            "Reconciliation differs from the terminal result receipt")


def registry_git_root(root):
    """Refuse tracked registry contents before creating the private ignore rule."""
    ancestor = root
    while not ancestor.is_dir() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    probe = git_probe('-C', str(ancestor), 'rev-parse', '--show-toplevel')
    if probe.returncode:
        return None
    repo = Path(os.fsdecode(probe.stdout).removesuffix('\n')).resolve()
    require(root != repo, 'Registry root must be a separate folder, not the Git worktree root')
    tracked = git(repo, '--literal-pathspecs', 'ls-files', '-z', '--', str(root.relative_to(repo)))
    require(not tracked, 'Registry folder contains tracked files; remove them from the Git index before init: ' + str(root))
    return repo


def protect_registry_root(root):
    repo = registry_git_root(root)
    ignore = root / '.gitignore'
    require(not ignore.is_symlink(), 'Registry .gitignore symlink refused')
    body = ignore.read_bytes() if ignore.exists() else b''
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    if not body or body.splitlines()[-1:] != [b'*']:
        atomic(ignore, body + (b'\n' if body and not body.endswith(b'\n') else b'') + b'*\n')
    if repo is not None:
        ignored = git_probe('-C', str(repo), 'check-ignore', '--quiet', '--', str(root / 'registry.sqlite3'))
        require(ignored.returncode == 0, 'Registry folder is not ignored by Git: ' + str(root))


class Connection(sqlite3.Connection):
    """A connection that finishes its own cursors when it closes.

    sqlite3 defers closing a connection while one of its statements is unfinished, so a cursor still
    referenced elsewhere would keep the closed connection's read snapshot, and stop every checkpoint, until
    that cursor is collected. Both cursor() and execute() are tracked: the C execute() does not call cursor()."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cursors, self._prune_at = [], 64

    def _track(self, cursor):
        refs = self._cursors
        refs.append(weakref.ref(cursor))
        if len(refs) >= self._prune_at:
            refs[:] = [ref for ref in refs if ref() is not None]
            self._prune_at = max(64, 2 * len(refs))
        return cursor

    def cursor(self, *args, **kwargs):
        return self._track(super().cursor(*args, **kwargs))

    def execute(self, *args):
        return self._track(super().execute(*args))

    def executemany(self, *args):
        return self._track(super().executemany(*args))

    def executescript(self, *args):
        return self._track(super().executescript(*args))

    def close(self):
        refs, self._cursors = self._cursors, []
        failure = None
        for ref in refs:
            cursor = ref()
            if cursor is not None:
                try:
                    cursor.close()
                except sqlite3.Error as exc:
                    failure = failure or exc
        super().close()
        if failure is not None:
            raise failure


class Store:
    def __init__(self, root, create=False, read_only=False, *, database=None):
        """`database` opens a private copy read-only; the root stays the live registry for its anchor and artifacts."""
        self.root = Path(root).resolve()
        self.read_only = read_only
        self.database = None if database is None else Path(database)
        # The live file identity whose private copy audit() reads; only the dashboard's background audit sets it.
        self._audit_copy = None
        self._views = []
        self._integrity_failed = False
        self._cache_mismatch = False
        self._summary_context = None
        self._wait_memo = None
        self._shared_start_memo = None
        self.commit_count = 0
        self.last_commit = None
        require(self.database is None or (read_only and not create), "A registry copy opens read-only")
        if create:
            protect_registry_root(self.root)
        require(self.root.is_dir(), "Unknown workspace: " + str(self.root))
        self.path = self.root / "registry.sqlite3" if self.database is None else self.database
        require(not self.path.is_symlink(), "Registry symlink refused")
        if not create:
            require(self.path.is_file(), "Workspace not initialized")
        if not create and not read_only:
            try:
                fd = os.open(self.root / '.gitignore', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, 'wb') as handle:
                    handle.write(b'*\n')
                    handle.flush()
                    os.fsync(handle.fileno())
        self.db = sqlite3.connect(self.path.as_uri() + "?mode=ro" if read_only else self.path,
                                  uri=read_only, timeout=BUSY_MS / 1000, isolation_level=None, factory=Connection)
        self.db.row_factory = sqlite3.Row
        if read_only:
            return
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA journal_size_limit=%d" % WAL_LIMIT)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,
            utc TEXT NOT NULL, kind TEXT NOT NULL, actor TEXT NOT NULL, data TEXT NOT NULL,
            previous TEXT NOT NULL, hash TEXT NOT NULL);
          CREATE TRIGGER IF NOT EXISTS no_update BEFORE UPDATE ON events
            BEGIN SELECT RAISE(ABORT, 'append-only'); END;
          CREATE TRIGGER IF NOT EXISTS no_delete BEFORE DELETE ON events
            BEGIN SELECT RAISE(ABORT, 'append-only'); END;
        """)
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='shared_proof_starts'").fetchone() is None:
            busy = self.db.execute('PRAGMA busy_timeout').fetchone()[0]
            try:
                self.db.execute('PRAGMA busy_timeout=0')
                self.db.execute('CREATE INDEX IF NOT EXISTS shared_proof_starts ON events(seq) WHERE ' + SHARED_START_PREDICATE)
            except sqlite3.DatabaseError:
                pass  # An optional discovery index; the authenticated view remains authoritative.
            finally:
                self.db.execute('PRAGMA busy_timeout=' + str(busy))
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(events)")}
        if 'hash_format' not in columns:
            self.db.execute("ALTER TABLE events ADD COLUMN hash_format INTEGER")
        os.chmod(self.path, 0o600)

    def release_views(self):
        """Close every view even when one fails; the first failure is raised afterwards."""
        views, self._views = self._views, []
        failure = None
        for view in views:
            try:
                view.close()
            except BaseException as exc:
                failure = failure or exc
        if failure is not None:
            raise failure

    def close(self):
        """The database is closed even when a view or the close itself fails; the first failure is raised."""
        failure = None
        try:
            self.release_views()
        except BaseException as exc:
            failure = exc
        self._summary_context = self._wait_memo = None
        try:
            self.db.close()
        except BaseException as exc:
            failure = failure or exc
        if failure is not None:
            raise failure

    def compact_wal(self, busy_ms):
        """One TRUNCATE checkpoint that waits at most busy_ms for readers: (busy, frames, backfilled)."""
        self.db.execute("PRAGMA busy_timeout=%d" % busy_ms)
        try:
            return tuple(self.db.execute("PRAGMA main.wal_checkpoint(TRUNCATE)").fetchone())
        finally:
            self.db.execute("PRAGMA busy_timeout=%d" % BUSY_MS)

    def _trim_wal(self):
        """After a commit, an oversized write-ahead log is copied back and emptied if no reader holds it."""
        try:
            if os.stat(str(self.path) + "-wal").st_size > WAL_LIMIT:
                self.compact_wal(TRIM_BUSY_MS)
        except (OSError, sqlite3.Error):
            pass  # The commit stands; the next commit, or doctor --repair, tries again.

    def decode_event(self, row):
        event = {k: row[k] for k in ["id", "utc", "kind", "actor", "previous"]}
        event["data"] = json.loads(row["data"])
        version = row['hash_format'] if 'hash_format' in row.keys() else None
        require(version in (None, 2, 3), "Unsupported event hash format")
        if version is not None:
            event['format'] = version
        require(digest(canonical(event).encode()) == row["hash"],
                "Event integrity failure at sequence " + str(row["seq"]))
        return {**event, "hash": row["hash"], "seq": row["seq"]}

    def events_since(self, seq=0, head=None):
        previous, result = head or "0" * 64, []
        if seq:
            anchor = self.db.execute("SELECT * FROM events WHERE seq=?", (seq,)).fetchone()
            require(anchor is not None and anchor["hash"] == previous, "Event anchor changed")
            self.decode_event(anchor)
        expected = seq + 1
        for row in self.db.execute("SELECT * FROM events WHERE seq>? ORDER BY seq", (seq,)):
            event = self.decode_event(row)
            require(event["seq"] == expected and event["previous"] == previous,
                    "Event chain failure at sequence " + str(event["seq"]))
            expected += 1
            previous = event["hash"]
            result.append(event)
        return result

    def events(self):
        return self.events_since()

    def compact_snapshot(self, value):
        from source_manifests import compact
        return compact(value, self.artifact_bytes)

    def resolve_snapshot(self, value):
        from source_manifests import resolve
        return resolve(value, self.root / "artifacts")

    def task(self, name, state=None):
        return self.resolved(state)['tasks'][name]

    def shared_starts(self, count):
        """A bounded memo of discovery hints; callers still verify the authenticated run."""
        marker = count, self.db.execute('PRAGMA data_version').fetchone()[0]
        if self._shared_start_memo is None or self._shared_start_memo[0] != marker:
            rows = self.db.execute('SELECT actor,data FROM events WHERE ' + SHARED_START_PREDICATE + ' AND seq<=?',
                                   (count,)).fetchall()
            self._shared_start_memo = marker, [(row['actor'], json.loads(row['data'])) for row in rows]
        return self._shared_start_memo[1]

    def resolved(self, state=None, *, compact=False):
        from source_manifests import materialize_task, transform_run
        source = self.view() if state is None else state
        store = self
        class Resolved(dict):
            pass
        result = Resolved(source.items())
        result.current = current_index(source)
        if hasattr(source, 'wait_context'):
            result.wait_context = source.wait_context
        associations = None

        def task_value(name, task):
            nonlocal associations
            if associations is None:
                associations = {}
                for actor, start in store.shared_starts(source['event_count']):
                    run_id = start['id']
                    if run_id not in source['runs']:
                        continue
                    run = source['runs'][run_id]
                    if start.get('shared_proof') is None:
                        continue
                    try:
                        shared_members(start)
                    except Error:
                        continue
                    def same(key):
                        left, right = plain(run.get(key)), start.get(key)
                        if key == 'before' and left != right:
                            left, right = store.resolve_snapshot(left), store.resolve_snapshot(right)
                        return left == right
                    if run['agent'] != actor or not all(same(key) for key in SHARED_IMMUTABLE_FIELDS):
                        continue
                    for entry in start['shared_proof']['tasks']:
                        associations.setdefault(entry['task'], []).append((run, entry))
            if compact and name not in associations:
                return task
            value = materialize_task(plain(task), store.root / 'artifacts')
            if name in associations:
                seen = {proof['id'] for proof in value['proofs']}
                added = []
                for raw, entry in associations[name]:
                    if raw['id'] not in seen:
                        run = transform_run(plain(raw), store.resolve_snapshot)
                        proof = effective_proof(value, run, plain(entry))
                        if proof is not None:
                            if compact:
                                proof.update(before=plain(raw['before']), after=plain(raw['after']))
                            added.append(proof)
                            seen.add(proof['id'])
                if added:
                    base = plain(task) if compact else value
                    return {**base, 'proofs': [*base['proofs'], *added]}
            return task if compact else value

        result['tasks'] = ReadEntities(source['tasks'], task_value)
        if not compact:
            result['runs'] = ReadEntities(source['runs'], lambda _, run: transform_run(plain(run), store.resolve_snapshot))
        return result

    def normalized_state(self, events, writer=None):
        from source_manifests import compact, transform_task, transform_run, transform_event
        state = self.state(events, resolve=False, with_waits=False)
        def reference(body, name, original):
            return {"sha256": digest(body), "name": name, "original": original, "bytes": len(body)}
        convert = lambda value: compact(value, writer or reference)
        state['tasks'] = {k: transform_task(v, convert) for k, v in state['tasks'].items()}
        state['runs'] = {k: transform_run(v, convert) for k, v in state['runs'].items()}
        state['challenges'] = [transform_event('challenge', item, convert) for item in state['challenges']]
        return state

    def audit(self, cancelled=None):
        """Full chain/artifact verification and independent derived-root comparison.

        A store the dashboard marks with the live file's identity audits a private copy taken now, so the long
        read never holds the live write-ahead log; a copy of a file that changed meanwhile is refused."""
        if self._audit_copy is None:
            return self._audit(cancelled)
        from registry_prepare import AuditSnapshot
        snapshot = AuditSnapshot(self.root, self._audit_copy, cancelled=cancelled)
        try:
            result = snapshot.store._audit(cancelled)
            self._cache_mismatch = snapshot.store._cache_mismatch
        except BaseException as exc:
            self._integrity_failed = True
            try:
                snapshot.close()
            except BaseException as cleanup:
                exc.add_note('The audit copy was not removed either: ' + str(cleanup))
            raise
        snapshot.close()
        return result

    def _audit(self, cancelled=None):
        from registry_cache import Tree, load
        from registry_radix import IntegrityError
        def check_cancelled():
            if cancelled is not None and cancelled():
                raise Error('Registry audit cancelled')
        def checked(rows):
            for row in rows:
                check_cancelled()
                yield row
        if cancelled is not None:
            self.db.set_progress_handler(lambda: int(cancelled()), 1000)
        own_transaction = not self.db.in_transaction
        if own_transaction:
            self.db.execute('BEGIN')
        completed = False
        try:
            check_cancelled()
            events = self.events()
            state = self.state(checked(events))
            check_cancelled()
            scrubbed = scrub_index(events)
            self.verify_artifacts(state, scrubbed=scrubbed)
            check_cancelled()
            memory = sqlite3.connect(':memory:', isolation_level=None)
            memory.row_factory = sqlite3.Row
            if cancelled is not None:
                memory.set_progress_handler(lambda: int(cancelled()), 1000)
            try:
                normalized = self.normalized_state(checked(events))
                expected = Tree.build(memory, normalized).flush()
                check_cancelled()
                import registry_current
                current_state = registry_current.build(checked(events), apply, initial)
                expected_current = Tree.build(memory, current_state, domain='current').flush()
                check_cancelled()
                prefix_agrees = False
                try:
                    cached, meta = load(self)
                    agrees = meta['state'] == expected and meta['current'] == expected_current and meta['seq'] == len(events)
                    if agrees:
                        # Traversal checks committed leaves as well as the independently computed root.
                        agrees = (plain(cached.root) == normalized and
                                  plain(cached.current_tree.root) == current_state)
                    elif 0 <= meta['seq'] < len(events):
                        prefix = events[:meta['seq']]
                        prefix_agrees = (plain(cached.root) == self.normalized_state(checked(prefix)) and
                                         plain(cached.current_tree.root) == registry_current.build(checked(prefix), apply, initial))
                except sqlite3.DatabaseError as exc:
                    check_cancelled()
                    if 'no such table' not in str(exc):
                        raise
                    agrees = False
                except (IntegrityError, ValueError, KeyError, TypeError):
                    check_cancelled()
                    agrees = False
            finally:
                memory.close()
            from registry_prepare import cache_presence
            status = 'matched' if agrees else 'unavailable' if prefix_agrees else cache_presence(self)
            self._cache_mismatch = status == 'mismatch'
            result = {'events': events, 'state': state, 'cache': status,
                      'scrubbed': scrub_report(self.root, events, scrubbed)}
            completed = True
            return result
        except BaseException:
            self._integrity_failed = True
            raise
        finally:
            # The read must end on every path: a failed COMMIT falls back to ROLLBACK, and its error is
            # raised only when the audit itself succeeded, so an audit failure is never masked.
            ending = None
            if cancelled is not None:
                try:
                    self.db.set_progress_handler(None, 0)
                except BaseException as exc:
                    ending = exc
            if own_transaction:
                try:
                    self.db.execute('COMMIT')
                except BaseException as exc:
                    ending = ending or exc
                    if self.db.in_transaction:
                        self.db.execute('ROLLBACK')
            if ending is not None and completed:
                raise ending

    def _rebuild_cache(self, events=None):
        from registry_cache import Tree, initialize, save
        from source_manifests import transform_task, transform_run
        events = self.events() if events is None else events
        state = self.normalized_state(events, self.artifact_bytes)
        initialize(self.db)
        for name in ('derived_radix_nodes', 'derived_radix_roots', 'derived_state_meta'):
            self.db.execute('DELETE FROM ' + name)
        tree = Tree.build(self.db, state)
        import registry_current
        tree.current_tree = Tree.build(self.db, registry_current.build(events, apply, initial), domain='current')
        tree.root.current = tree.current_tree.root
        meta = save(self, tree, events[-1]['seq'] if events else 0,
                    events[-1]['hash'] if events else '0' * 64)
        return tree, meta

    def _cached(self):
        from registry_cache import load, initialize, save
        from registry_radix import IntegrityError
        initialize(self.db)
        try:
            tree, meta = load(self, writable=True)
            suffix = self.events_since(meta['seq'], meta['head'])
            for event in suffix:
                from source_manifests import transform_event
                event = {**event, 'data': transform_event(event['kind'], event['data'], self.compact_snapshot)}
                apply(tree.root, event)
                import registry_current
                registry_current.advance(tree.current_tree.root, tree.root, event)
            if suffix:
                meta = save(self, tree, suffix[-1]['seq'], suffix[-1]['hash'])
            return tree, meta
        except sqlite3.DatabaseError as exc:
            if 'no such table' not in str(exc):
                raise
            raise IntegrityError('Derived cache preparation required') from exc
        except (IntegrityError, ValueError, KeyError, TypeError) as exc:
            raise IntegrityError('Derived cache preparation required') from exc

    def view(self):
        """A demanded-value view on one read transaction; no derived writes."""
        require(not self._cache_mismatch, 'Cache mismatch; run squadra doctor --repair')
        from registry_cache import load
        from registry_radix import IntegrityError
        reader = Store(self.root, read_only=True, database=self.database)
        reader.db.execute('BEGIN')
        try:
            tree, meta = load(reader)
            tail = reader.db.execute('SELECT seq,hash FROM events ORDER BY seq DESC LIMIT 1').fetchone()
            if tail and (tail['seq'], tail['hash']) != (meta['seq'], meta['head']):
                # An old writer does not maintain the derived tables.
                result = reader.state()
                result = wait_view(result, result.wait_context)
                reader.close()
                return result
            tree.recover_callback = lambda: reader.normalized_state(reader.events())
            import registry_current
            tree.current_tree.recover_callback = lambda: registry_current.build(reader.events(), apply, initial)
            self._views.extend([reader, tree, tree.current_tree])
            return wait_view(tree.root, self._wait_context(reader, meta))
        except (IntegrityError, sqlite3.DatabaseError, ValueError, KeyError, TypeError):
            try:
                result = reader.state()
                return wait_view(result, result.wait_context)
            finally:
                reader.close()
        except BaseException:
            reader.close()
            raise

    def _wait_context(self, reader, meta):
        """One bounded memo; file/WAL changes invalidate even an unchanged journal head."""
        def marker():
            stamps = []
            for path in (self.path, Path(str(self.path) + '-wal')):
                try:
                    value = path.stat()
                    stamps.append((value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns))
                except FileNotFoundError:
                    stamps.append(None)
            return (self.db.execute('PRAGMA data_version').fetchone()[0], *stamps,
                    meta['seq'], meta['head'])
        key = marker()
        if self._wait_memo is not None and self._wait_memo[0] == key:
            return self._wait_memo[1]
        rows = reader.db.execute("SELECT * FROM events WHERE kind IN ('transport','plan','settings') AND seq<=? ORDER BY seq",
                                 (meta['seq'],))
        facts = wait_facts(reader.decode_event(row) for row in rows)
        if marker() == key:
            self._wait_memo = key, facts
        return facts

    def state(self, events=None, *, resolve=True, with_waits=True):
        state = initial()
        referenced = False
        relevant = []
        for event in self.events() if events is None else events:
            apply(state, event)
            if with_waits and event['kind'] in {'transport', 'plan', 'settings'}:
                relevant.append(event)
            if resolve and not referenced:
                keys = (('before', 'after') if event['kind'] in {'run.start', 'run.end'} else
                        ('snapshot',) if event['kind'] == 'deliver' else
                        ('candidate',) if event['kind'] == 'challenge' else ())
                referenced = any(isinstance(event['data'].get(key), dict) and
                                 'manifest' in event['data'][key] for key in keys)
        state["root"] = str(self.root)
        if resolve and referenced:
            from source_manifests import materialize_known
            state = materialize_known(state, self.root / 'artifacts')
        if with_waits:
            state = ReadState(state)
            state.wait_context = wait_facts(relevant)
        return state

    def append(self, kind, actor, data, event_id=None):
        return self.batch([(kind, actor, data, event_id)])[0]

    def batch(self, operations):
        require(not self._integrity_failed, "Full verification failed; no further writes on this connection")
        operations = list(operations)
        with ExitStack() as leases:
            for run_id in sorted({data["id"] for kind, _, data, _ in operations if kind == "run.abandon" or
                                  (kind == "run.end" and data.get("reconciled") is not None)}):
                leases.enter_context(run_lease(self.root, run_id))
            return self._batch_owned(operations)

    def _batch_owned(self, operations, recovering=False):
        from registry_prepare import PreparedCache, StalePreparation, identity, position
        from registry_radix import IntegrityError
        require(not self._cache_mismatch, 'Cache mismatch; run squadra doctor --repair')
        prepared = None
        try:
            for attempt in range(3):
                declarations = {data['id']: data for kind, _, data, _ in operations if kind == 'task'}
                closing = [data['task'] for kind, _, data, _ in operations if kind == 'close']
                gate = bool(closing) and any(declarations.get(name, self.view()['tasks'].get(name, {})).get('release_gate')
                                             for name in closing)
                audited = None
                if gate:
                    marker = identity(self), self.db.execute('PRAGMA data_version').fetchone()[0]
                    result = self.audit()
                    require(result['cache'] != 'mismatch', 'Cache mismatch; run squadra doctor --repair')
                    for _, _, data, _ in operations:
                        self.verify_artifacts(data)
                    events = result['events']
                    audited = (marker, (events[-1]['seq'], events[-1]['hash']) if events else (0, '0' * 64))
                try:
                    return self._commit_batch(operations, prepared, audited)
                except (IntegrityError, StalePreparation) as exc:
                    if attempt == 2:
                        raise sqlite3.OperationalError('registry busy, retry: database is locked')
                    if prepared is not None:
                        prepared.close()
                    prepared = None
                    if isinstance(exc, IntegrityError):
                        prepared = PreparedCache(self)
                        prepared.force_replacement = getattr(exc, 'force_replacement', False)
            raise sqlite3.OperationalError('registry busy, retry: database is locked')
        finally:
            if prepared is not None:
                prepared.close()

    def _commit_batch(self, operations, prepared, audited):
        from registry_prepare import StalePreparation, identity, position
        from registry_radix import IntegrityError
        if prepared is not None:
            prepared.attach(self)
        self.db.execute("BEGIN IMMEDIATE")
        loaded = False
        try:
            if audited is not None:
                marker = identity(self), self.db.execute('PRAGMA data_version').fetchone()[0]
                if marker != audited[0] or position(self) != audited[1]:
                    raise StalePreparation('Registry changed after release-gate audit')
            if prepared is not None and getattr(prepared, 'force_replacement', False):
                prepared.install(self)
            try:
                tree, meta = self._cached()
            except IntegrityError:
                if prepared is None:
                    raise
                prepared.install(self)
                tree, meta = self._cached()
            loaded = True
            state = tree.root
            previous = meta['head']
            last_seq = meta['seq']
            saved = []
            appended = []
            for kind, actor, data, event_id in operations:
                if kind == 'review' and state['tasks'].get(data.get('task'), {}).get('delivery_policy') == 'frozen-v1':
                    data = {**data, 'handoff': data.get('handoff', review_handoff(state, state['tasks'][data['task']], actor))}
                    data['request'] = data.get('request') or review_request(actor, data)
                    event_id = event_id or data['request']
                event_id = event_id or uuid.uuid4().hex
                from source_manifests import transform_event, transform_packet
                data = json.loads(canonical(transform_event(kind, data, self.compact_snapshot)))
                old = self.db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
                if old:
                    old_data = transform_event(old["kind"], json.loads(old["data"]), self.compact_snapshot)
                    require((old["kind"], old["actor"], canonical(old_data)) == (kind, actor, canonical(data)),
                            "Idempotency key reused with different data")
                    saved.append(event_id)
                    continue
                from source_manifests import transform_event, transform_packet
                checked_data = transform_event(kind, data, self.resolve_snapshot)
                if kind in {'deliver', 'reassign'} and 'packet_data' in checked_data:
                    checked_data = {**checked_data, 'packet_data': transform_packet(checked_data['packet_data'], self.resolve_snapshot)}
                if kind in {'run.start', 'run.end', 'deliver', 'reassign', 'review', 'close', 'challenge'}:
                    target = self.resolved(state)
                elif kind == 'availability' and data.get('ping'):
                    # A sweep re-checks that its ping is due, and only a delivered ping can be due.
                    rows = self.db.execute("SELECT * FROM events WHERE kind='transport' ORDER BY seq")
                    target = wait_view(state, wait_facts(self.decode_event(row) for row in rows))
                else:
                    target = state
                validate(target, kind, actor, checked_data, stored_packet=data.get("packet_data") if kind in {"deliver", "reassign"} else None)
                if kind == "run.abandon":
                    validate_abandonment_files(self, state["runs"][data["id"]], data)
                if kind == "run.end" and data.get("reconciled") is not None:
                    validate_reconcile_files(self, state["runs"][data["id"]], data)
                if kind == "decision" and "scrub" in checked_data:
                    validate_scrub_artifact(self.events(), checked_data["scrub"])
                from source_manifests import transform_event
                data = json.loads(canonical(transform_event(kind, data, self.compact_snapshot)))
                event = {"id": event_id, "utc": now(), "kind": kind, "actor": actor,
                         "data": data, "previous": previous, "format": 2}
                if (kind == 'reassign' or (kind == 'task' and data.get('delivery_policy') == 'frozen-v1') or
                        self.db.execute('SELECT 1 FROM events WHERE hash_format=3 LIMIT 1').fetchone() is not None):
                    event['format'] = 3
                # A restore of a package with no new-format event keeps the envelope the saving version can read.
                if kind == "restore.freeze" and self.db.execute(
                        "SELECT 1 FROM events WHERE hash_format IN (2,3) LIMIT 1").fetchone() is None:
                    del event["format"]
                h = digest(canonical(event).encode())
                cursor = self.db.execute("INSERT INTO events(id,utc,kind,actor,data,previous,hash,hash_format) VALUES(?,?,?,?,?,?,?,?)",
                                         (event_id, event["utc"], kind, actor, canonical(data), previous, h, event.get("format")))
                recorded = {**event, "seq": cursor.lastrowid}
                apply(state, recorded)
                import registry_current
                registry_current.advance(tree.current_tree.root, state, recorded)
                last_seq = cursor.lastrowid
                previous = h
                saved.append(event_id)
                appended.append({'event': event_id, 'seq': last_seq})
            from registry_cache import save
            save(self, tree, last_seq, previous)
            self.db.execute("COMMIT")
        except IntegrityError as exc:
            self.db.execute('ROLLBACK')
            exc.force_replacement = loaded
            raise
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        if appended:
            self.commit_count += len(appended)
            self.last_commit = appended[-1]
        self._trim_wal()
        return saved

    def repair_cache(self):
        """Explicitly rebuild derived tables; a saved package is not a writable workspace."""
        from registry_prepare import PreparedCache, StalePreparation, saved_registry
        require(not saved_registry(self.root),
                'Cache repair refused inside a saved package or checkpoint')
        writer = Store(self.root)
        try:
            for attempt in range(3):
                prepared = PreparedCache(writer)
                try:
                    prepared.attach(writer)
                    writer.db.execute('BEGIN IMMEDIATE')
                    try:
                        prepared.install(writer)
                        writer._cached()
                        writer.db.execute('COMMIT')
                    except BaseException:
                        writer.db.execute('ROLLBACK')
                        raise
                    checked = writer.audit()
                    require(checked['cache'] == 'matched', 'Cache repair did not match the journal')
                    self._cache_mismatch = False
                    return checked
                except StalePreparation:
                    pass
                finally:
                    prepared.close()
            raise sqlite3.OperationalError('registry busy, retry: database is locked')
        finally:
            writer.close()

    def artifact(self, path):
        path = Path(path).resolve(strict=True)
        require(path.is_file() and path.stat().st_size <= 16 * 1024 * 1024, "Artifact must be a file <=16MiB")
        return self.artifact_bytes(path.read_bytes(), path.name, str(path))

    def artifact_bytes(self, body, name, original):
        require(len(body) <= 16 * 1024 * 1024, "Artifact exceeds 16MiB")
        sha = digest(body)
        folder = self.root / "artifacts"
        folder.mkdir(mode=0o700, exist_ok=True)
        destination = folder / sha
        if destination.exists():
            require(digest(destination.read_bytes()) == sha, "Artifact corruption")
        else:
            atomic(destination, body)
        return {"sha256": sha, "name": name, "original": original, "bytes": len(body)}

    def verify_artifacts(self, value=None, scrubbed=None):
        """Every referenced artifact is present and unchanged; a recorded scrub decision excuses only an absent file."""
        state = self.state() if value is None else value
        def excused(sha):
            nonlocal scrubbed
            if scrubbed is None:
                scrubbed = scrub_index(self.events())
            return sha in scrubbed
        def walk(value):
            if isinstance(value, dict):
                if {"sha256", "name", "bytes", "original"} <= value.keys():
                    path = self.root / "artifacts" / value["sha256"]
                    if os.path.lexists(path) or not excused(value["sha256"]):
                        require(path.is_file() and digest(path.read_bytes()) == value["sha256"], "Missing/changed artifact " + value["name"])
                for v in value.values():
                    walk(v)
            elif isinstance(value, list):
                for v in value:
                    walk(v)
        walk(state)
        return True

    def publish(self):
        from registry_projections import publish
        require(not self.read_only, "Read-only store cannot publish projections")
        self._summary_context = None
        def summary():
            state = self.view()
            view = attention(state)
            self._summary_context = state, view
            index = current_index(state)
            en_summary = state["workspace"].get("lang", "it") == "en"
            if en_summary:
                lines = ["# Squadra · current summary", "", "Registry #" + str(state['event_count']),
                         "Full state/mail: use squadra export"]
            else:
                lines = ["# Squadra · riepilogo corrente", "", "Registro #" + str(state['event_count']),
                         "Stato e posta completi: squadra export"]
            lines += forwarding_lines(fold_standby(state, view['waiting']), state['workspace'].get('lang', 'it'))
            lines += ["", *composition_lines(composition(state, lang="en" if en_summary else "it"),
                                             "en" if en_summary else "it")]
            lines += ["", "| Window | Next step |" if en_summary else "| Finestra | Prossimo passo |", "|---|---|"]
            for actor in state['agents']:
                if index is not None:
                    from registry_current import latest_output
                    output = latest_output(index, state, actor)
                else:
                    output = next((o for o in reversed(state['outputs']) if o['agent'] == actor), {})
                lines.append("| " + cell(actor) + " | " + cell(output.get('next', '—')) + " |")
            atomic(self.root / 'stato.md', ('\n'.join(lines) + '\n').encode())
        return publish(self, summary=summary)

    def project(self, with_events=False, audited=False):
        require(not self.read_only, "Read-only store cannot export projections")
        require(not self._cache_mismatch, 'Cache mismatch; run squadra doctor --repair')
        if not audited:
            require(self.audit()['cache'] != 'mismatch', 'Cache mismatch; run squadra doctor --repair')
        # The export lock covers fresh reads and all derived files; concurrent exporters cannot regress state.
        lock = self.root / "projection.lock"
        with lock.open("a+b") as f:
            if os.name == "nt":
                import msvcrt
                if f.seek(0, 2) == 0:
                    f.write(b"0"); f.flush()
                f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX)
            try:
                self.db.execute("BEGIN")
                events = self.events()
                state = self.state(events)
                state['tasks'] = plain(self.resolved(state)['tasks'])
                state.pop("root", None)
                self.db.execute("COMMIT")
                atomic(self.root / "state.json", (canonical(state) + "\n").encode())
                atomic(self.root / "events.jsonl", b"".join((canonical(e) + "\n").encode() for e in events))
                # Join once to avoid repeatedly copying growing strings.
                mail = ["# Squadra — posta condivisa\n\nRegistro SQLite autorevole; consegna e ACK sono distinti.\n"]
                for m in state["messages"].values():
                    ack = m["ack"]["utc"] if m["ack"] else "NOT REQUIRED" if m.get("info") is True else "PENDING"
                    label = "INFO · " if m.get("info") is True else ""
                    mail.append(f"\n## {m['id']} · {m['from']} → {m['to']} · {label}ACK {ack}\n\n{m['body']}\n")
                atomic(self.root / "squadra-posta.md", "".join(mail).encode())
                daily = {}
                for e in events:
                    day = e["utc"][:10]
                    from registry_projections import render_daily
                    daily.setdefault(day, ["# Squadra — scambi del giorno UTC\n\nProiezione ricostruibile del registro comune; non una seconda fonte.\n"]).append(render_daily(e))
                folder = self.root / "posta"; folder.mkdir(mode=0o700, exist_ok=True)
                for day, parts in daily.items():
                    atomic(folder / ("exchange-" + day + ".md"), "".join(parts).encode())
                summary_state = wait_view({**state, 'root': str(self.root)}, wait_facts(events))
                summary_attention = attention(summary_state)
                self._summary_context = summary_state, summary_attention
                atomic(self.root / "stato.md", (table(summary_state, events=events,
                       attention_value=summary_attention, advice=True) + "\n").encode())
                from registry_projections import mark_full_export
                mark_full_export(self, events[-1]['seq'] if events else 0,
                                 events[-1]['hash'] if events else '0' * 64, locked=True)
                return (state, events) if with_events else state
            finally:
                if os.name == "nt":
                    f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)

    def checkpoint(self, destination, *, audit_source=True, report=None):
        from registry_checkpoint import CheckpointError, copy_immutable_tree, verify_tree
        require(not self._cache_mismatch, 'Cache mismatch; run squadra doctor --repair')
        if audit_source:
            require(self.audit()['cache'] != 'mismatch', 'Cache mismatch; run squadra doctor --repair')
        destination = Path(destination).resolve()
        require(not destination.exists(), "Checkpoint destination must be new")
        destination.mkdir(parents=True, mode=0o700)
        target = sqlite3.connect(destination / "registry.sqlite3")
        try:
            self.db.backup(target)
        finally:
            target.close()
        copied = {}
        try:
            for name in ("artifacts", "run-leases"):
                source = self.root / name
                if os.path.lexists(source):
                    copied[name] = copy_immutable_tree(source, destination / name,
                                                       skip_lease_locks=name == "run-leases")
        except (OSError, CheckpointError) as exc:
            raise Error("Checkpoint file copy refused: " + str(exc)) from exc
        from frozen_candidate import checkpoint as copy_candidates
        candidates = copy_candidates(self, destination)
        if report is not None:
            report['candidates'] = candidates
        check = Store(destination)
        try:
            checked = check.audit()
            require(checked['cache'] != 'mismatch', 'Cache mismatch; run squadra doctor --repair')
            events = checked["events"]
            if report is not None:
                report.update(event_count=len(events), event_head=events[-1]["hash"] if events else "0" * 64)
                if checked["scrubbed"]:
                    report["scrubbed"] = checked["scrubbed"]
            check.project(audited=True)
        finally:
            check.close()
        try:
            for name, manifest in copied.items():
                verify_tree(destination / name, manifest["files"])
        except (OSError, CheckpointError) as exc:
            raise Error("Checkpoint file verification refused: " + str(exc)) from exc
        hashes = {str(f.relative_to(destination)): digest(os.fsencode(os.readlink(f)) if f.is_symlink() else f.read_bytes()) for f in destination.rglob("*")
                  if (f.is_file() or f.is_symlink()) and f.name not in {"registry.sqlite3-wal", "registry.sqlite3-shm"}}
        atomic(destination / "CHECKSUMS.json", (canonical(hashes) + "\n").encode())
        return str(destination)


def cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def board_times(events):
    """Derive timing separately so replayed task state keeps its existing shape."""
    times = {}
    for event in events:
        data, kind = event["data"], event["kind"]
        if kind == "task":
            times[data["id"]] = {"assigned": event["utc"]}
        elif kind in {"start", "close"} and data.get("task") in times:
            if kind == "start":
                times[data["task"]].setdefault("started", event["utc"])
            elif kind == "close":
                times[data["task"]]["closed"] = event["utc"]
    return times


def board_time(task, timing, clock, en):
    """Round recorded elapsed time; estimates belong to the coordinator."""
    done = task["status"] == "done"
    start = timing.get("assigned") if done else timing.get("started", timing.get("assigned"))
    if not start or (done and not timing.get("closed")):
        return "—"
    end = dt.datetime.fromisoformat(timing["closed"]) if done else clock
    seconds = max(0, (end - dt.datetime.fromisoformat(start)).total_seconds())
    unit, label = (60, "min") if seconds < 3600 else (3600, "h") if seconds < 86400 else (86400, "days" if en else "giorni")
    amount = int(seconds / unit + 0.5)
    if amount == 1 and label in {"days", "giorni"}:
        label = "day" if en else "giorno"
    return f"{'about' if en else 'circa'} {amount} {label}"


def board(state, lang=None, read_at=None, events=(), *, live=True):
    """Build the progress table; saved views retain fixed durations, not running ages."""
    lang = lang or state["workspace"].get("lang", "it")
    require(lang in {"it", "en"}, "Board language must be it or en")
    clock = None
    if live:
        at = now() if read_at is None else read_at
        clock = dt.datetime.fromisoformat(at) if isinstance(at, str) else at
        require(clock.tzinfo is not None, "Timezone-aware clock required")
    index = current_index(state)
    times = index["task_timings"] if index is not None and not events else board_times(events)
    en = lang == "en"
    labels = {
        "waiting-ack": ("presa in carico da confermare", "awaiting assignment acknowledgement"),
        "ready": ("da avviare", "ready to start"),
        "working": ("in costruzione", "being built"),
        "delivered": ("consegnato: lettura del codice", "delivered: code review"),
        "changes-required": ("da correggere", "to be fixed"),
        "reviewed": ("da chiudere", "ready to close"),
        "done": ("chiuso", "closed"),
        "plan-read": ("piano in lettura", "plan review"),
        "plan-fix": ("piano da correggere", "plan to be fixed"),
        "plan-blocked": ("lettura del piano bloccata", "plan review blocked"),
        "gate-read": ("chiuso — da leggere al gate", "closed — read at the gate"),
    }
    roles = {"builder": ("costruttore", "builder"), "reviewer": ("lettore", "reader"),
             "coordinator": ("coordinatore", "coordinator"), "executor": ("esecutore", "executor")}
    tasks = [t for t in state["tasks"].values() if not t.get("superseded_by") and t["status"] != "withdrawn"]
    done = sorted((t for t in tasks if t["status"] == "done"), key=lambda t: t.get("status_utc", ""), reverse=True)
    active, todo = [], []
    for task in tasks:
        if task["status"] == "done":
            continue
        started = task["status"] not in {"waiting-ack", "ready"} or task.get("plan") or task.get("delivery") or task["reviews"]
        (active if started else todo).append(task)
    name = state["workspace"].get("name", "Squadra")
    mode = ("PAUSED" if state["paused"] else "ACTIVE") if en else ("IN PAUSA" if state["paused"] else "OPERATIVO")
    lines = [f"{name} · {'Registry' if en else 'Registro'} #{state['event_count']} · {mode}", ""]
    for heading, rows in zip(("In progress", "To do", "Done") if en else ("In corso", "Da fare", "Fatti"),
                             (active, todo, done[:10])):
        lines += ["## " + heading, "", "| Window | Work | State | Time (approx.) |" if en else "| Finestra | Lavoro | Stato | Tempo (circa) |", "|---|---|---|---|"]
        for task in rows:
            step = next_step(task)
            names = [task["owner"]] if task["status"] == "done" else step["who"]
            agent = state["agents"].get(next(iter(names), None))
            window = (" / ".join(names) + " (" + roles[step["role"]][en] + ")") if agent else ("Reader to assign" if en else "Lettore da assegnare")
            elapsed = "—" if rows is todo or (rows is active and not live) else board_time(task, times.get(task["id"], {}), clock, en)
            lines.append("| " + " | ".join(cell(v) for v in (window, task["title"], labels[step["phase"]][en], elapsed)) + " |")
        if not rows:
            lines.append("| — | " + ("No tasks" if en else "Nessun incarico") + " | — | — |")
        lines.append("")
    if len(done) > 10:
        lines += [("and " + str(len(done) - 10) + " more") if en else ("e altri " + str(len(done) - 10)), ""]
    lines.append(f"Done {len(done)} of {len(tasks)}" if en else f"Realizzati {len(done)} su {len(tasks)}")
    unread = sum(gate_pending(t) for t in done)
    if unread:
        lines.append(f"Closed without a read, to be read at the gate: {unread}" if en
                     else f"Chiusi senza lettura, da leggere al gate: {unread}")
    return "\n".join(lines)


def step_text(step, en):
    """A next step in the workspace language; a recorded deferral reason stays as written."""
    if not en:
        return step["text"]
    _, deferred, tail = step["text"].partition(" · rinviato da ")
    return STEP_TEXT_EN.get(step["phase"], step["text"]) + (" · deferred by " + tail if deferred else "")


def wait_text(wait, en):
    """A wait in the workspace language; the Italian sentence is the recorded one."""
    if not en:
        return wait["text"]
    kind = "pool" if wait["kind"] == "ack" and wait.get("handoffs") else wait["kind"]
    return WAIT_TEXT_EN.get(kind) or STEP_TEXT_EN.get(kind) or wait["text"]


MODEL_NAMES = {"claude": "Claude", "codex": "Codex"}
COMPOSITION_PAIRS = 6


def composition_windows(state):
    """Registered windows that can build or read, by identity; the coordinator's and those in stand-by are left out."""
    agents = state["agents"]
    coordinating = {a["identity"] for a in agents.values() if a["role"] == "coordinator"}
    found = {}
    for name, agent in agents.items():
        if (agent["role"] in {"builder", "reviewer"} and agent["identity"] not in coordinating
                and not standby(state, name)):
            found.setdefault(agent["identity"], agent["client"])
    return found


def composition_pairs(state):
    """For the newest open tasks of Claude or Codex builders: the reader of the other model, or the free candidates.

    A task with a recorded same-client reason, such as a final block's paired read, already says why its
    reader shares the owner's model, so it gets no advice."""
    agents, result = state["agents"], []
    open_tasks = [t for t in state["tasks"].values() if t["status"] != "done" and not t.get("superseded_by")
                  and t.get("same_client_reason") is None]
    for task in list(reversed(open_tasks))[:COMPOSITION_PAIRS]:
        own = agents[task["owner"]]["client"]
        if own not in MODEL_CLIENTS:
            continue
        other = MODEL_CLIENTS[1 - MODEL_CLIENTS.index(own)]
        named = [r for r in [task.get("reviewer"), *(task.get("pool") or [])] if r]
        crossing = [r for r in named if agents[r]["client"] == other]
        candidates = crossing or sorted(
            n for n, a in agents.items() if a["role"] == "reviewer" and a["client"] == other
            and a["identity"] != agents[task["owner"]]["identity"] and not standby(state, n))
        result.append({"task": task["id"], "builder": task["owner"], "client": own, "readers": candidates,
                       "named": bool(crossing)})
    return result


def composition_message(counts, en):
    """The advice for a count of Claude and Codex windows, and its case."""
    claude, codex = counts["claude"], counts["codex"]
    if claude + codex == 0:
        return "none", ("No Claude or Codex window can build or read yet: open one of each, so that one model "
                        "writes and the other reads." if en else
                        "Nessuna finestra Claude o Codex può ancora costruire o leggere: aprine una per tipo, "
                        "così un modello scrive e l'altro rilegge.")
    if not claude or not codex:
        only, missing = ("claude", "codex") if claude else ("codex", "claude")
        mine, theirs, size = MODEL_NAMES[only], MODEL_NAMES[missing], str(counts[only])
        if en:
            text = ("Only " + mine + " windows (" + size + "): no read by the other model is possible. Open one " +
                    theirs + " window to read; otherwise every read needs a recorded --same-client-reason.")
        else:
            text = ("Solo finestre " + mine + " (" + size + "): la rilettura con l'altro modello non è possibile. "
                    "Apri una finestra " + theirs + " che rilegga; altrimenti ogni lettura ha bisogno di un motivo "
                    "registrato con --same-client-reason.")
        if only == "codex":
            text += " Only a Claude window coordinates." if en else " Ricorda che coordina solo una finestra Claude."
        return "one-client", text
    if claude != codex:
        few, many = ("claude", "codex") if claude < codex else ("codex", "claude")
        lone, small, large = counts[few] == 1, MODEL_NAMES[few], MODEL_NAMES[many]
        if en:
            who = "The lone " + small + " window" if lone else "The " + str(counts[few]) + " " + small + " windows"
            text = (who + " will read everything the " + str(counts[many]) + " " + large + " windows build and "
                    "become the bottleneck. Give " + ("it" if lone else "one of them") + " a build lane read by a " +
                    large + " window, or open one more " + small + " window.")
        else:
            who = ("L'unica finestra " + small + " rileggerà" if lone else
                   "Le " + str(counts[few]) + " finestre " + small + " rileggeranno")
            text = (who + " tutto quello che costruiscono le " + str(counts[many]) + " finestre " + large + " e " +
                    ("diventerà" if lone else "diventeranno") + " il collo di bottiglia. " +
                    ("Affidale" if lone else "Affida a una di loro") + " un lavoro da costruire, riletto da una "
                    "finestra " + large + ", oppure apri un'altra finestra " + small + ".")
        return "imbalance", text
    return "balanced", ("Balanced: " + str(claude) + " Claude and " + str(codex) + " Codex. Pair each builder with "
                        "a reader of the other model." if en else
                        "In equilibrio: " + str(claude) + " Claude e " + str(codex) + " Codex. Abbina a ogni "
                        "costruttore un lettore dell'altro modello.")


def composition(state=None, observed=None, lang="it", scan="observed"):
    """How the Claude and Codex windows can split the work under the cross-model rule.

    Advice only: it assigns, changes and refuses nothing. It counts the registered windows, or, before any
    registration, the processes a census saw; observed is that census list, and a partial scan is said, never filled in."""
    en = lang == "en"
    if state is not None:
        clients = list(composition_windows(state).values())
        source = ("registered windows (the coordinator's and those in stand-by left out)" if en else
                  "finestre registrate (esclusa quella del coordinatore e quelle in stand-by)")
    else:
        clients = [p["client"] for p in observed or []]
        source = ("census only: processes seen in the project, roles not known yet, the coordinator's window "
                  "among them" if en else
                  "solo censimento: processi visti nel progetto, ruoli non ancora noti, tra cui la finestra "
                  "del coordinatore")
    counts = {model: clients.count(model) for model in MODEL_CLIENTS}
    seen = None
    if state is not None and observed is not None:
        seen = {model: sum(p["client"] == model for p in observed) for model in MODEL_CLIENTS}
        source += (("; the census also sees " if en else "; il censimento vede anche ") +
                   ", ".join(str(seen[m]) + " " + MODEL_NAMES[m] for m in MODEL_CLIENTS) +
                   (" processes (a process is not a registered window)" if en else
                    " processi (un processo non è una finestra registrata)"))
    if observed is not None and scan != "observed":
        source += ("; the census is partial and may not see every window" if en else
                   "; il censimento è parziale e può non vedere tutte le finestre")
    case, message = composition_message(counts, en)
    return {"claude": counts["claude"], "codex": counts["codex"],
            "other": sum(c not in MODEL_CLIENTS for c in clients), "source": source, "observed": seen,
            "case": case, "message": message,
            "pairs": composition_pairs(state) if case == "balanced" and state is not None else []}


def composition_lines(value, lang="it"):
    """The composition as plain lines for status and stato.md."""
    en = lang == "en"
    other = (", " + str(value["other"]) + (" other" if en else " altre")) if value["other"] else ""
    out = [("Composition: " if en else "Composizione: ") + str(value["claude"]) + " Claude, " + str(value["codex"]) +
           " Codex" + other + " · " + ("source: " if en else "fonte: ") + value["source"],
           ("Advice: " if en else "Consiglio: ") + value["message"]]
    for pair in value["pairs"]:
        readers = ", ".join(pair["readers"]) or ("no free reader of the other model" if en else
                                                  "nessun lettore libero dell'altro modello")
        out.append("- " + pair["task"] + ": " + pair["builder"] + " (" + MODEL_NAMES[pair["client"]] + ") → " +
                   readers + ("" if pair["named"] else (" (to name)" if en else " (da nominare)")))
    return out


def table(state, read_at=None, events=(), *, compact=False, lang=None, attention_value=None, advice=False):
    """Print the board and the owner, wait and next-step blocks in the workspace language.

    advice adds the composition advice; status and stato.md show it, the answers of other commands stay short."""
    lang = lang or state["workspace"].get("lang", "it")
    require(lang in {"it", "en"}, "Board language must be it or en")
    en = lang == "en"
    tasks = list(state["tasks"].values())
    view = attention(state) if attention_value is None else attention_value
    lines = [board(state, lang, read_at=read_at, events=events, live=read_at is not None), "",
             *([*composition_lines(composition(state, lang=lang), lang), ""] if advice else []),
             "Needs you:" if en else "Serve a te:"]
    information = information_count(state)
    if information:
        lines.insert(1, ("Information messages with no confirmation required: " if en
                         else "Informazioni senza conferma richiesta: ") + str(information))
    for item in view["for_owner"]:
        question = OWNER_TEXT_EN.get(item["kind"], item["question"]) if en else item["question"]
        lines.append("- " + question + (" · " + item["task"] if item.get("task") else ""))
        for option in item.get("options", []):
            lines.append("  " + option["id"] + ": " + option["label"] + (" · Say: " if en else " · Frase: ") + option["say"])
    if not view["for_owner"]:
        lines.append("No request recorded — a question asked only in chat does not appear here." if en
                     else "Nessuna richiesta registrata — una domanda fatta solo in chat non compare qui.")
    lang = state['workspace'].get('lang', 'it')
    if read_at is None:
        lines += forwarding_lines(fold_standby(state, view['waiting']), lang)
    if read_at is not None:
        lines += ["", ("Waits computed at " if en else "Attese calcolate al ") + str(read_at) + ":"]
        waiting = waits(state, read_at, attention_value=view)
        held = [w for w in waiting if w['kind'] == 'stand-by']
        waiting = [w for w in waiting if w['kind'] != 'stand-by']
        if compact:
            counts = {kind: sum(w['kind'] == kind for w in waiting)
                      for kind in sorted({w['kind'] for w in waiting})}
            lines.append(str(len(waiting)) + (" in total · " if en else " totali · ")
                         + ", ".join(k + ": " + str(v) for k, v in counts.items()))
            active = {state['agents'][actor]['identity'] for step in view['next_steps']
                      if state['tasks'][step['task']]['status'] != 'done'
                      for actor in step['who'] if actor in state['agents']}

            def blocking(wait):
                if wait['kind'] == 'alive':
                    return any(state['agents'].get(actor, {}).get('identity') in active
                               for actor in wait['who'])
                if wait['kind'] != 'ack':
                    return True
                message = state['messages'].get(wait['id'], {})
                task = state['tasks'].get(wait.get('task') or message.get('task') or message.get('related_task'), {})
                if not task:
                    return False
                if message.get('task') and task['status'] == 'waiting-ack':
                    return True
                delivery = task.get('delivery', {})
                return task['status'] == 'delivered' and (wait['id'] == delivery.get('handoff')
                    or wait['id'] in delivery.get('handoffs', []))

            grouped = {}
            for wait in fold_forward_waits(waiting):
                if not blocking(wait):
                    continue
                message = state['messages'].get(wait.get('id'), {})
                task = wait.get('task') or message.get('task') or message.get('related_task')
                key = (wait['kind'], wait['id'] if wait['kind'] == 'to-forward' else task or wait.get('id', ''))
                previous = grouped.get(key)
                oldest = wait if previous is None or wait['seconds'] > previous['seconds'] else previous
                grouped[key] = {**oldest, 'count': 1 + (previous['count'] if previous else 0)}
            priority = {'to-forward': -1, 'ack': 0, 'plan': 0, 'delivered': 0, 'reviewed': 0, 'alive': 1, 'cleanup-unknown': 2, 'heartbeat': 3}
            waiting = sorted(grouped.values(), key=lambda w: (priority.get(w['kind'], 2),
                             -w['seconds'], w['kind'], str(w.get('task', w.get('id', '')))))[:5]
        lines += ["- " + w["kind"] + " · " + str(w['id'] if w['kind'] == 'to-forward' else w.get("task", w.get("id", ""))) + " · " + str(w["seconds"]) + "s"
                  + ((" (" + str(w['count']) + (" waits, the oldest)" if en else " attese, la più vecchia)"))
                     if w.get('count', 1) > 1 else "")
                  + " · " + (w['text_en'] if en and w.get('text_en') else wait_text(w, en))
                  + (' · ' + w['ack_command'] if w.get('ack_command') else '') for w in waiting]
        lines += standby_lines(held, 'en' if en else 'it')
        if compact:
            lines.append("All waits and details: squadra status --all." if en
                         else "Tutte le attese e i dettagli: squadra status --all.")
    if compact:
        steps = [item for item in view['next_steps'] if item['status'] != 'done']
        lines += ["", "Next step per task:" if en else "Prossimo passo per incarico:",
                  "| Task | Window / role | Next step |" if en else "| Incarico | Finestra / ruolo | Prossimo passo |",
                  "|---|---|---|"]
        for item in steps[:5]:
            who = ', '.join(item['who']) or ('To assign' if en else 'Da assegnare')
            lines.append('| ' + ' | '.join(cell(value) for value in
                         (item['task'], who + ' / ' + item['role'], step_text(item, en))) + ' |')
        if not steps:
            lines.append('| — | — | No pending step |' if en else '| — | — | Nessun passo pendente |')
        if len(steps) > 5:
            lines.append((str(len(steps) - 5) + ' more steps: squadra status --all.') if en
                         else ('Altri ' + str(len(steps) - 5) + ' passi: squadra status --all.'))
        return "\n".join(lines)
    lines += ["", "Next step per task:" if en else "Prossimo passo per incarico:"]
    lines += ["- " + item["task"] + ": " + step_text(item, en) for item in view["next_steps"]]
    if view["superseded"]:
        lines += ["", "Replaced (history kept, outside next steps and waits):" if en
                  else "Sostituiti (storia conservata, fuori dai prossimi passi e dalle attese):"]
        lines += ["- " + item["task"] + " → " + item["by"] for item in view["superseded"]]
    lines += ["",
             "| Window / role | Task / candidate | State / proof | Skill | Next step |" if en
             else "| Finestra / ruolo | Incarico / candidato | Stato / prova | Skill | Prossimo passo |",
             "|---|---|---|---|---|"]
    for a in state["agents"].values():
        owned = [t for t in tasks if t["owner"] == a["id"]]
        read = [t for t in tasks if any(r["reviewer"] == a["id"] for r in t["reviews"])]
        index = current_index(state)
        if index is not None:
            from registry_current import latest_output, related
            output = latest_output(index, state, a["id"])
            linked = lambda task: related(index, a["id"], task)
        else:
            output = next((o for o in reversed(state["outputs"]) if o["agent"] == a["id"]), {})
            linked = lambda task: any(m.get("related_task") == task and m["to"] == a["id"] for m in state["messages"].values())
        mentioned = [t for t in tasks if t["id"] == output.get("task") or t.get("coordinator") == a["id"] or
                     linked(t["id"])]
        relevant = owned or read or mentioned
        work = ", ".join(t["id"] + (" @ " + t["candidate"]["head"][:10] if t.get("candidate") else "") for t in relevant) or (
            "No task assigned" if en else "Nessun incarico assegnato")
        status = ", ".join(t["status"] for t in owned) or ("Recorded" if en else "Registrato")
        if relevant:
            status = ", ".join(t["status"] + ((" / latest proof " if en else " / ultima prova ") + t["proofs"][-1]["status"]
                                              + " rc=" + str(t["proofs"][-1]["native_rc"]) + " @"
                                              + t["proofs"][-1].get("after", {}).get("sha256", "unknown" if en else "ignota")[:8]
                                              if t["proofs"] else (" / proof pending" if en else " / prova pendente"))
                               for t in relevant)
        if standby(state, a["id"]):
            status = "STAND-BY · " + status
        declared = ("Declared by the window · output " if en else "Dichiarato dalla finestra · output ")
        values = [a["id"] + " / " + a["role"] + " / " + a["client"], work, status,
                  ", ".join(s["name"] + "@" + s["sha256"][:8] for s in a.get("skills", [])) or ("Not declared" if en else "Non dichiarate"),
                  (declared + output["event"][:8] + ": " if output else "") + output.get("next", "Not recorded yet" if en else "Da registrare")]
        lines.append("| " + " | ".join(cell(v) for v in values) + " |")
    held = [a["id"] for a in state["agents"].values() if standby(state, a["id"])]
    if held:
        lines += ["", ("Windows on stand-by: " + ", ".join(held) + ". No new tasks; work and reservations kept.") if en
                  else ("Finestre in stand-by: " + ", ".join(held) + ". Nessun nuovo incarico; lavoro e riserve conservati.")]
    if read_at is not None and (bool(current_index(state)["recipient_ping"]) if current_index(state) is not None else any(m.get("ping") for m in state["messages"].values())):
        lines += ["", "Declared presence and replies (not proof of a live process):" if en
                  else "Presenza dichiarata e risposte (non prova di processo vivo):"]
        for row in presence(state, read_at):
            lines.append("- " + row["agent"] + " · " + row["availability"] + (" · last reply " if en else " · ultima risposta ")
                         + str(row["last_answer"])
                         + (" · ping " + row["ping"] + " · " + str(row["seconds"]) + "s / " + str(row["threshold"])
                            + "s · " + ((("ACK received" if en else "ACK ricevuto") if row["answer"]
                                         else ("reply pending" if en else "risposta pendente"))) if row["ping"] else ""))
    pending = len(pending_ack_ids(state, attention_value=view))
    if en:
        lines += ["", f"Messages without ACK: {pending}. Active reservations: {len(state['resources'])}.",
                  "Registration and heartbeat do not prove that the client ran, accepted or woke up."]
    else:
        lines += ["", f"Messaggi senza ACK: {pending}. Riserve attive: {len(state['resources'])}.",
                  "Registrazione e heartbeat non dimostrano esecuzione, accettazione o risveglio del client."]
    return "\n".join(lines)
