#!/usr/bin/env python3
"""Squadra CLI: one durable record shared by Codex, Claude and human seats."""
import argparse
import json
import os
import errno
from pathlib import Path
import shlex
import sqlite3
import sys
import tempfile
import uuid

# The documented minimum, refused before any project module loads; install.py imports this module first.
PYTHON = (3, 12)
if sys.version_info[:2] < PYTHON:
    sys.exit("Squadra: needs Python %d.%d or newer; this is Python %d.%d." % (PYTHON + tuple(sys.version_info[:2])))

from registry import (Store, Error, VERSION, plain, BRIEF_BYTES, REFERENCE_KINDS, canonical, digest, require, snapshot, table, board, now,
                      git, waits, requirement_matrix, presence, standby, latest_ping, return_ping, coordinator_ping,
                      current_proofs, candidate_checks, require_candidate, cross_client_read, gate_checks, read_at_gate, pending_ack_ids,
                      wait_settings, active_pool, scrub_report, SCRUB_NOTE)


class InputRefusal(Error):
    """A missing ID supplied to the CLI, without a diagnostic event."""


# doctor --repair waits at most this long for readers before it leaves the write-ahead log as it is.
REPAIR_BUSY_MS = 10000


def repair_wal(store):
    """doctor --repair: one TRUNCATE checkpoint with a bounded wait for readers; it deletes nothing."""
    from registry_prepare import saved_registry
    if saved_registry(store.root):
        return {'skipped': 'saved package or checkpoint'}
    wal = Path(str(store.path) + '-wal')
    size = lambda: wal.stat().st_size if wal.exists() else 0
    before = size()
    writer = Store(store.root)
    try:
        busy, frames, backfilled = writer.compact_wal(REPAIR_BUSY_MS)
    finally:
        writer.close()
    return {'truncated': busy == 0, 'readers_prevented': busy == 1, 'frames': frames, 'backfilled': backfilled,
            'bytes_before': before, 'bytes_after': size()}


def known_id(values, key, kind):
    if key not in values:
        raise InputRefusal('Unknown ' + kind + ': ' + str(key))


def coverage_preflight(state, task_ids, covers):
    """The CLI guard precedes prechecks; a shared invocation covers each member completely."""
    ids = []
    for task_id in task_ids:
        known_id(state['tasks'], task_id, 'task')
        ids.extend(r['id'] for r in state['tasks'][task_id].get('requirements', []))
    if ids and not covers:
        raise InputRefusal('prove requires --covers for requirement IDs: ' + ', '.join(dict.fromkeys(ids)))
    unknown = [value for value in covers if value not in ids]
    if unknown:
        raise InputRefusal('Unknown requirement IDs in --covers: ' + ', '.join(unknown))
    if len(task_ids) > 1:
        for task_id in task_ids:
            missing = [r['id'] for r in state['tasks'][task_id].get('requirements', []) if r['id'] not in covers]
            if missing:
                raise InputRefusal('Shared prove requires --covers for ' + task_id + ': ' + ', '.join(missing))


def coverage_on_this_source(store, task_ids, current):
    results = {}
    state = store.view()
    for task_id in task_ids:
        task = store.task(task_id, state)
        ids = [r['id'] for r in task.get('requirements', [])]
        if ids:
            covered = {ident for p in current_proofs(task, current) for ident in p.get('covers', [])}
            results[task_id] = {'source': current['sha256'], 'covered': [i for i in ids if i in covered],
                                'uncovered': [i for i in ids if i not in covered]}
    return results


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


def workspace_lang(value=None):
    """The recorded language of an existing registry; Italian when there is none or it cannot be read."""
    root = workspace_path(value)
    if not (root / "registry.sqlite3").is_file():
        return "it"
    store = None
    try:
        store = Store(root, read_only=True)
        return store.view()["workspace"].get("lang", "it")
    except (Error, OSError, ValueError, KeyError, sqlite3.Error):
        return "it"
    finally:
        if store:
            store.close()


def selftest_table(result, lang):
    count = f"{result.get('tests', 0)}/{result['collected']}" if 'collected' in result else str(result.get('tests', 0))
    if lang == "en":
        return ("\n| Role | Proof | Result | Record | Next step |\n|---|---|---|---|---|\n"
                f"| selftest | {count} synthetic tests | {result['status']} | {result['folder']}/RESULT.json | Read the report; no product acceptance |")
    return ("\n| Ruolo | Prova | Esito | Registro | Prossimo passo |\n|---|---|---|---|---|\n"
            f"| selftest | {count} test sintetici | {result['status']} | {result['folder']}/RESULT.json | Leggere il rapporto; nessuna accettazione di prodotto |")


def bindings(store, task_id, require_review=True):
    state = store.resolved()
    task = state["tasks"][task_id]
    from frozen_candidate import current as delivered_current
    current = delivered_current(task, store.root)
    require_candidate(task, current, "deliver/prove the new exact source")
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
            measure(task, current, root=store.root)
    candidate_checks(task, current, require_review and not read_at_gate(task))
    if require_review:
        cross_client_read(state, task, current)
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


QUEUE_HELP_SECONDS = 15
QUEUE_SECONDS = 30
OUTPUT_TAIL = 2000


def run_group(argv, timeout):
    """Run a command in its own session; on timeout kill its whole process group, then raise TimeoutExpired."""
    import signal
    import subprocess
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        proc.stdout.close(); proc.stderr.close()
        raise
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


def bounded(name, text):
    """Keep the last OUTPUT_TAIL characters of a native output and its original length."""
    text = text or ""
    return {name: text[-OUTPUT_TAIL:], name + "_length": len(text)}


def codex_queue(session, text):
    """Hand a message to the Codex queue; every outcome is a result to record, never an exception."""
    import subprocess
    stage = "help"
    try:
        probe = run_group(["codex", "queue", "--help"], timeout=QUEUE_HELP_SECONDS)
        if probe.returncode != 0 or "--thread" not in probe.stdout or "--message" not in probe.stdout:
            return {"delivered": False, "stage": stage, "native_rc": probe.returncode, **bounded("stderr", probe.stderr),
                    "reason": "queue_not_verified"}
        stage = "queue"
        proc = run_group(["codex", "queue", "--thread", session, "--message", text], timeout=QUEUE_SECONDS)
    except subprocess.TimeoutExpired:
        return {"delivered": None, "stage": stage, "native_rc": None, "reason": "timeout"}
    except OSError as exc:
        return {"delivered": False, "stage": stage, "native_rc": None,
                "reason": "not_found" if isinstance(exc, FileNotFoundError) else "os_error"}
    return {"delivered": proc.returncode == 0, "stage": stage, "native_rc": proc.returncode,
            **bounded("receipt", proc.stdout), **bounded("stderr", proc.stderr)}


