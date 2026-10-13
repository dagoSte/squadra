"""Check decisions, presence and conversations together in one page state."""
import ast
import json
from pathlib import Path
import re
import shutil
import subprocess
import threading
import unittest
import urllib.request

import test_squadra as support
from comms import COMMUNICATION, OTHER_KNOWN
from dashboard_test_support import make_server

ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "assets/dashboard.html").read_text(encoding="utf-8")
TABS = ["Quadro", "Finestre", "Incarichi", "Posta", "Comunicazioni", "Decisioni", "Letture", "Prove", "Output", "Cronologia", "Storico"]
LIGHT = {"standby": "#3d5fc4", "comms-sent": "#5b6878", "comms-queued": "#a65a00",
         "comms-confirmed": "#0b7a55", "comms-overdue": "#c42b3c"}


def blocks():
    return re.findall(r"<script\b([^>]*)>([\s\S]*?)</script>", PAGE)


def function(name):
    lines = PAGE.splitlines()
    found = []
    for i, line in enumerate(lines):
        if not re.match(r"(async )?function " + name + r"\(", line):
            continue
        if line.endswith("{"):
            end = next(j for j in range(i + 1, len(lines)) if lines[j] == "}")
            found.append("\n".join(lines[i:end + 1]))
        else:
            found.append(line)
    return found


class IntegratedPage(unittest.TestCase):
    def test_one_dictionary_one_switch_and_no_branch_fallback(self):
        self.assertEqual(PAGE.count("window.I18N=Object.assign("), 1, "one page-wide dictionary")
        self.assertEqual(PAGE.count("window.tr="), 1, "one translator")
        self.assertNotIn("globalThis", PAGE, "branch fallbacks are removed")
        self.assertIsNone(re.search(r"\b(var|let|const)\s+(I18N|UI_LANG|tr)\b", PAGE), "no second declaration")
        writers = [attributes.strip() for attributes, body in blocks() if re.search(r"UI_LANG\s*=(?!=)", body)]
        self.assertEqual(writers, ['id="squadra-i18n"'], "only the language block sets the language")
        self.assertEqual(PAGE.count('id="lang"'), 1, "one language switch")
        self.assertEqual(len(re.findall(r"^Object\.assign\(I18N, ?\{", PAGE, re.M)), 3, "presence, comms and connected navigation extend the same dictionary")

    def test_light_theme_redeclares_every_root_variable_with_the_validated_values(self):
        light = re.search(r"@media \(prefers-color-scheme: light\)\{:root\{([^}]*)\}", PAGE).group(1)
        dark = "".join(re.findall(r"(?<!\{):root\{([^}]*)\}", PAGE))
        names = lambda text: set(re.findall(r"--([\w-]+):", text))
        self.assertTrue(set(LIGHT) <= names(dark), "comms and stand-by variables are declared on :root")
        self.assertEqual(names(dark) - names(light), set(), "light theme leaves no dark variable behind")
        values = dict(re.findall(r"--([\w-]+):([^;]+)", light))
        self.assertEqual({k: values[k] for k in LIGHT}, LIGHT)
        for rule in (".s-sent,.s-manual,.s-unconfirmed{stroke:var(--comms-sent)", ".s-queued{stroke:var(--comms-queued)",
                     ".s-acked,.s-recorded{stroke:var(--comms-confirmed)}", ".s-overdue{stroke:var(--comms-overdue)",
                     ".s-stand-by{stroke:var(--standby)", ".comms-lane.standby{color:var(--standby)}"):
            self.assertIn(rule, PAGE)

    def test_tab_list_is_exact_and_every_tab_has_both_languages(self):
        lists = re.findall(r"const tabs=\[([^\]]*)\]", PAGE)
        self.assertEqual(len(lists), 1, "one tab list")
        self.assertEqual(re.findall(r"'([^']+)'", lists[0]), TABS)
        for name in TABS:
            entry = re.search(r'"tab\.' + name + r'":\{it:"([^"]+)",en:"([^"]+)"\}', PAGE)
            self.assertIsNotNone(entry, name)
            self.assertEqual(entry.group(1), name)

    def test_one_render_refresh_and_dispatch_with_the_comms_anchor_around_everything(self):
        render, refresh = function("render"), function("refresh")
        self.assertEqual((len(render), len(refresh)), (1, 1), "one render and one refresh")
        render, refresh = render[0], refresh[0]
        self.assertTrue(render.startswith("function render(){if(!state)return;const anchor=commsAnchor();"
                                          "if(typeof renderHeaderExtras==='function')renderHeaderExtras();"),
                        "the place is taken before the overview header, notice, metrics, tabs or content change")
        self.assertTrue(render.endswith("commsRestore(anchor)}"), "the place is restored by the last statement")
        self.assertIn("if(tab==='Comunicazioni')comms(content);else{", render)
        self.assertEqual(len(re.findall(r"\(\{Finestre:team,Incarichi:tasks,Posta:mail,Prove:evidence,Output:outputs,"
                                        r"Cronologia:timeline,Storico:legacyView,Decisioni:decisionsView\}\)\[tab\]\(\)", PAGE)), 1)
        catch = refresh.split("}catch(e){", 1)[1].split("}finally{", 1)[0]
        self.assertLess(catch.index("const anchor=commsAnchor()"), catch.index("$('notice').replaceChildren("))
        self.assertLess(catch.index("$('notice').replaceChildren("), catch.index("commsRestore(anchor)"))
        self.assertIn("scheduleRefresh(loadUI.searchAgain?0:2000)", refresh.split("}finally{", 1)[1])


