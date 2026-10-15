"""Declared quick checks run on the exact candidate before proof and delivery."""
import contextlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import test_squadra as support
import precheck
import runtime
from registry import Error, Store, digest, snapshot
from runtime import execute
from squadra import main

ROOT = Path(__file__).resolve().parents[1]
NAME = precheck.NAME


def py(code):
    return [sys.executable, "-c", code]


def check(name, argv, timeout=5):
    return {"name": name, "argv": argv, "timeout_seconds": timeout}


class Precheck(unittest.TestCase):
    git = support.Workflow.git
    ack = support.Workflow.ack
    task = support.Workflow.task

    def setUp(self):
        support.Workflow.setUp(self)
        self.ack()
        self.marker = self.base / "proof-ran"

    def declare(self, checks, total=10, body=None):
        (self.repo / NAME).write_bytes(body if body is not None else
                                       json.dumps({"schema": 1, "total_seconds": total, "checks": checks}).encode())
        self.git("add", NAME)
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "declare checks")

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(["--root", str(self.root), *args])
        return rc, out.getvalue(), err.getvalue()

    def prove(self, task="T", argv=None):
        return self.cli("prove", "--actor", "A", "--task", task, "--", *(argv or py("print('proof')")))

    def marking(self):
        return py("open(" + repr(str(self.marker)) + ", 'w').write('ran')")

    @staticmethod
    def output(text):
        return json.JSONDecoder().raw_decode(text)[0]

    def runs(self, mode):
        return [r for r in self.store.state()["runs"].values() if r["mode"] == mode]

    def starts(self, mode):
        return [e for e in self.store.events() if e["kind"] == "run.start" and e["data"]["mode"] == mode]

    def reviewed_task(self, name="V"):
        data = {"id": name, "title": "Synthetic reviewed change", "owner": "A", "reviewer": "R",
                "repo": str(self.repo), "base": snapshot(self.repo)["head"], "scope": self.store.artifact(self.scope),
                "proof": None, "expected": None, "depends": [], "full_gate": False}
        self.store.batch([("task", "C", data, None), ("message", "C", {"id": "assign-" + name, "to": "A",
                          "task": name, "body": "Scoped work"}, None)])
        self.ack(name)

    def get(self, name="T"):
        return self.store.state()["tasks"][name]

    # PC1
    def test_without_declaration_prove_and_deliver_behave_as_before(self):
        with patch("precheck.git", side_effect=AssertionError("no git call")), \
             patch("precheck.snapshot", side_effect=AssertionError("no snapshot")), \
             patch("runtime.execute", wraps=runtime.execute) as spy:
            rc, out, err = self.prove()
            self.assertEqual(rc, 0, err)
            self.assertNotIn("precheck", self.output(out))
            self.assertIsNone(spy.call_args.kwargs["checked"])
            rc, out, err = self.cli("deliver", "--actor", "A", "--task", "T")
            self.assertEqual(rc, 0, err)
            self.assertNotIn("precheck", self.output(out))
        self.assertEqual(self.runs("precheck"), [])

    # PC2
    def test_passing_checks_are_recorded_like_probes_before_the_proof(self):
        self.declare([check("first", py("print('one')")), check("second", py("import sys; print('two', file=sys.stderr)"))])
        rc, out, err = self.prove()
        self.assertEqual(rc, 0, err)
        summary = self.output(out)["precheck"]
        self.assertEqual([(c["name"], c["status"], c["native_rc"]) for c in summary["checks"]],
                         [("first", "passed", 0), ("second", "passed", 0)])
        self.assertEqual(summary["declaration"], digest((self.repo / NAME).read_bytes()))
        self.assertGreater(summary["seconds"], 0)
        prechecks, proof = self.runs("precheck"), self.runs("proof")[0]
        self.assertEqual(len(prechecks), 2)
        for run in prechecks:
            self.assertEqual(run["status"], "passed")
            self.assertEqual(run["native_rc"], 0)
            self.assertIsInstance(run["seconds"], float)
            self.assertEqual(run["argv"][0], sys.executable)
            self.assertEqual(run["before"], run["after"])
            self.assertEqual(run["before"]["sha256"], proof["before"]["sha256"])
            self.assertEqual([a["name"] for a in run["artifacts"]], ["stdout.log", "stderr.log"])
        second = next(r for r in prechecks if r["check"] == "second")
        self.assertIn(b"two", (self.root / "artifacts" / second["artifacts"][1]["sha256"]).read_bytes())
        self.assertLess(max(e["seq"] for e in self.starts("precheck")), self.starts("proof")[0]["seq"])
        self.assertEqual(self.get()["proofs"][0]["id"], proof["id"])

    def test_deliver_runs_the_checks_before_the_delivery_and_its_handoff(self):
        self.reviewed_task()
        self.declare([check("only", py("print('ok')"))])
        self.assertEqual(self.prove("V")[0], 0)
        rc, out, err = self.cli("deliver", "--actor", "A", "--task", "V")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.output(out)["precheck"]["checks"][0]["status"], "passed")
        events = self.store.events()
        delivered = next(e for e in events if e["kind"] == "deliver")
        handoff = next(e for e in events if e["kind"] == "message" and e["data"]["id"].startswith("review-"))
        latest = max(e["seq"] for e in self.starts("precheck"))
        ended = max(e["seq"] for e in events if e["kind"] == "run.end" and e["data"]["id"] in
                    {r["id"] for r in self.runs("precheck")})
        self.assertLess(latest, delivered["seq"]); self.assertLess(ended, delivered["seq"])
        self.assertLess(delivered["seq"], handoff["seq"])
        self.assertEqual(self.runs("precheck")[-1]["before"]["sha256"], delivered["data"]["snapshot"]["sha256"])

    def test_a_failing_check_refuses_prove_by_name_and_starts_no_proof(self):
        self.declare([check("good", py("print('fine')")),
                      check("bad", py("import sys; print('bridge differs', file=sys.stderr); sys.exit(3)")),
                      check("after", py("print('still runs')"))])
        rc, out, err = self.prove(argv=self.marking())
        self.assertEqual(rc, 1)
        self.assertRegex(err, r"precheck_failed: bad \(failed, rc 3, run [0-9a-f]{32}: bridge differs\)")
        self.assertNotIn("good (", err)
        self.assertEqual({r["check"]: r["status"] for r in self.runs("precheck")},
                         {"good": "passed", "bad": "failed", "after": "passed"})
        self.assertEqual(self.starts("proof"), [])
        self.assertFalse(self.marker.exists())
        diagnostic = [e for e in self.store.events() if e["kind"] == "diagnostic"][-1]["data"]
        self.assertEqual(diagnostic["command"], "prove"); self.assertIn("precheck_failed: bad", diagnostic["error"])

    def test_timed_out_unrunnable_and_source_writing_checks_are_named(self):
        cases = {
            "slow": ([check("slow", py("import time; time.sleep(30)"), 1)], r"slow \(timeout, rc -?\d+"),
            "missing": ([check("missing", [str(self.base / "no-such-program")])], r"missing \(failed, rc None, run \w+: unrunnable"),
            "writer": ([check("writer", py("open('new-file.txt', 'w').write('x')")), check("later", py("print(1)"))],
                       r"writer \(failed, rc 0, run \w+: source changed during the check\); later \(interrupted"),
        }
        for label, (checks, pattern) in cases.items():
            with self.subTest(label):
                self.declare(checks)
                started = time.monotonic()
                rc, out, err = self.prove(argv=self.marking())
                self.assertEqual(rc, 1)
                self.assertRegex(err, "precheck_failed: " + pattern)
                self.assertLess(time.monotonic() - started, 8)
                self.assertEqual(self.starts("proof"), [])
                self.assertFalse(self.marker.exists())
                self.assertFalse(any(r["status"] == "running" for r in self.runs("precheck")))
                (self.repo / "new-file.txt").unlink(missing_ok=True)

    def test_deliver_refuses_a_failing_check_without_delivery_or_handoff(self):
        self.reviewed_task()
        flag = self.base / "flag"
        self.declare([check("flagged", py("import os, sys; sys.exit(4 if os.path.exists(" + repr(str(flag)) + ") else 0)"))])
        self.assertEqual(self.prove("V")[0], 0)
        flag.write_text("x")
        status = self.get("V")["status"]
        rc, out, err = self.cli("deliver", "--actor", "A", "--task", "V")
        self.assertEqual(rc, 1)
        self.assertIn("precheck_failed: flagged (failed, rc 4", err)
        self.assertFalse(any(e["kind"] == "deliver" for e in self.store.events()))
        self.assertFalse(any(m.startswith("review-") for m in self.store.state()["messages"]))
        self.assertEqual(self.get("V")["status"], status)

    def test_proof_refuses_bytes_changed_after_the_checks(self):
        self.declare([check("only", py("print('ok')"))])
        real = precheck.run

        def checked_then_changed(*args, **kwargs):
            result = real(*args, **kwargs)
            (self.repo / "code.py").write_text("VALUE = 9\n")
            return result

        with patch("precheck.run", checked_then_changed):
            rc, out, err = self.prove(argv=self.marking())
        self.assertEqual(rc, 1)
        self.assertIn("precheck_failed: source changed after the checks", err)
        self.assertEqual(self.starts("proof"), [])
        self.assertFalse(self.marker.exists())
        self.assertEqual([r["status"] for r in self.runs("precheck")], ["passed"])
        (self.repo / "code.py").write_text("VALUE = 1\n")
        rc, out, err = self.prove(argv=self.marking())
        self.assertEqual(rc, 0, err)
        self.assertTrue(self.marker.exists())

    def test_delivery_refuses_source_changed_after_the_checks(self):
        self.reviewed_task()
        self.declare([check("only", py("print('ok')"))])
        self.assertEqual(self.prove("V")[0], 0)
        real = precheck.run

        def checked_then_changed(*args, **kwargs):
            result = real(*args, **kwargs)
            (self.repo / "code.py").write_text("VALUE = 2\n")
            return result

        with patch("precheck.run", checked_then_changed):
            rc, out, err = self.cli("deliver", "--actor", "A", "--task", "V")
        self.assertEqual(rc, 1)
        self.assertIn("precheck_failed: source changed after the checks", err)
        self.assertFalse(any(e["kind"] == "deliver" for e in self.store.events()))
        self.assertFalse(any(m.startswith("review-") for m in self.store.state()["messages"]))
        (self.repo / "code.py").write_text("VALUE = 1\n")
        rc, out, err = self.cli("deliver", "--actor", "A", "--task", "V")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.get("V")["delivery"]["snapshot"], snapshot(self.repo, self.root))
        self.assertTrue(any(m.startswith("review-") for m in self.store.state()["messages"]))

    def test_setup_delay_past_the_deadline_starts_no_process(self):
        marker = self.base / "check-started"
        command = py("open(" + repr(str(marker)) + ", 'w').write('ran')")
        self.declare([check("only", command, 1)], total=1)
        real_mkdir, real_popen, spawned = Path.mkdir, precheck.subprocess.Popen, []

        def slow_setup(path, *args, **kwargs):
            if path.name == "tmp" and path.parent.parent == self.root / "runs":
                time.sleep(1.5)
            return real_mkdir(path, *args, **kwargs)

        def popen(argv, *args, **kwargs):
            if argv == command:
                spawned.append(argv)
            return real_popen(argv, *args, **kwargs)

        with patch.object(Path, "mkdir", slow_setup), patch("precheck.subprocess.Popen", popen):
            with self.assertRaisesRegex(Error, r"precheck_failed: only \(interrupted, rc None, run \w+: deadline reached before start"):
                precheck.run(self.store, "A", self.get())
        self.assertEqual(spawned, [])
        self.assertFalse(marker.exists())
        self.assertEqual([r["status"] for r in self.runs("precheck")], ["interrupted"])
        with patch("precheck.subprocess.Popen", popen):
            checked, summary = precheck.run(self.store, "A", self.get())
        self.assertEqual(summary["checks"][0]["status"], "passed")
        self.assertEqual(spawned, [command])
        self.assertTrue(marker.exists())

    def test_execute_refuses_a_stale_checked_snapshot_before_any_record(self):
        stale = snapshot(self.repo, self.root)
        (self.repo / "code.py").write_text("VALUE = 2\n")
        count = len(self.store.events())
        folders = sorted((self.root / "runs").iterdir()) if (self.root / "runs").exists() else []
        with self.assertRaisesRegex(Error, r"precheck_failed: source changed after the checks \(checked [0-9a-f]{12}, now [0-9a-f]{12}\)"):
            execute(self.store, "A", "T", argv=self.marking(), checked=stale)
        self.assertEqual(len(self.store.events()), count)
        self.assertEqual(sorted((self.root / "runs").iterdir()) if (self.root / "runs").exists() else [], folders)
        self.assertFalse(self.marker.exists())
        result = execute(self.store, "A", "T", argv=self.marking(), checked=snapshot(self.repo, self.root))
        self.assertEqual(result["status"], "passed")

    def test_delivery_snapshot_must_be_the_checked_one(self):
        self.declare([check("only", py("print('ok')"))])
        stale = snapshot(self.repo, self.root)
        (self.repo / "code.py").write_text("VALUE = 2\n")
        with self.assertRaisesRegex(Error, "precheck_failed: source changed before the checks"):
            precheck.run(self.store, "A", self.get(), current=stale)
        self.assertEqual(self.runs("precheck"), [])

    def test_internal_failures_end_every_run_without_a_pass(self):
        self.declare([check("one", py("print(1)")), check("two", py("print(2)"))])
        real, calls = precheck.snapshot, []

        def failing_after_first(*args):
            calls.append(1)
            if len(calls) > 1:
                raise OSError("snapshot device failure")
            return real(*args)

        failures = {"snapshot": patch("precheck.snapshot", failing_after_first),
                    "artifact": patch.object(Store, "artifact", side_effect=OSError("evidence disk full"))}
        phrases = {"snapshot": "snapshot failed", "artifact": "evidence capture failed"}
        for label, fault in failures.items():
            with self.subTest(label):
                known = {r["id"] for r in self.runs("precheck")}
                with fault, self.assertRaisesRegex(Error, "precheck_failed: one \\(failed"):
                    precheck.run(self.store, "A", self.get())
                runs = [r for r in self.runs("precheck") if r["id"] not in known]
                self.assertEqual(len(runs), 2)
                self.assertFalse(any(r["status"] in {"passed", "running"} for r in runs))
                self.assertIn(phrases[label], " ".join(str(r.get("error")) for r in runs))

    # PC3
    def test_total_deadline_starts_no_process_after_it(self):
        late = self.base / "late-started"
        self.declare([check("slow", py("import time; time.sleep(5)"), 2),
                      check("late", py("open(" + repr(str(late)) + ", 'w').write('x')"), 1)], total=2)
        started = time.monotonic()
        rc, out, err = self.prove(argv=self.marking())
        self.assertEqual(rc, 1)
        self.assertRegex(err, r"slow \(timeout, .*late \(interrupted, rc None, run \w+: deadline reached before start")
        self.assertFalse(late.exists())
        self.assertFalse(self.marker.exists())
        self.assertLess(time.monotonic() - started, 6)

    def test_invalid_declarations_refuse_before_any_run(self):
        def body(**top):
            value = {"schema": 1, "total_seconds": 5, "checks": [check("a", py("print(1)"), 1)]}
            value.update(top)
            return json.dumps(value).encode()

        def item(**fields):
            return body(checks=[{**check("a", py("print(1)"), 1), **fields}])

        cases = {
            "string argv": item(argv="python3 -c pass"), "empty argv": item(argv=[]),
            "non-string item": item(argv=[sys.executable, 3]), "empty item": item(argv=[sys.executable, ""]),
            "missing timeout": body(checks=[{"name": "a", "argv": py("print(1)")}]),
            "zero timeout": item(timeout_seconds=0), "long timeout": item(timeout_seconds=61),
            "bool timeout": item(timeout_seconds=True), "timeout above total": body(total_seconds=2, checks=[check("a", py("1"), 3)]),
            "unknown key": body(extra=1), "unknown check key": item(shell=True),
            "duplicate key": b'{"schema": 1, "schema": 1, "total_seconds": 5, "checks": []}',
            "duplicate name": body(checks=[check("a", py("1"), 1), check("a", py("2"), 1)]),
            "bad name": item(name="Bad Name"), "schema 2": body(schema=2), "bool schema": body(schema=True),
            "zero total": body(total_seconds=0), "large total": body(total_seconds=121),
            "no checks": body(checks=[]), "too many checks": body(checks=[check("c" + str(i), py("1"), 1) for i in range(33)]),
            "bad json": b"{", "not utf-8": b"\xff\xfe", "too large": body() + b" " * (16 * 1024),
        }
        for label, data in cases.items():
            with self.subTest(label):
                self.declare(None, body=data)
                with self.assertRaisesRegex(Error, "^precheck_invalid: "):
                    precheck.run(self.store, "A", self.get())
                self.assertEqual(self.runs("precheck"), [])
        rc, out, err = self.prove(argv=self.marking())
        self.assertEqual(rc, 1); self.assertIn("precheck_invalid", err)
        self.assertEqual(self.starts("proof"), []); self.assertFalse(self.marker.exists())

    def test_declaration_must_be_a_tracked_regular_file(self):
        valid = json.dumps({"schema": 1, "total_seconds": 5, "checks": [check("a", py("print(1)"), 1)]}).encode()
        (self.repo / NAME).write_bytes(valid)
        with self.assertRaisesRegex(Error, "precheck_invalid: .* not tracked"):
            precheck.run(self.store, "A", self.get())
        (self.repo / ".gitignore").write_text(NAME + "\n")
        self.git("add", ".gitignore")
        with self.assertRaisesRegex(Error, "precheck_invalid: .* not tracked"):
            precheck.run(self.store, "A", self.get())
        (self.repo / NAME).unlink()
        elsewhere = self.base / "declaration.json"; elsewhere.write_bytes(valid)
        (self.repo / NAME).symlink_to(elsewhere)
        self.git("add", "-f", NAME)
        with self.assertRaisesRegex(Error, "precheck_invalid: .* symlink"):
            precheck.run(self.store, "A", self.get())
        self.assertEqual(self.runs("precheck"), [])

    def test_fix_task_precheck_carries_the_fix_binding(self):
        from fix_challenge import binding, register
        self.declare([check("only", py("print('ok')"))])
        (self.repo / "tests").mkdir()
        (self.repo / "tests/test_bug.py").write_text("import unittest\nclass Bug(unittest.TestCase):\n    def test_value(self):\n        self.assertTrue(True)\n")
        config = self.base / "oracle.json"
        config.write_text(json.dumps({"ids": ["test_bug.Bug.test_value"], "files": ["tests/test_bug.py"]}))
        register(self.store, "C", "T", config, "Regression test")
        checked, summary = precheck.run(self.store, "A", self.get())
        self.assertEqual(summary["checks"][0]["status"], "passed")
        self.assertEqual(self.runs("precheck")[-1]["fix_binding"], binding(self.get()))

    def test_light_task_precheck_carries_the_contract(self):
        from light_route import POLICY, contract
        self.declare([check("only", py("print('ok')"))])
        data = {"id": "L", "title": "Small change", "owner": "A", "reviewer": "R", "repo": str(self.repo),
                "base": snapshot(self.repo)["head"], "scope": self.store.artifact(self.scope),
                "proof": py("print('proof')"), "requirements": [{"id": "R1", "text": "Keep"}], "depends": [],
                "full_gate": False, "expected": None, "route": "light", "light_policy": dict(POLICY)}
        data["contract"] = contract(data)
        self.store.batch([("task", "C", data, None), ("message", "C", {"id": "assign-L", "to": "A", "task": "L", "body": "Read"}, None)])
        self.ack("L")
        checked, summary = precheck.run(self.store, "A", self.get("L"))
        self.assertEqual(summary["checks"][0]["status"], "passed")
        self.assertEqual(self.runs("precheck")[-1]["contract"], self.get("L")["contract"])