def blob_ids(repo, current):
    """Each present snapshot row's mode and Git blob id, from the bytes the row fingerprints; refuses moved bytes."""
    import hashlib
    algorithm = git(repo, "rev-parse", "--show-object-format").decode().strip()
    ids = {}
    for row in current["files"]:
        if row["kind"] == "deleted":
            continue
        path, blob, fingerprint = Path(repo) / row["path"], hashlib.new(algorithm), hashlib.sha256()
        if row["kind"] == "symlink":
            target, mode = os.fsencode(os.readlink(path)), "120000"
            blob.update(b"blob %d\0" % len(target) + target); fingerprint.update(target)
        else:
            mode = "100755" if row["executable"] else "100644"
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as body:
                blob.update(b"blob %d\0" % os.fstat(body.fileno()).st_size)
                for chunk in iter(lambda: body.read(1024 * 1024), b""):
                    blob.update(chunk); fingerprint.update(chunk)
        require(fingerprint.hexdigest() == row["sha256"], "Source changed after its snapshot: " + row["path"])
        ids[row["path"]] = (mode, blob.hexdigest())
    return ids


def uncommitted(current):
    """The snapshot paths whose bytes or mode differ from its commit, by blob id: the index's skip-worktree and
    assume-unchanged flags cannot hide a change."""
    repo = current["repo"]
    committed = {}
    for raw in git(repo, "ls-tree", "-r", "-z", current["head"]).split(b"\0"):
        if raw:
            meta, path = raw.split(b"\t", 1)
            mode, _, oid = meta.decode().split()
            committed[os.fsdecode(path)] = (mode, oid)
    present = blob_ids(repo, current)
    return sorted(path for path in present.keys() | committed.keys() if present.get(path) != committed.get(path))


def lacking(current, changed):
    return (", NOT commit " + current["head"] + ", which lacks " + str(len(changed)) + " uncommitted or untracked paths: " +
            ", ".join(changed[:10]) + (", ..." if len(changed) > 10 else ""))


def candidate_line(current, task_id):
    """What the reader must read: the commit when the snapshot equals it, otherwise the working-tree snapshot."""
    changed = uncommitted(current)
    if not changed:
        return "Read exact candidate " + current["head"] + " for " + task_id + "."
    return ("Read exact candidate for " + task_id + ": the working tree of " + current["repo"] + " matching snapshot " +
            current["sha256"] + lacking(current, changed) + ".")


def frozen_copy(root, frozen, repo):
    """Where a frozen delivery is read: the registry's copy, which the builder cannot edit, never the live tree."""
    relative = "candidates/" + frozen["sha256"] + "/repo"
    return ("the frozen copy " + str(Path(root) / relative) + " (registry path " + relative + ")",
            "The builder's working tree " + repo + " may differ after delivery; read only the frozen copy.")


def frozen_line(root, frozen, current, task_id):
    """What the reader of a frozen delivery must read, with the paths its commit lacks."""
    where, warning = frozen_copy(root, frozen, current["repo"])
    changed = uncommitted(current)
    return ("Read exact candidate for " + task_id + ": " + where + " matching snapshot " + current["sha256"] +
            (lacking(current, changed) if changed else " on commit " + current["head"]) + ". " + warning)


