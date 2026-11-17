"""errata: lessons recorded in the registry, shown again at brief and start, marked when they fail."""
from __future__ import annotations

import fnmatch
import json
from pathlib import Path
import re
import shlex
import unicodedata

from registry import plain, require

KINDS = ("lesson", "lesson.retire", "lesson.recall", "lesson.ineffective", "lesson.promote")
SHOWN = 5
TEXT_MAX = 1000
REASON_MAX = 500
TRIGGER_MAX = 10
LESSON_ID = re.compile(r"L\d{4,}")
PROMOTED = Path("lessons") / "promoted.json"
PROMOTE_TASKS = 2
# The agent replaces these placeholders; every word taken from the registry is shell-quoted.
PLACEHOLDERS = ' --text "<what to do differently next time>" --path "<glob>" --word "<word>"'


def _text_ok(value, maximum):
    return (isinstance(value, str) and 0 < len(value.strip()) <= maximum
            and not any(unicodedata.category(c).startswith("C") and c != "\n" for c in value))


def _text(value, maximum, code):
    require(_text_ok(value, maximum), code)


def _trigger_ok(trigger):
    if not (isinstance(trigger, dict) and set(trigger) <= {"paths", "words"}):
        return False
    paths, words = trigger.get("paths", []), trigger.get("words", [])
    return (isinstance(paths, list) and isinstance(words, list)
            and len(paths) <= TRIGGER_MAX and len(words) <= TRIGGER_MAX
            and all(isinstance(p, str) and 0 < len(p) <= 200 and not {"\n", "\0"} & set(p) for p in paths)
            and all(isinstance(w, str) and 2 <= len(w) <= 60 and w == w.strip() and "\n" not in w for w in words))


def _lesson(state, name):
    lesson = state.get("lessons", {}).get(name)
    require(lesson is not None, "lesson_unknown")
    return lesson


def validate_event(state, kind, actor, d):
    """Refuse a malformed lesson event; the registry calls this before appending."""
    require(isinstance(d, dict), "lesson_fields_invalid")
    if kind == "lesson":
        require(set(d) <= {"id", "text", "trigger", "source", "despite"}, "lesson_fields_invalid")
        _text(d.get("text"), TEXT_MAX, "lesson_text_required")
        trigger = d.get("trigger")
        require(isinstance(trigger, dict) and set(trigger) <= {"paths", "words"}
                and (trigger.get("paths") or trigger.get("words")), "lesson_trigger_required")
        require(_trigger_ok(trigger), "lesson_trigger_invalid")
        if "despite" in d:  # recorded on purpose although these active lessons already matched its trigger
            despite = d["despite"]
            require(isinstance(despite, dict) and set(despite) == {"lessons", "reason"}
                    and isinstance(despite["lessons"], list) and despite["lessons"]
                    and len(set(despite["lessons"])) == len(despite["lessons"])
                    and all(n in state.get("lessons", {}) for n in despite["lessons"])
                    and _text_ok(despite["reason"], REASON_MAX), "lesson_despite_invalid")
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
    elif kind == "lesson.promote":
        require({"lesson", "path", "reason"} <= set(d) <= {"lesson", "path", "reason", "absorbs"}, "lesson_fields_invalid")
        lesson = _lesson(state, d["lesson"])
        require(lesson["retired"] is None, "lesson_promote_retired")
        require(not lesson["ineffective"], "lesson_promote_ineffective")
        require(len(lesson["recalled_tasks"]) >= PROMOTE_TASKS, "lesson_promote_needs_recall_in_two_tasks")
        require(isinstance(d["path"], str) and Path(d["path"]).is_absolute()
                and Path(d["path"]).parts[-2:] == PROMOTED.parts, "lesson_promote_path_invalid")
        _text(d["reason"], REASON_MAX, "lesson_reason_required")
        if "absorbs" in d:  # retired lessons this one replaces; they leave the file
            absorbs = d["absorbs"]
            require(isinstance(absorbs, list) and absorbs and len(set(absorbs)) == len(absorbs)
                    and d["lesson"] not in absorbs, "lesson_absorbs_invalid")
            for name in absorbs:
                require(_lesson(state, name)["retired"] is not None, "lesson_absorbed_not_retired")


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


def _tokens(text):
    tokens = {t.rstrip(".,;:)") for t in re.findall(r"[\w./*-]+", text)}
    return {t for t in tokens if "/" in t or re.search(r"\.\w{1,8}$", t)}


