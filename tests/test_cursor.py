"""Check Cursor registration, discovery and installation without launching it."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from benchmark import example, run as bench_run
from census import parse_processes
from dashboard_test_support import make_server
from registry import Error, Store
from runtime import CURSOR_REFUSAL, cli_command
from squadra import main

GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "Synthetic", "GIT_AUTHOR_EMAIL": "s@example.invalid",
           "GIT_COMMITTER_NAME": "Synthetic", "GIT_COMMITTER_EMAIL": "s@example.invalid"}


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, env=os.environ | GIT_ENV, check=True, capture_output=True)


def cli(root, *args):
    with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
        try:
            rc = main(["--root", str(root), *args])
        except SystemExit as exit_:
            rc = exit_.code
    return rc, out.getvalue(), err.getvalue()


class CursorSeat(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="squadra-cursor-")
        self.base = Path(self.temp.name).resolve()
        self.repo, self.root, self.files = self.base / "repo", self.base / "record", self.base / "files"
        for folder in (self.repo, self.files):
            folder.mkdir()
        git(self.repo, "init", "-q")
        (self.repo / "code.py").write_text("VALUE = 1\n")
        git(self.repo, "add", ".")
        git(self.repo, "-c", "commit.gpgsign=false", "commit", "-qm", "baseline")
        # Leave a marker if the fake client starts, even for a version check.
        self.marker = self.base / "cursor-agent-started"
        self.fake = self.base / "bin" / "cursor-agent"
        self.fake.parent.mkdir()
        self.fake.write_text(f"#!/bin/sh\necho \"$@\" >> '{self.marker}'\necho '{{}}'\n")
        self.fake.chmod(0o755)
        (self.files / "scope.md").write_text("Synthetic scope.\n")
        (self.files / "proof.json").write_text(json.dumps([sys.executable, "-c", "pass"]))
        (self.files / "prompt.txt").write_text("Synthetic prompt.\n")
        self.ok("init", "--name", "Cursor test", "--project", str(self.repo), "--temporary")
        for seat, identity, role, client in [("coord", "w-coord", "coordinator", "codex"), ("cur", "w-cur", "builder", "cursor"),
                                             ("rcur", "w-rcur", "reviewer", "cursor"), ("r1", "w-r1", "reviewer", "claude")]:
            self.ok("agent", "--id", seat, "--identity", identity, "--role", role, "--client", client, "--repo", str(self.repo))
        self.ok("task", "--actor", "coord", "--repo", str(self.repo), "--scope-file", str(self.files / "scope.md"),
                "--proof-file", str(self.files / "proof.json"), "--id", "T1", "--owner", "cur", "--reviewer", "rcur", "--title", "Synthetic task")
        self.ok("ack", "--actor", "cur", "--message", "assign-T1")

    def tearDown(self):
        self.temp.cleanup()

    def ok(self, *args):
        rc, out, err = cli(self.root, *args)
        self.assertEqual(rc, 0, (args, out, err))
        return out

    def kinds(self):
        store = Store(self.root, read_only=True)
        try:
            return [e["kind"] for e in store.events()]
        finally:
            store.close()

    def test_registration_accepts_cursor_and_keeps_the_other_clients(self):
        state = json.loads((self.root / "state.json").read_text())
        self.assertEqual({k: a["client"] for k, a in state["agents"].items()},
                         {"coord": "codex", "cur": "cursor", "rcur": "cursor", "r1": "claude"})
        rc, _, err = cli(self.root, "agent", "--id", "g1", "--identity", "w-g1", "--role", "builder", "--client", "gemini",
                         "--repo", str(self.repo))
        self.assertEqual(rc, 2, "an unknown client is still refused")
        self.ok("agent", "--id", "m1", "--identity", "w-m1", "--role", "executor", "--client", "manual", "--repo", str(self.repo))

    def test_read_and_build_turns_of_a_cursor_seat_are_refused_before_anything_exists(self):
        before = self.kinds()
        env = {"PATH": str(self.fake.parent) + os.pathsep + os.environ.get("PATH", "")}
        for actor, mode in (("cur", "build"), ("rcur", "read")):
            with self.subTest(mode=mode), patch.dict(os.environ, env):
                rc, out, err = cli(self.root, "run", "--actor", actor, "--task", "T1", "--mode", mode, "--stage", "code",
                                   "--prompt-file", str(self.files / "prompt.txt"), "--timeout", "30", "--cli", str(self.fake))
                self.assertNotEqual(rc, 0)
                self.assertIn(CURSOR_REFUSAL, out + err)
        self.assertFalse(self.marker.exists(), "cursor-agent was never started, not even for --version")
        self.assertFalse((self.root / "runs").exists() and any((self.root / "runs").iterdir()), "no run folder was created")
        after = self.kinds()
        self.assertFalse({"run.start", "run.end"} & set(after[len(before):]), "no run event was recorded")

    def test_cli_command_refuses_cursor_for_every_mode(self):
        for mode in ("read", "build"):
            with self.subTest(mode=mode), self.assertRaises(Error) as caught:
                cli_command("cursor", mode, self.base, None, str(self.fake))
            self.assertEqual(str(caught.exception), CURSOR_REFUSAL)

    def test_a_cursor_seat_still_runs_its_bound_proof(self):
        out = self.ok("prove", "--actor", "cur", "--task", "T1", "--", sys.executable, "-c", "pass")
        self.assertEqual(json.loads(out[:out.index("\n}") + 2])["status"], "passed")
        self.assertFalse(self.marker.exists())

    def test_benchmark_refuses_a_cursor_arm_before_any_output(self):
        made = example(self.base / "bench")
        manifest = json.loads(Path(made["manifest"]).read_text())
        manifest["arms"][1]["reader"] = "cursor"  # Other clients in this manifest remain valid.
        Path(made["manifest"]).write_text(json.dumps(manifest))
        destination = self.base / "bench-out"
        with self.assertRaises(Error) as caught:
            bench_run(made["manifest"], destination)
        self.assertEqual(str(caught.exception), CURSOR_REFUSAL)
        self.assertFalse(destination.exists(), "no arm ran and no output exists")

    def test_census_recognises_cursor_agent_by_name_only(self):
        table = " 21 /Users/x/.local/bin/cursor-agent\n22 cursor-agent\n23 agent\n24 node\n25 cursor-helper\n26 /Applications/Codex\n27 claude\n"
        self.assertEqual(parse_processes(table), {21: "cursor", 22: "cursor", 26: "codex", 27: "claude"})

    def test_no_cursor_credential_is_read_passed_or_stored(self):
        for path in sorted((ROOT / "scripts").glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for word in ("CURSOR_API_KEY", "CURSOR_AUTH_TOKEN", "--api-key"):
                with self.subTest(file=path.name, word=word):
                    self.assertNotIn(word, text)


class CursorInstall(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="squadra-cursor-install-")
        self.base = Path(self.temp.name).resolve()
        self.repo, self.home = self.base / "repo", self.base / "home"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        (self.repo / "SKILL.md").write_text("---\nname: squadra\n---\n")
        (self.repo / "scripts").mkdir()
        (self.repo / "scripts/squadra.py").write_text('print("synthetic installed")\n')
        git(self.repo, "add", ".")
        git(self.repo, "-c", "commit.gpgsign=false", "commit", "-qm", "candidate")
        for name in (".codex", ".claude", ".cursor"):
            dest = self.home / name / "skills/squadra"
            dest.mkdir(parents=True)
            (dest / "OLD").write_text(name)

    def tearDown(self):
        self.temp.cleanup()

    def test_install_places_the_cursor_skill_at_the_same_version_and_keeps_the_old_one(self):
        from install import install
        result = install(self.repo, self.home, self.base / "backup")
        for name in (".codex", ".claude", ".cursor"):
            self.assertEqual((self.home / name / "skills/squadra").resolve(), Path(result["version"]))
        self.assertEqual((self.base / "backup/cursor-squadra/OLD").read_text(), ".cursor")
        self.assertIn(str(self.home / ".cursor/skills/squadra"), [t["path"] for t in result["targets"]])

    def test_a_failed_install_restores_the_cursor_skill_too(self):
        from install import install
        with patch.object(Path, "symlink_to", side_effect=OSError("synthetic failure")):
            with self.assertRaises(OSError):
                install(self.repo, self.home, self.base / "backup")
        for name in (".codex", ".claude", ".cursor"):
            self.assertEqual((self.home / name / "skills/squadra/OLD").read_text(), name)


class CursorPage(unittest.TestCase):
    """Check Cursor labels with the page scripts and real server state; Node is required."""

    def test_js_a_cursor_seat_is_named_cursor_in_both_languages(self):
        node = shutil.which("node")
        if not node:
            self.fail("Node is required: the page contract was not measured")
        seat = CursorSeat("test_registration_accepts_cursor_and_keeps_the_other_clients")
        seat.setUp()
        try:
            server, token = make_server(seat.root)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = urllib.request.Request("http://127.0.0.1:%d/api/state" % server.server_port, headers={"X-Squadra-Token": token})
                with urllib.request.urlopen(request) as response:
                    state = response.read()
            finally:
                server.shutdown()
                server.server_close()
            fixture = seat.base / "state.json"
            fixture.write_bytes(state)
            argv = [node, str(ROOT / "tests/cursor_contract.js"), str(ROOT / "assets/dashboard.html"), str(fixture)]
            result = subprocess.run(argv, capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("cursor contract passed", result.stdout)
        finally:
            seat.tearDown()


if __name__ == "__main__":
    unittest.main()
