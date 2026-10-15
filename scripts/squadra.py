#!/usr/bin/env python3
"""Squadra CLI: one durable record shared by Codex, Claude and human seats."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid

from registry import (Store, Error, VERSION, plain, BRIEF_BYTES, REFERENCE_KINDS, canonical, digest, require, snapshot, table, board, now,
                      git, waits, requirement_matrix, presence, standby, latest_ping, return_ping,
                      current_proofs, candidate_checks, gate_checks, read_at_gate)


def resolve_follows(state, values):
    """Resolve ask:<id> and decision:<id> to their recorded events."""
    targets = []
    for value in values:
        kind, separator, name = value.partition(":")
        require(bool(separator) and kind in REFERENCE_KINDS and bool(name),
                "--follows needs a qualified target: indica ask: o decision: (got " + repr(value) + ")")
        seq = state["refs"][kind].get(name)
        require(seq is not None, "Unknown " + kind + ": " + name)
        targets.append({"kind": kind, "id": name, "seq": seq})
    return targets


def workspace_path(value=None):
    if value:
        return Path(value).resolve()
    if os.environ.get("SQUADRA_ROOT"):
        return Path(os.environ["SQUADRA_ROOT"]).resolve()
    for path in [Path.cwd(), *Path.cwd().parents]:
        if (path / ".squadra/registry.sqlite3").is_file():
            return path / ".squadra"
    return Path.cwd() / ".squadra"


def bindings(store, task_id, require_review=True):
    state = store.resolved()
    task = state["tasks"][task_id]
    current = snapshot(task["repo"], store.root)
    require(current == task.get("candidate"), "Candidate drift; deliver/prove the new exact source")
    store.verify_artifacts(task)
    plan = task.get("plan")
    if plan:
        require(Path(plan["original"]).is_file() and digest(Path(plan["original"]).read_bytes()) == plan["sha256"], "Plan drift")
        plans = [r for r in task["reviews"] if r["stage"] == "plan"]
        require(plans and plans[-1]["binding"] == plan["sha256"] and plans[-1]["verdict"] == "accepted", "Current plan review pending")
    if task.get("route") == "light" or task.get("contract"):
        from light_route import check_contract, measure
        check_contract(task)
        if task["route"] == "light":
            measure(task, current)
    candidate_checks(task, current, require_review and not read_at_gate(task))
    if require_review and task.get("fix"):
        from fix_challenge import evidence, gate_exception
        gate_exception(task)
        evidence(task, current, store.root, require_review=not read_at_gate(task))
    if require_review and task.get("release_gate") is True:
        gate_checks(state, task, current)
    return current


def fix_review_fields(task):
    if not task.get("fix"):
        return {}
    from fix_challenge import binding, disposition
    return {"fix_binding": binding(task), "fix_disposition": disposition(task)}


def deliver(store, actor, task_id, reconcile=False):
    state = store.resolved(); task = state["tasks"][task_id]
    require(actor == task["owner"] or (reconcile and state["agents"][actor]["role"] == "coordinator"),
            "Assigned owner required for delivery")
    current = snapshot(task["repo"], store.root)
    previous = task.get("delivery", {})
    from source_manifests import transform_packet
    previous_packet = transform_packet(previous.get("packet_data", {}), store.resolve_snapshot)
    if reconcile:
        require(task.get("candidate") == current, "Candidate drift; owner must prove and deliver again")
        if not task.get("reviewer") and not task.get("pool"):
            return {"manual_review": True, "reason": "No configured reader; existing independent review protocol applies"}

    operations = []
    data = {"task": task_id, "snapshot": current}
    if task.get("fix"):
        from fix_challenge import binding, disposition
        data.update(fix_binding=binding(task), fix_disposition=disposition(task))
    if task.get("contract"):
        data["contract"] = task["contract"]
        from light_route import check_contract, measure
        check_contract(task)
        if task["route"] == "light":
            data["light"] = measure(task, current)
    reader = task.get("reviewer")
    if reader or task.get("pool"):
        packet = {"task": task_id, "candidate": current, "base": task["base"],
                  **({"pool": list(task["pool"])} if task.get("pool") else {"reviewer": reader}),
                  "scope": task["scope"], "plan": task.get("plan"), "requirements": task.get("requirements", []),
                  "proofs": current_proofs(task, current),
                  "instructions": "ACK this exact message, read frozen candidate in an isolated copy, record independent verdict and limitations; never publish or edit the candidate."}
        if task.get("contract"):
            packet["contract"] = task["contract"]
            if "light" in data:
                packet["light"] = data["light"]
        if task.get("fix"):
            packet.update(fix=task["fix"], fix_result=task.get("fix_result"))
        if previous.get("handoff") and previous_packet == packet:
            return {"handoff": previous["handoff"], "packet": previous["packet"], "already_delivered": True}
        if previous.get("handoffs") and previous_packet == packet:
            # The same packet keeps its handoffs, ACKs and claim.
            return {"handoffs": previous["handoffs"], "packet": previous["packet"], "already_delivered": True}
        require(actor == task["owner"], "Owner must reconcile changed/missing handoff; coordinator may inspect an identical existing packet")
        from source_manifests import transform_packet
        packet = transform_packet(packet, store.compact_snapshot)
        key = digest(canonical(packet).encode())
        message = "review-" + key
        artifact = store.artifact_bytes((canonical(packet) + "\n").encode(), message + ".json", "generated-handoff:" + message)
        body = "Read exact candidate " + current["head"] + " for " + task_id + ".\nImmutable packet " + artifact["sha256"] + " in " + str(store.root / "artifacts") + ".\nActual recipient ACK required before independent review."
        if task.get("pool"):
            handoffs = [message + "-" + member for member in task["pool"]]
            data.update(packet=artifact, packet_data=packet, handoffs=handoffs)
            for member, handoff in zip(task["pool"], handoffs):
                operations.append(("message", task["coordinator"], {"id": handoff, "to": member, "related_task": task_id,
                                   "pool": handoffs, "body": body + "\nThe first ACK in the pool takes the read."}, handoff))
        else:
            data.update(packet=artifact, packet_data=packet, handoff=message)
            operations.append(("message", task["coordinator"], {"id": message, "to": reader, "related_task": task_id,
                               "body": body}, message))
    from precheck import run as precheck
    checked = precheck(store, actor, task, current)
    if checked:
        # The delivered bytes must still be the checked ones when the delivery is written.
        final = snapshot(task["repo"], store.root)
        require(final == checked[0] and final == current,
                "precheck_failed: source changed after the checks (checked " + checked[0]["sha256"][:12] +
                ", now " + final["sha256"][:12] + "); deliver again")
    operations.insert(0, ("deliver", actor, data, None))
    store.batch(operations)
    if "handoffs" in data:
        result = {"handoffs": data["handoffs"], "packet": data["packet"], "snapshot": current["sha256"]}
    else:
        result = {"handoff": data.get("handoff"), "packet": data.get("packet"), "snapshot": current["sha256"]}
    if checked:
        result["precheck"] = checked[1]
    return result


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", help="Shared persistent workspace; or SQUADRA_ROOT")
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("forecast", help="Read-only comparison with similar completed tasks; no estimate below three samples")
    a.add_argument("--task", required=True)
    a = sub.add_parser("balance", help="Read-only advice on windows and models by load, quota and history; assigns nothing")
    a.add_argument("--task", help="Rank build and read candidates for this open task")
    a.add_argument("--reference", help="JSON file with the declared Claude reference {\"claude\":{\"h5\":N,\"week\":N}}")
    a = sub.add_parser("usage", help="Read-only local token usage; partial coverage after a 30-second collection deadline")
    a.add_argument("--json", action="store_true", help="Print the same measured data as JSON")
    a.add_argument("--prices", metavar="FILE", help="Optional local price table for a labelled estimate; no default prices")
    a = sub.add_parser("init"); a.add_argument("--name", required=True); a.add_argument("--project", required=True)
    a.add_argument("--lang", choices=["it", "en"], help="Workspace board language (default it)")
    a.add_argument("--temporary", action="store_true", help="Explicit disposable test/demo workspace")
    a.add_argument("--plan-rounds", type=int, default=3); a.add_argument("--review-rounds", type=int, default=2)
    a = sub.add_parser("agent"); a.add_argument("--id", required=True); a.add_argument("--identity", required=True)
    a.add_argument("--role", choices=["coordinator", "builder", "executor", "reviewer"], required=True)
    a.add_argument("--client", choices=["codex", "claude", "cursor", "manual"], required=True)
    a.add_argument("--model", default=""); a.add_argument("--session", default=""); a.add_argument("--repo", required=True)
    a.add_argument("--skill", action="append", default=[])
    a = sub.add_parser("census", help="Observe local Codex/Claude processes and registered replies without writes")
    a.add_argument("--project", help="Project path; required before init, otherwise registry project")
    a = sub.add_parser("ping", help="Record/reuse challenges, or reconcile availability as coordinator")
    a.add_argument("--actor", required=True)
    target = a.add_mutually_exclusive_group(required=True)
    target.add_argument("--to", help="Target seat; a stand-by seat can receive a fresh challenge")
    target.add_argument("--all", action="store_true", help="One challenge per other active identity")
    target.add_argument("--sweep", action="store_true", help="Record due stand-by and fresh-ACK returns; no process actions")
    a = sub.add_parser("standby", help="Coordinator records stand-by or an explicit return; work/resources stay owned")
    a.add_argument("--actor", required=True); a.add_argument("--agent", required=True)
    a.add_argument("--reason", required=True); a.add_argument("--resume", action="store_true", help="Record return to active")
    a = sub.add_parser("task"); a.add_argument("--id", required=True); a.add_argument("--title", required=True)
    a.add_argument("--actor", required=True); a.add_argument("--owner", required=True); a.add_argument("--repo", required=True)
    a.add_argument("--scope-file", required=True); a.add_argument("--proof-file", help="JSON argv, not a shell string")
    a.add_argument("--expected", type=int); a.add_argument("--full-gate", action="store_true")
    a.add_argument("--depends", action="append", default=[])
    a.add_argument("--reviewer", action="append", help="Reader; repeat for a pool of 2-4 where the first ACK takes the read")
    a.add_argument("--requirements-file")
    a.add_argument("--read-at-gate", action="store_true", help="Light only: close without its own read; a release gate reads it")
    a.add_argument("--release-gate", action="store_true", help="Check/close require every outstanding read-at-gate task covered or deferred")
    a.add_argument("--defect-fix", action="store_true", help="Require a bound original-red/remedy-green challenge before close")
    a.add_argument("--challenge-file", help="JSON oracle agreement; default runner python-unittest-v1")
    a.add_argument("--route", choices=["full", "light"], default="full", help="Explicit light-v1 small-change contract; default full")
    a.add_argument("--supersedes", help="Existing open task this one replaces; its history stays")
    a = sub.add_parser("route", help="Coordinator promotes light to full, retaining base, history and budgets")
    a.add_argument("--actor", required=True); a.add_argument("--task", required=True)
    a.add_argument("--full", action="store_true", required=True)
    a.add_argument("--full-gate", action="store_true", help="Require a reserved whole gate on the full route")
    a.add_argument("--scope-file", required=True); a.add_argument("--proof-file", required=True)
    a.add_argument("--reason", required=True); a.add_argument("--expected", type=int, default=argparse.SUPPRESS)
    a = sub.add_parser("supersede", help="Coordinator records that an existing later task replaces an open one; history stays")
    a.add_argument("--actor", required=True); a.add_argument("--task", required=True, help="Open task being replaced")
    a.add_argument("--by", required=True, help="Existing task recorded after it"); a.add_argument("--reason", required=True)
    a = sub.add_parser("brief"); a.add_argument("--actor", required=True)
    target = a.add_mutually_exclusive_group(required=True)
    target.add_argument("--task"); target.add_argument("--project", action="store_true")
    a.add_argument("--file", required=True, help="Plain text, at most " + str(BRIEF_BYTES) + " bytes")
    a = sub.add_parser("send"); a.add_argument("--actor", required=True); recipient = a.add_mutually_exclusive_group(required=True)
    recipient.add_argument("--to"); recipient.add_argument("--to-new")
    a.add_argument("--body-file", required=True); a.add_argument("--id"); a.add_argument("--reply-to"); a.add_argument("--task")
    a.add_argument("--info", action="store_true", help="Information only; no recipient ACK required")
    a = sub.add_parser("inbox"); a.add_argument("--agent", required=True)
    a = sub.add_parser("ack"); a.add_argument("--actor", required=True); a.add_argument("--message", required=True)
    a = sub.add_parser("transport"); a.add_argument("--actor", required=True); a.add_argument("--message", required=True)
    a.add_argument("--via", choices=["codex-queue", "manual"], required=True)
    a = sub.add_parser("plan"); a.add_argument("--actor", required=True); a.add_argument("--task", required=True); a.add_argument("--file", required=True)
    for command in ["start", "deliver", "advance", "check", "close"]:
        a = sub.add_parser(command); a.add_argument("--actor", required=True); a.add_argument("--task", required=True)
    a = sub.add_parser("review"); a.add_argument("--actor", required=True); a.add_argument("--task", required=True)
    a.add_argument("--stage", choices=["plan", "code"], default="code")
    a.add_argument("--verdict", choices=["accepted", "changes-required", "blocked"], required=True)
    a.add_argument("--report", required=True)
    a.add_argument("--findings-file", help="Optional bounded JSON observations (severity and one-line title)")
    a.add_argument("--covers", action="append", default=[])
    a.add_argument("--lens", choices=["rasoio"], help="Optional lean-code lens (references/skills/rasoio.md)")
    a.add_argument("--net-lines", type=int, help="With --lens: declared lines that could be removed")
    a.add_argument("--gate-covers", action="append", default=[], help="Release gate read: an outstanding read-at-gate task this read covers")
    a = sub.add_parser("lesson", help="Record, retire, mark ineffective or list errata lessons")
    a.add_argument("--actor"); a.add_argument("--task"); a.add_argument("--source-event", type=int)
    a.add_argument("--text"); a.add_argument("--path", action="append", default=[]); a.add_argument("--word", action="append", default=[])
    a.add_argument("--any-source", action="store_true", help="Cite an event of the task that is not a red")
    form = a.add_mutually_exclusive_group()
    form.add_argument("--retire"); form.add_argument("--ineffective"); form.add_argument("--list", action="store_true")
    a.add_argument("--recall", type=int); a.add_argument("--reason")
    a = sub.add_parser("output"); a.add_argument("--actor", required=True); a.add_argument("--task")
    a.add_argument("--file", required=True); a.add_argument("--next", required=True)
    a.add_argument("--artifact", action="append", default=[])
    for command in ["prove", "probe", "run"]:
        a = sub.add_parser(command); a.add_argument("--actor", required=True); a.add_argument("--task", required=True)
        a.add_argument("--timeout", type=int, default=120 if command != "run" else 600)
        if command in {"prove", "probe"}:
            if command == "prove":
                a.add_argument("--gate", action="store_true")
                a.add_argument("--covers", action="append", default=[])
            else:
                a.add_argument("--expected-rc", type=int, required=True)
            a.add_argument("argv", nargs=argparse.REMAINDER)
        else:
            a.add_argument("--mode", choices=["read", "build"], required=True)
            a.add_argument("--stage", choices=["plan", "code"], default="code")
            a.add_argument("--prompt-file", required=True); a.add_argument("--cli")
    a = sub.add_parser("cancel"); a.add_argument("--actor", required=True); a.add_argument("--run", required=True)
    a.add_argument("--abandon", action="store_true", help="Starter declares an orphan with no process and no results")
    a.add_argument("--reason"); a.add_argument("--evidence", help="Explicit squadra-run-abandon/1 observation JSON")
    a = sub.add_parser("resource"); a.add_argument("action", choices=["acquire", "release"])
    a.add_argument("--actor", required=True); a.add_argument("--name", required=True); a.add_argument("--reason", required=True)
    for command in ["pause", "resume", "decision"]:
        a = sub.add_parser(command); a.add_argument("--actor", required=True); a.add_argument("--reason", required=True)
        if command == "decision":
            a.add_argument("--answers"); a.add_argument("--choice")
            a.add_argument("--channel", choices=["typed-here", "relayed"])
            a.add_argument("--owner-words"); a.add_argument("--withdraws")
            a.add_argument("--id", help="Stable decision ID; default dec-e<event number>")
            a.add_argument("--task", action="append", default=[], help="Existing task this decision concerns")
            a.add_argument("--follows", action="append", default=[], help="Earlier ask:<id> or decision:<id>")
            a.add_argument("--defer-read", help="Outstanding read-at-gate task deferred past --gate")
            a.add_argument("--gate", help="Open release gate the --defer-read task is deferred from")
    a = sub.add_parser("ask"); a.add_argument("--actor", required=True); a.add_argument("--id", required=True)
    a.add_argument("--question", required=True); a.add_argument("--options-file", required=True)
    a.add_argument("--recommended"); a.add_argument("--task"); a.add_argument("--waiting", action="append", default=[])
    a.add_argument("--evidence", action="append", default=[])
    a = sub.add_parser("legacy"); a.add_argument("--actor", required=True); a.add_argument("--path", required=True)
    a = sub.add_parser("checkpoint"); a.add_argument("--to", required=True)
    a = sub.add_parser("save"); a.add_argument("--actor", required=True); a.add_argument("--to", required=True)
    a.add_argument("--repo", action="append", default=[]); a.add_argument("--include", action="append", default=[])
    a.add_argument("--temporary", action="store_true"); a.add_argument("--declared-offsite")
    a = sub.add_parser("restore-check"); a.add_argument("--from", dest="package", required=True)
    a.add_argument("--to", required=True); a.add_argument("--actor")
    a = sub.add_parser("settings"); a.add_argument("--actor", required=True); a.add_argument("--file", required=True)
    a = sub.add_parser("challenge"); a.add_argument("--actor", required=True); a.add_argument("--oracle")
    a.add_argument("--original"); a.add_argument("--remedy"); a.add_argument("--positive")
    a.add_argument("--output"); a.add_argument("--timeout", type=int, default=30)
    a.add_argument("--task"); mode = a.add_mutually_exclusive_group()
    mode.add_argument("--register", action="store_true", help="Coordinator marks a fix or replaces its oracle agreement")
    mode.add_argument("--exception", action="store_true", help="Coordinator declares a candidate-bound exception")
    a.add_argument("--contract-file"); a.add_argument("--reason")
    a = sub.add_parser("bench-example"); a.add_argument("--to", required=True)
    a = sub.add_parser("bench-run"); a.add_argument("--actor", required=True); a.add_argument("--manifest", required=True)
    a.add_argument("--output", required=True); a.add_argument("--allow-models", action="store_true"); a.add_argument("--budget-file")
    a = sub.add_parser("bench-score"); a.add_argument("--actor", required=True); a.add_argument("--from", dest="run_folder", required=True); a.add_argument("--oracle", required=True)
    a = sub.add_parser("plugin", help="List or install plugins bundled in this release")
    commands = a.add_subparsers(dest="plugin_command", required=True)
    commands.add_parser("list")
    install = commands.add_parser("install"); install.add_argument("name")
    a = sub.add_parser("status"); a.add_argument("--all", action="store_true", help="Print every wait and the full detailed table")
    sub.add_parser("doctor"); sub.add_parser("export")
    a = sub.add_parser("board"); a.add_argument("--lang", choices=["it", "en"], help="Override the workspace board language")
    a = sub.add_parser("dash"); a.add_argument("--port", type=int, default=0); a.add_argument("--open", action="store_true")
    a = sub.add_parser("selftest"); a.add_argument("--output", help="New directory for durable test receipts")
    for child in sub.choices.values():
        child.add_argument("--root", default=argparse.SUPPRESS, help="Shared persistent workspace")
    return p


def seq_of(store, event_id):
    return store.db.execute("SELECT seq FROM events WHERE id=?", (event_id,)).fetchone()[0]


def recall(store, state, actor, task, at, operation, extra=""):
    """Append brief/start, plus the lessons it showed; without a match nothing differs from before."""
    from errata import select
    shown = select(state, store.root, task, extra) if task in state["tasks"] and state.get("lessons") else []
    if not shown:
        store.batch([operation])
        return None
    saved = store.batch([operation, ("lesson.recall", actor, {"task": task, "at": at,
                                     "lessons": [s["id"] for s in shown]}, None)])
    return {"lessons": shown, "recall": seq_of(store, saved[1])}


def lesson(store, state, a):
    import errata
    if a.list:
        return errata.listing(state)
    require(bool(a.actor), "lesson_actor_required")
    if a.retire:
        event = store.append("lesson.retire", a.actor, {"lesson": a.retire, "reason": a.reason})
        return {"lesson": a.retire, "retired": True, "event": seq_of(store, event)}
    if a.ineffective:
        event = store.append("lesson.ineffective", a.actor, {"lesson": a.ineffective, "recall": a.recall, "reason": a.reason})
        return {"lesson": a.ineffective, "ineffective": True, "event": seq_of(store, event)}
    require(a.task in state["tasks"] and a.source_event is not None, "lesson_source_required")
    eligible = errata.source_check(state, store.events(), a.task, a.source_event, a.any_source)
    name = errata.next_id(state)
    trigger = {k: v for k, v in (("paths", a.path), ("words", a.word)) if v}
    event = store.append("lesson", a.actor, {"id": name, "text": a.text, "trigger": trigger,
                         "source": {"task": a.task, "event": a.source_event, "eligible": eligible}})
    return {"lesson": name, "event": seq_of(store, event), "source_eligible": eligible}


def dispatch(a, store):
    command = a.command
    state = store.view()
    actor = getattr(a, "actor", "system")
    if command == "init":
        root = store.root
        temporary_roots = [Path(tempfile.gettempdir()).resolve(), Path("/private/tmp")]
        require(a.temporary or not any(t == root or t in root.parents for t in temporary_roots),
                "Persistent workspace cannot live in tmp; --temporary is for explicit demos/tests")
        project = str(Path(a.project).resolve(strict=True))
        require(a.plan_rounds > 0 and a.review_rounds > 0, "Positive round limits required")
        store.append("init", "system", {"name": a.name, "project": project, "version": VERSION,
                     "temporary": a.temporary, "limits": {"plan": a.plan_rounds, "code": a.review_rounds, "build": 3},
                     **({"lang": a.lang} if a.lang else {})})
    elif command == "agent":
        skills = []
        for file in a.skill:
            artifact = store.artifact(file)
            skills.append({"name": Path(file).parent.name, "path": str(Path(file).resolve()), "sha256": artifact["sha256"]})
        store.append("agent", a.id, {"id": a.id, "identity": a.identity, "role": a.role, "client": a.client,
                    "model": a.model, "session": a.session, "repo": str(Path(a.repo).resolve(strict=True)), "skills": skills})
    elif command == "task":
        base = snapshot(a.repo, store.root)
        proof = json.loads(Path(a.proof_file).read_text()) if a.proof_file else None
        require(proof is None or (isinstance(proof, list) and proof and all(isinstance(x, str) and x for x in proof)), "Proof file must contain a nonempty argv array")
        task = {"id": a.id, "title": a.title, "owner": a.owner, "repo": base["repo"], "base": base["head"],
                "scope": store.artifact(a.scope_file), "proof": proof, "expected": a.expected,
                "full_gate": a.full_gate, "depends": a.depends,
                "reviewer": a.reviewer[0] if a.reviewer and len(a.reviewer) == 1 else None,
                "requirements": json.loads(Path(a.requirements_file).read_text()) if a.requirements_file else []}
        if a.reviewer and len(a.reviewer) > 1:
            task["pool"] = a.reviewer
        if a.read_at_gate:
            task["read_at_gate"] = True
        if a.release_gate:
            task["release_gate"] = True
        require(not a.challenge_file or a.defect_fix, "--challenge-file requires --defect-fix")
        if a.defect_fix:
            from fix_challenge import prepare
            task["fix"] = prepare(store, task, a.challenge_file)
        if a.route == "light":
            from light_route import POLICY, contract
            task.update(route="light", light_policy=dict(POLICY))
            task["contract"] = contract(task)
        if a.supersedes is not None:
            task["supersedes"] = a.supersedes
        assignment = "assign-" + a.id
        store.batch([("task", actor, task, None), ("message", actor, {"id": assignment, "to": a.owner,
                     "task": a.id, "body": f"Assignment {a.id}: {a.title}\nBase {base['head']}\nScope {task['scope']['sha256']}\nACK required before starting."}, None)])
    elif command == "route":
        from light_route import contract
        require(a.task in state["tasks"], "Unknown task")
        task = state["tasks"][a.task]
        data = {"task": a.task, "route": "full", "scope": store.artifact(a.scope_file),
                "proof": json.loads(Path(a.proof_file).read_text()), "reason": a.reason, "full_gate": a.full_gate,
                "expected": getattr(a, "expected", task.get("expected")),
                "previous_contract": task.get("contract")}
        data["contract"] = contract({**task, **data})
        store.append("route", actor, data)
    elif command == "supersede":
        store.append("supersede", actor, {"task": a.task, "by": a.by, "reason": a.reason})
    elif command == "send":
        data = {"id": a.id or uuid.uuid4().hex, "to": a.to or a.to_new, "body": Path(a.body_file).read_text(), "reply_to": a.reply_to}
        if a.info:
            data["info"] = True
        if a.to_new:
            data["awaited"] = True
        if a.task:
            # Only task creation creates an assignment requiring task-start acknowledgment.
            require(a.task in state["tasks"], "Unknown task")
            data["related_task"] = a.task
        store.append("message", actor, data)
        return data
    elif command == "inbox":
        from registry_current import attached, message_ids
        index = attached(state)
        return ([state["messages"][key] for key in message_ids(index, recipient=a.agent)] if index is not None
                else [m for m in state["messages"].values() if m["to"] == a.agent])
    elif command == "ack":
        store.append("ack", actor, {"message": a.message}, "ack:" + a.message + ":" + actor)
    elif command == "standby":
        store.append("availability", actor, {"agent": a.agent, "status": "active" if a.resume else "stand-by",
                                             "reason": a.reason})
    elif command == "ping":
        require(actor in state["agents"], "Registered actor required")
        if a.sweep:
            require(state["agents"][actor]["role"] == "coordinator", "Coordinator role required")
            changes, seen = [], set()
            for row in presence(state, now()):
                if row["identity"] in seen:
                    continue
                seen.add(row["identity"])
                agent = row["agent"]
                returned = return_ping(state, agent)
                if row["availability"] == "active" and row["due"]:
                    changes.append({"agent": agent, "status": "stand-by", "ping": row["ping"],
                                    "reason": "Unanswered ping reached the current alive threshold"})
                elif returned:
                    changes.append({"agent": agent, "status": "active", "ping": returned["id"],
                                    "reason": "Recipient ACKed a post-stand-by challenge"})
            store.batch([("availability", actor, d, None) for d in changes])
            return {"changes": changes, "process_actions": False}
        require(not a.all or state["agents"][actor]["role"] == "coordinator", "Coordinator role required for --all")
        targets = [a.to] if a.to else [x["id"] for x in state["agents"].values()
                  if x["identity"] != state["agents"][actor]["identity"] and not standby(state, x["id"])]
        messages, seen, operations = [], set(), []
        for target in targets:
            require(target in state["agents"], "Registered target required")
            require(state["agents"][actor]["role"] == "coordinator" or state["agents"][target]["role"] == "coordinator",
                    "Only coordinator may ping workers; entrants may ping a coordinator")
            agent = state["agents"][target]
            if agent["identity"] in seen:
                continue
            seen.add(agent["identity"])
            previous = latest_ping(state, target)
            after = agent.get("availability", {}).get("event")
            if previous and not previous["ack"] and previous.get("availability_event") == after:
                messages.append({"id": previous["id"], "to": previous["to"], "from": previous["from"], "reused": True})
                continue
            ident = "ping-" + uuid.uuid4().hex
            message = {"id": ident, "to": target, "ping": True,
                       "body": "Presence check: ACK " + ident + " as " + target + ". A queue receipt is not an answer."}
            if after:
                message["availability_event"] = after
            operations.append(("message", actor, message, ident))
            messages.append({"id": ident, "to": target, "from": actor, "reused": False})
        store.batch(operations)
        return {"messages": messages, "transport": "Use recorded transport; no client was invoked"}
    elif command == "transport":
        import subprocess
        message = state["messages"][a.message]
        require(message["from"] == actor, "Only sender can deliver")
        instruction = ("Information only; no ACK required." if message.get("info") is True
                       else f"ACK via squadra ack --actor {message['to']} --message {a.message} (with shared --root).")
        if a.via == "manual":
            result = {"message": a.message, "via": "manual", "delivered": False,
                      "instruction": f"Run squadra inbox --agent {message['to']} --root {store.root}. {instruction}"}
        else:
            agent = state["agents"][message["to"]]
            require(agent["client"] == "codex" and agent["session"], "Registered Codex session required")
            help_result = subprocess.run(["codex", "queue", "--help"], capture_output=True, text=True, timeout=15)
            require(help_result.returncode == 0 and "--thread" in help_result.stdout and "--message" in help_result.stdout, "Compatible queue not verified")
            text = f"Squadra message {a.message}; workspace {store.root}.\n{message['body']}\n{instruction}"
            proc = subprocess.run(["codex", "queue", "--thread", agent["session"], "--message", text], capture_output=True, text=True, timeout=30)
            result = {"message": a.message, "via": a.via, "delivered": proc.returncode == 0,
                      "native_rc": proc.returncode, "receipt": proc.stdout, "stderr": proc.stderr,
                      "ack": "not required; information only" if message.get("info") is True else "still pending; native queue does not prove wakeup"}
        store.append("transport", actor, result)
        return result
    elif command == "plan":
        store.append("plan", actor, {"task": a.task, "artifact": store.artifact(a.file)})
    elif command == "start":
        return recall(store, state, actor, a.task, "start", ("start", actor, {"task": a.task}, None))
    elif command in {"deliver", "advance"}:
        return deliver(store, actor, a.task, reconcile=command == "advance")
    elif command in {"check", "close"}:
        if command == 'close' and state['tasks'][a.task].get('release_gate') is True:
            store.audit()
        current = bindings(store, a.task)
        result = {"task": a.task, "verified": True, "snapshot": current["sha256"], "publication_authorized": False}
        if command == "close":
            event = store.append("close", actor, {"task": a.task})
            if state["tasks"][a.task].get("fix"):
                from errata import hint
                result["errata"] = hint(a.task, seq_of(store, event), actor, "defect fix closed")
        return result
    elif command == "review":
        from reads import load_findings
        extra = {'findings': load_findings(a.findings_file)} if a.findings_file is not None else {}
        task = store.task(a.task, state)
        if a.stage == "plan":
            require(bool(task.get("plan")), "No registered plan")
            binding = task["plan"]["sha256"]
            require(digest(Path(task["plan"]["original"]).read_bytes()) == binding, "Plan drift")
        else:
            current = bindings(store, a.task, require_review=False)
            binding = current["sha256"]
        lens = {k: v for k, v in (("lens", a.lens), ("net_lines", a.net_lines)) if v is not None}
        event = store.append("review", actor, {"task": a.task, "stage": a.stage, "verdict": a.verdict,
                     "binding": binding, "covers": a.covers, "report": store.artifact(a.report), **extra,
                     **({"gate_covers": a.gate_covers} if a.gate_covers else {}),
                     **({"contract": task["contract"]} if task.get("contract") else {}),
                     **fix_review_fields(task), **lens})
        if a.stage == "code" and a.verdict == "changes-required":
            from errata import hint
            return {"errata": hint(a.task, seq_of(store, event), actor, "code read asked for changes")}
    elif command == "output":
        output = {"task": a.task, "text": Path(a.file).read_text(), "next": a.next,
                  "artifacts": [store.artifact(f) for f in a.artifact]}
        event = store.append("output", actor, output)
        return {"receipt": event, "saved": True, "task": a.task}
    elif command in {"prove", "probe", "run"}:
        from runtime import execute
        argv = a.argv[1:] if command != "run" and a.argv and a.argv[0] == "--" else getattr(a, "argv", None)
        checked = None
        if command == "prove" and a.task in state["tasks"]:
            from precheck import run as precheck
            checked = precheck(store, actor, state["tasks"][a.task])
        result = execute(store, actor, a.task, argv=argv, mode=a.mode if command == "run" else "proof" if command == "prove" else "probe",
                         prompt=Path(a.prompt_file).read_text() if command == "run" else None,
                         timeout=a.timeout, gate=getattr(a, "gate", False), executable=getattr(a, "cli", None),
                         stage=getattr(a, "stage", "code"), expected_rc=getattr(a, "expected_rc", 0), covers=getattr(a, "covers", []),
                         checked=checked[0] if checked else None)
        if result["status"] not in {"passed", "matched"}:
            if command == "prove":
                from errata import hint
                end = next((e for e in reversed(store.events()) if e["kind"] == "run.end"
                            and e["data"].get("id") == result.get("id")), None)
                if end:
                    result["errata"] = hint(a.task, end["seq"], actor, "proof failed")
            raise Error(json.dumps(result, ensure_ascii=False))
        if checked:
            result["precheck"] = checked[1]
        return result
    elif command == "cancel":
        if a.abandon:
            from registry import abandonment_observation
            require(a.reason and a.evidence, "--abandon requires --reason and --evidence")
            require(a.run in state["runs"], "Unknown run")
            with Path(a.evidence).open("rb") as source:
                body = source.read(64 * 1024 + 1)
            require(len(body) <= 64 * 1024, "Abandonment observation exceeds 64 KiB")
            observation = abandonment_observation(json.loads(body), a.run)
            run = state["runs"][a.run]
            store.append("run.abandon", actor, {"id": a.run,
                "starter": state["agents"][run["agent"]]["identity"], "reason": a.reason,
                "observation": observation, "evidence": store.artifact(a.evidence),
                "legacy": not run.get("lease_version")})
        else:
            require(not a.reason and not a.evidence, "--reason and --evidence require --abandon")
            store.append("run.cancel", actor, {"id": a.run})
    elif command == "resource":
        store.append("resource." + a.action, actor, {"name": a.name, "reason": a.reason})
    elif command == "ask":
        store.append("ask", actor, {"id": a.id, "question": a.question,
                     "options": json.loads(Path(a.options_file).read_text()), "recommended": a.recommended,
                     "task": a.task, "waiting": a.waiting, "evidence": [store.artifact(f) for f in a.evidence]})
    elif command in {"pause", "resume", "decision"}:
        data = {"reason": a.reason}
        if command == "decision":
            data.update({k: getattr(a, k) for k in ("answers", "choice", "channel", "owner_words", "withdraws", "id")
                         if getattr(a, k) is not None})
            require(bool(a.defer_read) == bool(a.gate), "gate_defer_invalid: --defer-read and --gate go together")
            if a.defer_read:
                data["defer_read"] = {"task": a.defer_read, "gate": a.gate}
                a.task = list(dict.fromkeys(a.task + [a.defer_read, a.gate]))
            if a.task:
                data["tasks"] = a.task
            if a.follows:
                data["follows"] = resolve_follows(state, a.follows)
        store.append(command, actor, data)
    elif command == "brief":
        path = Path(a.file).resolve(strict=True)
        body = path.read_bytes()
        require(len(body) <= BRIEF_BYTES, "Brief exceeds " + str(BRIEF_BYTES) + " bytes")
        text = body.decode("utf-8")
        data = {"project": True} if a.project else {"task": a.task}
        # The registered artifact is exactly the bytes whose text is recorded.
        operation = ("brief", actor, {**data, "text": text, "artifact": store.artifact_bytes(body, path.name, str(path))}, None)
        if a.project or a.task not in state["tasks"]:
            store.batch([operation])
        else:
            return recall(store, state, actor, a.task, "brief", operation, extra=text)
    elif command == "lesson":
        return lesson(store, state, a)
    elif command == "legacy":
        path = Path(a.path).resolve(strict=True)
        files = []
        for name in ["manifest.json", "MANIFEST.json", "stato.md", "CURRENT.md", "RIPRESA.md", "squadra-posta.md"]:
            if (path / name).is_file():
                files.append(store.artifact(path / name))
        require(bool(files), "No recognized legacy records; original source not modified")
        store.append("legacy", actor, {"path": str(path), "files": files,
                     "notice": "Historical snapshot only; not a new assignment, ACK or independently verified acceptance"})
    elif command == "checkpoint":
        return {"checkpoint": store.checkpoint(a.to), "canonical_events_and_artifacts": True,
                "source_code": "Not included: preserve worktree and Git bundle separately"}
    elif command == "save":
        from recovery import save
        require(state["agents"][actor]["role"] == "coordinator", "Coordinator role required")
        result = save(store, a.to, a.repo, a.include, a.temporary, a.declared_offsite)
        store.append("save", actor, {"path": str(Path(a.to).resolve()), "manifest": store.artifact(Path(a.to) / "MANIFEST.json")})
        return {"saved": str(Path(a.to).resolve()), "repositories": len(result["repositories"]),
                "scope": result["scope"], "destination": result["destination"], "offsite_verified": False}
    elif command == "settings":
        store.append("settings", actor, {"wait_seconds": json.loads(Path(a.file).read_text())})
    elif command == "challenge":
        if a.task:
            from fix_challenge import register, execute
            require(not any((a.original, a.remedy, a.positive, a.oracle)), "Task challenge uses the registered inputs")
            if a.register:
                require(not a.output, "Registration does not execute a challenge")
                register(store, actor, a.task, a.contract_file, a.reason)
                return {"task": a.task, "registered": True}
            require(not a.contract_file, "--contract-file requires --register")
            require(a.exception or (a.output and not a.reason), "Challenge output required; reasons belong to exceptions")
            require(not a.exception or a.reason, "fix_exception_reason_required")
            result = execute(store, actor, a.task, a.output, a.timeout, a.reason if a.exception else None)
            require(result.get("exception") or result["status"] == "matched", "fix_challenge_not_matched; read retained logs")
            return result
        require(all((a.oracle, a.original, a.remedy, a.positive, a.output)) and
                not any((a.register, a.exception, a.contract_file, a.reason)), "Standalone challenge needs oracle/original/remedy/positive/output")
        from challenge import run
        require(not state["paused"], "Workspace paused")
        require(actor in state["agents"], "Registered actor required")
        result = run(a.oracle,a.original,a.remedy,a.positive,a.output,a.timeout)
        files = [p for p in Path(a.output).iterdir() if p.is_file() and p.suffix in {".log", ".json", ".py"}]
        store.append("challenge", actor, {"status": result["status"], "artifacts": [store.artifact(p) for p in files]})
        require(result["status"] == "matched", "Challenge did not distinguish original assertion from remedy/positive; read preserved results")
        return result
    elif command in {"bench-run", "bench-score"}:
        from benchmark import run, score
        require(actor in state["agents"] and not state["paused"], "Registered actor and resumed workspace required")
        if command == "bench-run":
            result=run(a.manifest,a.output,a.allow_models,a.budget_file,store=store);folder=Path(a.output)
            files=[folder/'RUNS.json',folder/'manifest.json']
        else:
            result=score(a.run_folder,a.oracle);folder=Path(a.run_folder);files=[folder/'SCORE.json',folder/'REPORT.html']
        store.append("benchmark",actor,{"command":command,"status":result["status"],"artifacts":[store.artifact(p) for p in files]})
        require(result["status"] in {"completed", "scored"}, "Benchmark failed; native receipt and logs preserved")
        return {"status":result["status"],"folder":str(folder.resolve()),"mode":result.get("mode"),"quality_claim":False}
    elif command == "doctor":
        checked = store.audit()
        events, state = checked['events'], checked['state']
        compatibility = ({"compatibility": "This registry requires Squadra 3.10.1 or later."}
                         if any(event.get('format') == 2 for event in events) else {})
        return {**compatibility, "events": len(events), "integrity": "passed", "cache": checked["cache"], "unknown_running": [r["id"] for r in state["runs"].values() if r["status"] in {"running", "cleanup-unknown"}],
                "abandoned": [{"id": r["id"], "native_rc": None, "results": "none",
                               "legacy": r["abandonment"]["legacy"], "reason": r["abandonment"]["reason"]}
                              for r in state["runs"].values() if r["status"] == "abandoned"],
                "pending_ack": [m["id"] for m in state["messages"].values() if m["ack"] is None and m.get("info") is not True],
                "waits": waits(state, now()),
                "warning": "Integrity verifies the record, not truth of agents' claims or external process liveness"}
    elif command == "dash":
        from dashboard import serve
        serve(store.root, port=a.port, open_browser=a.open)
    return None


def main(argv=None):
    a = parser().parse_args(argv)
    store = None
    try:
        if a.command == "usage":
            import time
            import usage
            store = Store(workspace_path(a.root), read_only=True)
            events = store.events()
            state = store.state(events, resolve=False)
            deadline = time.monotonic() + 30
            while True:
                result = usage.collect(events, state)
                if result['coverage']['status'] != 'collecting' or time.monotonic() >= deadline:
                    break
            if a.prices:
                result['cost'] = usage.estimate(result, usage.read_prices(a.prices))
            print(json.dumps(result, ensure_ascii=False, indent=2) if a.json else usage.text_report(result))
            return 0
        if a.command == "forecast":
            from forecast import calculate
            store = Store(workspace_path(a.root), read_only=True)
            state = store.view()
            events = () if getattr(state, "current", None) is not None else store.events()
            print(json.dumps(calculate(state, events, a.task), ensure_ascii=False, indent=2))
            return 0
        if a.command == "balance":
            import balance
            reference = balance.parse_reference(Path(a.reference).read_bytes()[:balance.REFERENCE_BYTES + 1]) if a.reference else None
            store = Store(workspace_path(a.root), read_only=True)
            events = store.events()
            state = store.state(events, resolve=False)
            if a.task is not None and a.task not in state["tasks"]:
                raise Error("Unknown task: " + a.task)
            print(json.dumps(balance.calculate(state, events, a.task, balance.collect_usage(events, state), reference),
                             ensure_ascii=False, indent=2))
            return 0
        if a.command == "board":
            store = Store(workspace_path(a.root), read_only=True)
            print(board(store.view(), a.lang))
            return 0
        if a.command == "census":
            from census import collect
            root = workspace_path(a.root)
            state = None
            if (root / "registry.sqlite3").is_file():
                store = Store(root, read_only=True)
                state = store.resolved()
            require(a.project or state, "--project required before registry initialization")
            print(json.dumps(collect(a.project or state["workspace"]["project"], state), ensure_ascii=False, indent=2))
            return 0
        if a.command == "bench-example":
            from benchmark import example
            print(json.dumps(example(a.to),indent=2));return 0
        if a.command == "restore-check":
            from recovery import restore_check
            result = restore_check(a.package, a.to)
            if a.root and a.actor:
                store = Store(workspace_path(a.root))
                store.append("restore.check", a.actor, {"result": store.artifact(Path(a.to) / "RESTORE-RESULT.json")})
                store.project()
                result["recorded"] = True
            else:
                result["recorded"] = False
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if a.command == "selftest":
            from selftest import run
            result = run(a.output)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            print("\n| Ruolo | Prova | Esito | Registro | Prossimo passo |\n|---|---|---|---|---|\n"
                  f"| selftest | {result.get('tests', 0)} test sintetici | {result['status']} | {result['folder']}/RESULT.json | Leggere il rapporto; nessuna accettazione di prodotto |")
            return 0 if result["status"] == "passed" else 1
        if a.command == "plugin":
            import plugin_store
            import plugins
            try:
                result = (plugin_store.catalogue(Path.home()) if a.plugin_command == "list"
                          else plugin_store.install(Path.home(), a.name))
            except (plugins.Refused, OSError) as exc:
                print("Squadra: " + (exc.code if isinstance(exc, plugins.Refused) else "io_error"), file=sys.stderr)
                return 1
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        read_only = (a.command in {'status', 'inbox', 'check', 'doctor'} or
                     (a.command == 'lesson' and a.list))
        store = Store(workspace_path(a.root), create=a.command == "init", read_only=read_only)
        result = dispatch(a, store)
        if a.command != "dash":
            if a.command == 'export':
                state, events = store.project(with_events=True)
            else:
                if not read_only:
                    store.publish()
                state, events = store.view(), ()
            if result is not None:
                visible = dict(result) if isinstance(result, dict) else result
                if isinstance(visible, dict):
                    for key in ["after", "before", "snapshot"]:
                        value = visible.get(key)
                        if isinstance(value, dict) and "files" in value:
                            visible[key] = {"head": value["head"], "sha256": value["sha256"], "files": len(value["files"])}
                print(json.dumps(plain(visible), ensure_ascii=False, indent=2))
            if a.command not in {"inbox", "doctor"}:
                print(table(state, read_at=now(), events=events, compact=a.command != 'status' or not a.all))
                if a.command == 'supersede':
                    print('- ' + a.task + ' → ' + a.by)
            forecast_task = a.id if a.command == 'task' else getattr(a, 'task', None)
            if a.command in {'task', 'brief', 'start'} and forecast_task:
                from forecast import calculate
                import balance
                if getattr(state, "current", None) is None:
                    events = store.events()
                predicted = calculate(state, events, forecast_task)
                print(json.dumps({'forecast': predicted}, ensure_ascii=False, indent=2))
                print(json.dumps({'balance': balance.calculate(state, events, forecast_task, forecast_result=predicted)},
                                 ensure_ascii=False, indent=2))
        return 0
    except (Error, OSError, ValueError, KeyError, RuntimeError) as exc:
        if store and not store.read_only and a.command not in {"census", "board", "forecast", "balance", "usage"}:
            try:
                diagnostic = {"command": a.command, "error": str(exc), "utc": now()}
                if isinstance(getattr(a, "task", None), str):
                    diagnostic["task"] = a.task
                store.append("diagnostic", "system", diagnostic)
                store.publish()
            except Exception:
                pass
        print("Squadra: " + ("usage_unavailable" if a.command == 'usage' else str(exc)), file=sys.stderr)
        return 1
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
