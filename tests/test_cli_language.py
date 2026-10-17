"""Every CLI view speaks the workspace language: English for init --lang en, Italian otherwise."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import registry
from squadra import main

RECORDED, READ = "2026-10-15T12:00:00+00:00", "2026-10-15T14:00:00+00:00"
# Phrases that only the Italian views print, and phrases that only the English views print.
ITALIAN = ("Serve a te", "Frase:", "Attese calcolate", "totali", "Tutte le attese", "Prossimo passo", "Incarico",
           "Finestra", "Lavoro", "Stato", "Realizzati", "In corso", "Da fare", "Fatti", "Nessun", "Presa in carico",
           "Lettura in pool", "dell'esecutore", "Lettura non autore", "Piano in attesa", "Ping senza risposta",
           "Area in pausa", "Informazioni senza conferma", "Sostituiti", "Finestre in stand-by", "Presenza dichiarata",
           "ultima risposta", "risposta pendente", "Dichiarato dalla finestra", "Non dichiarate", "prova pendente",
           "ultima prova", "Messaggi senza ACK", "Riserve attive", "Registrazione e heartbeat", "riepilogo", "Registro",
           "test sintetici", "Ruolo", "Esito", "Leggere il rapporto", "IN PAUSA", "circa")
ENGLISH = ("Needs you", "Say:", "Waits computed", "in total", "All waits", "Next step", "| Task", "Window", "| Work |",
           "State", "Done ", "In progress", "To do", "No task", "No request", "Receipt to be confirmed", "Pool read",
           "Executor", "Non-author read", "Plan waiting", "Ping without a reply", "Area paused", "Information messages",
           "Replaced", "Windows on stand-by", "Declared presence", "last reply", "reply pending", "Declared by the window",
           "Not declared", "proof pending", "latest proof", "Messages without ACK", "Active reservations",
           "Registration and heartbeat", "current summary", "Registry", "synthetic tests", "| Role |", "Result",
           "Read the report", "PAUSED", "about")
# Lines each language must print, so a clean result cannot come from a view that skipped its blocks.
EXPECTED = {
    "en": {"status": ["Needs you:", "- Area paused: resume only at the owner's request", "  A: Keep · Say: Keep it",
                      "Information messages with no confirmation required: 1", "Waits computed at " + READ + ":",
                      " in total · ", "Receipt to be confirmed", "Pool read: the first ACK takes it", "Non-author read",
                      "All waits and details: squadra status --all.",
                      "Next step per task:", "| Task | Window / role | Next step |", "Executor ACK",
                      "Plan waiting for a non-author read", "· PAUSED", "## In progress", "Done 0 of 4"],
           "all": ["Ping without a reply; stand-by needs a coordinator sweep", "Replaced (history kept, outside next steps and waits):", "- T4 → T5", "Windows on stand-by: X.",
                   "| Window / role | Task / candidate | State / proof | Skill | Next step |", "Not declared",
                   "Declared by the window · output ", "/ latest proof passed rc=0 @", "/ proof pending",
                   "Declared presence and replies (not proof of a live process):", "· last reply None · ping ",
                   "reply pending", "Messages without ACK: ", "Registration and heartbeat do not prove"],
           "summary": ["# Squadra · current summary", "Registry #", "| Window | Next step |"],
           "selftest": ["| Role | Proof | Result | Record | Next step |", "| selftest | 3 synthetic tests | passed |",
                        "Read the report; no product acceptance"]},
    "it": {"status": ["Serve a te:", "- Area in pausa: ripresa solo su richiesta del proprietario", "  A: Keep · Frase: Keep it",
                      "Informazioni senza conferma richiesta: 1", "Attese calcolate al " + READ + ":", " totali · ",
                      "Presa in carico da confermare", "Lettura in pool: il primo ACK la prende", "Lettura non autore",
                      "Tutte le attese e i dettagli: squadra status --all.", "Prossimo passo per incarico:",
                      "| Incarico | Finestra / ruolo | Prossimo passo |", "ACK dell'esecutore",
                      "Piano in attesa di lettura non autore", "· IN PAUSA", "## In corso", "Realizzati 0 su 4"],
           "all": ["Ping senza risposta; stand-by richiede sweep del coordinatore",
                   "Sostituiti (storia conservata, fuori dai prossimi passi e dalle attese):", "- T4 → T5",
                   "Finestre in stand-by: X.", "| Finestra / ruolo | Incarico / candidato | Stato / prova | Skill | Prossimo passo |",
                   "Non dichiarate", "Dichiarato dalla finestra · output ", "/ ultima prova passed rc=0 @", "/ prova pendente",
                   "Presenza dichiarata e risposte (non prova di processo vivo):", "· ultima risposta None · ping ",
                   "risposta pendente", "Messaggi senza ACK: ", "Registrazione e heartbeat non dimostrano"],
           "summary": ["# Squadra · riepilogo corrente", "Registro #", "| Finestra | Prossimo passo |"],
           "selftest": ["| Ruolo | Prova | Esito | Registro | Prossimo passo |", "| selftest | 3 test sintetici | passed |",
                        "Leggere il rapporto; nessuna accettazione di prodotto"]},
}


# The echo after a write and the saved projection carry the same localized blocks.
ECHO = {"en": ["Needs you:", "Waits computed at " + READ + ":", "Next step per task:"],
        "it": ["Serve a te:", "Attese calcolate al " + READ + ":", "Prossimo passo per incarico:"]}
PROJECTION = {"en": ["Done 0 of 4", "Needs you:", "| Window / role | Task / candidate | State / proof | Skill | Next step |"],
              "it": ["Realizzati 0 su 4", "Serve a te:", "| Finestra / ruolo | Incarico / candidato | Stato / prova | Skill | Prossimo passo |"]}


def walk(base, lang):
    """Drive one workspace through the CLI and return every view a user reads, keyed by name."""
    repo, root = base / "repo", base / "record"
    repo.mkdir()
    env = os.environ | {"GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "Synthetic", "GIT_AUTHOR_EMAIL": "s@example.invalid",
                        "GIT_COMMITTER_NAME": "Synthetic", "GIT_COMMITTER_EMAIL": "s@example.invalid"}
    (repo / "value.txt").write_text("Synthetic fixture\n")
    for args in [("init", "-q"), ("add", "."), ("-c", "commit.gpgsign=false", "commit", "-qm", "fixture")]:
        subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True, check=True)
    files = {"scope": "Synthetic work\n", "plan": "Synthetic plan\n", "body": "Synthetic body\n", "report": "Synthetic report\n",
             "proof": json.dumps([sys.executable, "-c", "pass"]),
             "options": json.dumps([{"id": "A", "label": "Keep", "say": "Keep it"}, {"id": "B", "label": "Drop", "say": "Drop it"}])}
    for name, text in files.items():
        (base / name).write_text(text)
    views = {}

    def cli(at, *args, name=None):
        out = io.StringIO()
        with patch("registry.now", return_value=at), patch("squadra.now", return_value=at), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = main(["--root", str(root), *args])
        if rc != 0:
            raise AssertionError((args, rc, out.getvalue()))
        if name:
            views[name] = out.getvalue()

    def task(name, *extra):
        cli(RECORDED, "task", "--actor", "C", "--id", name, "--title", "Job " + name, "--owner", "A", "--repo", str(repo),
            "--scope-file", str(base / "scope"), "--proof-file", str(base / "proof"), *extra)

    cli(RECORDED, "init", "--name", "Fixture", "--project", str(repo), "--temporary", *(["--lang", lang] if lang else []))
    for agent, role in [("C", "coordinator"), ("A", "builder"), ("R", "reviewer"), ("R2", "reviewer"), ("X", "builder")]:
        cli(RECORDED, "agent", "--id", agent, "--identity", "identity-" + agent, "--role", role, "--client", "manual",
            "--repo", str(repo))
    task("T1", "--reviewer", "R")
    task("T2", "--reviewer", "R")
    cli(RECORDED, "ack", "--actor", "A", "--message", "assign-T2")
    cli(RECORDED, "start", "--actor", "A", "--task", "T2")
    cli(RECORDED, "plan", "--actor", "A", "--task", "T2", "--file", str(base / "plan"))
    cli(RECORDED, "output", "--actor", "A", "--task", "T2", "--file", str(base / "report"), "--next", "T2 -> R")
    task("T3", "--reviewer", "R", "--reviewer", "R2")
    cli(RECORDED, "ack", "--actor", "A", "--message", "assign-T3")
    cli(RECORDED, "start", "--actor", "A", "--task", "T3")
    cli(RECORDED, "prove", "--actor", "A", "--task", "T3", "--", sys.executable, "-c", "pass")
    cli(RECORDED, "deliver", "--actor", "A", "--task", "T3")
    task("T4")
    task("T5")
    cli(RECORDED, "supersede", "--actor", "C", "--task", "T4", "--by", "T5", "--reason", "Synthetic")
    cli(RECORDED, "send", "--actor", "C", "--to", "A", "--info", "--body-file", str(base / "body"))
    cli(RECORDED, "standby", "--actor", "C", "--agent", "X", "--reason", "Synthetic")
    cli(RECORDED, "ping", "--actor", "C", "--to", "R")
    # A ping becomes overdue only after a delivered transport, so the fixture records the queue receipt.
    with patch("registry.now", return_value=RECORDED):
        store = registry.Store(root)
        try:
            store.append("transport", "C", {"message": registry.latest_ping(store.state(), "R")["id"],
                                             "via": "codex-queue", "delivered": True})
        finally:
            store.close()
    cli(READ, "pause", "--actor", "C", "--reason", "Synthetic", name="pause")
    cli(READ, "ask", "--actor", "C", "--id", "Q1", "--question", "Q1?", "--options-file", str(base / "options"), name="ask")
    cli(READ, "send", "--actor", "C", "--to", "A", "--id", "M1", "--body-file", str(base / "body"), name="send")
    views["summary"] = (root / "stato.md").read_text()
    cli(READ, "status", name="status")
    cli(READ, "status", "--all", name="all")
    cli(READ, "board", name="board")
    cli(READ, "export", name="export")
    views["projection"] = (root / "stato.md").read_text()
    result = {"tests": 3, "status": "passed", "folder": str(base / "selftest")}
    with patch("selftest.run", return_value=result):
        cli(READ, "selftest", name="selftest")
    return views


class Language(unittest.TestCase):
    def views(self, lang):
        temp = tempfile.TemporaryDirectory(prefix="squadra-lang-")
        self.addCleanup(temp.cleanup)
        return walk(Path(temp.name).resolve(), lang)

    def check(self, views, lang, foreign):
        for name, text in views.items():
            for phrase in foreign:
                self.assertNotIn(phrase, text, (lang, name))
        for name, lines in EXPECTED[lang].items():
            for line in lines:
                self.assertIn(line, views[name], (lang, name))
        for write in ("pause", "ask", "send"):
            for line in ECHO[lang]:
                self.assertIn(line, views[write], (lang, write))
        self.assertTrue(views["status"].startswith(views["board"].rstrip() + "\n"))
        for line in PROJECTION[lang]:
            self.assertIn(line, views["projection"], lang)

    def test_english_workspace_prints_no_italian(self):
        self.check(self.views("en"), "en", ITALIAN)

    def test_italian_workspace_prints_no_english(self):
        self.check(self.views("it"), "it", ENGLISH)

    def test_default_workspace_is_italian(self):
        self.check(self.views(None), "it", ENGLISH)

    def test_empty_workspace_speaks_its_language(self):
        for lang, foreign, lines in [("en", ITALIAN, ["No request recorded — a question asked only in chat does not appear here.",
                                                      "| — | — | No pending step |"]),
                                     ("it", ENGLISH, ["Nessuna richiesta registrata — una domanda fatta solo in chat non compare qui.",
                                                      "| — | — | Nessun passo pendente |"])]:
            temp = tempfile.TemporaryDirectory(prefix="squadra-lang-empty-")
            self.addCleanup(temp.cleanup)
            root, out = Path(temp.name).resolve(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                rc = main(["--root", str(root / "record"), "init", "--name", "Empty", "--project", str(root),
                           "--temporary", "--lang", lang])
                rc += main(["--root", str(root / "record"), "status"])
            self.assertEqual(rc, 0, out.getvalue())
            for phrase in foreign:
                self.assertNotIn(phrase, out.getvalue(), lang)
            for line in lines:
                self.assertIn(line, out.getvalue(), lang)


class Sentences(unittest.TestCase):
    def test_every_step_phase_and_printed_wait_has_english_and_recorded_reasons_stay(self):
        task = {"id": "L1", "status": "done", "owner": "A", "read_at_gate": True, "route": "light",
                "gate_deferrals": [{"gate": "G1", "actor": "C", "reason": "Owner moved it"}]}
        step = registry.next_step(task)
        self.assertEqual(step["phase"], "gate-read")
        self.assertEqual(registry.step_text(step, False), step["text"])
        self.assertEqual(registry.step_text(step, True),
                         "Closed without a read: to be read at the gate · deferred by G1 (C): Owner moved it")
        phases = set(registry.TASK_STATUSES) | {"gate-read", "plan-read", "plan-fix", "plan-blocked"}
        self.assertEqual(set(registry.STEP_TEXT_EN), phases)
        for kind in ("ack", "delivered", "reviewed", "cleanup-unknown", "heartbeat", "alive"):
            wait = {"kind": kind, "text": "Testo registrato"}
            self.assertEqual(registry.wait_text(wait, False), "Testo registrato")
            self.assertNotEqual(registry.wait_text(wait, True), "Testo registrato", kind)
        self.assertEqual(registry.wait_text({"kind": "ack", "handoffs": ["h"], "text": "x"}, True),
                         "Pool read: the first ACK takes it")
        self.assertEqual(set(registry.OWNER_TEXT_EN), {"budget", "paused"})


class QuickStart(unittest.TestCase):
    def inits(self, name):
        lines = [line for line in (ROOT / name).read_text().splitlines() if line.startswith("squadra init ")]
        self.assertTrue(lines, name)
        return lines

    def test_english_quick_starts_create_an_english_workspace(self):
        for name in ("README.md", "references/start.md"):
            for line in self.inits(name):
                self.assertIn(" --lang en", line, name)

    def test_italian_quick_start_keeps_the_default_language(self):
        for line in self.inits("README.it.md"):
            self.assertNotIn("--lang", line)


if __name__ == "__main__":
    unittest.main()
