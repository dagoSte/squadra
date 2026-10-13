"""The squadra plugin manifest, icon, coordinator instructions and registry panels under `$HOME/.squadra`.

Written to the SQ-PLUGINS contract v1 and nothing more: unknown keys are never written, strings stay within 4 KiB,
the panel within 500 items and 2 MiB, files are 0600 and folders 0700, and no symlink is followed below `.squadra`.
A withheld item shows only its reason code.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .connector import now_iso, saved_settings
from .source import Refusal

NAME = "memoranda"
MANIFEST_LIMIT = 64 * 1024
PANEL_LIMIT = 2 * 1024 * 1024
ICON_LIMIT = 32 * 1024
ICON = Path(__file__).resolve().parents[2] / "icon.png"
AGENTS = Path(__file__).resolve().parents[2] / "AGENTS.md"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
TEXT_LIMIT = 4096
ITEM_LIMIT = 500
TOO_LONG = "(troppo lungo da mostrare / too long to show)"
BUNDLE = Path(__file__).resolve().parents[2]
MANIFEST = json.loads((BUNDLE / "plugin.json").read_text(encoding="utf-8"))

POLICY = {
    "it": "Solo proposte: l'approvazione segue la policy dello scope dell'abbonato, manuale per default.",
    "en": "Propose-only: approval follows the subscriber's scope policy, manual by default.",
}
TYPES = {"decision": ("Decisione", "Decision"), "constraint": ("Vincolo", "Constraint"),
         "trap": ("Trappola già pagata", "Trap already paid for")}
CERTAINTY = {"sure": ("sicuro", "sure"), "likely": ("probabile", "likely")}
WITHHELD = {"private": "private", "unanswered": "unanswered"}
LEDGER_STATUS = {None: "draft", "resend": "draft", "sending": "error", "uncertain": "error", "sent": "sent",
                 "approved": "approved", "merged": "approved", "rejected": "rejected", "error": "error",
                 "abandoned": "error"}
LABEL = {
    "declared": ("Dichiarata da", "Declared by"), "task": ("Incarico", "Task"), "evidence": ("Prova", "Evidence"),
    "proposal": ("Proposta memoranda", "memoranda proposal"), "memory": ("Memoria", "Memory"),
    "state": ("Stato", "State"), "code": ("Codice", "Code"), "reason": ("Motivo", "Reason"),
    "objection": ("Obiezione", "Objection"),
}


def _label(key):
    it, en = LABEL[key]
    return {"it": it, "en": en}


def _fit(text):
    return text if len(text.encode("utf-8")) <= TEXT_LIMIT else TOO_LONG


def _meta(key, value):
    return {"label": _label(key), "value": _fit(str(value))}


def _evidence(entries):
    shown = []
    for entry in entries:
        if "event" in entry:
            shown.append(f"#{entry['event']} {entry['kind']}")
        elif "artifact" in entry:
            shown.append(entry["artifact"][:16])
    return ", ".join(shown) or "—"


def _state(entry):
    """The panel status and the extra meta lines a ledger entry contributes; only fixed words and validated ids."""
    status = (entry or {}).get("status")
    meta = []
    if entry and entry.get("proposal_id"):
        meta.append(_meta("proposal", entry["proposal_id"]))
    if status in ("sending", "uncertain"):
        meta.append(_meta("state", "esito incerto: risolvere esplicitamente / uncertain outcome: resolve explicitly"))
    elif status == "approved" and entry.get("by_policy"):
        meta.append(_meta("state", "approvata automaticamente dalla policy dell'abbonato / "
                                   "approved automatically by the subscriber's policy"))
    elif status == "merged":
        meta.append(_meta("state", "unita a / merged into " + (entry.get("merged_into") or "—")))
    elif status == "rejected" and entry.get("reason_code"):
        meta.append(_meta("reason", entry["reason_code"]))
    elif status == "abandoned":
        meta.append(_meta("state", "abbandonata esplicitamente / abandoned explicitly"))
    if status in ("error", "uncertain", "sending") and entry.get("error_code"):
        meta.append(_meta("code", entry["error_code"]))
    if entry and entry.get("memory_id"):
        meta.append(_meta("memory", entry["memory_id"]))
    if entry and entry.get("redacted"):
        meta.append(_meta("state", "in parte oscurata da memoranda / redacted by memoranda"))
    return LEDGER_STATUS.get(status, "error"), meta


def _declared(item):
    it, en = TYPES[item["type"]]
    sure_it, sure_en = CERTAINTY[item["certainty"]]
    meta = [_meta("declared", f"{item['declared_by']['seat']} #{item['declared_by']['seq']}"),
            _meta("task", item["task"] or "—"), _meta("evidence", _evidence(item["evidence"]))]
    return f"{it} — {sure_it} / {en} — {sure_en}", meta


def items(result, ledger):
    entries = (ledger or {}).get("items", {})
    rows = []
    for item in result["candidates"]:
        name = f"{item['declared_by']['seq']}:{item['item']}"
        title, meta = _declared(item)
        status, extra = _state(entries.get(name))
        rows.append(((item["declared_by"]["seq"], item["item"]),
                     {"id": name, "title": title, "text": _fit(item["sentence"]), "status": status,
                      "selectable": status == "draft",
                      "meta": meta + extra}))
    for item in result["contested"]:
        title, meta = _declared(item)
        for objection in item["objections"]:
            meta.append(_meta("objection", f"{objection['by']} #{objection['seq']}"))
        rows.append(((item["declared_by"]["seq"], item["item"]),
                     {"id": f"{item['declared_by']['seq']}:{item['item']}", "title": title,
                      "text": _fit(item["sentence"]), "status": "draft", "meta": meta}))
    for row in result["withheld"]:
        rows.append(((row["seq"], row["item"]),
                     {"id": f"{row['seq']}:{row['item']}", "title": "Trattenuta / Withheld",
                      "text": WITHHELD.get(row["reason"], "withheld"), "status": "withheld",
                      "meta": [_meta("evidence", _evidence(row["evidence"]))]}))
    for name, code in (("evidence_not_found", "evidence-not-found"), ("malformed", "malformed")):
        for row in result[name]:
            index = row["item"] if row.get("item") is not None else -1
            rows.append(((row["seq"], index),
                         {"id": f"{row['seq']}:{'—' if index < 0 else index}", "title": "Non valida / Not valid",
                          "text": code, "status": "error", "meta": [_meta("code", code)]}))
    rows.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _, row in rows]


def build(result, ledger, updated=None, saved=None):
    every = items(result, ledger)
    kept = every[:ITEM_LIMIT]
    while True:
        counts = {status: sum(1 for row in kept if row["status"] == status)
                  for status in ("draft", "sent", "approved", "rejected", "withheld", "error")}
        left_out = len(every) - len(kept)
        automatic = (ledger or {}).get("scope_policy") == "automatic"
        accepted = (ledger or {}).get("accepted_scope_policy") is not None
        connected = bool(saved and saved.get("endpoint") and saved.get("key"))
        link_it = "memoranda collegato." if connected else "memoranda non collegato: apri Impostazioni qui sotto."
        link_en = "memoranda connected." if connected else "memoranda not connected: open Settings below."
        note_it = (f"{link_it} Invii automatici dalla finestra coordinatrice; l'approvazione resta in memoranda. "
                   f"{POLICY['it']} Le voci trattenute mostrano solo il motivo; un esito incerto non viene mai "
                   "reinviato da solo e va risolto esplicitamente. Invio per revisione salva soltanto la scelta: "
                   "una finestra esegue send. Annulla richiesta non ferma un invio già avviato: termina il gruppo "
                   "preso in carico, circa 2,4 s per voce (500 voci: circa 20 minuti, oltre ai tempi di rete). "
                   "Una richiesta con esiti incerti resta salvata; dopo resolve --resend la voce parte al prossimo "
                   "send senza nuova selezione, con possibile duplicazione. Puoi annullare la richiesta e "
                   "sceglierne un'altra; annullarla non disattiva gli invii automatici.")
        note_en = (f"{link_en} Automatic sends by the coordinator window; approval stays in memoranda. "
                   f"{POLICY['en']} Withheld items show only their reason; an uncertain outcome is never "
                   "resent on its own and must be resolved explicitly. Send for review only saves the selection: "
                   "a squad window runs send. Cancel request does not stop a send already running: it finishes "
                   "its admitted batch, about 2.4 s per item (500 items: about 20 minutes, plus network time). "
                   "An uncertain request remains saved; after resolve --resend the item goes at the next send "
                   "without reselection, with possible duplication. You can cancel the request and choose again; "
                   "cancelling it does not disable automatic sends.")
        if automatic and accepted:
            note_it += (" Questo scope approva automaticamente le proposte; l'invio continuo è stato accettato "
                        "esplicitamente con --accept-scope-policy.")
            note_en += (" This scope approves proposals automatically; continued sending was accepted explicitly "
                        "with --accept-scope-policy.")
        elif automatic:
            note_it += " Questo scope approva automaticamente le proposte: altri invii richiedono --accept-scope-policy."
            note_en += " This scope approves proposals automatically: further sends need --accept-scope-policy."
        if left_out:
            note_it += f" {left_out} voci più vecchie non sono mostrate."
            note_en += f" {left_out} older items are not shown."
        panel = {
            "schema": 1, "updated": updated or now_iso(),
            "summary": {
                "it": f"{len(kept)} memorie: {counts['draft']} da inviare, {counts['sent']} inviate, "
                      f"{counts['approved']} approvate, {counts['rejected']} respinte, {counts['withheld']} "
                      f"trattenute, {counts['error']} con errore.",
                "en": f"{len(kept)} memories: {counts['draft']} to send, {counts['sent']} sent, "
                      f"{counts['approved']} approved, {counts['rejected']} rejected, {counts['withheld']} "
                      f"withheld, {counts['error']} with an error.",
            },
            "note": {"it": note_it, "en": note_en},
            "items": kept,
        }
        body = (json.dumps(panel, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        if len(body) <= PANEL_LIMIT or not kept:
            return body
        kept = kept[:-1]


def icon_bytes():
    """The memoranda.dev site mark shipped with the package: a PNG of at most 32 KiB, 1–256 px a side."""
    body = ICON.read_bytes()
    width, height = int.from_bytes(body[16:20], "big"), int.from_bytes(body[20:24], "big")
    if len(body) > ICON_LIMIT or not body.startswith(PNG_SIGNATURE) or body[12:16] != b"IHDR" \
            or not (1 <= width <= 256 and 1 <= height <= 256):
        raise Refusal("E-PANEL")
    return body


def agents_bytes():
    return AGENTS.read_bytes()


def write(home, registry_id, result, ledger):
    """Refresh only the panel of an explicitly installed plugin; managed files stay immutable."""
    import plugins as p
    import plugin_settings as files
    try:
        with files.installed(home, NAME, 'actions') as (parent, _):
            for name in ('plugin.json', 'icon.png', 'AGENTS.md'):
                expected = (BUNDLE / name).read_bytes()
                if p._read_bytes(parent, name, MANIFEST_LIMIT, files.private) != expected:
                    raise Refusal('E-PANEL')
            try:
                os.mkdir('panels', 0o700, dir_fd=parent)
            except FileExistsError:
                pass
            fd = files.optional(parent, 'panels', True)
            try:
                files.atomic(fd, f"{registry_id}.json", build(result, ledger, saved=saved_settings(home)))
            finally:
                os.close(fd)
    except (p.Refused, OSError):
        raise Refusal('E-PANEL') from None
