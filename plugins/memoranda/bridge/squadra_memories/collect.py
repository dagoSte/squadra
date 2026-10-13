"""Collects the answers agents recorded to the «da ricordare» question. Nothing is inferred from other records.

An answer is an `output` event carrying one artifact named `da-ricordare.json`. A declaration lists items; a reply
objects to an item, keeps it or drops it. A sentence is copied only from an item declared `private_content: "no"`
whose evidence all verifies against the snapshot, and a reply's `why` only from a reply declared the same way.
"""
from __future__ import annotations

import json

from . import DECLARATION_FORM, DECLARATION_NAME, LISTS

TYPES = ("decision", "constraint", "trap")
CERTAINTY = ("sure", "likely")
VERDICTS = ("object", "keep", "drop")


def event_task(event):
    data = event["data"] if isinstance(event["data"], dict) else {}
    if event["kind"] == "task":
        return data.get("id")
    if isinstance(data.get("task"), str):
        return data["task"]
    if event["kind"] == "decision" and data.get("tasks"):
        return list(data["tasks"])
    return None


def _parse(body):
    try:
        doc = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return doc if isinstance(doc, dict) and doc.get("form") == DECLARATION_FORM else None


def collect(events, artifacts, agents):
    """Returns the seven lists and the counts. `agents` maps a seat to its recorded identity and role."""
    by_seq = {event["seq"]: event for event in events}
    result = {name: [] for name in LISTS}
    counts = {"declarations": 0, "answered_no": 0, "replies": 0}
    items, declared = {}, set()

    def malformed(seq, index):
        result["malformed"].append({"seq": seq, "item": index})

    def withhold(seq, index, reason, evidence):
        result["withheld"].append({"seq": seq, "item": index, "reason": reason, "evidence": evidence})

    def check_evidence(pointers):
        if not isinstance(pointers, list) or not pointers:
            return [], False
        verified, ok = [], True
        for pointer in pointers:
            if isinstance(pointer, dict) and set(pointer) == {"event"} and type(pointer["event"]) is int \
                    and pointer["event"] in by_seq:
                cited = by_seq[pointer["event"]]
                verified.append({"event": cited["seq"], "hash": cited["hash"], "kind": cited["kind"],
                                 "task": event_task(cited)})
            elif isinstance(pointer, dict) and set(pointer) == {"artifact"} and artifacts.registered(pointer["artifact"]):
                artifacts.read(pointer["artifact"])
                verified.append({"artifact": pointer["artifact"], "bytes": artifacts.refs[pointer["artifact"]]["bytes"]})
            else:
                ok = False
        return verified, ok

    for event in events:
        if event["kind"] != "output":
            continue
        seq, actor = event["seq"], event["actor"]
        refs = [a for a in event["data"].get("artifacts") or [] if isinstance(a, dict) and a.get("name") == DECLARATION_NAME]
        if not refs:
            continue
        if len(refs) != 1:
            malformed(seq, None)
            continue
        doc = _parse(artifacts.read(refs[0]["sha256"]))
        if doc is None:
            malformed(seq, None)
            continue
        if "replies_to" in doc:
            counts["replies"] += 1
            target, index, verdict = doc.get("replies_to"), doc.get("item"), doc.get("verdict")
            private, why = doc.get("private_content"), doc.get("why")
            if type(target) is not int or type(index) is not int or verdict not in VERDICTS \
                    or (target, index) not in declared or private not in (None, "yes", "no") \
                    or (private == "yes" and "why" in doc) or ("why" in doc and not isinstance(why, str)):
                malformed(seq, None)
                continue
            shown = why if private == "no" and isinstance(why, str) and why.strip() else None
            item = items.get((target, index))
            if item is None:
                continue
            replier = agents.get(actor, {})
            if verdict == "object":
                if replier.get("identity") == item["declared_by"]["identity"]:
                    result["unauthorized"].append({"seq": seq, "verdict": verdict, "reason": "same-identity"})
                    continue
                item["objections"].append({"seq": seq, "by": actor, "why": shown})
                item["settlement"] = None
            elif replier.get("role") != "coordinator":
                result["unauthorized"].append({"seq": seq, "verdict": verdict, "reason": "not-coordinator"})
            elif replier.get("identity") == item["declared_by"]["identity"]:
                result["unauthorized"].append({"seq": seq, "verdict": verdict, "reason": "self-settlement"})
            else:
                item["settlement"] = {"seq": seq, "verdict": verdict, "by": actor}
            continue
        task = event["data"].get("task")
        if doc.get("answer") not in ("yes", "no") or doc.get("task", task) != task:
            malformed(seq, None)
            continue
        counts["declarations"] += 1
        if doc["answer"] == "no":
            counts["answered_no"] += 1
            continue
        entries = doc.get("items")
        if not isinstance(entries, list) or not entries:
            malformed(seq, None)
            continue
        declarer = agents.get(actor, {})
        for index, entry in enumerate(entries):
            declared.add((seq, index))
            if not isinstance(entry, dict):
                malformed(seq, index)
                continue
            private = entry.get("private_content")
            if private == "yes" and "sentence" in entry:
                private = "conflict"
            if private == "conflict":
                malformed(seq, index)
                continue
            evidence, ok = check_evidence(entry.get("evidence"))
            if private == "yes":
                withhold(seq, index, "private", evidence)
                continue
            if private is None:
                withhold(seq, index, "unanswered", evidence)
                continue
            sentence = entry.get("sentence")
            if private != "no" or entry.get("type") not in TYPES or entry.get("certainty") not in CERTAINTY \
                    or not isinstance(sentence, str) or not sentence.strip() \
                    or not isinstance(entry.get("who", ""), str):
                malformed(seq, index)
                continue
            if not ok:
                result["evidence_not_found"].append({"seq": seq, "item": index})
                continue
            channel = next((by_seq[e["event"]]["data"].get("channel") for e in evidence
                            if "event" in e and e["kind"] == "decision"), None)
            items[(seq, index)] = {
                "type": entry["type"], "sentence": sentence, "who": entry.get("who"), "certainty": entry["certainty"],
                "owner_channel": channel, "evidence": evidence, "task": task, "item": index,
                "declared_by": {"seat": actor, "identity": declarer.get("identity"), "seq": seq},
                "objections": [], "settlement": None}

    for key in sorted(items):
        item = items.pop(key)
        settlement = item.pop("settlement")
        if settlement and settlement["verdict"] == "drop":
            result["dropped"].append({"seq": key[0], "item": key[1]})
        elif item["objections"] and not settlement:
            result["contested"].append(item)
        else:
            result["candidates"].append(item)
    return result, counts
