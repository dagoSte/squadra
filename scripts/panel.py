"""Count recorded activity for dashboard charts."""
from __future__ import annotations

import datetime as dt
import math

STEP = 900
ORDER = ("comms", "work", "reads", "other")
FAMILIES = {
    "comms": {"message", "ack", "transport", "availability"},
    "work": {"task", "route", "fix", "supersede", "brief", "plan", "start", "deliver", "output", "run.start", "run.end", "run.cancel",
             "resource.acquire", "resource.release"},
    "reads": {"review", "close", "ask", "decision", "pause", "resume"},
    "other": {"init", "agent", "legacy", "diagnostic", "settings", "save", "restore.check", "restore.freeze",
              "challenge", "benchmark"},
}
HEARTBEAT = "run.progress"


def _family(kind):
    for name in ORDER:
        if kind in FAMILIES[name]:
            return ORDER.index(name)
    return ORDER.index("other")


def activity(events, at, days=15):
    """Count events in 15-minute UTC buckets for the requested time range.

    Whole buckets compose local hours and days when UTC offsets and DST shifts
    are multiples of 15 minutes.
    Buckets cover [start, start + 900 seconds); the final bucket includes at.
    Count future events, invalid times and heartbeats separately, outside charts.
    """
    clock = dt.datetime.fromisoformat(at) if isinstance(at, str) else at
    if clock is None or clock.tzinfo is None:
        raise ValueError("Timezone-aware clock required")
    since = math.floor(clock.timestamp() / STEP) * STEP - days * 86400
    buckets, heartbeats, future, skipped = {}, 0, 0, 0
    for event in events:
        try:
            moment = dt.datetime.fromisoformat(event["utc"])
            if moment.tzinfo is None:
                raise ValueError("naive time")
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        if moment > clock:
            future += 1
            continue
        seconds = moment.timestamp()
        if seconds < since:
            continue
        if event.get("kind") == HEARTBEAT:
            heartbeats += 1
            continue
        index = int((seconds - since) // STEP)
        buckets.setdefault(index, [index, 0, 0, 0, 0])[1 + _family(event.get("kind"))] += 1
    return {"as_of": clock.isoformat(), "since": dt.datetime.fromtimestamp(since, dt.timezone.utc).isoformat(),
            "step": STEP, "families": list(ORDER), "buckets": [buckets[i] for i in sorted(buckets)],
            "heartbeats": heartbeats, "future": future, "skipped": skipped}