def match(trigger, text, tokens):
    """The one recall score: 3 per path glob matching a path in the text, 1 per whole word found."""
    score = 3 * sum(any(fnmatch.fnmatch(t, glob) for t in tokens) for glob in trigger.get("paths", []))
    return score + sum(bool(re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", text, re.I))
                       for w in trigger.get("words", []))


def select(state, root, task_id, extra=""):
    """At most five matching lessons, best first: 3 per matching path glob, 1 per word, +1 if already ineffective."""
    text = task_text(root, state["tasks"][task_id], extra)
    tokens = _tokens(text)
    shown = []
    for lesson in state.get("lessons", {}).values():
        if lesson["retired"]:
            continue
        trigger = lesson["trigger"]
        score = match(trigger, text, tokens)
        if score:
            score += 1 if lesson["ineffective"] else 0
            shown.append({"id": lesson["id"], "text": lesson["text"], "trigger": trigger,
                          "status": status(lesson), "score": score, "seq": lesson["seq"]})
    shown.sort(key=lambda s: (-s["score"], -(s["seq"] or 0)))
    return [{k: v for k, v in s.items() if k != "seq"} for s in shown[:SHOWN]]


def overlaps(state, trigger):
    """Active lessons whose trigger meets this one: the recall score, run both ways on the trigger text."""
    def words(t):
        return "\n".join([*t.get("paths", ()), *t.get("words", ())])
    mine = words(trigger)
    found = []
    for lesson in state.get("lessons", {}).values():
        theirs = words(lesson["trigger"])
        if lesson["retired"] is None and (match(lesson["trigger"], mine, _tokens(mine))
                                          or match(trigger, theirs, _tokens(theirs))):
            found.append({"id": lesson["id"], "text": lesson["text"], "status": status(lesson)})
    return found


def overlap_notice(name, found):
    """The notice that names every overlapping lesson and the ways forward."""
    named = ", ".join('%s (%s) "%s"' % (f["id"], f["status"], f["text"][:60]) for f in found)
    return ("lesson_overlaps: " + name + " recorded, but " + named + " already match its trigger. "
            "If the mistake came back, mark the old one --ineffective LID --recall SEQ --reason; if it no "
            "longer applies, --retire LID --reason; if " + name + " is a twin by mistake, --retire " + name +
            ". A twin on purpose is recorded with " + " ".join("--despite " + f["id"] for f in found) + " --reason TEXT")


def read_promoted(path):
    """The repository's promoted lessons by id; ValueError when the file is not the documented shape."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not (isinstance(data, dict) and data.get("version") == 1 and isinstance(data.get("lessons"), list)):
        raise ValueError('expected {"version": 1, "lessons": [...]}')
    entries = {}
    for entry in data["lessons"]:
        if not (isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"] not in entries
                and _text_ok(entry.get("text"), TEXT_MAX) and _trigger_ok(entry.get("trigger"))
                and (entry["trigger"].get("paths") or entry["trigger"].get("words"))):
            raise ValueError("every lesson needs a unique id, a text and a trigger")
        entries[entry["id"]] = entry
    return entries


def dump_promoted(entries):
    """Stable bytes: sorted by id and by key, so the same promotions always diff to nothing."""
    body = {"version": 1, "lessons": [entries[k] for k in sorted(entries)]}
    return (json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def promoted_entry(state, name, event):
    """One file entry: the lesson as recorded, and where it came from, verifiable in the registry."""
    lesson = plain(state["lessons"][name])
    return {"id": name, "text": lesson["text"], "trigger": lesson["trigger"],
            "provenance": {"registry": state["workspace"].get("name"), "task": lesson["source"]["task"],
                           "lesson_event": lesson["seq"], "promote_event": event["seq"],
                           "promote_hash": event["hash"], "utc": event["utc"]}}


def promoted(state, root, task_id, extra, shown):
    """Promoted lessons of the task's repository that match it, by the same score; never a lesson already shown."""
    task = state["tasks"][task_id]
    path = Path(task.get("repo") or "-") / PROMOTED
    if not path.is_file():
        return [], None
    try:
        entries = read_promoted(path)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return [], "%s ignored: %s" % (PROMOTED, str(exc)[:120])
    text = task_text(root, task, extra)
    tokens, seen = _tokens(text), {(s["id"], s["text"]) for s in shown}
    found = [{"id": e["id"], "text": e["text"], "trigger": e["trigger"], "promoted": True,
              "score": match(e["trigger"], text, tokens)} for e in entries.values() if (e["id"], e["text"]) not in seen]
    found = sorted((f for f in found if f["score"]), key=lambda f: (-f["score"], f["id"]))
    return found[:SHOWN], None


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
            "command": shlex.join(["squadra", "lesson", "--actor", str(responsible), "--task", str(task),
                                   "--source-event", str(event)]) + PLACEHOLDERS}


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