def deliver(store, actor, task_id, reconcile=False, note=None):
    state = store.resolved(); task = state["tasks"][task_id]
    require(actor == task["owner"] or (reconcile and state["agents"][actor]["role"] == "coordinator"),
            "Assigned owner required for delivery")
    current = snapshot(task["repo"], store.root)
    previous = task.get("delivery", {})
    from source_manifests import transform_packet
    previous_packet = transform_packet(previous.get("packet_data", {}), store.resolve_snapshot)
    if reconcile:
        require_candidate(task, current, "owner must prove and deliver again")
        if not task.get("reviewer") and not task.get("pool"):
            return {"manual_review": True, "reason": "No configured reader; existing independent review protocol applies"}

    operations = []
    data = {"task": task_id, "snapshot": current}
    if note:
        data['note'] = note
    if task.get('delivery_policy') == 'frozen-v1':
        from frozen_candidate import freeze
        data['frozen'] = freeze(store, task, current)
        # freeze releases the registry views it would otherwise hold while copying; read them again.
        state = store.resolved(); task = state["tasks"][task_id]
    if task.get("fix"):
        from fix_challenge import binding, disposition
        data.update(fix_binding=binding(task), fix_disposition=disposition(task))
    if task.get("contract"):
        data["contract"] = task["contract"]
        from light_route import check_contract, measure
        check_contract(task)
        if task["route"] == "light":
            data["light"] = measure(task, current, root=store.root)
    reader = task.get("reviewer")
    if reader or task.get("pool"):
        packet = {"task": task_id, "candidate": current, "base": task["base"],
                  **({"pool": list(task["pool"])} if task.get("pool") else {"reviewer": reader}),
                  "scope": task["scope"], "plan": task.get("plan"), "requirements": task.get("requirements", []),
                  "proofs": current_proofs(task, current),
                  "instructions": "ACK this exact message, read frozen candidate in an isolated copy, record independent verdict and limitations; never publish or edit the candidate."}
        if data.get('frozen'):
            packet['frozen'] = data['frozen']
        if note:
            packet['note'] = note
        shared_notes = ["Shared proof run " + proof['id'] + " from task " + proof['shared_from'] +
                        "; this additional-task read requires a build whose prove --help lists --also."
                        for proof in packet['proofs'] if proof.get('shared_from')]
        if shared_notes:
            packet['instructions'] += '\n' + '\n'.join(shared_notes)
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
        body = (frozen_line(store.root, data["frozen"], current, task_id) if data.get("frozen") else
                candidate_line(current, task_id)) + "\nImmutable packet " + artifact["sha256"] + " in " + str(store.root / "artifacts") + ".\nActual recipient ACK required before independent review."
        if shared_notes:
            body += '\n' + '\n'.join(shared_notes)
        if task.get("pool"):
            # Members in stand-by get no handoff; the pool is refused only when every member is.
            members = active_pool(state, task)
            require(bool(members), "Reviewer is in stand-by; no new review handoff: " + ", ".join(task["pool"]))
            handoffs = [message + "-" + member for member in members]
            data.update(packet=artifact, packet_data=packet, handoffs=handoffs)
            for member, handoff in zip(members, handoffs):
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
        skipped = [member for member in task["pool"] if member not in active_pool(state, task)]
        if skipped:
            result["skipped_standby"] = skipped
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
    a = sub.add_parser("context", help="Read-only bounded task resume packet; no model call or client compaction")
    a.add_argument("--task", required=True)
    a.add_argument("--max-chars", type=int, default=8000, help="Complete output cap, 4000..32000 characters (default 8000)")
    a.add_argument("--json", action="store_true")
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
    a.add_argument("--scope-file", required=True)
    proof = a.add_mutually_exclusive_group()
    proof.add_argument("--proof-file", help="JSON argv, not a shell string")
    proof.add_argument("--proof", nargs=argparse.REMAINDER, help="Agreed proof argv; place last, use --proof -- argv to pass task-named options")
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
    a.add_argument("--same-client-reason", help="Recorded reason to let a reader of the owner's client be the read that counts")
    a = sub.add_parser("route", help="Coordinator promotes light to full, retaining base, history and budgets")
    a.add_argument("--actor", required=True); a.add_argument("--task", required=True)
    a.add_argument("--full", action="store_true", required=True)
    a.add_argument("--full-gate", action="store_true", help="Require a reserved whole gate on the full route")
    a.add_argument("--scope-file", required=True); a.add_argument("--proof-file", required=True)
    a.add_argument("--reason", required=True); a.add_argument("--expected", type=int, default=argparse.SUPPRESS)
    a = sub.add_parser("supersede", help="Coordinator records that an existing later task replaces an open one; history stays")
    a.add_argument("--actor", required=True); a.add_argument("--task", required=True, help="Open task being replaced")
    a.add_argument("--by", required=True, help="Existing task recorded after it"); a.add_argument("--reason", required=True)
    a = sub.add_parser('reassign', help='Change the reader of the same delivery, keeping proofs and budgets')
    a.add_argument('--actor', required=True); a.add_argument('--task', required=True)
    a.add_argument('--reviewer', required=True); a.add_argument('--reason', required=True)
    a.add_argument('--same-client-reason', help='Reason for a new same-client reader; cross-client transfers clear the old reason')
    a = sub.add_parser('candidate', help='Inspect, explicitly repair or retire frozen source caches')
    a.add_argument('action', choices=['status', 'repair', 'retire', 'limits'])
    a.add_argument('--actor'); a.add_argument('--task'); a.add_argument('--reason')
    a.add_argument('--sha256', help='Retire a historical delivered candidate by its SHA instead of the current task delivery')
    a.add_argument('--max-copy-mib', type=int); a.add_argument('--max-total-mib', type=int)
    a = sub.add_parser('migliora', help='Inspect development friction or apply and commit a checked project rule')
    a.add_argument('action', choices=['inspect', 'apply', 'commit'])
    a.add_argument('--task'); a.add_argument('--actor'); a.add_argument('--rule-file')
    a.add_argument('--defer-commit', action='store_true', help='Prepare a rule for a required independent review')
    a.add_argument('--timeout', type=int, default=120)
    a = sub.add_parser("brief"); a.add_argument("--actor", required=True)
    target = a.add_mutually_exclusive_group(required=True)
    target.add_argument("--task"); target.add_argument("--project", action="store_true")
    a.add_argument("--file", required=True, help="Plain text, at most " + str(BRIEF_BYTES) + " bytes")
    a = sub.add_parser("send"); a.add_argument("--actor", required=True); recipient = a.add_mutually_exclusive_group(required=True)
    recipient.add_argument("--to"); recipient.add_argument("--to-new")
    a.add_argument("--body-file", required=True); a.add_argument("--id"); a.add_argument("--reply-to"); a.add_argument("--task")
    a.add_argument("--info", action="store_true", help="Information only; no recipient ACK required")
    a = sub.add_parser("inbox"); a.add_argument("--agent", required=True, action="append")
    shown = a.add_mutually_exclusive_group()
    shown.add_argument("--all", action="store_true",
                       help="Every message (default: those waiting for an ACK plus the latest " + str(INBOX_RECENT) + ")")
    shown.add_argument("--message", help="One message to the named agents, in full")
    a.add_argument("--follow", action="store_true", help="Watch read-only JSONL mail for all named agents")
    a.add_argument("--timeout", type=float, help="Stop following after this many seconds (otherwise until interrupted)")
    a.add_argument("--since", type=int, help="Resume following after this event number, including the re-arm gap")
    a.add_argument("--until-first", action="store_true",
                   help="Exit after the first batch that shows mail, for a background command that wakes its window")
    a.add_argument('--cursor-file', help='Persistent local cursor; default is under the user state folder')
    a.add_argument('--no-cursor', action='store_true', help='Follow without saving a resume cursor')
    a = sub.add_parser("ack"); a.add_argument("--actor", required=True); a.add_argument("--message", required=True)
    a = sub.add_parser("transport"); a.add_argument("--actor", required=True); a.add_argument("--message", required=True)
    a.add_argument("--via", choices=["codex-queue", "manual"], required=True)
    a = sub.add_parser("plan"); a.add_argument("--actor", required=True); a.add_argument("--task", required=True); a.add_argument("--file", required=True)
    for command in ["start", "deliver", "advance", "check", "close"]:
        a = sub.add_parser(command); a.add_argument("--actor", required=True); a.add_argument("--task", required=True)
        if command in {'deliver', 'close'}:
            a.add_argument('--note', help='A note retained with this delivery or closure')
    a = sub.add_parser("review"); a.add_argument("--actor", required=True); a.add_argument("--task", required=True)
    a.add_argument("--stage", choices=["plan", "code"], default="code")
    a.add_argument("--verdict", choices=["accepted", "changes-required", "blocked"], required=True)
    a.add_argument("--report", required=True)
    a.add_argument('--amend', help='Explicitly replace this recorded review request; consumes a review round')
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
                a.add_argument('--also', action='append', default=[], metavar='TASK',
                               help='Serve another task with the same exact proof execution (repeatable)')
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
    form = a.add_mutually_exclusive_group()
    form.add_argument("--abandon", action="store_true", help="Starter declares an orphan with no process and no results")
    form.add_argument("--reconcile", action="store_true",
                      help="Starter ends an orphan that left a receipt or output: from the receipt, else interrupted with its logs")
    a.add_argument("--reason"); a.add_argument("--evidence", help="Explicit squadra-run-abandon/1 observation JSON")
    a = sub.add_parser("resource"); a.add_argument("action", choices=["acquire", "release", "status"])
    a.add_argument("--actor"); a.add_argument("--name", required=True); a.add_argument("--reason")
    a.add_argument('--task', help='Open task owning this reservation or wait')
    a.add_argument("--wait", type=float, help="Acquire only: wait in FIFO order up to SECONDS (0 tries the resource once, allowing up to 250 ms per queue mutex lock)", metavar="SECONDS")
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
            a.add_argument("--scrub-artifact", metavar="SHA256",
                           help="Registered artifact whose file may be absent from now on (a leaked secret); coordinator only")
    a = sub.add_parser("ask"); a.add_argument("--actor", required=True); a.add_argument("--id", required=True)
    a.add_argument("--question", required=True); a.add_argument("--options-file", required=True)
    a.add_argument("--recommended"); a.add_argument("--task"); a.add_argument("--waiting", action="append", default=[])
    a.add_argument("--evidence", action="append", default=[])
    a = sub.add_parser("legacy"); a.add_argument("--actor", required=True); a.add_argument("--path", required=True)
    a = sub.add_parser("checkpoint"); a.add_argument("--to", required=True)
    a = sub.add_parser("save"); a.add_argument("--actor", required=True); a.add_argument("--to", required=True)
    a.add_argument("--repo", action="append", default=[]); a.add_argument("--include", action="append", default=[])
    a.add_argument("--temporary", action="store_true"); a.add_argument("--declared-offsite")
    a.add_argument('--allow-name', action='append', default=[], metavar='REL',
                   help='Coordinator records a one-save exception for this exact tracked path; requires one --repo and --reason; private keys remain refused')
    a.add_argument('--reason', help='Reason for the explicit --allow-name exception')
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
    a = sub.add_parser("doctor")
    mode = a.add_mutually_exclusive_group()
    mode.add_argument('--quick', action='store_true', help='Read-only anchor, suffix and demanded-cache checks; not a full audit')
    mode.add_argument('--repair', action='store_true', help='Full audit and explicit derived-cache repair; refuses saved packages')
    sub.add_parser("export")
    a = sub.add_parser("board"); a.add_argument("--lang", choices=["it", "en"], help="Override the workspace board language")
    a = sub.add_parser("dash"); a.add_argument("--port", type=int, default=0); a.add_argument("--open", action="store_true")
    a = sub.add_parser("selftest"); a.add_argument("--output", help="New directory for durable test receipts")
    a.add_argument("--timeout", type=int, default=1800, help="Seconds for the whole parallel suite and each test module")
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


