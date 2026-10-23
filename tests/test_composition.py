"""Advice on how Claude and Codex windows split the work: counted, sourced, shown, and never acting."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

import test_squadra as support
from dashboard_test_support import make_server
from registry import Store, canonical, composition, composition_lines, snapshot
from squadra import main

ROOT = Path(__file__).resolve().parents[1]


def agent(name, role, client, identity=None):
    return {"id": name, "role": role, "identity": identity or "window-" + name, "client": client,
            "repo": "/synthetic", "skills": [], "session": ""}


def squad(*rows):
    return {"agents": {r[0]: agent(*r) for r in rows}, "tasks": {}}


COORD = ("coord", "coordinator", "claude")


class Advice(unittest.TestCase):
    # PT7
    def test_one_client_only_asks_for_the_other_model_or_a_recorded_reason(self):
        value = composition(squad(COORD, ("b1", "builder", "claude"), ("r1", "reviewer", "claude")))
        self.assertEqual((value["case"], value["claude"], value["codex"]), ("one-client", 2, 0))
        self.assertIn("Solo finestre Claude (2): la rilettura con l'altro modello non è possibile. Apri una finestra "
                      "Codex che rilegga; altrimenti ogni lettura ha bisogno di un motivo registrato con "
                      "--same-client-reason.", value["message"])
        self.assertNotIn("coordina", value["message"])
        value = composition(squad(COORD, ("x1", "builder", "codex"), ("x2", "reviewer", "codex")), lang="en")
        self.assertEqual(value["message"], "Only Codex windows (2): no read by the other model is possible. Open one "
                         "Claude window to read; otherwise every read needs a recorded --same-client-reason. Only a "
                         "Claude window coordinates.")

    # PT7
    def test_two_claude_and_one_codex_name_the_lone_reader_as_the_bottleneck(self):
        rows = (COORD, ("b1", "builder", "claude"), ("b2", "builder", "claude"), ("x1", "reviewer", "codex"))
        value = composition(squad(*rows))
        self.assertEqual((value["case"], value["claude"], value["codex"]), ("imbalance", 2, 1))
        self.assertEqual(value["message"], "L'unica finestra Codex rileggerà tutto quello che costruiscono le 2 finestre "
                         "Claude e diventerà il collo di bottiglia. Affidale un lavoro da costruire, riletto da una "
                         "finestra Claude, oppure apri un'altra finestra Codex.")
        self.assertEqual(composition(squad(*rows), lang="en")["message"],
                         "The lone Codex window will read everything the 2 Claude windows build and become the "
                         "bottleneck. Give it a build lane read by a Claude window, or open one more Codex window.")
        value = composition(squad(COORD, ("b1", "builder", "codex"), ("b2", "builder", "codex"), ("b3", "builder", "codex"),
                                  ("r1", "reviewer", "claude"), ("r2", "reviewer", "claude")), lang="en")
        self.assertTrue(value["message"].startswith("The 2 Claude windows will read everything the 3 Codex windows"))

    # PT7
    def test_a_balanced_squad_pairs_each_open_task_with_a_reader_of_the_other_model(self):
        state = squad(COORD, ("b1", "builder", "claude"), ("b2", "builder", "codex"),
                      ("r1", "reviewer", "codex"), ("r2", "reviewer", "claude"))
        state["tasks"] = {
            "T1": {"id": "T1", "owner": "b1", "reviewer": "r1", "status": "working"},
            "T2": {"id": "T2", "owner": "b2", "reviewer": None, "status": "waiting-ack"},
            "T0": {"id": "T0", "owner": "b2", "reviewer": "r2", "status": "done"}}
        value = composition(state)
        self.assertEqual((value["case"], value["claude"], value["codex"]), ("balanced", 2, 2))
        self.assertEqual(value["message"], "In equilibrio: 2 Claude e 2 Codex. Abbina a ogni costruttore un lettore "
                         "dell'altro modello.")
        self.assertEqual([(p["task"], p["builder"], p["readers"], p["named"]) for p in value["pairs"]],
                         [("T2", "b2", ["r2"], False), ("T1", "b1", ["r1"], True)])
        self.assertEqual(composition_lines(value)[2:], ["- T2: b2 (Codex) → r2 (da nominare)", "- T1: b1 (Claude) → r1"])

    # PT7
    def test_the_coordinator_window_and_windows_in_stand_by_are_not_counted(self):
        state = squad(COORD, ("coord-review", "reviewer", "claude", "window-coord"),
                      ("b1", "builder", "claude"), ("x1", "reviewer", "codex"), ("x2", "builder", "codex"))
        self.assertEqual((composition(state)["claude"], composition(state)["case"]), (1, "imbalance"))
        state["agents"]["x2"]["availability"] = {"status": "stand-by"}
        value = composition(state)
        self.assertEqual((value["claude"], value["codex"], value["case"]), (1, 1, "balanced"))
        self.assertIn("esclusa quella del coordinatore e quelle in stand-by", value["source"])

    # PT7
    def test_the_source_is_named_and_the_census_is_never_filled_in(self):
        state = squad(COORD, ("b1", "builder", "claude"), ("x1", "reviewer", "codex"))
        seen = [{"pid": 1, "client": "claude"}, {"pid": 2, "client": "claude"}, {"pid": 3, "client": "codex"}]
        value = composition(state, seen, "en")
        self.assertEqual(value["source"], "registered windows (the coordinator's and those in stand-by left out); the "
                         "census also sees 2 Claude, 1 Codex processes (a process is not a registered window)")
        self.assertEqual((value["claude"], value["codex"]), (1, 1), "processes are reported, never counted as windows")
        value = composition(None, seen, "en")
        self.assertTrue(value["source"].startswith("census only: processes seen in the project, roles not known yet"))
        self.assertEqual((value["claude"], value["codex"], value["case"]), (2, 1, "imbalance"))
        value = composition(None, [], "en", scan="partial")
        self.assertIn("the census is partial and may not see every window", value["source"])
        self.assertEqual(value["case"], "none")


class Views(unittest.TestCase):
    """Real registry: the CLI, stato.md and the dashboard show the advice, and nothing changes because of it."""
    git = support.Workflow.git
    task = support.Workflow.task

    def setUp(self):
        support.Workflow.setUp(self)
        for name, role, client in [("B", "builder", "claude"), ("X", "reviewer", "codex")]:
            self.store.append("agent", name, {"id": name, "role": role, "identity": "window-" + name, "client": client,
                                              "repo": str(self.repo), "skills": [], "session": ""})

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(["--root", str(self.root), *args])
        return rc, out.getvalue(), err.getvalue()

    def frozen(self):
        return len(self.store.events()), canonical(self.store.state())

    # PT7
    def test_status_census_and_stato_md_show_it_and_change_nothing(self):
        # A (Codex) and B (Claude) build; R (Claude) and X (Codex) read; A-alias is A's own window.
        before = self.frozen()
        rc, out, err = self.cli("status")
        self.assertEqual(rc, 0, err)
        self.assertIn("Composizione: 2 Claude, 2 Codex · fonte: finestre registrate", out)
        self.assertIn("Consiglio: In equilibrio: 2 Claude e 2 Codex.", out)
        self.assertIn("- T: A (Codex) → R (da nominare)", out)
        seen = {"project": str(self.repo), "processes": [{"pid": 7, "client": "codex", "cwd": str(self.repo)}],
                "agents": [], "scan": "observed", "errors": [], "limitations": []}
        with patch("census.collect", return_value=seen):
            rc, out, err = self.cli("census")
        self.assertEqual(rc, 0, err)
        census = json.loads(out)["composition"]
        self.assertEqual((census["case"], census["observed"]), ("balanced", {"claude": 0, "codex": 1}))
        self.assertEqual(self.frozen(), before, "advice records nothing")
        self.store.publish()
        stato = (self.root / "stato.md").read_text()
        self.assertIn("Composizione: 2 Claude, 2 Codex", stato)
        self.assertIn("Consiglio: In equilibrio", stato)
        self.assertEqual(self.store.state()["tasks"]["T"].get("reviewer"), None, "no reader was named by the advice")

    # PT7
    def test_other_commands_keep_their_short_answer(self):
        body = self.base / "note.md"; body.write_text("Synthetic note\n")
        rc, out, err = self.cli("send", "--actor", "C", "--to", "B", "--body-file", str(body), "--info")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("Composizione", out)
        self.assertIn("Composizione", self.cli("status")[1])

    # PT7
    def test_a_one_client_warning_blocks_nothing(self):
        self.store.append("availability", "C", {"agent": "X", "status": "stand-by", "reason": "Window away"})
        self.store.append("availability", "C", {"agent": "A", "status": "stand-by", "reason": "Window away"})
        rc, out, err = self.cli("status")
        self.assertIn("Consiglio: Solo finestre Claude", out)
        rc, out, err = self.cli("task", "--actor", "C", "--id", "N", "--title", "New", "--owner", "B", "--repo",
                                str(self.repo), "--scope-file", str(self.scope))
        self.assertEqual(rc, 0, err)

    # PT7
    def test_js_the_overview_shows_the_advice_in_both_languages(self):
        node = shutil.which("node")
        if not node:
            self.fail("Node is required: the page contract was not measured")
        # Two Claude builders and one Codex reader once A and its alias are away.
        for name in ("A", "R"):
            self.store.append("availability", "C", {"agent": name, "status": "stand-by", "reason": "Window away"})
        self.store.append("agent", "B2", {"id": "B2", "role": "builder", "identity": "window-B2", "client": "claude",
                                          "repo": str(self.repo), "skills": [], "session": ""})
        server, token = make_server(self.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request("http://127.0.0.1:%d/api/state" % server.server_port,
                                             headers={"X-Squadra-Token": token})
            with urllib.request.urlopen(request) as response:
                body = response.read()
        finally:
            server.shutdown()
            server.server_close()
        state = json.loads(body)
        self.assertEqual((state["composition"]["it"]["case"], state["composition"]["en"]["claude"]), ("imbalance", 2))
        fixture = self.base / "state.json"
        fixture.write_bytes(body)
        argv = [node, str(ROOT / "tests/composition_contract.js"), str(ROOT / "assets/dashboard.html"), str(fixture)]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("composition contract passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
