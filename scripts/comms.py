"""Build dashboard conversations from recorded events."""
from __future__ import annotations

import datetime as dt
import re

COMMUNICATION = {"message", "ack", "transport", "availability"}
OTHER_KNOWN = {"init", "agent", "task", "plan", "start", "deliver", "review", "close", "output",
               "run.start", "run.end", "run.cancel", "run.progress", "resource.acquire",
               "resource.release", "pause", "resume", "legacy", "decision", "ask", "diagnostic",
               "settings", "save", "restore.check", "restore.freeze", "challenge", "benchmark", "brief",
               "supersede", "route"}
AVAILABILITY = {"active", "stand-by"}
LIMIT = 500


def _clock(value):
    clock = dt.datetime.fromisoformat(value) if isinstance(value, str) else value
    if clock is None or clock.tzinfo is None:
        raise ValueError("Timezone-aware clock required")
    return clock


def _seconds(start, end):
    """Whole seconds between two recorded timestamps, or None when either is unreadable."""
    try:
        a = dt.datetime.fromisoformat(start) if isinstance(start, str) else start
        b = dt.datetime.fromisoformat(end) if isinstance(end, str) else end
        return max(0, int((b - a).total_seconds()))
    except (TypeError, ValueError):
        return None


def task_name(task, untitled=None):
    """The name every view gives a task: the first line of its recorded brief, else its recorded title."""
    brief = task.get("brief")
    text = brief.get("text") if isinstance(brief, dict) else None
    for line in re.split(r"\r?\n", text) if isinstance(text, str) else []:
        line = re.sub(r"^#+\s*", "", line).strip()
        if line:
            return line, True
    return task.get("title", untitled), False


