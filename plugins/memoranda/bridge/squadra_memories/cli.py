"""Collect declared candidates, propose them to memoranda, and read decisions without approving."""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
import math
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile

from . import LISTS
from . import actions, connector, panel
from .collect import collect
from .render import document, preview
from .source import Artifacts, Refusal, load_registry, locate_scripts, snapshot, verified_events

REPO = Path(__file__).resolve().parents[1]
MESSAGES = {
    "E-MIGRATION": "migration is pending; preserve its marker and retry the same stopped inputs before sending.",
    "E-PIN": "squadra's code, or a kind of event in the registry, is not in the reviewed pin; nothing collected.",
    "E-SNAPSHOT": "the registry database could not be copied as a regular file; nothing collected.",
    "E-DRIFT": "the registry changed during three copy attempts; nothing collected.",
    "E-CHAIN": "the registry copy failed squadra's chain or database check; nothing collected.",
    "E-ARTIFACT": "a registered artifact is missing or changed in the artifact store; nothing collected.",
    "E-OUTPUT": "the output location is refused (inside the registry or bridge, missing, a symlink, or present); nothing created.",
    "E-INTERNAL": "internal error; nothing written.",
    "E-INTERRUPTED": "interrupted; the registry copy and any partial output were removed.",
    "E-PANEL": "the plugin folder below .squadra holds a symlink or a foreign file type; nothing written there.",
    "E-ACTION": "the saved selection or its private files are invalid; the request was not cleared.",
    "E-ACTION-BUSY": "the saved selection is locked; try again after the other operation finishes.",
    "E-KEY": "no usable agent key: save it in the squadra panel, set MEMORANDA_API_KEY or give an owner-only --token-file.",
    "E-ENDPOINT": "no usable endpoint: save it in the squadra panel or give https://<host>/t/<tenant>/mcp.",
    "E-SCOPE": "the scope is not a valid memoranda scope.",
    "E-TIMEOUT": "the timeout must be more than 0 and at most 60 seconds.",
    "E-BINDING": "this ledger is bound to another endpoint, tenant, scope or key; nothing sent.",
    "E-BUSY": "another send, status or resolve holds this ledger; nothing sent.",
    "E-LEDGER": "the ledger is unreadable or not a regular file; nothing sent.",
    "E-LEDGER-SIZE": "the ledger would exceed its size limit; nothing more recorded.",
    "E-AUTH": "memoranda refused the key (401/403); the run stopped.",
    "E-NETWORK": "the request did not complete; the run stopped.",
    "E-RESPONSE": "memoranda's answer could not be read; the run stopped.",
    "E-RPC": "memoranda refused the MCP request; the run stopped.",
    "E-SERVER-BUSY": "memoranda was busy; the run stopped.",
    "E-RATE-LIMITED": "memoranda asked to slow down beyond the allowed wait; the run stopped.",
    "E-UNCERTAIN": "a proposal's outcome is uncertain; it is never resent on its own: resolve it explicitly.",
    "E-SCOPE-POLICY": "this scope approves proposals automatically; further sends need --accept-scope-policy.",
    "E-QUEUE-FULL": "your pending proposals reached memoranda's limit; the run stopped.",
    "E-SCOPE-QUEUE-FULL": "this scope's pending proposals reached memoranda's limit; the run stopped.",
    "E-REMOTE": "memoranda refused the request with an unrecognised code.",
    "E-INVALID-ARGUMENTS": "memoranda refused the arguments.",
    "E-POLICY-INVALID": "memoranda's policy for the scope is invalid.",
    "E-NOT-FOUND": "memoranda did not find what was asked.",
    "E-REMOTE-INTERNAL": "memoranda reported an internal error.",
    "E-ITEM": "no ledger item with that name.",
    "E-NOT-UNCERTAIN": "only an uncertain item can be resolved.",
    "E-ID": "that is not a memoranda proposal id.",
}


def _inside(path, folder):
    return path == folder or folder in path.parents


def _write(path, text):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as handle:
        handle.write(text)


def _locations(args):
    registry = Path(os.path.realpath(args.registry))
    if not registry.is_dir():
        raise Refusal("E-SNAPSHOT")
    from .storage import data_root
    default = data_root(Path.home())
    given = Path(args.out_root) if args.out_root else default
    if os.path.islink(given) or not given.is_dir():
        raise Refusal("E-OUTPUT")
    out_root = Path(os.path.realpath(given))
    if _inside(out_root, registry) or _inside(out_root, Path(os.path.realpath(REPO))):
        raise Refusal("E-OUTPUT")
    return registry, out_root


