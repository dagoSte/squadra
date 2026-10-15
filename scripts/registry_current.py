"""Current lookup indexes, advanced after the authoritative reducer applies an event."""
from __future__ import annotations


SCHEMA = 1
LIVE_STATUSES = ("running", "cleanup-unknown")


def empty():
    return {"schema": SCHEMA, "information_count": 0, "pending_messages": {}, "pending_by_recipient": {},
            "messages_by_recipient": {}, "messages_by_task": {}, "related_tasks": {},
            "live_runs": {status: {} for status in LIVE_STATUSES}, "run_order": {}, "run_location": {},
            "runs_by_task": {}, "latest_output": {}, "latest_ping": {}, "ping_order": {},
            "last_answer": {}, "recipient_ping": {}, "recipient_answer": {},
            "task_timings": {}, "current_tasks": {}, "message_order": {}, "forecast_events": {}}


def _membership(mapping, key, member):
    mapping.setdefault(key, {})[member] = member


def _identity(indexes, state, recipient):
    agent = state["agents"].get(recipient)
    if agent is None:
        return
    identity = agent["identity"]
    ping = indexes["recipient_ping"].get(recipient)
    if ping is not None and ping["order"] > indexes["ping_order"].get(identity, -1):
        indexes["latest_ping"][identity] = ping["id"]
        indexes["ping_order"][identity] = ping["order"]
    answer = indexes["recipient_answer"].get(recipient)
    if answer is not None:
        indexes["last_answer"][identity] = max(answer, indexes["last_answer"].get(identity, answer))


def _message(indexes, state, name, order, *, created):
    message = state["messages"][name]
    recipient = message["to"]
    if created:
        if message.get("info") is True:
            indexes["information_count"] += 1
        indexes["message_order"][name] = order
        _membership(indexes["messages_by_recipient"], recipient, name)
        for task in (message.get("task"), message.get("related_task")):
            if task is not None:
                _membership(indexes["messages_by_task"], task, name)
        if message.get("related_task") is not None:
            _membership(indexes["related_tasks"], recipient, message["related_task"])
        if message.get("ping"):
            indexes["recipient_ping"][recipient] = {"id": name, "order": order}
    pending = indexes["pending_by_recipient"].setdefault(recipient, {})
    if message.get("ack") is None and message.get("info") is not True:
        indexes["pending_messages"][name] = name
        pending[name] = name
    else:
        indexes["pending_messages"].pop(name, None)
        pending.pop(name, None)
    if message.get("ack") is not None:
        stamp = message["ack"]["utc"]
        # Match presence()'s existing max(UTC strings), without changing time semantics.
        indexes["recipient_answer"][recipient] = max(stamp, indexes["recipient_answer"].get(recipient, stamp))
    _identity(indexes, state, recipient)


def _task(indexes, state, name):
    task = state["tasks"].get(name)
    if task is None:
        return
    if task.get("superseded_by") or task["status"] in {"done", "withdrawn"}:
        indexes["current_tasks"].pop(name, None)
    else:
        indexes["current_tasks"][name] = name


def _run(indexes, state, name, order, *, started):
    run = state["runs"][name]
    location = {key: run.get(key, "code" if key == "stage" else None) for key in ("task", "mode", "stage")}
    old = indexes["run_location"].get(name)
    if started:
        indexes["run_order"][name] = order
    if old is not None and old != location:
        previous = indexes["runs_by_task"][old["task"]]
        previous["all"].pop(name, None)
        mode = previous["mode"][old["mode"]]
        mode.pop(name, None)
        if old["mode"] == "read" and isinstance(old["stage"], str):
            previous["read_stage"][old["stage"]].pop(name, None)
        if previous["latest_mode"].get(old["mode"]) == name:
            if mode:
                # Only a legacy terminal record changing its start coordinates needs this repair.
                previous["latest_mode"][old["mode"]] = max(mode, key=lambda key: indexes["run_order"][key])
            else:
                previous["latest_mode"].pop(old["mode"], None)
    if started or old != location:
        task = indexes["runs_by_task"].setdefault(run["task"],
            {"all": {}, "mode": {}, "read_stage": {}, "latest_mode": {}})
        task["all"][name] = name
        _membership(task["mode"], run["mode"], name)
        latest = task["latest_mode"].get(run["mode"])
        if latest is None or indexes["run_order"][latest] < indexes["run_order"][name]:
            task["latest_mode"][run["mode"]] = name
        if run["mode"] == "read" and isinstance(run.get("stage", "code"), str):
            _membership(task["read_stage"], run.get("stage", "code"), name)
        indexes["run_location"][name] = location
    for status in LIVE_STATUSES:
        if run["status"] == status:
            indexes["live_runs"][status][name] = indexes["run_order"][name]
        else:
            indexes["live_runs"][status].pop(name, None)


