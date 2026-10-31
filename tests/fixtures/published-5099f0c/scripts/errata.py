"""errata: lessons recorded in the registry, shown again at brief and start, marked when they fail."""
from __future__ import annotations

import fnmatch
import re
import unicodedata

from registry import require

KINDS = ("lesson", "lesson.retire", "lesson.recall", "lesson.ineffective")
SHOWN = 5
TEXT_MAX = 1000
REASON_MAX = 500
TRIGGER_MAX = 10
LESSON_ID = re.compile(r"L\d{4,}")
TEMPLATE = ('squadra lesson --actor {actor} --task {task} --source-event {event} '
            '--text "<what to do differently next time>" --path "<glob>" --word "<word>"')


def _text(value, maximum, code):
    require(isinstance(value, str) and 0 < len(value.strip()) <= maximum
            and not any(unicodedata.category(c).startswith("C") and c != "\n" for c in value), code)


def _lesson(state, name):
    lesson = state.get("lessons", {}).get(name)
    require(lesson is not None, "lesson_unknown")
    return lesson


def validate_event(state, kind, actor, d):
    """Refuse a malformed lesson event; the registry calls this before appending."""
    require(isinstance(d, dict), "lesson_fields_invalid")
    if kind == "lesson":
        require(set(d) <= {"id", "text", "trigger", "source"}, "lesson_fields_invalid")
        _text(d.get("text"), TEXT_MAX, "lesson_text_required")
        trigger = d.get("trigger")
        require(isinstance(trigger, dict) and set(trigger) <= {"paths", "words"}
                and (trigger.get("paths") or trigger.get("words")), "lesson_trigger_required")
        paths, words = trigger.get("paths", []), trigger.get("words", [])
        require(isinstance(paths, list) and isinstance(words, list)
                and len(paths) <= TRIGGER_MAX and len(words) <= TRIGGER_MAX
                and all(isinstance(p, str) and 0 < len(p) <= 200 and not {"\n", "\0"} & set(p) for p in paths)
                and all(isinstance(w, str) and 2 <= len(w) <= 60 and w == w.strip() and "\n" not in w for w in words),
                "lesson_trigger_invalid")
        source = d.get("source")
        require(isinstance(source, dict) and set(source) == {"task", "event", "eligible"}
                and source["task"] in state["tasks"] and type(source["event"]) is int
                and 1 <= source["event"] <= state["event_count"] and type(source["eligible"]) is bool,
                "lesson_source_required")
        require(isinstance(d.get("id"), str) and LESSON_ID.fullmatch(d["id"]), "lesson_id_invalid")
        require(d["id"] not in state.get("lessons", {}), "lesson_id_taken")
    elif kind == "lesson.retire":
        require(set(d) == {"lesson", "reason"}, "lesson_fields_invalid")
        lesson = _lesson(state, d["lesson"])
        require(lesson["retired"] is None, "lesson_already_retired")
        _text(d["reason"], REASON_MAX, "lesson_reason_required")
        require(actor == lesson["author"] or state["agents"][actor]["role"] == "coordinator",
                "lesson_retire_not_allowed")
    elif kind == "lesson.recall":
        names = d.get("lessons")
        require(set(d) == {"task", "at", "lessons"} and d["task"] in state["tasks"] and d["at"] in {"brief", "start"}
                and isinstance(names, list) and 0 < len(names) <= SHOWN and len(set(names)) == len(names)
                and all(state.get("lessons", {}).get(n, {}).get("retired", True) is None for n in names),
                "lesson_recall_invalid")
    elif kind == "lesson.ineffective":
        require(set(d) == {"lesson", "recall", "reason"}, "lesson_fields_invalid")
        _lesson(state, d["lesson"])
        require(any(r["seq"] == d["recall"] and d["lesson"] in r["lessons"]
                    for r in state.get("lesson_recalls", [])), "lesson_recall_unknown")
        _text(d["reason"], REASON_MAX, "lesson_reason_required")