INBOX_RECENT = 10


class Inbox(list):
    """Message rows in their usual shape; total and pending say what a default read left out."""
    total = pending = 0


def inbox(state, agents, everything=False, message=None):
    """Mail to the named agents, oldest first: the ACK waits plus the latest INBOX_RECENT, all of it, or one message."""
    from registry_current import attached, message_ids
    names = set(agents)
    if message is not None:
        found = state["messages"].get(message)
        require(found is not None and found["to"] in names, "Unknown message for the named agents: " + message)
        return Inbox([found])
    index = attached(state)
    if index is not None:
        keys = sorted({key for agent in names for key in message_ids(index, recipient=agent)},
                      key=lambda key: index["message_order"][key])
    else:
        keys = [key for key, m in state["messages"].items() if m["to"] in names]
    pending, shown = [], set(keys)
    if not everything:
        # The attention ACK rows: the waits status, doctor and the follower's backlog count.
        pending = [key for key in dict.fromkeys(pending_ack_ids(state)) if state["messages"][key]["to"] in names]
        shown = set(pending).union(keys[-INBOX_RECENT:])
    rows = Inbox(state["messages"][key] for key in keys if key in shown)
    rows.total, rows.pending = len(keys), len(pending)
    return rows


def dispatch(a, store):
    command = a.command
    waiting = command == 'resource' and a.action == 'acquire' and a.wait is not None
    state = None if command == 'doctor' or waiting else store.view()
    actor = getattr(a, "actor", "system")
    if isinstance(getattr(a, 'actor', None), str) and not waiting:
        known_id(state['agents'], actor, 'agent')
    task_id = getattr(a, 'task', None)
    if isinstance(task_id, str) and command != 'supersede' and not waiting:
        known_id(state['tasks'], task_id, 'task')
    message_id = getattr(a, 'message', None)
    if command in {'ack', 'transport'}:
        known_id(state['messages'], message_id, 'message')
    if command == 'cancel':
        known_id(state['runs'], a.run, 'run')
    if command == 'ping' and a.to:
        known_id(state['agents'], a.to, 'agent')
    if command == "init":
        root = store.root
        validate_init(a, root)
        project = str(Path(a.project).resolve(strict=True))
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
        if a.proof:
            if a.proof[0] == '--':
                a.proof = a.proof[1:]
            else:
                options = {flag for action in parser()._subparsers._group_actions[0].choices['task']._actions for flag in action.option_strings}
                require(not any(arg.split('=', 1)[0] in options for arg in a.proof),
                        'Task option inside --proof argv; put task options first or use --proof -- argv')
        base = snapshot(a.repo, store.root)
        proof = json.loads(Path(a.proof_file).read_text()) if a.proof_file else a.proof
        require(proof is None or (isinstance(proof, list) and proof and all(isinstance(x, str) and x for x in proof)), "Proof file must contain a nonempty argv array")
        task = {"id": a.id, "title": a.title, "owner": a.owner, "repo": base["repo"], "base": base["head"],
                "scope": store.artifact(a.scope_file), "proof": proof, "expected": a.expected,
                "full_gate": a.full_gate, "depends": a.depends,
                "reviewer": a.reviewer[0] if a.reviewer and len(a.reviewer) == 1 else None,
                "requirements": json.loads(Path(a.requirements_file).read_text()) if a.requirements_file else [],
                'delivery_policy': 'frozen-v1'}
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
        # Tasks recorded from 3.10.4 on carry the cross-client rule; older tasks are never re-judged.
        task["cross_client"] = True
        if a.same_client_reason is not None:
            task["same_client_reason"] = a.same_client_reason
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
    elif command == 'reassign':
        from source_manifests import transform_packet
        task = store.task(a.task, state)
        old = task.get('delivery', {})
        if task.get('reviewer') == a.reviewer and task.get('reassignments', [{}])[-1].get('reason') == a.reason:
            return {'handoff': old.get('handoff'), 'already_reassigned': True}
        same_reason = a.same_client_reason
        if task.get('cross_client'):
            from registry import same_client_readers
            if not same_client_readers(state, task['owner'], [a.reviewer]):
                require(same_reason is None, 'Cross-client transfer does not need --same-client-reason')
        packet = transform_packet(old.get('packet_data', {}), store.resolve_snapshot)
        packet.pop('pool', None)
        packet.update(reviewer=a.reviewer, reassignment={'previous': old.get('handoff'),
                      'previous_pool': old.get('handoffs', []), 'reason': a.reason})
        if task.get('cross_client'):
            packet['reassignment']['same_client_reason'] = same_reason
        packet = transform_packet(packet, store.compact_snapshot)
        handoff = 'review-' + digest(canonical(packet).encode())
        artifact = store.artifact_bytes((canonical(packet) + '\n').encode(), handoff + '.json', 'generated-handoff:' + handoff)
        data = {'task': a.task, 'reviewer': a.reviewer, 'reason': a.reason,
                'previous': old.get('handoff'), 'previous_pool': old.get('handoffs', []),
                'packet': artifact, 'packet_data': packet, 'handoff': handoff}
        if task.get('cross_client'):
            data['same_client_reason'] = same_reason
        notifications = []
        former = {state['messages'][h]['to'] for h in [old.get('handoff'), *old.get('handoffs', [])] if h in state['messages']} - {a.reviewer}
        for recipient in sorted(former):
            mid = handoff + '-replaced-' + recipient
            notifications.append(('message', actor, {'id': mid, 'to': recipient, 'info': True, 'related_task': a.task,
                                  'body': 'Your read was reassigned to ' + a.reviewer + '. Previous handoff is replaced: ' + a.reason}, mid))
        bindings(store, a.task, require_review=False)
        where = ''
        if old.get('frozen'):
            copy, warning = frozen_copy(store.root, old['frozen'], task['repo'])
            where = ': ' + copy + '. ' + warning
        store.batch([('reassign', actor, data, 'reassign:' + handoff),
                     ('message', actor, {'id': handoff, 'to': a.reviewer, 'related_task': a.task,
                      'body': 'Read reassigned on the same proved candidate' + (where or '.') + ' ACK this exact handoff.\n' +
                              canonical(packet)}, handoff), *notifications])
        return {'handoff': handoff, 'proof_reused': True, 'snapshot': task['candidate']['sha256']}
    elif command == 'candidate':
        import frozen_candidate
        require(a.action == 'status' or (a.actor and (a.task or a.action == 'limits' or (a.action == 'retire' and a.sha256))),
                'Candidate mutation requires --actor and --task (or retire --sha256 SHA)')
        require(not a.sha256 or (a.action == 'retire' and not a.task), '--sha256 is for retire only, instead of --task')
        return frozen_candidate.manage(store, actor, a.task, a.action, a.reason, a.max_copy_mib, a.max_total_mib, sha256=a.sha256)
    elif command == 'migliora':
        import migliora
        if a.action == 'inspect':
            return migliora.inspect(store, a.task)
        require(a.actor and a.task and a.rule_file, 'Migliora apply/commit requires --actor, --task and --rule-file')
        if a.action == 'commit':
            return migliora.finish(store, actor, a.task, a.rule_file)
        return migliora.apply(store, actor, a.task, a.rule_file, defer_commit=a.defer_commit, timeout=a.timeout)
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
        return {'id': data['id'], 'to': data['to'], 'saved': True, 'ack_required': not a.info}
    elif command == "inbox":
        return inbox(state, a.agent, a.all, a.message)
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
            own = state["agents"][actor]["identity"]
            for row in presence(state, now()):
                if row["identity"] in seen or row["identity"] == own:
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
        message = state["messages"][a.message]
        relay = message["from"] != actor
        require(not relay or coordinator_ping(state, message, actor) or
                state["agents"].get(actor, {}).get("role") == "coordinator", "Only the sender or the coordinator can deliver")
        instruction = ("Information only; no ACK required." if message.get("info") is True
                       else f"ACK via squadra ack --actor {message['to']} --message {a.message} (with shared --root).")
        if a.via == "manual":
            from inbox_follow import command as inbox_command
            # A hand-off by hand is unconfirmed, not failed: only the recipient's ACK says it arrived.
            result = {"message": a.message, "via": "manual", "delivered": None,
                      "instruction": f"Follow incoming mail with {inbox_command(store.root, [message['to']], '--follow')}. {instruction}"}
        else:
            agent = state["agents"][message["to"]]
            require(agent["client"] == "codex" and agent["session"], "Registered Codex session required")
            text = f"Squadra message {a.message}; workspace {store.root}.\n{message['body']}\n{instruction}"
            result = {"message": a.message, "via": a.via, **codex_queue(agent["session"], text),
                      "ack": "not required; information only" if message.get("info") is True else "still pending; native queue does not prove wakeup"}
        if relay:
            result["relayed_by"] = actor
        store.append("transport", actor, result)
        receipt = {key: value for key, value in result.items() if key not in
                   {"receipt", "receipt_length", "stderr", "stderr_length"}}
        if result.get('delivered') is not True and result.get('stderr'):
            receipt['stderr'] = result['stderr'][-300:]
        return receipt
    elif command == "plan":
        store.append("plan", actor, {"task": a.task, "artifact": store.artifact(a.file)})
    elif command == "start":
        return recall(store, state, actor, a.task, "start", ("start", actor, {"task": a.task}, None))
    elif command in {"deliver", "advance"}:
        return deliver(store, actor, a.task, reconcile=command == "advance", note=getattr(a, 'note', None))
    elif command in {"check", "close"}:
        if command == 'close' and state['tasks'][a.task].get('release_gate') is True:
            store.audit()
        current = bindings(store, a.task)
        result = {"task": a.task, "verified": True, "snapshot": current["sha256"], "publication_authorized": False}
        if command == "close":
            event = store.append("close", actor, {"task": a.task, **({'note': a.note} if a.note else {})})
            from migliora import inspect as inspect_friction
            result['migliora'] = inspect_friction(store, a.task)
            if state["tasks"][a.task].get("fix"):
                from errata import hint
                result["errata"] = hint(a.task, seq_of(store, event), actor, "defect fix closed")
        return result
    elif command == "review":
        from reads import load_findings
        from registry import review_handoff, review_request
        extra = {'findings': load_findings(a.findings_file)} if a.findings_file is not None else {}
        task = store.task(a.task, state)
        if a.stage == "plan":
            require(bool(task.get("plan")), "No registered plan")
            binding = task["plan"]["sha256"]
            require(digest(Path(task["plan"]["original"]).read_bytes()) == binding, "Plan drift")
        else:
            current = bindings(store, a.task, require_review=False)
            binding = current["sha256"]
        modern = task.get('delivery_policy') == 'frozen-v1'
        require(modern or a.amend is None, 'Legacy intentional rereads use a new report within the existing budget')
        lens = {k: v for k, v in (("lens", a.lens), ("net_lines", a.net_lines)) if v is not None}
        data = {"task": a.task, "stage": a.stage, "verdict": a.verdict,
                "binding": binding, "covers": a.covers, "report": store.artifact(a.report), **extra,
                **({"gate_covers": a.gate_covers} if a.gate_covers else {}),
                **({"contract": task["contract"]} if task.get("contract") else {}),
                **fix_review_fields(task), **lens}
        handoff = review_handoff(state, task, actor)
        if modern:
            data.update(handoff=handoff, **({'amends': a.amend} if a.amend else {}))
        request = review_request(actor, {**data, 'handoff': handoff})
        if modern:
            data['request'] = request
            prior = next((r for r in task['reviews'] if r.get('request') == request), None)
            if prior:
                return {'receipt': prior['request'], 'already_recorded': True}
        before = store.commit_count
        event = store.append('review', actor, data, request)
        result = {'receipt': event, 'already_recorded': store.commit_count == before}
        if a.stage == "code" and a.verdict == "changes-required":
            from errata import hint
            result['errata'] = hint(a.task, seq_of(store, event), actor, "code read asked for changes")
            if not modern:
                return {'errata': result['errata']}
        return result if modern else None
    elif command == "output":
        output = {"task": a.task, "text": Path(a.file).read_text(), "next": a.next,
                  "artifacts": [store.artifact(f) for f in a.artifact]}
        event = store.append("output", actor, output)
        return {"receipt": event, "saved": True, "task": a.task}
    elif command in {"prove", "probe", "run"}:
        from runtime import execute
        argv = a.argv[1:] if command != "run" and a.argv and a.argv[0] == "--" else getattr(a, "argv", None)
        checked = None
        proof_tasks = [a.task, *getattr(a, 'also', [])]
        if command == 'prove':
            coverage_preflight(state, proof_tasks, a.covers)
        if command == "prove" and a.task in state["tasks"]:
            if a.also:
                from registry import shared_coverage
                shared_coverage(state, [a.task, *a.also], a.covers)
            from precheck import run as precheck
            checked = precheck(store, actor, state["tasks"][a.task])
        shared = {'also': a.also} if command == 'prove' and a.also else {}
        result = execute(store, actor, a.task, argv=argv, mode=a.mode if command == "run" else "proof" if command == "prove" else "probe",
                         prompt=Path(a.prompt_file).read_text() if command == "run" else None,
                         timeout=a.timeout, gate=getattr(a, "gate", False), executable=getattr(a, "cli", None),
                         stage=getattr(a, "stage", "code"), expected_rc=getattr(a, "expected_rc", 0), covers=getattr(a, "covers", []),
                         checked=checked[0] if checked else None, **shared)
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
        if command == 'prove':
            coverage = coverage_on_this_source(store, proof_tasks, result['after'])
            if coverage:
                result['coverage_on_this_source'] = coverage
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
        elif a.reconcile:
            from registry import reconcile_end
            require(not a.reason and not a.evidence, "--reason and --evidence require --abandon")
            require(a.run in state["runs"], "Unknown run")
            run = state["runs"][a.run]
            require(actor == run["agent"], "Only the original run starter may reconcile its run")
            require(run["status"] == "running", "Run already ended")
            data = reconcile_end(store, run)
            store.append("run.end", actor, data, "run-end:" + a.run)
            return {"run": a.run, "status": data["status"], "native_rc": data["native_rc"],
                    "source": data["reconciled"]["source"], "artifacts": [x["name"] for x in data["artifacts"]]}
        else:
            require(not a.reason and not a.evidence, "--reason and --evidence require --abandon")
            store.append("run.cancel", actor, {"id": a.run})
    elif command == "resource":
        if a.action == 'status':
            from resource_wait import status
            return status(store, a.name)
        require(a.actor and a.reason, 'Resource acquire/release requires --actor and --reason')
        require(a.wait is None or a.action == 'acquire', '--wait applies only to resource acquire')
        if waiting:
            from resource_wait import acquire, CacheUnavailable
            try:
                acquire(store, actor, a.name, a.reason, a.wait, task=a.task)
            except CacheUnavailable as exc:
                raise InputRefusal(str(exc)) from exc
        else:
            store.append("resource." + a.action, actor, {"name": a.name, "reason": a.reason,
                         **({'task': a.task} if a.task else {})})
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
            if a.scrub_artifact is not None:
                from registry import artifact_uses
                sha = a.scrub_artifact.lower()
                data["scrub"] = {"sha256": sha, "tasks": artifact_uses(store.events(), sha)["tasks"]}
                a.task = list(dict.fromkeys(a.task + data["scrub"]["tasks"]))
            if a.task:
                data["tasks"] = a.task
            if a.follows:
                data["follows"] = resolve_follows(state, a.follows)
        event = store.append(command, actor, data)
        if "scrub" in data:
            from registry import decision_id
            path = store.root / "artifacts" / data["scrub"]["sha256"]
            present = os.path.lexists(path)
            return {"scrub": data["scrub"], "decision": decision_id(data, seq_of(store, event)),
                    "file": "present" if present else "absent",
                    **({"next": "Delete " + str(path) + " to complete the removal"} if present else {})}
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
        report = {}
        return {"checkpoint": store.checkpoint(a.to, report=report), "canonical_events_and_artifacts": True,
                "source_code": "Not included: preserve worktree and Git bundle separately",
                **({"scrubbed": report["scrubbed"]} if report.get("scrubbed") else {})}
    elif command == "save":
        from recovery import save, allow_names, DestinationRefused
        require(state["agents"][actor]["role"] == "coordinator", "Coordinator role required")
        require(not a.reason or a.allow_name, "--reason requires --allow-name")
        try:
            allowance = allow_names(store, actor, a.to, a.repo, a.allow_name, a.reason, a.temporary) if a.allow_name else None
            result = save(store, a.to, a.repo, a.include, a.temporary, a.declared_offsite, name_allowance=allowance)
        except DestinationRefused as exc:
            raise InputRefusal(str(exc)) from exc
        store.append("save", actor, {"path": str(Path(a.to).resolve()), "manifest": store.artifact(Path(a.to) / "MANIFEST.json")})
        return {"saved": str(Path(a.to).resolve()), "repositories": len(result["repositories"]),
                "scope": result["scope"], "destination": result["destination"], "offsite_verified": False,
                **({"scrubbed": result["scrubbed"]} if result.get("scrubbed") else {})}
    elif command == "settings":
        store.append("settings", actor, wait_settings(json.loads(Path(a.file).read_text())))
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
        from registry import attention
        from registry_prepare import wal_state
        # Read before doctor's own read begins, so its snapshot is not among the readers reported.
        wal = wal_state(store.root)
        if a.quick:
            from registry_prepare import quick
            store.db.execute('BEGIN')
            checked = quick(store)
            count, state = checked['events'], checked['state']
        else:
            checked = store.audit()
            if checked['cache'] == 'mismatch':
                require(a.repair, 'Cache mismatch; run squadra doctor --repair')
                checked = store.repair_cache()
            elif a.repair and checked['cache'] != 'matched':
                checked = store.repair_cache()
            count, state = len(checked['events']), checked['state']
        compaction = repair_wal(store) if a.repair else None
        columns = {row[1] for row in store.db.execute('PRAGMA table_info(events)')}
        modern = ('hash_format' in columns and
                  store.db.execute('SELECT 1 FROM events WHERE hash_format=2 LIMIT 1').fetchone() is not None)
        workflow3 = ('hash_format' in columns and
                     store.db.execute('SELECT 1 FROM events WHERE hash_format=3 LIMIT 1').fetchone() is not None)
        compatibility = ({'compatibility': 'This registry requires Squadra 3.11.0 or later.'} if workflow3 else
                         {"compatibility": "This registry requires Squadra 3.10.1 or later."} if modern else {})
        from registry import git_probe
        tracked = git_probe('-C', str(store.root), '--literal-pathspecs', 'ls-files', '--error-unmatch', '--', 'registry.sqlite3')
        git_warning = ({'git_warning': 'Registry is tracked by Git; remove it from the index with git rm --cached -r '
                        + shlex.quote(str(store.root))} if tracked.returncode == 0 else {})
        view = attention(state)
        verification = ({'verification': 'quick', 'integrity': 'limited',
                         'scope': ['anchor', 'suffix', 'demanded cache values'],
                         'not_verified': ['earlier events', 'untouched leaves', 'all artifacts', 'replay-root equality']}
                        if a.quick else {'verification': 'full', 'integrity': 'passed'})
        from frozen_candidate import inventory
        candidates = inventory(store) if not a.quick else None
        return {**compatibility, **git_warning, **verification,
                'candidate_integrity': 'not_checked' if a.quick else 'degraded' if any(r['status'] in {'missing', 'damaged'} for r in candidates) else 'passed',
                'candidates': candidates, "events": count, "cache": checked["cache"], "unknown_running": [r["id"] for r in state["runs"].values() if r["status"] in {"running", "cleanup-unknown"}],
                "abandoned": [{"id": r["id"], "native_rc": None, "results": "none",
                               "legacy": r["abandonment"]["legacy"], "reason": r["abandonment"]["reason"]}
                              for r in state["runs"].values() if r["status"] == "abandoned"],
                "pending_ack": pending_ack_ids(state, attention_value=view),
                "waits": waits(state, now(), attention_value=view),
                **({"scrubbed": checked["scrubbed"], "scrubbed_note": SCRUB_NOTE} if checked.get("scrubbed") else {}),
                "wal": wal, **({"wal_compaction": compaction} if compaction is not None else {}),
                "warning": "Integrity verifies the record, not truth of agents' claims or external process liveness"}
    elif command == "dash":
        from dashboard import serve
        root = store.root
        store.close()
        serve(root, port=a.port, open_browser=a.open)
    return None


