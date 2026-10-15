"""Check the board through CLI actions and recorded proofs and reviews."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from registry import Store, attention, board
from squadra import main, parser


class Board(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="squadra-board-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root, self.repo = self.base / "record", self.base / "repo"
        self.repo.mkdir()
        env = os.environ | {"GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "Synthetic", "GIT_AUTHOR_EMAIL": "s@example.invalid",
                            "GIT_COMMITTER_NAME": "Synthetic", "GIT_COMMITTER_EMAIL": "s@example.invalid"}
        for args in [("init", "-q"),]:
            subprocess.run(["git", *args], cwd=self.repo, env=env, capture_output=True, check=True)
        (self.repo / "value.txt").write_text("Synthetic fixture\n")
        for args in [("add", "."), ("-c", "commit.gpgsign=false", "commit", "-qm", "fixture")]:
            subprocess.run(["git", *args], cwd=self.repo, env=env, capture_output=True, check=True)
        self.scope = self.base / "scope.md"; self.scope.write_text("Synthetic work\n")
        self.proof = self.base / "proof.json"; self.proof.write_text(json.dumps([sys.executable, "-c", "pass"]))
        self.report = self.base / "read.md"; self.report.write_text("Synthetic independent read\n")
        self.plan = self.base / "plan.md"; self.plan.write_text("Synthetic plan v1\n")
        self.cli("init", "--name", "Board fixture", "--project", str(self.repo), "--temporary")
        for name, role in [("C", "coordinator"), ("A", "builder"), ("R", "reviewer"), ("Other", "reviewer")]:
            self.cli("agent", "--id", name, "--identity", "identity-" + name, "--role", role, "--client", "manual", "--repo", str(self.repo))

    def cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            rc = main(["--root", str(self.root), *args])
        self.assertEqual(rc, 0, (args, err.getvalue(), out.getvalue()))
        return out.getvalue()

    def state(self):
        store = Store(self.root, read_only=True)
        try:
            return store.state()
        finally:
            store.close()

    def task(self, name="T", reader="R", supersedes=None, title=None, owner="A"):
        args = ["task", "--actor", "C", "--id", name, "--title", title or "Work " + name, "--owner", owner,
                "--repo", str(self.repo), "--scope-file", str(self.scope), "--proof-file", str(self.proof)]
        if reader: args += ["--reviewer", reader]
        if supersedes: args += ["--supersedes", supersedes]
        self.cli(*args)

    def start(self, name="T"):
        self.cli("ack", "--actor", "A", "--message", "assign-" + name)
        self.cli("start", "--actor", "A", "--task", name)

    def deliver(self, name="T"):
        self.cli("prove", "--actor", "A", "--task", name, "--", sys.executable, "-c", "pass")
        self.cli("deliver", "--actor", "A", "--task", name)
        handoff = self.state()["tasks"][name]["delivery"]["handoff"]
        self.cli("ack", "--actor", "R", "--message", handoff)

    def review(self, stage="code", verdict="accepted", name="T"):
        self.cli("review", "--actor", "R", "--task", name, "--stage", stage, "--verdict", verdict, "--report", str(self.report))

    def groups(self, text):
        return {m[1]: m[2] for m in re.finditer(r"^## ([^\n]+)\n(.*?)(?=^## |\Z)", text, re.M | re.S)}

    def test_empty_board_both_languages_and_read_only(self):
        before = {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                  for p in self.root.iterdir() if p.is_file() and not p.name.endswith(("-wal", "-shm"))}
        with patch.object(Store, "project", side_effect=AssertionError("board must not project")):
            italian = self.cli("board"); english = self.cli("board", "--lang", "en")
        self.assertEqual(list(self.groups(italian)), ["In corso", "Da fare", "Fatti"])
        self.assertEqual(list(self.groups(english)), ["In progress", "To do", "Done"])
        self.assertEqual(italian.count("| Finestra | Lavoro | Stato | Tempo (circa) |"), 3)
        self.assertEqual(english.count("| Window | Work | State | Time (approx.) |"), 3)
        self.assertIn("Realizzati 0 su 0", italian); self.assertIn("Done 0 of 0", english)
        self.assertEqual(italian.count("| — | Nessun incarico | — | — |"), 3)
        self.assertEqual(english.count("| — | No tasks | — | — |"), 3)
        after = {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                 for p in self.root.iterdir() if p.is_file() and not p.name.endswith(("-wal", "-shm"))}
        self.assertEqual(before, after)

    def test_waiting_ack_ready_and_started_are_different(self):
        self.task()
        self.assertIn("| A (costruttore) | Work T | presa in carico da confermare | — |", self.groups(self.cli("board"))["Da fare"])
        self.cli("ack", "--actor", "A", "--message", "assign-T")
        self.assertIn("| A (builder) | Work T | ready to start | — |", self.groups(self.cli("board", "--lang", "en"))["To do"])
        self.cli("start", "--actor", "A", "--task", "T")
        self.assertIn("| A (costruttore) | Work T | in costruzione |", self.groups(self.cli("board"))["In corso"])
        self.assertNotIn("Work T", self.groups(self.cli("board"))["Da fare"])

    def test_plan_current_bytes_control_reader_or_builder(self):
        self.task(); self.cli("ack", "--actor", "A", "--message", "assign-T")
        self.cli("plan", "--actor", "A", "--task", "T", "--file", str(self.plan))
        self.assertIn("| R (lettore) | Work T | piano in lettura |", self.groups(self.cli("board"))["In corso"])
        self.assertEqual(attention(self.state())["next_steps"][0]["who"], ["R"])
        self.review("plan", "changes-required")
        self.assertIn("| A (costruttore) | Work T | piano da correggere |", self.cli("board"))
        self.plan.write_text("Synthetic plan v2\n"); self.cli("plan", "--actor", "A", "--task", "T", "--file", str(self.plan))
        self.assertIn("| R (reader) | Work T | plan review |", self.cli("board", "--lang", "en"))
        self.review("plan")
        self.assertIn("| A (builder) | Work T | ready to start |", self.groups(self.cli("board", "--lang", "en"))["In progress"])
        self.plan.write_text("Synthetic plan v3\n"); self.cli("plan", "--actor", "A", "--task", "T", "--file", str(self.plan))
        self.assertIn("| R (lettore) | Work T | piano in lettura |", self.cli("board"))

    def test_blocked_plan_is_recorded_as_blocked_not_active_read(self):
        self.task(); self.cli("plan", "--actor", "A", "--task", "T", "--file", str(self.plan)); self.review("plan", "blocked")
        self.assertIn("| A (builder) | Work T | plan review blocked |", self.cli("board", "--lang", "en"))

    def test_code_delivery_fix_and_close_responsibilities(self):
        self.task(); self.start(); self.deliver()
        self.assertIn("| R (lettore) | Work T | consegnato: lettura del codice |", self.cli("board"))
        self.assertEqual(attention(self.state())["next_steps"][0]["who"], ["R"])
        self.review(verdict="changes-required")
        self.assertIn("| A (costruttore) | Work T | da correggere |", self.cli("board"))
        self.cli("start", "--actor", "A", "--task", "T"); self.deliver(); self.review()
        self.assertIn("| C (coordinator) | Work T | ready to close |", self.cli("board", "--lang", "en"))
        self.cli("check", "--actor", "C", "--task", "T"); self.cli("close", "--actor", "C", "--task", "T")
        self.assertIn("| A (costruttore) | Work T | chiuso |", self.groups(self.cli("board"))["Fatti"])
        self.assertIn("Realizzati 1 su 1", self.cli("board"))

    def test_missing_reader_is_not_invented_from_available_seats(self):
        self.task(reader=None); self.start(); self.cli("plan", "--actor", "A", "--task", "T", "--file", str(self.plan))
        self.assertIn("| Lettore da assegnare | Work T | piano in lettura |", self.cli("board"))
        self.assertIn("| Reader to assign | Work T | plan review |", self.cli("board", "--lang", "en"))

    def test_task_role_not_global_seat_role_and_escaped_title(self):
        self.task(owner="C", title="Title | second\nline")
        self.assertIn("| C (costruttore) | Title \\| second line |", self.cli("board"))
        self.assertNotIn("C (coordinatore)", self.cli("board"))

    def test_superseded_work_is_not_active_or_counted(self):
        self.task("OldReady"); self.task("NewReady", supersedes="OldReady")
        self.task("OldWorking"); self.start("OldWorking"); self.task("NewWorking", supersedes="OldWorking")
        text = self.cli("board")
        self.assertNotIn("Work OldReady", text); self.assertNotIn("Work OldWorking", text)
        self.assertIn("Realizzati 0 su 2", text)
        self.assertIn("Work NewReady", self.groups(text)["Da fare"])

    def test_last_ten_done_sorted_by_close_not_creation(self):
        names = [f"T{i:02}" for i in range(12)]
        for name in names: self.task(name)
        for name in names[6:] + names[:6]:
            self.start(name); self.deliver(name); self.review(name=name); self.cli("close", "--actor", "C", "--task", name)
        self.task("Next")
        text = self.cli("board"); section = self.groups(text)["Fatti"]
        self.assertEqual(re.findall(r"\| Work (T\d+) \|", section), list(reversed(names[6:] + names[:6]))[:10])
        self.assertIn("e altri 2", text); self.assertIn("Realizzati 12 su 13", text)
        english = self.cli("board", "--lang", "en")
        self.assertIn("and 2 more", english); self.assertIn("Done 12 of 13", english)

    def test_status_begins_with_live_board_and_projection_omits_running_age(self):
        self.task(); self.start()
        expected = self.cli("board").rstrip()
        self.assertTrue(self.cli("status").startswith(expected + "\n\n"))
        live_row = next(line for line in expected.splitlines() if "| Work T | in costruzione |" in line)
        saved = expected.replace(live_row, "| A (costruttore) | Work T | in costruzione | — |")
        self.cli("export")
        self.assertTrue((self.root / "stato.md").read_text().startswith(saved + "\n\n"))

    def test_exports_at_two_clocks_keep_fixed_durations_and_live_cli_ages(self):
        with patch("registry.now", return_value="2026-10-01T00:00:00Z"):
            self.task("Closed"); self.start("Closed")
            self.deliver("Closed"); self.review(name="Closed")
        with patch("registry.now", return_value="2026-10-01T02:00:00Z"):
            self.cli("close", "--actor", "C", "--task", "Closed")
            self.task("Active"); self.start("Active")
            self.task("Waiting")
        events = (self.root / "events.jsonl").read_bytes()
        saved = []
        for at, en_age, it_age in [("2026-10-01T03:00:00Z", "about 1 h", "circa 1 h"),
                                   ("2026-10-04T02:00:00Z", "about 3 days", "circa 3 giorni")]:
            with patch("registry.now", return_value=at), patch("squadra.now", return_value=at):
                self.cli("export")
                saved.append((self.root / "stato.md").read_bytes())
                self.assertIn(f"| Work Active | being built | {en_age} |", self.cli("board", "--lang", "en"))
                self.assertIn(f"| Work Active | in costruzione | {it_age} |", self.cli("board"))
                self.assertIn(f"| Work Active | in costruzione | {it_age} |", self.cli("status"))
                self.assertEqual((self.root / "stato.md").read_bytes(), saved[-1])
        self.assertEqual(saved[0], saved[1])
        text = saved[0].decode()
        self.assertIn("| Work Active | in costruzione | — |", text)
        self.assertIn("| Work Closed | chiuso | circa 2 h |", text)
        self.assertIn("| Work Waiting | presa in carico da confermare | — |", text)
        self.assertEqual((self.root / "events.jsonl").read_bytes(), events)

    def test_workspace_english_and_override_does_not_change_projection(self):
        self.root = self.base / "english"
        self.cli("init", "--name", "English", "--project", str(self.repo), "--temporary", "--lang", "en")
        self.assertEqual(self.cli("status").splitlines()[2], "## In progress")
        self.cli("export")
        self.assertEqual((self.root / "stato.md").read_text().splitlines()[2], "## In progress")
        before = (self.root / "stato.md").read_bytes()
        self.assertEqual(self.cli("board", "--lang", "it").splitlines()[2], "## In corso")
        self.assertEqual((self.root / "stato.md").read_bytes(), before)
        self.assertEqual(self.state()["workspace"]["lang"], "en")

    def test_declared_output_withdrawn_question_pause_do_not_finish_work(self):
        self.task(); self.start()
        self.cli("output", "--actor", "A", "--task", "T", "--file", str(self.report), "--next", "Everything is done")
        options = self.base / "options.json"; options.write_text(json.dumps([{"id":"a","label":"A","say":"A"},{"id":"b","label":"B","say":"B"}]))
        self.cli("ask", "--actor", "C", "--id", "Q", "--task", "T", "--question", "Choose", "--options-file", str(options))
        self.cli("decision", "--actor", "C", "--withdraws", "Q", "--reason", "Question no longer needed")
        before = self.cli("board")
        self.cli("pause", "--actor", "C", "--reason", "Synthetic pause")
        after = self.cli("board")
        self.assertEqual(after.splitlines()[1:], before.splitlines()[1:])
        self.assertIn("IN PAUSA", after.splitlines()[0])
        self.assertIn("Realizzati 0 su 1", before); self.assertIn("Work T", self.groups(before)["In corso"])

    def test_readme_command_tables_equal_cli(self):
        commands = next(a.choices for a in parser()._actions if hasattr(a, "choices") and isinstance(a.choices, dict))
        self.assertIn("board", commands)
        for name in ["README.md", "README.it.md"]:
            text = (ROOT / name).read_text(); section = text.split('<a id="commands"></a>')[1]
            cells = [line.split("|")[1] for line in section.splitlines() if line.startswith("| `")]
            listed = re.findall(r"`([^`]+)`", "\n".join(cells))
            self.assertEqual(set(listed) - {"--version"}, set(commands)); self.assertIn("**" + str(len(commands)) + " ", text)


if __name__ == "__main__":
    unittest.main()