def read_registry(args):
    """The verified read and the collected answers; the registry copy never enters the out-root."""
    registry, out_root = _locations(args)
    module, entry = load_registry(locate_scripts(args.squadra_scripts))
    # The registry copy lives in the system temporary folder, never in the out-root; it is removed on every exit
    # path Python runs, and the out-root receives a folder only once the read has succeeded.
    work = Path(os.path.realpath(tempfile.mkdtemp(prefix="sqmem-snapshot-")))
    try:
        if _inside(work, out_root) or _inside(work, registry):
            raise Refusal("E-OUTPUT")
        snapshot_dir = work / "snapshot"
        shas = snapshot(registry, snapshot_dir)
        events = verified_events(module, entry, snapshot_dir)
        shutil.rmtree(snapshot_dir)
        refs = {ref["sha256"]: ref for ref in module.artifact_refs([event["data"] for event in events])}
        agents = {}
        for event in events:
            if event["kind"] == "agent":
                agents.setdefault(event["data"]["id"], {"identity": event["data"]["identity"],
                                                        "role": event["data"]["role"]})
        result, counts = collect(events, Artifacts(registry, refs), agents)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    head = events[-1]
    binding = {"registry_id": events[0]["hash"], "first_seq": events[0]["seq"], "last_seq": head["seq"],
               "head_hash": head["hash"], "event_count": len(events), "snapshot_sha256": shas,
               "squadra": {key: entry[key] for key in ("label", "registry_py_sha256", "source_commit")}}
    name = events[0]["data"].get("name") if isinstance(events[0]["data"], dict) else None
    return {"out_root": out_root, "result": result, "counts": counts, "binding": binding, "refs": refs,
            "registry_id": events[0]["hash"], "name": name if isinstance(name, str) else None}


def _ledger_data(read):
    return connector.read_ledger(read["out_root"] / f"ledger-{read['registry_id']}.json")


def run_collect(args):
    read = read_registry(args)
    result, counts, binding, out_root = read["result"], read["counts"], read["binding"], read["out_root"]
    head = f"{binding['last_seq']}-{binding['head_hash'][:12]}"
    final = out_root / f"collect-{head}"
    if os.path.lexists(final):
        raise Refusal("E-OUTPUT")
    panel.write(Path.home(), read["registry_id"], result, _ledger_data(read))
    staging = Path(tempfile.mkdtemp(prefix=".collect-", suffix=".partial", dir=out_root))
    try:
        _write(staging / "candidates.json", document(result, counts, binding))
        _write(staging / "candidates.it.md", preview(result, binding, "it"))
        _write(staging / "candidates.en.md", preview(result, binding, "en"))
        os.rename(staging, final)
        staging = None
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
    return {"ok": True, "output": final.name, "counts": {**counts, **{key: len(result[key]) for key in LISTS}}}


def _summary(ledger, stopped):
    counts = {}
    for entry in ledger.data["items"].values():
        counts[entry.get("status")] = counts.get(entry.get("status"), 0) + 1
    return {"ok": stopped is None, "stopped": stopped, "counts": dict(sorted(counts.items()))}


def _connect(args):
    """Key first, before any registry read or network call; then the endpoint (flag, else the address saved from the
    squadra panel), scope and timeout. The saved settings are only read here, never written."""
    home = Path.home()
    key = connector.load_key(args.token_file, home)
    saved = connector.saved_settings(home)
    endpoint, tenant = connector.parse_endpoint(args.endpoint or saved["endpoint"] or "")
    if args.scope is not None and not connector.valid_scope(args.scope):
        raise Refusal("E-SCOPE")
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 60:
        raise Refusal("E-TIMEOUT")
    return key, endpoint, tenant


def run_send(args):
    key, endpoint, tenant = _connect(args)
    read = read_registry(args)
    scope = args.scope or connector.default_scope(read["name"])
    binding = {"endpoint": endpoint, "tenant": tenant, "scope": scope, "key_id_sha256": key.key_id_sha256}
    automatic = getattr(args, "auto", False)
    with connector.Ledger(read["out_root"], read["registry_id"]) as ledger:
        ledger.bind(binding)
        with (nullcontext(None) if automatic else actions.Selection(Path.home(), read["registry_id"])) as selection:
            if not automatic and selection.data is None:
                return {"ok": True, "outcome": "no_request", "sent": 0}
            chosen = None if automatic else set(selection.data["ids"]) & selection.selectable()
            candidates = [c for c in read["result"]["candidates"] if (automatic or connector.item_name(c) in chosen)
                          and (ledger.data["items"].get(connector.item_name(c)) or {}).get("status") in connector.PROPOSABLE]
            if args.accept_scope_policy and ledger.data.get("accepted_scope_policy") is None:
                ledger.data["accepted_scope_policy"] = {"at": connector.now_iso(), "by": "explicit"}
                ledger.save()
            client = connector.Client(endpoint, key, args.timeout)
            try:
                stopped = connector.send(ledger, client, candidates, scope, read["registry_id"], read["refs"])
            except connector.Stop as stop:
                stopped = stop.code
            except connector.Later:
                stopped = "E-RATE-LIMITED"
            panel.write(Path.home(), read["registry_id"], read["result"], ledger.data)
            if selection is not None and actions.complete(selection.data["ids"], read["result"]["candidates"], ledger.data):
                selection.clear()
            return _summary(ledger, stopped)