class EventKinds(unittest.TestCase):
    def test_comms_knows_every_event_kind_the_registry_accepts(self):
        tree = ast.parse((ROOT / "scripts/registry.py").read_text(encoding="utf-8"))
        validate = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "validate")
        call = next(n for n in ast.walk(validate) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "require"
                    and isinstance(n.args[1], ast.Constant) and n.args[1].value == "Unknown event kind")
        accepted = {element.value for element in call.args[0].comparators[0].elts}
        self.assertIn("brief", accepted)
        self.assertIn("availability", accepted)
        self.assertEqual(COMMUNICATION | OTHER_KNOWN, accepted, "a recorded kind is never shown as unrecognised")
        self.assertEqual(COMMUNICATION & OTHER_KNOWN, set())


class IntegratedState(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task
    cli = support.Workflow.cli

    def state(self):
        server, token = make_server(self.store.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        request = urllib.request.Request("http://127.0.0.1:%d/api/state" % server.server_port,
                                         headers={"X-Squadra-Token": token})
        with urllib.request.urlopen(request) as result:
            return json.loads(result.read())

    def test_real_cli_brief_decision_ping_and_stand_by_reach_every_view_in_one_state(self):
        brief = self.base / "brief.md"; brief.write_text("Servizio delle chiavi\nPerché: proteggere le chiavi.\n")
        self.assertEqual(self.cli("brief", "--actor", "C", "--task", "T", "--file", str(brief)), 0)
        self.assertEqual(self.cli("decision", "--actor", "C", "--id", "D-KEYS", "--task", "T", "--reason", "Keys first"), 0)
        self.assertEqual(self.cli("ping", "--actor", "C", "--to", "A"), 0)
        ping = next(m for m in self.store.state()["messages"].values() if m.get("ping") and m["to"] == "A")
        self.assertEqual(self.cli("ack", "--actor", "A", "--message", ping["id"]), 0)
        self.assertEqual(self.cli("standby", "--actor", "C", "--agent", "R", "--reason", "Explicit hold"), 0)
        state = self.state()
        self.assertTrue({"graph", "progress", "presence", "comms", "waits", "attention"} <= state.keys())
        self.assertEqual(state["tasks"]["T"]["brief"]["text"], brief.read_text())
        self.assertIn("decision:D-KEYS", {n["id"] for n in state["graph"]["nodes"]})
        self.assertIn({"from": "task:T", "to": "decision:D-KEYS", "kind": "concerns", "recorded": True}, state["graph"]["edges"])
        self.assertIn("T", state["progress"])
        held = {row["agent"]: row["availability"] for row in state["presence"]}
        self.assertEqual((held["R"], held["A"]), ("stand-by", "active"))
        edges, comms = state["comms"]["edges"], state["comms"]
        self.assertEqual(next(e for e in edges if e["kind"] == "ping" and e.get("message") == ping["id"])["state"], "acked")
        self.assertTrue(any(e["kind"] == "answer" and e.get("message") == ping["id"] for e in edges))
        self.assertTrue(any(e["kind"] == "availability" and e["state"] == "stand-by" for e in edges))
        self.assertEqual((comms.get("unknown_kinds") or {}, comms.get("malformed") or 0), ({}, 0),
                         "every event the presence CLI writes is a known communication or a known non-communication")
        self.assertEqual(comms["as_of"], state["presence"][0]["as_of"], "comms and presence read the same clock")
        assignment = next(e for e in edges if e["kind"] == "message" and e.get("message") == "assign-T")
        self.assertEqual((assignment["task_title"], assignment["task_described"]), ("Servizio delle chiavi", True))
        work = [w for lane in comms["lanes"] for w in lane["work"] if w["task"] == "T"]
        self.assertTrue(work and all(w["title"] == "Servizio delle chiavi" for w in work), "lanes use the same task name")

    def test_comms_facts_name_a_described_task_in_both_languages_javascript(self):
        node = shutil.which("node")
        if not node: self.fail("Node is required: the dashboard JS contract was not measured")
        brief = self.base / "brief.md"; brief.write_text("# Servizio delle chiavi\nPerché: proteggere le chiavi.\n")
        self.assertEqual(self.cli("brief", "--actor", "C", "--task", "T", "--file", str(brief)), 0)
        self.task("U")
        fixture = self.base / "state.json"; fixture.write_text(json.dumps(self.state(), ensure_ascii=False))
        result = subprocess.run([node, str(ROOT / "tests/integration_contract.js"), str(ROOT / "assets/dashboard.html"),
                                 str(fixture)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("integration contract passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