def _forecast(indexes, state, event):
    kind, data = event["kind"], event["data"]
    if kind == "task":
        task, fields = data["id"], ("id",)
    elif kind == "message":
        task = data.get("task")
        if task is None or data["id"] != "assign-" + task:
            return
        fields = ("id", "task")
    elif kind == "ack":
        message = state["messages"][data["message"]]
        task = message.get("task")
        if task is None or message["id"] != "assign-" + task:
            return
        fields = ("message",)
    elif kind in {"plan", "deliver", "review", "close"}:
        task = data["task"]
        fields = ("task", "stage", "verdict") if kind == "review" else ("task",)
    else:
        return
    indexes["forecast_events"].setdefault(task, []).append(
        {"kind": kind, "utc": event["utc"], "data": {key: data[key] for key in fields if key in data}})


def advance(indexes, state, event):
    """Update only the entities touched by this already-applied event; mutate indexes."""
    if indexes.get("schema") != SCHEMA:
        raise ValueError("Unsupported current-index schema")
    kind, data = event["kind"], event["data"]
    _forecast(indexes, state, event)
    order = event.get("seq", state["event_count"])
    if kind == "agent":
        _identity(indexes, state, data["id"])
    elif kind == "message":
        _message(indexes, state, data["id"], order, created=True)
    elif kind == "ack":
        _message(indexes, state, data["message"], order, created=False)
        message = state["messages"][data["message"]]
        if message.get("task") is not None:
            _task(indexes, state, message["task"])
    elif kind in {"run.start", "run.end", "run.abandon"}:
        _run(indexes, state, data["id"], order, started=kind == "run.start")
    elif kind == "output":
        position = len(state["outputs"]) - 1
        indexes["latest_output"][state["outputs"][position]["agent"]] = position
    elif kind == "task":
        indexes["task_timings"][data["id"]] = {"assigned": event["utc"]}
        _task(indexes, state, data["id"])
        if data.get("supersedes") is not None:
            _task(indexes, state, data["supersedes"])
    elif kind == "supersede":
        _task(indexes, state, data["task"])
        _task(indexes, state, data["by"])
    elif kind in {"start", "close", "plan", "deliver", "review", "route", "fix"}:
        name = data["task"]
        _task(indexes, state, name)
        timing = indexes["task_timings"].get(name)
        if timing is not None and kind == "start":
            timing.setdefault("started", event["utc"])
        elif timing is not None and kind == "close":
            timing["closed"] = event["utc"]
    return indexes


def build(events, apply, initial):
    """Offline independent replay; return indexes without retaining an event collection."""
    state, indexes = initial(), empty()
    for event in events:
        apply(state, event)
        advance(indexes, state, event)
    return indexes


def attached(state):
    """A public state view may expose authenticated indexes without exporting them."""
    return getattr(state, "current", None)


def message_ids(indexes, *, recipient=None, task=None, pending=False):
    if recipient is not None and task is not None:
        raise ValueError("Choose a recipient or a task message index")
    if recipient is not None:
        key = "pending_by_recipient" if pending else "messages_by_recipient"
        members = indexes[key].get(recipient, {})
    elif task is not None:
        members = indexes["messages_by_task"].get(task, {})
        members = (name for name in members if not pending or name in indexes["pending_messages"])
    elif pending:
        members = indexes["pending_messages"]
    else:
        raise ValueError("Complete message history requires an explicit recipient or task")
    return iter(sorted(members, key=lambda name: indexes["message_order"][name]))


def pending_rows(indexes, state, recipient=None):
    return (state["messages"][name] for name in message_ids(indexes, recipient=recipient, pending=True))


def live_rows(indexes, state, status=None):
    if status is not None and status not in LIVE_STATUSES:
        raise ValueError("Invalid live-run status")
    statuses = (status,) if status is not None else LIVE_STATUSES
    members = [(order, name) for key in statuses for name, order in indexes["live_runs"][key].items()]
    return (state["runs"][name] for _, name in sorted(members))


def _run_members(indexes, task, mode, stage):
    if stage is not None and mode not in {None, "read"}:
        raise ValueError("A stage belongs to read runs only")
    row = indexes["runs_by_task"].get(task, {})
    if stage is not None:
        return row.get("read_stage", {}).get(stage, {})
    return row.get("mode", {}).get(mode, {}) if mode is not None else row.get("all", {})


def run_count(indexes, task, mode=None, stage=None):
    return len(_run_members(indexes, task, mode, stage))


def runs_for(indexes, state, task, mode=None, stage=None):
    members = _run_members(indexes, task, mode, stage)
    return (state["runs"][name] for name in sorted(members, key=lambda key: indexes["run_order"][key]))


def latest_run(indexes, state, task, mode):
    name = indexes["runs_by_task"].get(task, {}).get("latest_mode", {}).get(mode)
    return state["runs"][name] if name is not None else None


def latest_output(indexes, state, actor):
    position = indexes["latest_output"].get(actor)
    return state["outputs"][position] if position is not None else {}


def latest_ping(indexes, state, agent):
    name = indexes["latest_ping"].get(state["agents"][agent]["identity"])
    return state["messages"][name] if name is not None else None


def last_answer(indexes, state, agent):
    return indexes["last_answer"].get(state["agents"][agent]["identity"])


def related(indexes, recipient, task):
    return task in indexes["related_tasks"].get(recipient, {})