def run_status(args):
    key, endpoint, tenant = _connect(args)
    read = read_registry(args)
    scope = args.scope or connector.default_scope(read["name"])
    binding = {"endpoint": endpoint, "tenant": tenant, "scope": scope, "key_id_sha256": key.key_id_sha256}
    with connector.Ledger(read["out_root"], read["registry_id"]) as ledger:
        ledger.bind(binding)
        try:
            stopped = connector.status(ledger, connector.Client(endpoint, key, args.timeout), scope)
        except connector.Stop as stop:
            stopped = stop.code
        except connector.Later:
            stopped = "E-RATE-LIMITED"
        panel.write(Path.home(), read["registry_id"], read["result"], ledger.data)
        return _summary(ledger, stopped)


def run_resolve(args):
    seq, colon, index = args.item.partition(":")
    if not colon or not seq.isdigit() or not index.isdigit() or len(args.item) > 40:
        raise Refusal("E-ITEM")
    read = read_registry(args)
    with connector.Ledger(read["out_root"], read["registry_id"]) as ledger:
        connector.resolve(ledger, f"{int(seq)}:{int(index)}", proposal=args.proposal, abandon=args.abandon,
                          resend=args.resend)
        panel.write(Path.home(), read["registry_id"], read["result"], ledger.data)
        return _summary(ledger, None)


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def _common(command):
    command.add_argument("--registry", required=True)
    command.add_argument("--out-root", help="Existing private folder; default: data/ in the installed plugin")
    command.add_argument("--squadra-scripts", help="Explicit pinned squadra scripts folder; default: the bundled reader")


def _remote(command):
    command.add_argument("--endpoint", help="https://<host>/t/<tenant>/mcp; default: the address saved in the squadra panel")
    command.add_argument("--scope", help="memoranda scope; default squadra/<registry name>")
    command.add_argument("--token-file", help="Owner-only file holding the agent key; default MEMORANDA_API_KEY")
    command.add_argument("--timeout", type=float, default=20.0, help="Network timeout in seconds, at most 60")


def main(argv=None):
    os.umask(0o077)
    for number in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(number, _interrupt)
    parser = argparse.ArgumentParser(prog="squadra_memories", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    one = commands.add_parser("collect", help="Collect declared candidates read-only and refresh the panel")
    _common(one)
    send = commands.add_parser("send", help="Propose collected candidates to memoranda.dev (propose-only)")
    _common(send)
    _remote(send)
    send.add_argument("--auto", action="store_true",
                      help="Propose every eligible draft without reading or clearing a dashboard request")
    send.add_argument("--accept-scope-policy", action="store_true",
                      help="Keep sending to a scope whose policy approves automatically; recorded in the ledger")
    state = commands.add_parser("status", help="Refresh decided proposals from memoranda.dev")
    _common(state)
    _remote(state)
    fix = commands.add_parser("resolve", help="A person's explicit disposition of one uncertain item")
    _common(fix)
    fix.add_argument("--item", required=True, help="<declaration event>:<item index>")
    way = fix.add_mutually_exclusive_group(required=True)
    way.add_argument("--proposal", help="Adopt this proposal id, read in the memoranda console")
    way.add_argument("--abandon", action="store_true")
    way.add_argument("--resend", action="store_true", help="Propose it again; this can duplicate a lost proposal")
    args = parser.parse_args(argv)
    try:
        summary = {"collect": run_collect, "send": run_send, "status": run_status, "resolve": run_resolve}[
            args.command](args)
        print(json.dumps(summary, sort_keys=True))
        if summary.get("outcome") == "no_request":
            return 5
        stopped = summary.get("stopped")
        if stopped:
            print(f"{stopped}: {MESSAGES.get(stopped, 'the run stopped.')}", file=sys.stderr)
            return 3
        return 0
    except Refusal as refusal:
        print(f"{refusal.code}: {MESSAGES.get(refusal.code, 'refused.')}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("E-INTERRUPTED: " + MESSAGES["E-INTERRUPTED"], file=sys.stderr)
        return 130
    except Exception:
        print("E-INTERNAL: " + MESSAGES["E-INTERNAL"], file=sys.stderr)
        return 4