# PC4
FILES = ["scripts/registry.py", ".claude-plugin/plugin.json", ".codex-plugin/plugin.json", "README.md", "README.it.md",
         "plugins/memoranda/bridge/squadra_memories/pin.json", "plugins/memoranda/bridge/squadra_memories/__init__.py"]
FILES += [p.relative_to(ROOT).as_posix() for pattern in ("plugins/*/bridge/registry.py", "plugins/*/plugin.json", "plugins/*/store.json")
          for p in sorted(ROOT.glob(pattern))]
CHECKS = ["bridge-copies", "versions", "readme-headings", "pin-label", "plugin-versions"]
LOCATIONS = ["scripts/registry.py", "plugins/memoranda/bridge/registry.py", "plugins/playwright/bridge/registry.py",
             "plugins/sentry/bridge/registry.py", ".claude-plugin/plugin.json", ".codex-plugin/plugin.json",
             "README.md", "README.it.md"]


class SquadraChecks(unittest.TestCase):
    def setUp(self):
        self.declaration = precheck.parse((ROOT / NAME).read_bytes())
        self.argv = {c["name"]: c["argv"] for c in self.declaration["checks"]}
        temp = tempfile.TemporaryDirectory(prefix="squadra-precheck-seed-")
        self.addCleanup(temp.cleanup)
        self.copy = Path(temp.name).resolve()
        for name in FILES:
            (self.copy / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, self.copy / name)
        self.version = re.search(r'^VERSION = "([^"]+)"$', (ROOT / "scripts/registry.py").read_text(), re.M).group(1)

    def run_check(self, name, root=None):
        argv = [sys.executable, *self.argv[name][1:], *(["--root", str(root)] if root else [])]
        return subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=30)

    def edit(self, name, old, new):
        path = self.copy / name
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def assertRed(self, name, *phrases):
        result = self.run_check(name, self.copy)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        for phrase in phrases:
            self.assertIn(phrase, result.stderr)

    def test_repository_declares_the_five_checks_and_the_version_locations(self):
        self.assertEqual([c["name"] for c in self.declaration["checks"]], CHECKS)
        self.assertEqual(self.argv["versions"][4:], LOCATIONS)
        self.assertEqual(self.argv["plugin-versions"][4:], ["plugins/memoranda/bridge/squadra_memories/__init__.py"])
        for argv in self.argv.values():
            self.assertEqual(argv[:3], ["python3", "-B", "tests/precheck_checks.py"])

    def test_each_check_passes_on_the_candidate_and_on_its_clean_copy(self):
        for name in CHECKS:
            with self.subTest(name):
                for root in (None, self.copy):
                    result = self.run_check(name, root)
                    self.assertEqual((result.returncode, result.stdout.strip()), (0, "ok " + name), result.stderr)

    def test_bridge_copies_catches_a_stale_copy(self):
        with (self.copy / "plugins/sentry/bridge/registry.py").open("ab") as handle:
            handle.write(b"\n")
        self.assertRed("bridge-copies", "plugins/sentry/bridge/registry.py: differs from scripts/registry.py")
        for path in self.copy.glob("plugins/*/bridge/registry.py"):
            path.unlink()
        self.assertRed("bridge-copies", "no bridge copies found")

    def test_versions_catches_each_location_left_behind(self):
        for location in LOCATIONS[1:]:
            with self.subTest(location):
                shutil.copy2(ROOT / location, self.copy / location)
                if location.endswith(".py"):
                    self.edit(location, 'VERSION = "' + self.version + '"', 'VERSION = "3.7.9"')
                elif location.endswith(".json"):
                    self.edit(location, '"version": "' + self.version + '"', '"version": "3.7.9"')
                else:
                    self.edit(location, " " + self.version + "**", " 3.7.9**")
                self.assertRed("versions", location + ": 3.7.9, expected " + self.version)
                shutil.copy2(ROOT / location, self.copy / location)
        (self.copy / "README.it.md").unlink()
        self.assertRed("versions", "README.it.md: missing")

    def test_readme_headings_catches_a_changed_or_missing_heading(self):
        text = (ROOT / "README.it.md").read_text(encoding="utf-8")
        second = re.search(r"^## .*$", text, re.M).group(0)
        self.edit("README.it.md", "\n" + second + "\n", "\n#" + second + "\n")
        self.assertRed("readme-headings", "README.it.md: heading", "has level 3")
        shutil.copy2(ROOT / "README.it.md", self.copy / "README.it.md")
        self.edit("README.it.md", "\n" + second + "\n", "\n")
        self.assertRed("readme-headings", "README.it.md:")
        shutil.copy2(ROOT / "README.it.md", self.copy / "README.it.md")
        with (self.copy / "README.md").open("a", encoding="utf-8") as handle:
            handle.write("\n```sh\n# a shell comment, not a heading\n```\n")
        self.assertEqual(self.run_check("readme-headings", self.copy).returncode, 0)

    def test_pin_label_catches_a_missing_current_entry(self):
        path = self.copy / "plugins/memoranda/bridge/squadra_memories/pin.json"
        label = '"label": "squadra ' + self.version + '"'
        text = path.read_text(encoding="utf-8")
        self.assertIn(label, text)
        path.write_text(text.replace(label, '"label": "squadra ' + self.version + '-old"'), encoding="utf-8")
        self.assertRed("pin-label", 'no accepted entry labelled "squadra ' + self.version + '"')

    def test_plugin_versions_catches_a_package_or_store_left_behind(self):
        package = "plugins/memoranda/bridge/squadra_memories/__init__.py"
        own = json.loads((ROOT / "plugins/memoranda/plugin.json").read_text())["about"]["version"]
        self.edit(package, 'VERSION = "' + own + '"', 'VERSION = "0.0.1"')
        self.assertRed("plugin-versions", package + ": 0.0.1, expected " + own + " (plugins/memoranda/plugin.json about.version)")
        shutil.copy2(ROOT / package, self.copy / package)
        playwright = json.loads((ROOT / "plugins/playwright/plugin.json").read_text())["about"]["version"]
        self.edit("plugins/playwright/store.json", '"accepted_version": "' + playwright + '"', '"accepted_version": "0.0.1"')
        self.assertRed("plugin-versions", "plugins/playwright/store.json: accepted_version 0.0.1, expected " + playwright)
        (self.copy / "plugins/sentry/store.json").unlink()
        self.assertRed("plugin-versions", "plugins/sentry/store.json: missing")

    def test_documented_example_is_a_valid_declaration(self):
        text = (ROOT / "references/work.md").read_text(encoding="utf-8")
        examples = [body for body in re.findall(r"```json\n(.*?)\n```", text, re.S) if '"total_seconds"' in body]
        self.assertEqual(len(examples), 1)
        self.assertEqual(precheck.parse(examples[0].encode())["checks"][0]["name"], "pin-label")

    def test_unknown_check_is_a_usage_error(self):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "tests/precheck_checks.py"), "nothing"],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
