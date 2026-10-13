"""The candidate interchange file and its Italian and English previews. Labels are translated; sentences never are."""
from __future__ import annotations

import json

from . import FORMAT, FORMAT_VERSION, LISTS

LABELS = {
    "en": {
        "title": "Memory candidates (private) — not saved, not sent, not approved",
        "notice": "Candidate interchange format squadra-memory-candidates v1, not a memoranda request. "
                  "Every candidate waits for the owner's decision.",
        "privacy": "The privacy answer is the declaring agent's own, not a check. The tool cannot remove what an "
                   "agent already wrote into the registry.",
        "attribution": "Owner attribution is what the record says; the channel does not authenticate the owner's words.",
        "read": "Registry read", "events": "events", "head": "head",
        "candidates": "Candidates", "contested": "Contested: objection without settlement", "withheld": "Withheld",
        "evidence_not_found": "Evidence not found", "malformed": "Malformed", "unauthorized": "Unauthorized replies",
        "dropped": "Dropped", "empty": "none",
        "type": {"decision": "Decision", "constraint": "Constraint", "trap": "Trap already paid for"},
        "certainty": {"sure": "sure", "likely": "likely"},
        "channel": {"typed-here": "typed here", "relayed": "relayed"}, "no_channel": "channel not recorded",
        "declared": "declared by {seat} at event #{seq}, task {task}, item {item}",
        "who": "decided by", "evidence": "evidence", "objection": "objection by {by} at event #{seq}",
        "why_hidden": "reason not exported: its privacy was not declared «no»",
        "row": "event #{seq}, item {item}", "reason": "reason",
    },
    "it": {
        "title": "Candidati di memoria (privato) — non salvati, non inviati, non approvati",
        "notice": "Formato di scambio squadra-memory-candidates v1, non una richiesta a memoranda. "
                  "Ogni candidato attende la decisione del proprietario.",
        "privacy": "La risposta sulla riservatezza è dell'agente che dichiara, non un controllo. Lo strumento non può "
                   "togliere ciò che un agente ha già scritto nel registro.",
        "attribution": "L'attribuzione al proprietario è quella del registro; il canale non autentica le sue parole.",
        "read": "Registro letto", "events": "eventi", "head": "ultimo",
        "candidates": "Candidati", "contested": "Contestati: obiezione senza decisione", "withheld": "Trattenuti",
        "evidence_not_found": "Prova non trovata", "malformed": "Non validi", "unauthorized": "Risposte non autorizzate",
        "dropped": "Scartati", "empty": "nessuno",
        "type": {"decision": "Decisione", "constraint": "Vincolo", "trap": "Trappola già pagata"},
        "certainty": {"sure": "sicuro", "likely": "probabile"},
        "channel": {"typed-here": "digitate qui", "relayed": "riferite"}, "no_channel": "canale non registrato",
        "declared": "dichiarato da {seat} all'evento #{seq}, incarico {task}, voce {item}",
        "who": "deciso da", "evidence": "prova", "objection": "obiezione di {by} all'evento #{seq}",
        "why_hidden": "motivo non esportato: la sua riservatezza non è stata dichiarata «no»",
        "row": "evento #{seq}, voce {item}", "reason": "motivo",
    },
}


def document(result, counts, binding):
    doc = {"format": FORMAT, "format_version": FORMAT_VERSION, "is_memoranda_request": False, "private": True,
           "notice": LABELS["en"]["notice"], "binding": binding,
           "counts": {**counts, **{name: len(result[name]) for name in LISTS}}}
    doc.update(result)
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _fence(text):
    longest = run = 0
    for char in text:
        run = run + 1 if char == "`" else 0
        longest = max(longest, run)
    return "`" * max(3, longest + 1)


def _evidence(entry, words):
    if "event" in entry:
        task = ", ".join(entry["task"]) if isinstance(entry["task"], list) else entry["task"] or "—"
        return f"{words['evidence']}: #{entry['event']} {entry['kind']} ({task}) {entry['hash'][:16]}"
    return f"{words['evidence']}: {entry['artifact'][:16]} ({entry['bytes']} B)"


def _literal(text):
    fence = _fence(text)
    return ["", fence, text, fence, ""]


def _item(item, words):
    lines = [f"### {words['type'][item['type']]} — {words['certainty'][item['certainty']]}"]
    lines += _literal(item["sentence"])
    lines.append("- " + words["declared"].format(seat=item["declared_by"]["seat"], seq=item["declared_by"]["seq"],
                                                  task=item["task"], item=item["item"]))
    if item["type"] == "decision":
        channel = words["channel"].get(item["owner_channel"], words["no_channel"])
        lines.append(f"- {words['who']} ({channel}):")
        lines += _literal(item["who"]) if item["who"] else ["  —"]
    lines += ["- " + _evidence(entry, words) for entry in item["evidence"]]
    for objection in item["objections"]:
        lines.append("- " + words["objection"].format(by=objection["by"], seq=objection["seq"]))
        lines += _literal(objection["why"]) if objection["why"] is not None else ["  " + words["why_hidden"]]
    return lines + [""]


def preview(result, binding, language):
    words = LABELS[language]
    lines = [f"# {words['title']}", "", words["notice"], "", words["privacy"], "", words["attribution"], "",
             f"{words['read']}: {binding['event_count']} {words['events']}, {words['head']} #{binding['last_seq']} "
             f"{binding['head_hash'][:16]}, {binding['squadra']['label']}", ""]
    for name in LISTS:
        rows = result[name]
        lines += [f"## {words[name]} ({len(rows)})", ""]
        if not rows:
            lines += [words["empty"], ""]
        elif name in ("candidates", "contested"):
            for item in rows:
                lines += _item(item, words)
        else:
            for row in rows:
                text = words["row"].format(seq=row["seq"], item="—" if row.get("item") is None else row["item"])
                if row.get("reason"):
                    text += f" — {words['reason']}: {row['reason']}"
                if row.get("verdict"):
                    text += f" ({row['verdict']})"
                lines.append("- " + text)
                lines += ["  - " + _evidence(entry, words) for entry in row.get("evidence", [])]
            lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"
