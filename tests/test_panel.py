"""Check activity charts, bundled styles and dashboard behavior."""
import ast
import contextlib
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from dashboard_test_support import make_server
from panel import FAMILIES, HEARTBEAT, ORDER, STEP, activity
from registry import Store
from squadra import main

ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "assets/dashboard.html").read_text(encoding="utf-8")
VENDOR = ROOT / "assets/vendor"
AT = dt.datetime(2026, 10, 10, 12, 7, 30, tzinfo=dt.timezone.utc)


def event(kind, utc, seq=1):
    return {"kind": kind, "utc": utc if isinstance(utc, str) else utc.isoformat(), "seq": seq, "actor": "C", "data": {}}


def accepted_kinds():
    tree = ast.parse((ROOT / "scripts/registry.py").read_text(encoding="utf-8"))
    validate = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "validate")
    call = next(n for n in ast.walk(validate) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "require"
                and isinstance(n.args[1], ast.Constant) and n.args[1].value == "Unknown event kind")
    return {element.value for element in call.args[0].comparators[0].elts}


class Activity(unittest.TestCase):
    def test_quarter_hour_buckets_on_the_callers_clock(self):
        result = activity([event("message", AT - dt.timedelta(minutes=7)), event("review", AT - dt.timedelta(minutes=8)),
                           event("task", AT - dt.timedelta(days=2))], AT)
        since = dt.datetime.fromisoformat(result["since"])
        self.assertEqual((result["as_of"], result["step"], list(result["families"])), (AT.isoformat(), STEP, list(ORDER)))
        self.assertEqual(since, dt.datetime(2026, 10, 10, 12, 0, tzinfo=dt.timezone.utc) - dt.timedelta(days=15))
        last = (15 * 86400) // STEP
        self.assertEqual(result["buckets"], [[last - 2 * 96, 0, 1, 0, 0], [last - 1, 0, 0, 1, 0], [last, 1, 0, 0, 0]])

    def test_boundaries_future_outside_and_unreadable_times(self):
        since = dt.datetime.fromisoformat(activity([], AT)["since"])
        events = [event("message", since), event("message", since - dt.timedelta(seconds=1)), event("message", AT),
                  event("message", AT + dt.timedelta(seconds=1)), event("message", since + dt.timedelta(seconds=STEP)),
                  event("message", "2026-10-10T12:00:00"), event("message", "not a time"), {"kind": "message"}]
        result = activity(events, AT)
        self.assertEqual(result["buckets"][0], [0, 1, 0, 0, 0], "an event at since is counted, one second earlier is outside")
        self.assertEqual(result["buckets"][1], [1, 1, 0, 0, 0], "a boundary event belongs to the later bucket")
        self.assertEqual(result["buckets"][-1], [(15 * 86400) // STEP, 1, 0, 0, 0], "an event exactly at the clock is counted")
        self.assertEqual((result["future"], result["skipped"]), (1, 3), "later events and unreadable or naive times are apart")
        with self.assertRaises(ValueError):
            activity([], "2026-10-10T12:00:00")

    def test_families_cover_exactly_the_accepted_kinds_and_do_not_overlap(self):
        named = [kinds for kinds in FAMILIES.values()]
        self.assertEqual(sum(len(k) for k in named), len(set().union(*named)), "no kind in two families")
        self.assertEqual(set().union(*named) | {HEARTBEAT}, accepted_kinds())
        self.assertNotIn(HEARTBEAT, set().union(*named))

    def test_heartbeats_are_counted_apart_and_never_drawn(self):
        result = activity([event(HEARTBEAT, AT), event(HEARTBEAT, AT - dt.timedelta(days=30)), event("run.start", AT)], AT)
        self.assertEqual(result["heartbeats"], 1, "only heartbeats inside the window are reported")
        self.assertEqual(result["buckets"], [[(15 * 86400) // STEP, 0, 1, 0, 0]])


class Vendor(unittest.TestCase):
    MANIFEST = (VENDOR / "VENDOR.md").read_text(encoding="utf-8")

    def pinned(self, name):
        match = re.search(r"`" + re.escape(name) + r"` \| ([0-9a-f]{64})", self.MANIFEST)
        self.assertIsNotNone(match, name + " is listed in VENDOR.md")
        return match.group(1)

    def test_page_carries_bootstrap_reboot_verbatim_as_pinned(self):
        block = re.search(r'<style id="squadra-vendor-reboot">([\s\S]*?)</style>', PAGE).group(1)
        original = (VENDOR / "bootstrap-reboot-5.3.8.min.css").read_bytes()
        self.assertEqual(block.encode("utf-8"), original, "the inline Reboot is the vendored file, byte for byte")
        self.assertEqual(hashlib.sha256(original).hexdigest(), self.pinned("bootstrap-reboot-5.3.8.min.css"))
        self.assertTrue(original.startswith(b"/*!\n * Bootstrap Reboot v5.3.8 "))
        self.assertLess(PAGE.index('<style id="squadra-vendor-reboot">'), PAGE.index("<style>"), "Reboot comes first")

    def test_icons_are_the_vendored_bootstrap_icons_path_for_path(self):
        icons = json.loads(re.search(r"const PANEL_ICONS=(\{.*?\});", PAGE).group(1))
        folder = VENDOR / "bootstrap-icons-1.13.1"
        files = sorted(p.stem for p in folder.glob("*.svg"))
        self.assertEqual(sorted(icons), files)
        self.assertEqual(len(files), 40)
        for name in files:
            svg = (folder / (name + ".svg")).read_text(encoding="utf-8")
            paths = [dict(re.findall(r'([a-z-]+)="([^"]*)"', m)) for m in re.findall(r"<path ([^>]*?)/?>", svg)]
            self.assertEqual(icons[name], [[p["d"], p["fill-rule"]] if "fill-rule" in p else [p["d"]] for p in paths], name)
            self.assertEqual(hashlib.sha256((folder / (name + ".svg")).read_bytes()).hexdigest(),
                             self.pinned("bootstrap-icons-1.13.1/" + name + ".svg"))
        used = set(re.findall(r"icon\('([a-z0-9-]+)'", PAGE)) | set(re.findall(r":'([a-z0-9-]+)'", re.search(r"const PANEL_TAB_ICONS=\{[^}]*\}", PAGE).group(0)))
        self.assertLessEqual(used, set(files), "every icon the page names is vendored")

    def test_vendor_manifest_hashes_and_licences(self):
        for name in ("LICENSE-bootstrap.txt", "LICENSE-bootstrap-icons.txt"):
            text = (VENDOR / name).read_text(encoding="utf-8")
            self.assertTrue(text.startswith("The MIT License (MIT)"), name)
            self.assertEqual(hashlib.sha256((VENDOR / name).read_bytes()).hexdigest(), self.pinned(name))
        listed = re.findall(r"`([^`]+)` \| ([0-9a-f]{64})", self.MANIFEST)
        on_disk = sorted(str(p.relative_to(VENDOR)) for p in VENDOR.rglob("*") if p.is_file() and p.name != "VENDOR.md")
        self.assertEqual(sorted(name for name, _ in listed), on_disk, "every vendored file is listed, and only those")
        for package, version in (("bootstrap", "5.3.8"), ("bootstrap-icons", "1.13.1")):
            self.assertIn(f"https://registry.npmjs.org/{package}/-/{package}-{version}.tgz", self.MANIFEST)

    def test_no_bootstrap_javascript_and_no_runtime_asset(self):
        self.assertEqual(list(ROOT.rglob("bootstrap*.js")), [])
        self.assertIsNone(re.search(r"<script[^>]+src=|<link\b|@import|url\(\s*['\"]?(https?:|//)", PAGE))
        self.assertEqual(set(re.findall(r"https?://[^\s\"'`)]+", PAGE.replace(re.search(r'<style id="squadra-vendor-reboot">[\s\S]*?</style>', PAGE).group(0), ""))),
                         {"http://www.w3.org/2000/svg", "https://github.com/dagoSte/squadra"},
                         "only the SVG namespace and the footer hyperlink, with no runtime asset URL")


def blocks(css):
    out = {}
    out["dark"] = dict(re.findall(r"([\w-]+):([^;]+)", re.match(r"\s*:root\{([^}]*)\}", css[css.index(":root{color-scheme:dark;"):]).group(1)))
    out["dark"].update(re.findall(r"([\w-]+):([^;]+)", re.search(r"\n:root\{(--standby:[^}]*)\}", css).group(1)))
    out["light"] = dict(re.findall(r"([\w-]+):([^;]+)", re.search(r"@media \(prefers-color-scheme: light\)\{:root\{([^}]*)\}", css).group(1)))
    for name in ("light", "dark"):
        out["twin-" + name] = dict(re.findall(r"([\w-]+):([^;]+)", re.search(r":root\[data-theme=" + name + r"\]\{([^}]*)\}", css).group(1)))
    return out


def resolve(tokens, value):
    for _ in range(5):
        value = re.sub(r"var\(--([\w-]+)\)", lambda m: tokens["--" + m.group(1)], value)
    return value.strip()


def wcag(a, b):
    def lum(h):
        r, g, b_ = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b_)
    high, low = sorted([lum(a), lum(b)], reverse=True)
    return (high + 0.05) / (low + 0.05)


def apca(text, background):
    def y(h):
        r, g, b = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        value = 0.2126729 * r ** 2.4 + 0.7151522 * g ** 2.4 + 0.0721750 * b ** 2.4
        return value + (0.022 - value) ** 1.414 if value < 0.022 else value
    yt, yb = y(text), y(background)
    if yb > yt:
        s = (yb ** 0.56 - yt ** 0.57) * 1.14
        return 0.0 if s < 0.1 else (s - 0.027) * 100
    s = (yb ** 0.65 - yt ** 0.62) * 1.14
    return 0.0 if s > -0.1 else -(s + 0.027) * 100


class Tokens(unittest.TestCase):
    CSS = "\n".join(re.findall(r"<style(?: id=\"squadra-(?:page|panel)-style\")?>([\s\S]*?)</style>", PAGE))
    TEXT = [("--text", s) for s in ("--bg", "--panel", "--sq-surface-2", "--sq-sidebar", "--sq-chip", "--sq-accent-subtle")] + \
           [("--muted", s) for s in ("--bg", "--panel", "--sq-surface-2", "--sq-sidebar", "--sq-chip")] + \
           [(f, s) for f in ("--sq-accent", "--green", "--orange", "--red", "--standby") for s in ("--bg", "--panel", "--sq-surface-2")] + \
           [("--sq-accent", "--sq-accent-subtle"), ("--green", "--sq-good-subtle"), ("--orange", "--sq-warn-subtle"),
            ("--red", "--sq-bad-subtle"), ("--orange", "--sq-notice-bg"), ("--sq-on-accent", "--sq-accent-solid"), ("--bg", "--text")]
    MARKS = [(f, s) for f in ("--sq-border-strong", "--sq-accent", "--sq-chart-good", "--sq-chart-bad", "--comms-sent", "--comms-queued",
                              "--comms-confirmed", "--comms-overdue") for s in ("--bg", "--panel", "--sq-surface-2")]

    def test_four_theme_blocks_declare_the_same_tokens_and_twins_match(self):
        found = blocks(self.CSS)
        self.assertEqual(set(found["dark"]), set(found["light"]))
        self.assertEqual(found["twin-light"], found["light"], "a manual light choice uses exactly the system light values")
        self.assertEqual(found["twin-dark"], found["dark"], "a manual dark choice uses exactly the default dark values")
        self.assertEqual(found["light"]["color-scheme"], "light")

    def test_text_pairs_meet_wcag_aa_and_apca_in_both_themes(self):
        for theme, tokens in blocks(self.CSS).items():
            colour = lambda name: resolve(tokens, tokens[name])[:7]
            for fg, bg in self.TEXT:
                with self.subTest(theme=theme, pair=(fg, bg)):
                    self.assertGreaterEqual(wcag(colour(fg), colour(bg)), 4.5)
                    self.assertGreaterEqual(abs(apca(colour(fg), colour(bg))), 60)
            for fg, bg in self.MARKS:
                with self.subTest(theme=theme, mark=(fg, bg)):
                    self.assertGreaterEqual(wcag(colour(fg), colour(bg)), 3)

    def test_components_use_tokens_not_literal_colours(self):
        css = re.sub(r":root(\[data-theme=(light|dark)\])?\{[^}]*\}|html\{--sq-font[^}]*\}", "", self.CSS)
        literals = [(selector.strip()[-60:], value) for selector, body in re.findall(r"([^{}]*)\{([^{}]*)\}", css)
                    for value in re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", body)]
        self.assertEqual(literals, [])


class PanelJavaScript(unittest.TestCase):
    """Run all page scripts in Node; Node is required."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="squadra-panel-")
        cls.base = Path(cls.temp.name).resolve()
        cls.fixture = cls.base / "state.json"
        cls.fixture.write_text(json.dumps(cls.build(cls.base), ensure_ascii=False), encoding="utf-8")
        cls.cards = cls.base / "cards.json"
        cls.cards.write_text(json.dumps(cls.build_cards(cls.base / "cards"), ensure_ascii=False), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @staticmethod
    def build(base):
        repo, root, files, home = base / "repo", base / "record", base / "files", base / "home"
        for folder in (repo, files, home):
            folder.mkdir()
        env = os.environ | {"HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1"}
        for args in (["init", "-q"], ["config", "user.name", "Synthetic"], ["config", "user.email", "test@example.invalid"]):
            subprocess.run(["git", *args], cwd=repo, env=env, check=True)
        (repo / "code.py").write_text("VALUE = 1\n")
        subprocess.run(["git", "add", "."], cwd=repo, env=env, check=True)
        subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-qm", "baseline"], cwd=repo, env=env, check=True)

        def write(name, text):
            (files / name).write_text(text, encoding="utf-8")
            return str(files / name)

        def cli(*args, ok=True):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = main(["--root", str(root), *args])
            assert (rc == 0) == ok, (args, rc)

        cli("init", "--name", "Panel test", "--project", str(repo), "--temporary")
        for seat, identity, role, client in [("coord-zx", "window-coord", "coordinator", "claude"), ("build-qv", "window-qv", "builder", "codex"),
                                             ("build-ht", "window-ht", "builder", "claude"), ("read-wy", "window-wy", "reviewer", "claude")]:
            cli("agent", "--id", seat, "--identity", identity, "--role", role, "--client", client, "--repo", str(repo))
        scope = write("scope.md", "Synthetic scope.\n")
        task = ["task", "--actor", "coord-zx", "--repo", str(repo), "--scope-file", scope]
        cli(*task, "--id", "TASK-KQ1", "--owner", "build-qv", "--reviewer", "read-wy", "--title", "Key service")
        cli(*task, "--id", "TASK-KQ2", "--owner", "build-ht", "--reviewer", "read-wy", "--title", "<img src=x onerror=alert(1)> page")
        cli(*task, "--id", "TASK-KQ3", "--owner", "build-qv", "--depends", "TASK-KQ2", "--title", "Guide")
        cli("brief", "--actor", "coord-zx", "--task", "TASK-KQ1", "--file", write("brief.md", "Servizio delle chiavi\nPerché: proteggere le chiavi.\n"))
        for seat, name in (("build-qv", "TASK-KQ1"), ("build-ht", "TASK-KQ2"), ("build-qv", "TASK-KQ3")):
            cli("ack", "--actor", seat, "--message", "assign-" + name)
        cli("plan", "--actor", "build-qv", "--task", "TASK-KQ1", "--file", write("plan-1.md", "First plan.\n"))
        cli("review", "--actor", "read-wy", "--task", "TASK-KQ1", "--stage", "plan", "--verdict", "changes-required", "--report", write("read-a.md", "Same words.\n"))
        cli("plan", "--actor", "build-qv", "--task", "TASK-KQ1", "--file", write("plan-2.md", "Second plan.\n"))
        cli("review", "--actor", "read-wy", "--task", "TASK-KQ1", "--stage", "plan", "--verdict", "accepted", "--report", write("read-b.md", "Same words.\n"))
        cli("start", "--actor", "build-qv", "--task", "TASK-KQ1")
        cli("prove", "--actor", "build-qv", "--task", "TASK-KQ1", "--", sys.executable, "-c", "import sys; sys.exit(3)", ok=False)
        cli("prove", "--actor", "build-qv", "--task", "TASK-KQ1", "--", sys.executable, "-c", "pass")
        cli("send", "--actor", "coord-zx", "--to", "build-qv", "--id", "MSG-ZZ1", "--task", "TASK-KQ1",
            "--body-file", write("hostile.md", "<script>alert(1)</script> hello\nsecond line"))
        cli("ack", "--actor", "build-qv", "--message", "MSG-ZZ1")
        for i in range(110):
            cli("send", "--actor", "coord-zx", "--to", "build-ht", "--id", f"MSG-BULK-{i:03d}", "--body-file", write("bulk.md", f"Bulk message {i}\n"))
        server, token = make_server(root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request("http://127.0.0.1:%d/api/state" % server.server_port, headers={"X-Squadra-Token": token})
            with urllib.request.urlopen(request) as result:
                return json.loads(result.read())
        finally:
            server.shutdown()
            server.server_close()

    @staticmethod
    def build_cards(base):
        """One builder window with several tasks, a long title, an old next step and a later decision."""
        repo, root, files, home = base / "repo", base / "record", base / "files", base / "home"
        for folder in (repo, files, home):
            folder.mkdir(parents=True)
        env = os.environ | {"HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1"}
        for args in (["init", "-q"], ["config", "user.name", "Synthetic"], ["config", "user.email", "test@example.invalid"]):
            subprocess.run(["git", *args], cwd=repo, env=env, check=True)
        (repo / "code.py").write_text("VALUE = 1\n")
        subprocess.run(["git", "add", "."], cwd=repo, env=env, check=True)
        subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-qm", "baseline"], cwd=repo, env=env, check=True)

        def write(name, text):
            (files / name).write_text(text, encoding="utf-8")
            return str(files / name)

        def cli(*args):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = main(["--root", str(root), *args])
            assert rc == 0, (args, rc)
            time.sleep(0.01)

        cli("init", "--name", "Cards test", "--project", str(repo), "--temporary")
        for seat, identity, role, client in [("coord-cq", "window-cq", "coordinator", "claude"), ("build-bx", "window-bx", "builder", "claude"),
                                             ("build-by", "window-by", "builder", "claude"), ("read-rx", "window-rx", "reviewer", "claude"),
                                             ("read-ry", "window-ry", "reviewer", "codex")]:
            cli("agent", "--id", seat, "--identity", identity, "--role", role, "--client", client, "--repo", str(repo))
        scope = write("scope.md", "Synthetic scope.\n")
        task = ["task", "--actor", "coord-cq", "--repo", str(repo), "--scope-file", scope]
        cli(*task, "--id", "TASK-LONG", "--owner", "build-bx", "--reviewer", "read-rx", "--title",
            "Unione dei tre registri con la verifica delle prove, la lettura indipendente e il rilascio sul server condiviso della squadra")
        cli(*task, "--id", "TASK-OTHER", "--owner", "build-bx", "--title", "Grafo delle scelte")
        cli(*task, "--id", "TASK-THIRD", "--owner", "build-by", "--reviewer", "read-ry", "--title", "Guida per chi arriva")
        for seat, name in (("build-bx", "TASK-LONG"), ("build-bx", "TASK-OTHER"), ("build-by", "TASK-THIRD")):
            cli("ack", "--actor", seat, "--message", "assign-" + name)
        cli("output", "--actor", "build-bx", "--task", "TASK-LONG", "--file", write("long.md", "Round closed with the budget used up.\n"),
            "--next", "Run make gate again on the frozen head and send the receipt to coord-cq before any release")
        cli("decision", "--actor", "coord-cq", "--task", "TASK-LONG", "--reason", "Il residuo passa a un altro incarico; questo resta come registrato.")
        cli("output", "--actor", "build-bx", "--task", "TASK-OTHER", "--file", write("other.md", "Plan written.\n"),
            "--next", "Consegno la versione da leggere appena la prova vincolata è verde, poi aspetto la lettura del revisore indipendente")
        cli("output", "--actor", "build-by", "--task", "TASK-THIRD", "--file", write("third.md", "Draft ready.\n"),
            "--next", "Wait for the old reviewer to answer")
        cli("brief", "--actor", "coord-cq", "--task", "TASK-THIRD", "--file", write("third-brief.md", "Guida per chi arriva\nPerché: spiegare la squadra in una pagina.\n"))
        server, token = make_server(root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request("http://127.0.0.1:%d/api/state" % server.server_port, headers={"X-Squadra-Token": token})
            with urllib.request.urlopen(request) as result:
                return json.loads(result.read())
        finally:
            server.shutdown()
            server.server_close()

    def contract(self, scenario, env=None, cases=None, fixture=None):
        node = shutil.which("node")
        if not node:
            self.fail("Node is required: the panel JS contract was not measured")
        argv = [node, str(ROOT / "tests/panel_contract.js"), str(ROOT / "assets/dashboard.html"), str(fixture or self.fixture), scenario]
        if cases:
            argv.append(str(cases))
        result = subprocess.run(argv, capture_output=True, text=True, timeout=120, env=os.environ | (env or {}))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("panel contract passed: " + scenario, result.stdout)

    def test_js_connected_native_no_task_filter_and_disappearing_origin_focus(self):
        self.contract("connected_edges")

    def test_js_connected_task_ids_reviews_return_and_refresh(self):
        self.contract("connected")

    def test_js_connected_window_identity_aliases_and_bilingual_labels(self):
        self.contract("connected_agents")

    def test_js_panel_shell_sidebar_drawer_theme_and_icons(self):
        self.contract("shell")

    def test_js_panel_charts_from_a_real_server_state(self):
        self.contract("charts")

    def test_js_panel_activity_local_days_and_hours_across_time_zones(self):
        utc = lambda text: dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        cases = []
        for name, zones, at, events, checks, flags in [
            ("half-hour midnight", ["Asia/Kolkata"], "2026-10-10T06:00:00Z", ["2026-10-09T18:10:00Z", "2026-10-09T18:40:00Z"],
             [{"days": [["2026-10-09", 1], ["2026-10-10", 1]]}], {}),
            ("quarter-hour midnight", ["Asia/Kathmandu"], "2026-10-10T06:00:00Z",
             ["2026-10-09T18:05:00Z", "2026-10-09T18:15:00Z", "2026-10-09T18:20:00Z", "2026-10-10T06:00:00Z", "2026-10-10T06:00:01Z"],
             [{"days": [["2026-10-09", 1], ["2026-10-10", 3]]}], {"future": 1}),
            ("repeated autumn hour", ["Europe/Rome"], "2026-10-25T05:00:00Z", ["2026-10-25T00:30:00Z", "2026-10-25T01:30:00Z"],
             [{"labels": ["02:00 (UTC+2)", "02:00 (UTC+1)"]}, {"dayHours": ["2026-10-25", 25]}, {"days": [["2026-10-25", 2]]}], {}),
            ("skipped spring hour", ["Europe/Rome"], "2026-03-29T05:00:00Z", ["2026-03-29T00:30:00Z", "2026-03-29T01:30:00Z"],
             [{"noLabel": "02:"}, {"dayHours": ["2026-03-29", 23]}, {"labels": ["01:00", "03:00"]}], {}),
            ("historical offset", ["Africa/Monrovia"], "1971-06-02T12:00:00Z", ["1971-06-01T23:50:00Z", "1971-06-02T00:20:00Z"],
             [{"days": [["1971-06-01", 1], ["1971-06-02", 1]]}], {"utc": True}),
            # Read 02:30 in the second repeated hour, with an event at its 02:15.
            ("second repeated hour, reader's payload", ["Europe/Rome"], "2026-10-25T01:30:00Z", ["2026-10-25T01:15:00Z"],
             [{"hours": [["02:00 (UTC+1)", 1], ["02:00 (UTC+2)", 0]]}, {"days": [["2026-10-25", 1]]}],
             {"total24": 1, "payload": {"as_of": "2026-10-25T01:30:00Z", "since": "2026-10-10T01:30:00Z", "step": 900,
                                         "buckets": [[1439, 1, 0, 0, 0]], "heartbeats": 0, "future": 0, "skipped": 0}}),
            ("before both repeated hours", ["Europe/Rome"], "2026-10-24T23:30:00Z", ["2026-10-24T22:40:00Z", "2026-10-24T23:10:00Z"],
             [{"hours": [["00:00", 1], ["01:00", 1]]}], {"total24": 2}),
            ("inside the first repeated hour", ["Europe/Rome"], "2026-10-25T00:30:00Z", ["2026-10-24T23:50:00Z", "2026-10-25T00:15:00Z"],
             [{"hours": [["01:00", 1], ["02:00", 1]]}], {"total24": 2}),
            ("inside the second repeated hour", ["Europe/Rome"], "2026-10-25T01:45:00Z",
             ["2026-10-25T00:15:00Z", "2026-10-25T00:45:00Z", "2026-10-25T01:15:00Z", "2026-10-25T01:40:00Z"],
             [{"hours": [["02:00 (UTC+2)", 2], ["02:00 (UTC+1)", 2]]}], {"total24": 4}),
            ("after both repeated hours", ["Europe/Rome"], "2026-10-25T02:30:00Z",
             ["2026-10-25T00:15:00Z", "2026-10-25T01:15:00Z", "2026-10-25T02:10:00Z"],
             [{"hours": [["02:00 (UTC+2)", 1], ["02:00 (UTC+1)", 1], ["03:00", 1]]}, {"days": [["2026-10-25", 3]]}], {"total24": 3}),
            ("ordinary day", ["Europe/Rome"], "2026-10-20T10:30:00Z", ["2026-10-20T09:59:00Z", "2026-10-20T10:15:00Z", "2026-10-19T10:59:00Z"],
             [{"hours": [["11:00", 1], ["12:00", 1]]}], {"total24": 2}),
            ("half-hour shift, repeated half hour", ["Australia/Lord_Howe"], "2026-04-04T15:20:00Z",
             ["2026-04-04T14:45:00Z", "2026-04-04T15:10:00Z", "2026-04-04T13:50:00Z"], [{"days": [["2026-04-05", 3]]}], {"total24": 3, "elapsed": True}),
            ("half-hour shift, skipped half hour", ["Australia/Lord_Howe"], "2026-10-03T16:10:00Z",
             ["2026-10-03T15:20:00Z", "2026-10-03T15:40:00Z", "2026-10-03T16:05:00Z"], [{"days": [["2026-10-04", 3]]}], {"total24": 3, "elapsed": True}),
        ]:
            payload = flags.pop("payload", None) or activity([{"kind": "message", "utc": e.replace("Z", "+00:00")} for e in events], utc(at))
            cases.append({"name": name, "zones": zones, "events": events, "activity": payload, "checks": checks, **flags})
        path = self.base / "time-cases.json"
        path.write_text(json.dumps(cases), encoding="utf-8")
        for zone in ("Asia/Kolkata", "Asia/Kathmandu", "Europe/Rome", "Africa/Monrovia", "Australia/Lord_Howe"):
            with self.subTest(zone=zone):
                self.contract("timezones", env={"TZ": zone}, cases=path)

    def test_js_panel_focus_survives_refresh_insert_language_theme_and_falls_back(self):
        self.contract("focus")

    def test_js_panel_same_bytes_links_keep_their_own_focus_and_fall_back_to_the_table_title(self):
        self.contract("links")

    def test_js_panel_tooltip_follows_its_mark_and_is_dismissed(self):
        self.contract("tooltip")

    def test_js_panel_tables_sort_filter_count_and_identifiers_only_in_details(self):
        self.contract("tables")

    def test_js_panel_landing_status_kpis_task_cards_and_no_empty_fields(self):
        self.contract("landing")

    def test_js_panel_task_story_in_time_order_with_ids_behind_details_and_focus_return(self):
        self.contract("story")

    def test_js_panel_story_details_stay_open_by_task_and_event_across_rebuilds(self):
        self.contract("storyDetails")

    def test_js_panel_task_cards_name_this_task_date_declared_steps_and_hold_two_lines(self):
        self.contract("cards", fixture=self.cards)


class ActivityServer(unittest.TestCase):
    def test_api_state_counts_the_full_log_on_the_shared_clock(self):
        with tempfile.TemporaryDirectory(prefix="squadra-activity-") as temp:
            store = Store(Path(temp) / "record", create=True)
            store.append("init", "system", {"name": "Activity", "project": temp, "temporary": True, "limits": {"plan": 3, "code": 2, "build": 3}})
            store.append("agent", "C", {"id": "C", "role": "coordinator", "identity": "c", "client": "manual", "repo": temp, "skills": [], "session": ""})
            store.append("agent", "A", {"id": "A", "role": "builder", "identity": "a", "client": "codex", "repo": temp, "skills": [], "session": ""})
            for i in range(150):
                store.append("message", "C", {"id": f"M{i}", "to": "A", "body": "x"})
            store.close()
            server, token = make_server(Path(temp) / "record")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = urllib.request.Request("http://127.0.0.1:%d/api/state" % server.server_port, headers={"X-Squadra-Token": token})
                with urllib.request.urlopen(request) as result:
                    state = json.loads(result.read())
            finally:
                server.shutdown()
                server.server_close()
        counted = sum(sum(b[1:]) for b in state["activity"]["buckets"])
        self.assertEqual(len(state["events"]), 120, "the page receives only the latest 120 events")
        self.assertEqual(counted, 153, "activity counts every event of the verified log")
        self.assertEqual(state["activity"]["as_of"], state["comms"]["as_of"], "one clock for activity and communications")


if __name__ == "__main__":
    unittest.main()