def status(lesson):
    if lesson["retired"]:
        return "retired"
    if lesson["ineffective"]:
        return "ineffective"
    return "active" if lesson["recalls"] else "new"


def task_text(root, task, extra=""):
    """What a lesson trigger is matched against: title, scope, requirements and brief."""
    parts = [task.get("title", ""), extra, (task.get("brief") or {}).get("text", "")]
    parts += [r.get("text", "") for r in task.get("requirements", [])]
    scope = task.get("scope") or {}
    path = root / "artifacts" / scope.get("sha256", "-")
    if scope.get("sha256") and path.is_file():
        parts.append(path.read_bytes().decode("utf-8", "replace"))
    return "\n".join(p for p in parts if p)


def select(state, root, task_id, extra=""):
    """At most five matching lessons, best first: 3 per matching path glob, 1 per word, +1 if already ineffective."""
    text = task_text(root, state["tasks"][task_id], extra)
    tokens = {t.rstrip(".,;:)") for t in re.findall(r"[\w./*-]+", text)}
    tokens = {t for t in tokens if "/" in t or re.search(r"\.\w{1,8}$", t)}
    shown = []
    for lesson in state.get("lessons", {}).values():
        if lesson["retired"]:
            continue
        trigger = lesson["trigger"]
        score = 3 * sum(any(fnmatch.fnmatch(t, glob) for t in tokens) for glob in trigger.get("paths", []))
        score += sum(bool(re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", text, re.I)) for w in trigger.get("words", []))
        if score:
            score += 1 if lesson["ineffective"] else 0
            shown.append({"id": lesson["id"], "text": lesson["text"], "trigger": trigger,
                          "status": status(lesson), "score": score, "seq": lesson["seq"]})
    shown.sort(key=lambda s: (-s["score"], -(s["seq"] or 0)))
    return [{k: v for k, v in s.items() if k != "seq"} for s in shown[:SHOWN]]


def source_check(state, events, task_id, seq, any_source):
    """The evidence a lesson cites: a red of this task, or any event of it with --any-source."""
    event = next((e for e in events if e["seq"] == seq), None)
    require(event is not None and task_id in state["tasks"], "lesson_source_required")
    d = event["data"]
    run = state["runs"].get(d.get("id"), {}) if event["kind"] == "run.end" else {}
    related = run.get("task") if event["kind"] == "run.end" else d.get("id") if event["kind"] == "task" else d.get("task")
    require(related == task_id, "lesson_source_unrelated")
    eligible = ((event["kind"] == "review" and d.get("stage") == "code" and d.get("verdict") == "changes-required")
                or (event["kind"] == "run.end" and run.get("mode") == "proof" and d.get("status") != "passed")
                or (event["kind"] == "close" and bool(state["tasks"][task_id].get("fix"))))
    require(eligible or any_source, "lesson_source_not_eligible")
    return eligible


def next_id(state):
    numbers = [int(name[1:]) for name in state.get("lessons", {})]
    return "L%04d" % (max(numbers, default=0) + 1)


def hint(task, event, responsible, reason):
    """Ask the agent who saw the red to record the lesson; the text is the agent's to write."""
    return {"record_lesson": True, "task": task, "source_event": event, "responsible": responsible, "reason": reason,
            "command": TEMPLATE.format(actor=responsible, task=task, event=event)}


def listing(state):
    """Lessons with their status, and the counts the System skills page shows."""
    lessons = state.get("lessons", {})
    reviews = [r for t in state["tasks"].values() for r in t.get("reviews", []) if r.get("lens") == "rasoio"]
    return {"lessons": [{**l, "status": status(l)} for l in lessons.values()],
            "lesson_events": len(lessons),
            "recall_events": len(state.get("lesson_recalls", [])),
            "recalled_pairs": sum(len(l["recalled_tasks"]) for l in lessons.values()),
            "ineffective_marks": sum(len(l["ineffective"]) for l in lessons.values()),
            "rasoio_reads": len(reviews),
            "net_lines_declared": sum(r.get("net_lines", 0) for r in reviews)}
