"""Store squad records in SQLite and generate Markdown views."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
import unicodedata


VERSION = "3.9.0"
WAIT_SECONDS = {"ack": 900, "delivered": 3600, "reviewed": 1800, "heartbeat": 1800, "alive": 300}
CHANNELS = ("typed-here", "relayed")
REFERENCE_KINDS = ("ask", "decision")
# Decisions recorded without --id are named dec-e<event sequence>; explicit IDs cannot take that form.
GENERATED_DECISION_ID = re.compile(r"^dec-e[0-9]+$")
BRIEF_BYTES = 8192

TASK_STATUSES = ("waiting-ack", "ready", "working", "delivered", "changes-required", "reviewed", "done")
NEXT_STEPS = {
    "waiting-ack": "ACK dell'esecutore",
    "ready": "Avvio dell'esecutore",
    "working": "Prova e consegna dell'esecutore",
    "delivered": "Lettura non autore",
    "changes-required": "Correzione dell'esecutore",
    "reviewed": "Check e chiusura del coordinatore",
    "done": "Nessun passo pendente",
}


FINDINGS_BYTES = 16 * 1024
SEVERITIES = ('grave', 'minor', 'info')


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


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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


def git(repo, *args):
    p = subprocess.run(["git", *args], cwd=repo, capture_output=True, timeout=30)
    if p.returncode:
        raise Error(p.stderr.decode(errors="replace").strip())
    return p.stdout


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


def decision_id(data, seq):
    """Use the supplied decision ID or a stable name from its event number."""
    return data.get("id") or (None if seq is None else "dec-e" + str(seq))


def read_at_gate(task):
    """A light task registered to be read at a release gate instead of on its own."""
    return task.get("read_at_gate") is True and task.get("route") == "light"


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
        state["messages"][d["message"]]["transport"] = d
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


def latest_ping(state, agent):
    identity = state["agents"][agent]["identity"]
    return next((m for m in reversed(list(state["messages"].values()))
                 if m.get("ping") and state["agents"][m["to"]]["identity"] == identity), None)


def presence(state, at, settings=None):
    """Read-time observations; neither missing replies nor elapsed time change availability."""
    clock = dt.datetime.fromisoformat(at) if isinstance(at, str) else at
    require(clock.tzinfo is not None, "Timezone-aware clock required")
    threshold = (WAIT_SECONDS | state["workspace"].get("wait_seconds", {}) | (settings or {}))["alive"]
    require(type(threshold) is int and threshold >= 0, "Nonnegative integer alive seconds required")
    result = []
    for agent in state["agents"].values():
        replies = [m["ack"]["utc"] for m in state["messages"].values()
                   if m["ack"] and state["agents"][m["to"]]["identity"] == agent["identity"]]
        ping = latest_ping(state, agent["id"])
        row = {"agent": agent["id"], "identity": agent["identity"],
               "availability": "stand-by" if standby(state, agent["id"]) else "active",
               "last_answer": max(replies, default=None), "threshold": threshold,
               "as_of": clock.isoformat(), "ping": None, "due": False}
        if ping:
            seconds = max(0, int((clock - dt.datetime.fromisoformat(ping["utc"])).total_seconds()))
            current = agent.get("availability", {})
            row.update(ping=ping["id"], seconds=seconds, answer=ping["ack"],
                       due=not ping["ack"] and seconds >= threshold and
                       (standby(state, agent["id"]) or ping.get("availability_event") == current.get("event")))
        result.append(row)
    return result


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
        who = [task["claim"]["agent"]] if task.get("claim") else list(task["pool"])
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
    # A superseded task loses only its own advancement items: its assignment ACK wait, its
    # status/plan/dependency/budget items and its next step. Other message waits, run alerts
    # and every open ask stay visible; dependents of it stay blocked.
    superseded = {t["id"]: t for t in state["tasks"].values() if t.get("superseded_by")}
    for message in state["messages"].values():
        # Pool handoffs wait once per delivery, below; a claimed pool's siblings stay unacknowledged.
        if not message["ack"] and message.get("task") not in superseded and not message.get("pool"):
            waiting.append({"kind": "ack", "id": message["id"], "who": [message["to"]],
                            "utc": message["utc"], "text": "Presa in carico da confermare"})
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
            readers = [task["claim"]["agent"]] if task.get("claim") else list(task["pool"])
        handoffs = task.get("delivery", {}).get("handoffs")
        if handoffs and not task.get("claim"):
            waiting.append({**common, "kind": "ack", "id": handoffs[0], "handoffs": list(handoffs),
                            "who": list(task["pool"]), "utc": task.get("delivered_utc"),
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
            waiting.append({**common, "kind": "plan", "who": readers, "text": "Piano in attesa di lettura non autore"})
        exhausted = []
        for stage in ("plan", "code"):
            reads = [r for r in task["reviews"] if r["stage"] == stage]
            if task["rounds"][stage] >= state["workspace"]["limits"][stage] and reads and reads[-1]["verdict"] != "accepted":
                exhausted.append(stage)
        builds = [r for r in state["runs"].values() if r["task"] == task["id"] and r["mode"] == "build"]
        code_reviews = [r for r in task["reviews"] if r["stage"] == "code"]
        accepted_code = (status == "reviewed" and code_reviews and code_reviews[-1]["verdict"] == "accepted"
                         and code_reviews[-1]["binding"] == task.get("candidate", {}).get("sha256"))
        if not accepted_code and len(builds) >= state["workspace"]["limits"]["build"] and builds[-1]["status"] not in {"running", "passed"}:
            exhausted.append("build")
        if exhausted and not any(a.get("task") == task["id"] for a in open_asks):
            owners.append({**common, "kind": "budget", "stages": exhausted,
                           "question": "Budget esaurito: decidere se estendere il perimetro o fermare"})
    for run in state["runs"].values():
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
    return {"for_owner": owners, "decided": decisions, "waiting": waiting, "next_steps": steps,
            "superseded": replaced}


def waits(state, at, settings=None):
    """Calculate ages on a caller-supplied UTC clock; alerts never perform actions."""
    thresholds = WAIT_SECONDS | state["workspace"].get("wait_seconds", {}) | (settings or {})
    require(set(thresholds) == set(WAIT_SECONDS) and all(type(v) is int and v >= 0 for v in thresholds.values()),
            "Nonnegative wait thresholds required")
    clock = dt.datetime.fromisoformat(at) if isinstance(at, str) else at
    require(clock.tzinfo is not None, "Timezone-aware clock required")
    result = []
    items = [w for w in attention(state)["waiting"] if w["kind"] in {"ack", "delivered", "reviewed", "cleanup-unknown"}]
    items += [{"kind": "heartbeat", "id": r["id"], "task": r["task"], "who": [r["agent"]],
               "utc": r.get("heartbeat", r["utc"]), "text": "Heartbeat vecchio; presenza del processo non dedotta"}
              for r in state["runs"].values() if r["status"] == "running"]
    for item in items:
        if not item.get("utc"):
            continue
        stamp = dt.datetime.fromisoformat(item["utc"])
        require(stamp.tzinfo is not None, "Timezone-aware event required")
        seconds = max(0, int((clock - stamp).total_seconds()))
        threshold = thresholds.get(item["kind"], 0)
        if seconds >= threshold:
            result.append({**item, "seconds": seconds, "threshold": threshold, "as_of": clock.isoformat()})
    for row in presence(state, clock, thresholds):
        if row["due"] and row["availability"] == "active" and row["agent"] == state["messages"][row["ping"]]["to"]:
            result.append({"kind": "alive", "id": row["ping"], "who": [row["agent"]],
                           "seconds": row["seconds"], "threshold": thresholds["alive"],
                           "as_of": clock.isoformat(), "text": "Ping senza risposta; stand-by richiede sweep del coordinatore"})
    return result


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


def current_proofs(task, current):
    return [p for p in task["proofs"] if p["status"] == "passed" and p["after"] == current
            and contract_matches(task, p)]


def candidate_checks(task, current, require_review=True, delivering=False):
    if not delivering:
        require(current == task.get("candidate"), "Candidate drift; deliver/prove the new exact source")
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
    not its author, and every path the task changed has its exact delivered bytes in the gate.
    """
    read = last_code_read(gate)
    if read.get("verdict") != "accepted" or read.get("binding") != current["sha256"]:
        read = {}
    reader = state["agents"].get(read.get("reviewer"), {}).get("identity")
    rows = {r["path"]: r for r in current["files"] if r["kind"] != "deleted"}
    unread, missing = [], []
    for task in state["tasks"].values():
        if not gate_pending(task) or gate_deferred(task, gate["id"]):
            continue
        if (task["id"] not in read.get("gate_covers", []) or reader is None or
                reader in {state["agents"][a]["identity"] for a in task["authors"]}):
            unread.append(task["id"])
        delivered = {r["path"]: r for r in task["candidate"]["files"] if r["kind"] != "deleted"}
        missing += [task["id"] + " (" + c["path"] + ")" for c in task["delivery"]["light"]["changes"]
                    if delivered.get(c["path"]) != rows.get(c["path"])]
    problems = (["gate_read_missing: " + ", ".join(unread)] if unread else []) + (
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
    held = sorted(name for name, r in state["resources"].items()
                  if r["owner"] == task["owner"] or (identity and state["agents"].get(r["owner"], {}).get("identity") == identity))
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
            runs[d.get("id")] = {"task": task, "mode": d.get("mode"), "gate": bool(d.get("gate"))}
        elif kind == "run.end" and runs.get(d.get("id"), {}).get("mode") == "proof":
            run, after = runs[d["id"]], d.get("after") or {}
            node = add("event:" + str(seq), "proof", event, seq, run=d["id"], status=d.get("status"),
                       native_rc=d.get("native_rc"), counts=d.get("counts"), error=d.get("error"),
                       after_sha=after.get("sha256"), after_head=after.get("head"), gate=run["gate"])
            step(run["task"], node, [])
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
        elif kind == "run.end" and runs.get(d["id"], {}).get("mode") == "proof" and runs[d["id"]].get("task") in facts:
            facts[runs[d["id"]]["task"]]["proofs"].append({**d, **mark})
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


def validate(state, kind, actor, d):
    require(kind in {"init", "agent", "task", "ack", "message", "transport", "plan", "start", "deliver",
                    "review", "close", "output", "run.start", "run.end", "run.cancel", "run.progress",
                    "resource.acquire", "resource.release", "pause", "resume", "legacy", "decision", "ask", "diagnostic",
                    "settings", "save", "restore.check", "restore.freeze", "challenge", "benchmark", "brief",
                    "availability", "supersede", "route", "fix",
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
                "availability", "supersede", "route", "fix"}:
        require(coordinator, "Coordinator role required")
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
                observed = next(r for r in presence(state, now()) if r["agent"] == d["agent"])
                require(observed["due"], "Ping no longer overdue; read current answer and alive threshold")
            else:
                require(return_ping(state, d["agent"]), "Return needs ACK of a post-stand-by ping")
    if kind == "settings":
        thresholds = d.get("wait_seconds")
        require(isinstance(thresholds, dict) and set(thresholds) <= set(WAIT_SECONDS), "Unknown wait threshold")
        require(all(type(v) is int and v >= 0 for v in thresholds.values()), "Nonnegative integer seconds required")
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
        validate_decision_links(state, d)
    if kind in {"start", "deliver", "run.start", "resource.acquire", "review"}:
        require(not state["paused"], "Workspace paused; explicit coordinator resume required")
    if kind == "task":
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
            require(not task.get("reviewer") or not standby(state, task["reviewer"]),
                    "Reviewer is in stand-by; no new review handoff")
            for member in task.get("pool") or []:
                require(not standby(state, member), "Reviewer is in stand-by; no new review handoff: " + member)
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
                if task.get("pool"):
                    # One handoff per member, each named after this exact packet.
                    key = "review-" + digest(canonical(packet).encode()) + "-"
                    require(d.get("handoffs") == [key + member for member in task["pool"]],
                            "Pool handoffs must name every member for this exact packet")
                require(packet.get("proofs") == [p for p in task["proofs"] if p["status"] == "passed" and
                        p["after"] == d["snapshot"] and contract_matches(task, p)], "Handoff proofs changed during delivery")
        if kind == "run.start":
            require(d["id"] not in state["runs"], "Run ID already exists; do not replay")
            require(task["status"] != "waiting-ack", "Assignment has no recipient ACK")
            if d["mode"] == "build":
                require(actor == task["owner"], "Only assigned builder can build")
                require(state["agents"][actor]["role"] != "reviewer", "Reviewer cannot become a builder silently")
                require(sum(r["task"] == d["task"] and r["mode"] == "build" for r in state["runs"].values())
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
                require(sum(r["task"] == d["task"] and r["mode"] == "read" and r.get("stage", "code") == stage
                            for r in state["runs"].values()) < state["workspace"]["limits"][stage],
                        "Independent CLI read budget exhausted")
                handoff = task.get("delivery", {}).get("handoff")
                if handoff and stage == "code":
                    require(actor == task["reviewer"] and state["messages"][handoff]["ack"] is not None,
                            "Configured reviewer must ACK exact handoff before read")
                elif task.get("delivery", {}).get("handoffs") and stage == "code":
                    require_claim(state, task, actor, "read")
            if d.get("gate"):
                require(state["resources"].get("gate", {}).get("owner") == actor,
                        "Acquire exclusive gate resource first")
                require(not any(r.get("gate") and r["status"] == "running" for r in state["runs"].values()),
                        "A whole gate is already running, including under the same owner")
        if kind == "review":
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
                handoff = task.get("delivery", {}).get("handoff")
                if handoff:
                    require(actor == task["reviewer"] and state["messages"][handoff]["ack"] is not None,
                            "Configured reviewer must ACK exact handoff before review")
                elif task.get("delivery", {}).get("handoffs"):
                    require_claim(state, task, actor, "review")
                if d["verdict"] == "accepted":
                    require(set(d.get("covers", [])) == {r["id"] for r in task.get("requirements", [])},
                            "Accepted review must cover every requirement explicitly")
            if "gate_covers" in d:
                check_gate_covers(state, task, actor, d)
        if kind == "close":
            require(task["status"] == "reviewed" or (read_at_gate(task) and task["status"] == "delivered"),
                    "Exact candidate independent review still pending")
            if task.get("release_gate") is True:
                gate_checks(state, task, snapshot(task["repo"], state.get("root")))
        if task.get("route") == "light" or task.get("contract"):
            from light_route import validate_event
            validate_event(state, kind, task, d)
    if kind == "ack":
        require(d["message"] in state["messages"], "Unknown message")
        require(state["messages"][d["message"]]["to"] == actor, "ACK must come from actual recipient seat")
        require(state["messages"][d["message"]]["ack"] is None, "Message already acknowledged")
        siblings = state["messages"][d["message"]].get("pool") or []
        winner = next((state["messages"][m]["ack"]["agent"] for m in siblings
                       if m != d["message"] and state["messages"].get(m, {}).get("ack")), None)
        require(winner is None, "review_claimed: " + str(winner) + " holds the read of "
                + str(state["messages"][d["message"]].get("related_task")))
    if kind == "message":
        require(isinstance(d.get("to"), str) and d["to"].strip(), "Nonempty recipient required")
        require(d["to"] not in state["agents"] if d.get("awaited") else d["to"] in state["agents"],
                "Awaited recipient already registered" if d.get("awaited") else "Recipient not registered")
        require(d["id"] not in state["messages"], "Message ID already exists")
        if d.get("pool") is not None:
            related = state["tasks"].get(d.get("related_task"), {})
            require(d["pool"] == related.get("delivery", {}).get("handoffs") and d["id"] in d["pool"],
                    "Pool handoff must belong to the current pool delivery")
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
        require(state["messages"][d["message"]]["from"] == actor, "Sender required")
    if kind in {"run.end", "run.cancel", "run.progress"}:
        require(d["id"] in state["runs"], "Unknown run")
        run = state["runs"][d["id"]]
        require(actor == run["agent"] or coordinator, "Run owner/coordinator required")
        require(run["status"] == "running", "Run already ended")
        task = state["tasks"][run["task"]]
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
    if kind == "resource.release":
        require(state["resources"].get(d["name"], {}).get("owner") == actor, "Only resource owner can release")


class Store:
    def __init__(self, root, create=False, read_only=False):
        self.root = Path(root).resolve()
        if create:
            self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        require(self.root.is_dir(), "Unknown workspace: " + str(self.root))
        self.path = self.root / "registry.sqlite3"
        require(not self.path.is_symlink(), "Registry symlink refused")
        if not create:
            require(self.path.is_file(), "Workspace not initialized")
        self.db = sqlite3.connect(self.path.as_uri() + "?mode=ro" if read_only else self.path,
                                  uri=read_only, timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        if read_only:
            return
        self.db.execute("PRAGMA journal_mode=WAL")
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
        os.chmod(self.path, 0o600)

    def close(self):
        self.db.close()

    def events(self):
        previous, result = "0" * 64, []
        for row in self.db.execute("SELECT * FROM events ORDER BY seq"):
            event = {k: row[k] for k in ["id", "utc", "kind", "actor", "previous"]}
            event["data"] = json.loads(row["data"])
            require(event["previous"] == previous and digest(canonical(event).encode()) == row["hash"],
                    "Event integrity failure at sequence " + str(row["seq"]))
            previous = row["hash"]
            result.append({**event, "hash": row["hash"], "seq": row["seq"]})
        return result

    def state(self, events=None):
        state = initial()
        for event in self.events() if events is None else events:
            apply(state, event)
        state["root"] = str(self.root)
        return state

    def append(self, kind, actor, data, event_id=None):
        return self.batch([(kind, actor, data, event_id)])[0]

    def batch(self, operations):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            events = self.events()
            state = initial()
            for event in events:
                apply(state, event)
            state["root"] = str(self.root)
            previous = events[-1]["hash"] if events else "0" * 64
            saved = []
            for kind, actor, data, event_id in operations:
                event_id = event_id or uuid.uuid4().hex
                old = self.db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
                if old:
                    require((old["kind"], old["actor"], old["data"]) == (kind, actor, canonical(data)),
                            "Idempotency key reused with different data")
                    saved.append(event_id)
                    continue
                validate(state, kind, actor, data)
                event = {"id": event_id, "utc": now(), "kind": kind, "actor": actor,
                         "data": data, "previous": previous}
                h = digest(canonical(event).encode())
                cursor = self.db.execute("INSERT INTO events(id,utc,kind,actor,data,previous,hash) VALUES(?,?,?,?,?,?,?)",
                                         (event_id, event["utc"], kind, actor, canonical(data), previous, h))
                apply(state, {**event, "seq": cursor.lastrowid})
                previous = h
                saved.append(event_id)
            self.db.execute("COMMIT")
            return saved
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

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

    def verify_artifacts(self):
        state = self.state()
        def walk(value):
            if isinstance(value, dict):
                if {"sha256", "name", "bytes", "original"} <= value.keys():
                    path = self.root / "artifacts" / value["sha256"]
                    require(path.is_file() and digest(path.read_bytes()) == value["sha256"], "Missing/changed artifact " + value["name"])
                for v in value.values():
                    walk(v)
            elif isinstance(value, list):
                for v in value:
                    walk(v)
        walk(state)
        return True

    def project(self, with_events=False):
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
                state = initial()
                for event in events:
                    apply(state, event)
                self.db.execute("COMMIT")
                atomic(self.root / "state.json", (canonical(state) + "\n").encode())
                atomic(self.root / "events.jsonl", b"".join((canonical(e) + "\n").encode() for e in events))
                # Join once to avoid repeatedly copying growing strings.
                mail = ["# Squadra — posta condivisa\n\nRegistro SQLite autorevole; consegna e ACK sono distinti.\n"]
                for m in state["messages"].values():
                    ack = m["ack"]["utc"] if m["ack"] else "PENDING"
                    mail.append(f"\n## {m['id']} · {m['from']} → {m['to']} · ACK {ack}\n\n{m['body']}\n")
                atomic(self.root / "squadra-posta.md", "".join(mail).encode())
                daily = {}
                for e in events:
                    day = e["utc"][:10]
                    daily.setdefault(day, ["# Squadra — scambi del giorno UTC\n\nProiezione ricostruibile del registro comune; non una seconda fonte.\n"]).append(
                        f"\n## {e['utc']} · {e['actor']} · {e['kind']} · evento {e['seq']}\n\n```json\n{canonical(e['data'])}\n```\n")
                folder = self.root / "posta"; folder.mkdir(mode=0o700, exist_ok=True)
                for day, parts in daily.items():
                    atomic(folder / ("exchange-" + day + ".md"), "".join(parts).encode())
                atomic(self.root / "stato.md", (table(state, events=events) + "\n").encode())
                return (state, events) if with_events else state
            finally:
                if os.name == "nt":
                    f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)

    def checkpoint(self, destination):
        destination = Path(destination).resolve()
        require(not destination.exists(), "Checkpoint destination must be new")
        destination.mkdir(parents=True, mode=0o700)
        target = sqlite3.connect(destination / "registry.sqlite3")
        try:
            self.db.backup(target)
        finally:
            target.close()
        if (self.root / "artifacts").exists():
            shutil.copytree(self.root / "artifacts", destination / "artifacts")
        check = Store(destination)
        try:
            check.verify_artifacts()
            check.project()
        finally:
            check.close()
        hashes = {str(f.relative_to(destination)): digest(f.read_bytes()) for f in destination.rglob("*")
                  if f.is_file() and f.name not in {"registry.sqlite3-wal", "registry.sqlite3-shm"}}
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
    times = board_times(events)
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


def table(state, read_at=None, events=()):
    tasks = list(state["tasks"].values())
    view = attention(state)
    lines = [board(state, read_at=read_at, events=events, live=read_at is not None), "", "Serve a te:"]
    for item in view["for_owner"]:
        lines.append("- " + item["question"] + (" · " + item["task"] if item.get("task") else ""))
        for option in item.get("options", []):
            lines.append("  " + option["id"] + ": " + option["label"] + " · Frase: " + option["say"])
    if not view["for_owner"]:
        lines.append("Nessuna richiesta registrata — una domanda fatta solo in chat non compare qui.")
    if read_at is not None:
        lines += ["", "Attese calcolate al " + str(read_at) + ":"]
        lines += ["- " + w["kind"] + " · " + str(w.get("task", w.get("id", ""))) + " · " + str(w["seconds"]) + "s · " + w["text"] for w in waits(state, read_at)]
    lines += ["", "Prossimo passo per incarico:"]
    lines += ["- " + item["task"] + ": " + item["text"] for item in view["next_steps"]]
    if view["superseded"]:
        lines += ["", "Sostituiti (storia conservata, fuori dai prossimi passi e dalle attese):"]
        lines += ["- " + item["task"] + " → " + item["by"] for item in view["superseded"]]
    lines += ["",
             "| Finestra / ruolo | Incarico / candidato | Stato / prova | Skill | Prossimo passo |",
             "|---|---|---|---|---|"]
    for a in state["agents"].values():
        owned = [t for t in tasks if t["owner"] == a["id"]]
        read = [t for t in tasks if any(r["reviewer"] == a["id"] for r in t["reviews"])]
        output = next((o for o in reversed(state["outputs"]) if o["agent"] == a["id"]), {})
        mentioned = [t for t in tasks if t["id"] == output.get("task") or t.get("coordinator") == a["id"] or
                     any(m.get("related_task") == t["id"] and m["to"] == a["id"] for m in state["messages"].values())]
        relevant = owned or read or mentioned
        work = ", ".join(t["id"] + (" @ " + t["candidate"]["head"][:10] if t.get("candidate") else "") for t in relevant) or "Nessun incarico assegnato"
        status = ", ".join(t["status"] for t in owned) or "Registrato"
        if relevant:
            status = ", ".join(t["status"] + (" / ultima prova " + t["proofs"][-1]["status"] + " rc=" + str(t["proofs"][-1]["native_rc"]) + " @" + t["proofs"][-1].get("after", {}).get("sha256", "ignota")[:8] if t["proofs"] else " / prova pendente") for t in relevant)
        if standby(state, a["id"]):
            status = "STAND-BY · " + status
        values = [a["id"] + " / " + a["role"] + " / " + a["client"], work, status,
                  ", ".join(s["name"] + "@" + s["sha256"][:8] for s in a.get("skills", [])) or "Non dichiarate",
                  ("Dichiarato dalla finestra · output " + output["event"][:8] + ": " if output else "") + output.get("next", "Da registrare")]
        lines.append("| " + " | ".join(cell(v) for v in values) + " |")
    held = [a["id"] for a in state["agents"].values() if standby(state, a["id"])]
    if held:
        lines += ["", "Finestre in stand-by: " + ", ".join(held) + ". Nessun nuovo incarico; lavoro e riserve conservati."]
    if read_at is not None and any(m.get("ping") for m in state["messages"].values()):
        lines += ["", "Presenza dichiarata e risposte (non prova di processo vivo):"]
        for row in presence(state, read_at):
            lines.append("- " + row["agent"] + " · " + row["availability"] + " · ultima risposta " + str(row["last_answer"])
                         + (" · ping " + row["ping"] + " · " + str(row["seconds"]) + "s / " + str(row["threshold"])
                            + "s · " + ("ACK ricevuto" if row["answer"] else "risposta pendente") if row["ping"] else ""))
    pending = sum(m["ack"] is None for m in state["messages"].values())
    lines += ["", f"Messaggi senza ACK: {pending}. Riserve attive: {len(state['resources'])}.",
              "Registrazione e heartbeat non dimostrano esecuzione, accettazione o risveglio del client."]
    return "\n".join(lines)