def communications(state, events, waits, at, limit=LIMIT):
    """Build conversation rows and pairs without copying message bodies.

    Use the caller's clock for ages. A message is overdue only when listed in
    waits; the smallest threshold wins. Keep unknown event kinds as neutral rows.
    """
    clock = _clock(at)
    agents = state.get("agents", {})
    messages = state.get("messages", {})
    tasks = state.get("tasks", {})

    lanes, lane_index = [], {}

    def lane(identity, registered):
        if identity not in lane_index:
            lane_index[identity] = len(lanes)
            lanes.append({"identity": identity, "registered": registered, "seats": [],
                          "availability": "active-registered" if registered else "unregistered",
                          "availability_utc": None, "work": []})
        return lanes[lane_index[identity]]

    for agent in agents.values():
        entry = lane(agent.get("identity") or agent["id"], True)
        entry["seats"].append({"id": agent["id"], "role": agent.get("role"), "client": agent.get("client")})
        availability = agent.get("availability") or {}
        if availability.get("status") == "stand-by":
            entry["availability"] = "stand-by"
            entry["availability_utc"] = availability.get("utc")
    for task in tasks.values():
        for seat, role in [(task.get("owner"), "builder"), (task.get("reviewer"), "reviewer"),
                           (task.get("coordinator"), "coordinator")]:
            if seat in agents:
                lane(agents[seat].get("identity") or seat, True)["work"].append(
                    {"task": task["id"], "title": task_name(task, "")[0], "as": role, "status": task.get("status")})

    def lane_of(seat):
        if seat in agents:
            return agents[seat].get("identity") or seat
        if seat not in lane_index:
            lane(seat, False)["seats"].append({"id": seat, "role": None, "client": None})
        return seat

    overdue = {}
    for wait in waits or []:
        key = wait.get("id")
        if key in messages and (key not in overdue or wait.get("threshold", 0) < overdue[key].get("threshold", 0)):
            overdue[key] = wait

    def task_fields(message):
        task = message.get("task") or message.get("related_task")
        name, described = task_name(tasks[task]) if task in tasks else (None, False)
        return {"task": task, "task_title": name, "task_described": described,
                "assignment": bool(message.get("task"))}

    def base(event, kind, sender, target):
        return {"id": event.get("id"), "seq": event.get("seq"), "utc": event.get("utc"), "kind": kind,
                "from": sender, "to": target, "from_lane": lane_of(sender) if sender else None,
                "to_lane": lane_of(target) if target else None, "message": None, "task": None,
                "task_title": None, "state": "unknown", "flags": [], "age_seconds": _seconds(event.get("utc"), clock)}

    def unknown(event, kind, data, malformed=False):
        target = None
        if isinstance(data, dict):
            for key in ("to", "agent"):
                if isinstance(data.get(key), str) and (data[key] in agents or data[key] in lane_index):
                    target = data[key]
                    break
        # Only text fields can identify a window or status.
        agent, status = (data.get("agent"), data.get("status")) if isinstance(data, dict) else (None, None)
        if (not malformed and isinstance(agent, str) and agent in agents
                and isinstance(status, str) and status in AVAILABILITY):
            edge = base(event, "availability", event.get("actor"), data["agent"])
            edge.update(state=data["status"], flags=["unrecognised-kind"], raw_kind=kind,
                        reason=data.get("reason"))
            return edge
        edge = base(event, "unknown", event.get("actor"), target)
        edge.update(raw_kind=kind, flags=["malformed"] if malformed else [])
        return edge

    edges, unknown_kinds, malformed = [], {}, 0
    for event in events:
        kind, data = event.get("kind"), event.get("data")
        if kind in OTHER_KNOWN:
            continue
        if kind not in COMMUNICATION:
            unknown_kinds[kind] = unknown_kinds.get(kind, 0) + 1
            edges.append(unknown(event, kind, data))
            continue
        try:
            if kind == "message":
                message = messages[data["id"]]
                edge = base(event, "ping" if data.get("ping") else "message", event["actor"], data["to"])
                transport = message.get("transport") or {}
                flags = []
                if transport:
                    if transport.get("via") == "manual":
                        flags.append("manual")
                    elif transport.get("delivered"):
                        flags.append("queued")
                    else:
                        flags.append("transport-unconfirmed")
                if data["to"] not in agents:
                    flags.append("unregistered-recipient")
                if message.get("ack"):
                    status = "acked"
                    edge.update(ack_utc=message["ack"].get("utc"),
                                reply_seconds=_seconds(event.get("utc"), message["ack"].get("utc")))
                elif data["id"] in overdue:
                    status = "overdue"
                    edge.update(wait_seconds=overdue[data["id"]].get("seconds"),
                                wait_threshold=overdue[data["id"]].get("threshold"))
                elif "queued" in flags:
                    status = "queued"
                else:
                    status = "sent"
                edge.update(message=data["id"], state=status, flags=flags, **task_fields(data))
            elif kind == "ack":
                message = messages[data["message"]]
                edge = base(event, "answer" if message.get("ping") else "ack", event["actor"], message["from"])
                edge.update(message=data["message"], state="recorded",
                            reply_seconds=_seconds(message.get("utc"), event.get("utc")), **task_fields(message))
            elif kind == "transport":
                message = messages[data["message"]]
                edge = base(event, "transport", event["actor"], message["to"])
                status = ("manual" if data.get("via") == "manual" else
                          "queued" if data.get("delivered") else "unconfirmed")
                edge.update(message=data["message"], state=status, via=data.get("via"),
                            native_rc=data.get("native_rc"), **task_fields(message))
            else:
                edge = base(event, "availability", event["actor"], data["agent"])
                edge["state"] = data["status"] if data.get("status") in AVAILABILITY else "unknown"
                edge["reason"] = data.get("reason")
                ping = data.get("ping")
                if ping:
                    edge["ping"] = ping
                    edge["ping_utc"] = messages.get(ping, {}).get("utc")
        except (KeyError, TypeError, AttributeError):
            malformed += 1
            edge = unknown(event, kind, data, malformed=True)
        if edge["kind"] in {"message", "ping"} and edge["to_lane"] in lane_index:
            if lanes[lane_index[edge["to_lane"]]]["availability"] == "stand-by":
                edge["flags"].append("recipient-stand-by")
        edges.append(edge)

    pairs = {}
    for edge in edges:
        if edge["kind"] not in {"message", "ping"}:
            continue
        key = tuple(sorted([edge["from_lane"], edge["to_lane"]]))
        pair = pairs.setdefault(key, {"lanes": list(key), "same_window": key[0] == key[1], "messages": 0,
                                      "acked": 0, "sent": 0, "queued": 0, "overdue": 0, "last_utc": None,
                                      "oldest_pending_seconds": None, "tasks": []})
        pair["messages"] += 1
        pair[edge["state"]] += 1
        pair["last_utc"] = edge["utc"]
        if edge["state"] != "acked" and edge["age_seconds"] is not None:
            pair["oldest_pending_seconds"] = max(pair["oldest_pending_seconds"] or 0, edge["age_seconds"])
        if edge["task"] and edge["task"] not in [t["task"] for t in pair["tasks"]]:
            pair["tasks"].append({"task": edge["task"], "title": edge["task_title"]})
    # Prioritize overdue and unconfirmed pairs, then their latest message.
    ordered = sorted(pairs.values(), key=lambda p: p["last_utc"] or "", reverse=True)
    ordered.sort(key=lambda p: (p["overdue"] == 0, p["sent"] + p["queued"] == 0))
    return {"as_of": clock.isoformat(), "lanes": lanes, "edges": edges[-limit:] if limit else [],
            "total": len(edges), "shown": min(len(edges), limit), "limit": limit,
            "unknown_kinds": unknown_kinds, "malformed": malformed, "pairs": ordered}