def validate_init(a, root):
    temporary_roots = [Path(tempfile.gettempdir()).resolve(), Path('/private/tmp')]
    require(a.temporary or not any(t == root or t in root.parents for t in temporary_roots),
            'Persistent workspace cannot live in tmp; --temporary is for explicit demos/tests')
    project = Path(a.project).resolve(strict=True)
    require(project.is_dir(), 'Project must be a directory: ' + str(project))
    require(a.plan_rounds > 0 and a.review_rounds > 0, 'Positive round limits required')


def refusal(exc, root):
    if isinstance(exc, sqlite3.Error):
        code = (getattr(exc, 'sqlite_errorcode', 0) or 0) & 255
        if code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} or 'locked' in str(exc).lower():
            return 'registry busy, retry'
        if not any(word in str(exc).lower() for word in ('readonly', 'read-only', 'unable to open', 'permission')):
            return 'Registry error in ' + str(root) + ': ' + getattr(exc, 'sqlite_errorname', 'sqlite_error')
    if (isinstance(exc, sqlite3.Error) or isinstance(exc, PermissionError)
            or isinstance(exc, OSError) and exc.errno in {errno.EACCES, errno.EPERM, errno.EROFS}):
        return ('Cannot access registry folder ' + str(root) + '; a Codex window needs the registry folder among its '
                'writable roots (codex --add-dir ' + shlex.quote(str(root)) + ')')
    if isinstance(exc, (KeyError, TypeError)):
        return 'internal error'
    return ' '.join(str(exc).splitlines())


def warning(text):
    try:
        print('Squadra: ' + ' '.join(text.splitlines()), file=sys.stderr, flush=True)
    except Exception:
        pass


def main(argv=None):
    a = parser().parse_args(argv)
    store = None
    result_rc = 0
    root = Path(a.root or os.environ.get('SQUADRA_ROOT') or Path.cwd() / '.squadra')
    try:
        root = workspace_path(a.root)
        if a.command == "inbox":
            import math
            require(a.follow or (a.timeout is None and a.since is None and not a.until_first and a.cursor_file is None and not a.no_cursor),
                    '--timeout, --since and --until-first require --follow')
            require(a.timeout is None or (math.isfinite(a.timeout) and a.timeout > 0), 'Positive finite --timeout required')
            require(a.since is None or a.since >= 0, 'Nonnegative --since required')
            require(not a.follow or not (a.all or a.message is not None), '--all and --message do not apply to --follow')
            if a.follow:
                from inbox_follow import follow
                return follow(workspace_path(a.root), a.agent, a.timeout, a.since, until_first=a.until_first,
                              cursor_file=a.cursor_file, persist=not a.no_cursor)
        if a.command == "context":
            import context_brief
            store = Store(workspace_path(a.root), read_only=True)
            state = store.resolved(store.view(), compact=True)
            known_id(state['tasks'], a.task, 'task')
            seq = state['event_count']
            row = store.db.execute('SELECT * FROM events WHERE seq=?', (seq,)).fetchone()
            head = store.decode_event(row)['hash'] if row else '0' * 64
            packet = context_brief.build(state, a.task, {'seq': seq, 'hash': head},
                                         registry_root=str(store.root), max_chars=a.max_chars, as_json=a.json)
            print(context_brief.render(packet, a.json), end='')
            return 0
        if a.command == "usage":
            import time
            import usage
            store = Store(workspace_path(a.root), read_only=True)
            events = store.events()
            state = store.state(events, resolve=False)
            deadline = time.monotonic() + 30
            while True:
                result = usage.collect(events, state, budget_seconds=max(0.25, deadline - time.monotonic()))
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
            from registry import composition
            found = collect(a.project or state["workspace"]["project"], state)
            lang = state["workspace"].get("lang", "it") if state else "it"
            found["composition"] = composition(state, found["processes"], lang, found["scan"])
            print(json.dumps(found, ensure_ascii=False, indent=2))
            return 0
        if a.command == "bench-example":
            from benchmark import example
            print(json.dumps(example(a.to),indent=2));return 0
        if a.command == "restore-check":
            from recovery import restore_check
            if a.actor:
                # Resolve the registry as every command does; refuse a non-coordinator before restoring anything.
                root = workspace_path(a.root)
                check = Store(root, read_only=True)
                try:
                    agents = check.view()["agents"]
                    require(a.actor in agents, "Unknown agent: " + a.actor)
                    require(agents[a.actor]["role"] == "coordinator", "Coordinator role required")
                finally:
                    check.close()
            result = restore_check(a.package, a.to)
            if a.actor:
                store = Store(root)
                store.append("restore.check", a.actor, {"result": store.artifact(Path(a.to) / "RESTORE-RESULT.json")})
                result["recorded"] = True
            else:
                result["recorded"] = False
            try:
                print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
                if result['recorded']:
                    store.project()
            except Exception:
                if store is None or type(store.commit_count) is not int or store.commit_count == 0:
                    raise
                warning('Command committed; summary unavailable; do not retry')
            return 0
        if a.command == "selftest":
            from selftest import run
            lang = workspace_lang(a.root)
            result = run(a.output, a.timeout)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            print(selftest_table(result, lang))
            return 0 if result["status"] == "passed" else 1
        if a.command == "plugin":
            import plugin_store
            import plugins
            try:
                result = (plugin_store.catalogue(Path.home()) if a.plugin_command == "list"
                          else plugin_store.install(Path.home(), a.name))
            except (plugins.Refused, OSError) as exc:
                print("Squadra: " + (plugin_store.explain(exc) if isinstance(exc, plugins.Refused) else "io_error"), file=sys.stderr)
                return 1
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        read_only = (a.command in {'status', 'inbox', 'check', 'doctor', 'dash'} or
                     (a.command in {'resource', 'candidate'} and a.action == 'status') or
                     (a.command == 'migliora' and a.action == 'inspect') or
                     (a.command == 'lesson' and a.list))
        if a.command == 'init':
            validate_init(a, root)
        store = Store(root, create=a.command == "init", read_only=read_only)
        before = getattr(store, 'commit_count', None)
        result = dispatch(a, store)
        if a.command == 'doctor':
            print(json.dumps(plain(result), ensure_ascii=False, indent=2))
            return 0
        committed = type(before) is int and store.commit_count > before
        if a.command == 'transport' and a.via == 'codex-queue' and result.get('delivered') is not True:
            result_rc = 1
        if a.command != "dash":
            try:
                if result is not None:
                    visible = dict(result) if isinstance(result, dict) else result
                    if isinstance(visible, dict):
                        for key in ["after", "before", "snapshot"]:
                            value = visible.get(key)
                            if isinstance(value, dict) and "files" in value:
                                visible[key] = {"head": value["head"], "sha256": value["sha256"], "files": len(value["files"])}
                    print(json.dumps(plain(visible), ensure_ascii=False, indent=2), flush=True)
                    if a.command == "inbox" and len(result) < result.total:
                        print(f"Squadra: showing {len(result)} of {result.total} messages (the {result.pending} waiting for an ACK "
                              f"and the latest {INBOX_RECENT}); --all shows every message, --message ID one in full.",
                              file=sys.stderr)
                if committed:
                    print(json.dumps({'committed': True, **store.last_commit}), file=sys.stderr, flush=True)
                if a.command == 'export':
                    state, events = store.project(with_events=True)
                    scrubbed = scrub_report(store.root, events)
                    if scrubbed:
                        print(json.dumps({"scrubbed": scrubbed, "scrubbed_note": SCRUB_NOTE}, ensure_ascii=False, indent=2))
                else:
                    if not read_only:
                        store.publish()
                    context = store.__dict__.get('_summary_context') if not read_only else None
                    if context is not None:
                        state, attention_value = context
                    else:
                        state, attention_value = store.view(), None
                    events = ()
                if a.command == 'export':
                    context = store.__dict__.get('_summary_context')
                    if context is not None:
                        state, attention_value = context
                    else:
                        attention_value = None
                if (a.command not in {"inbox", "doctor", "send", "transport", "migliora", "check"} and
                    not (a.command in {"resource", "candidate"} and a.action == "status") and
                    not (a.command == "lesson" and a.list)):
                    from registry import current_index
                    if current_index(state) is None and not events:
                        events = store.events()
                    print(table(state, read_at=now(), events=events, compact=a.command != 'status' or not a.all,
                                advice=a.command == 'status',
                                attention_value=attention_value))
                    if a.command == 'supersede':
                        print('- ' + a.task + ' → ' + a.by)
                forecast_task = a.id if a.command == 'task' else getattr(a, 'task', None)
                if a.command in {'task', 'brief', 'start'} and forecast_task:
                    from forecast import calculate
                    import balance
                    if getattr(state, "current", None) is None and not events:
                        events = store.events()
                    predicted = calculate(state, events, forecast_task)
                    print(json.dumps({'forecast': predicted}, ensure_ascii=False, indent=2))
                    ranked = balance.calculate(state, events, forecast_task, forecast_result=predicted)
                    print(json.dumps({'balance': balance.compact(ranked, state['workspace'].get('lang', 'it'))},
                                     ensure_ascii=False, indent=2))
            except Exception as exc:
                if not committed:
                    raise
                from registry_projections import JournalRollback
                # A rollback's message names the lost sequence range and the two ways out.
                warning(str(exc) if isinstance(exc, JournalRollback) else
                        'Transport attempt recorded; delivery failed; summary unavailable' if result_rc
                        else 'Command committed; summary unavailable; do not retry')
        return result_rc
    except (Error, OSError, sqlite3.Error, ValueError, KeyError, RuntimeError, TypeError) as exc:
        expected = (isinstance(exc, (InputRefusal, sqlite3.Error, PermissionError)) or
                    isinstance(exc, OSError) and exc.errno in {errno.EACCES, errno.EPERM, errno.EROFS})
        if store and not store.read_only and not expected:
            try:
                diagnostic = {"command": a.command, "error": str(exc), "utc": now()}
                if isinstance(getattr(a, "task", None), str):
                    diagnostic["task"] = a.task
                store.append("diagnostic", "system", diagnostic)
                store.publish()
            except Exception:
                pass
        warning("usage_unavailable" if a.command == 'usage' else refusal(exc, root))
        return 1
    finally:
        if store:
            try:
                store.close()
            except (OSError, sqlite3.Error):
                warning('Registry close failed in ' + str(root))


if __name__ == "__main__":
    raise SystemExit(main())
