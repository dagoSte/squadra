"""A measured run gets a short private temp folder outside the registry, and a drift refusal names its paths."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_squadra as support
import benchmark
import precheck
import runtime
import selftest
from registry import DRIFT_PATHS, Error, Store, snapshot
from runtime import TEMP_LIMIT, execute
from squadra import main

# The child leaves folders nobody may list behind, one inside another, and inside them a link to a folder
# outside, which the first removal attempt cannot reach.
CLOSED = ("import os\n"
          "tmp = os.environ['TMPDIR']\n"
          "for name in ('closed', 'outer/inner'):\n"
          "    os.makedirs(os.path.join(tmp, name)); open(os.path.join(tmp, name, 'f'), 'w').write('x')\n"
          "os.symlink(os.environ['OUTSIDE'], os.path.join(tmp, 'outer', 'link'))\n"
          "for name in ('closed', 'outer/inner', 'outer'):\n"
          "    os.chmod(os.path.join(tmp, name), 0)\n")

# The child reports what it was given, and leaves a read-only folder behind in its temp.
REPORT = ("import json, os, stat\n"
          "home, tmp = os.environ['HOME'], os.environ['TMPDIR']\n"
          "inner = os.path.join(tmp, 'kept'); os.mkdir(inner); open(os.path.join(inner, 'f'), 'w').write('x')\n"
          "os.chmod(inner, 0o500)\n"
          "print(json.dumps({'home': home, 'tmp': tmp, 'mode': stat.S_IMODE(os.stat(tmp).st_mode),"
          " 'uid': os.stat(tmp).st_uid}))\n")


def open_to_others(path):
    """Checked here independently of runtime: group/other-writable and not a root-owned sticky folder."""
    info = os.stat(path)
    return bool(info.st_mode & 0o022) and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)


class PrivateTemp(unittest.TestCase):
    git = support.Workflow.git
    ack = support.Workflow.ack
    task = support.Workflow.task

    def setUp(self):
        support.Workflow.setUp(self)
        self.ack()
        # A registry deep under a folder its group can write, like MAMP's htdocs.
        self.shared = self.base / "shared"; self.shared.mkdir(); self.shared.chmod(0o775)
        self.deep = self.shared.joinpath(*["registry-folder-%02d" % n for n in range(6)], "record")
        self.use_registry(self.deep)
        self.environ = patch.dict(os.environ); self.environ.start(); self.addCleanup(self.environ.stop)
        os.environ.pop("SQUADRA_TMP", None)

    def use_registry(self, root):
        """Move the synthetic squad and its task T to a new registry at root."""
        self.store.close()
        self.root = root
        self.store = Store(self.root, create=True); self.addCleanup(self.store.close)
        self.store.append("init", "system", {"name": "Synthetic team", "project": str(self.repo), "version": "2.0.0",
                          "limits": {"plan": 3, "code": 2, "build": 3}})
        for name, role, identity, client in [("C", "coordinator", "coord", "manual"),
                                             ("A", "builder", "author", "codex"), ("R", "reviewer", "outside", "claude")]:
            self.store.append("agent", name, {"id": name, "role": role, "identity": identity,
                              "client": client, "repo": str(self.repo), "skills": [], "session": ""})
        self.task(); self.ack()

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(["--root", str(self.root), *args])
        return rc, out.getvalue(), err.getvalue()

    def reported(self, run):
        return json.loads((Path(run["folder"]) / "stdout.log").read_text())

    def assert_private(self, seen, temp):
        self.assertEqual((seen["home"], seen["tmp"]), (temp, temp))
        self.assertLessEqual(len(temp), TEMP_LIMIT)
        self.assertTrue(Path(temp).name.startswith("sq-"))
        self.assertEqual((seen["mode"], seen["uid"]), (0o700, os.geteuid()))
        self.assertFalse(Path(os.path.realpath(temp)).is_relative_to(os.path.realpath(self.root)))
        for folder in {*Path(temp).parents, *Path(os.path.realpath(temp)).parents}:
            self.assertFalse(open_to_others(folder), str(folder))
        self.assertFalse(os.path.lexists(temp), "the private temp folder is removed after the run")

    # PT1, PT5
    def test_a_deep_registry_under_a_shared_folder_still_gets_a_short_private_temp(self):
        self.assertGreater(len(str(self.root / "runs" / ("0" * 32) / "tmp")), 104)
        self.assertTrue(open_to_others(self.shared))
        rc, out, err = self.cli("prove", "--actor", "A", "--task", "T", "--", sys.executable, "-c", REPORT)
        self.assertEqual(rc, 0, err)
        run = next(r for r in self.store.state()["runs"].values() if r["mode"] == "proof")
        self.assertEqual((run["status"], run["temp_removed"]), ("passed", True))
        self.assertEqual(Path(run["folder"]).parent, self.root / "runs")
        self.assertEqual(sorted(p.name for p in Path(run["folder"]).iterdir()), ["input.txt", "stderr.log", "stdout.log"])
        self.assert_private(self.reported(run), run["temp"])
        start = next(e for e in self.store.events() if e["kind"] == "run.start" and e["data"]["id"] == run["id"])
        self.assertEqual(start["data"]["temp"], run["temp"], "the run.start record names the temp path")

    # PT1, PT5
    def test_the_folder_goes_after_a_failing_run_and_after_a_probe(self):
        result = execute(self.store, "A", "T", argv=[sys.executable, "-c", REPORT + "raise SystemExit(3)"])
        run = self.store.state()["runs"][result["id"]]
        self.assertEqual((run["status"], run["native_rc"], run["temp_removed"]), ("failed", 3, True))
        self.assert_private(self.reported(run), run["temp"])
        result = execute(self.store, "A", "T", argv=[sys.executable, "-c", REPORT], mode="probe")
        run = self.store.state()["runs"][result["id"]]
        self.assertEqual((run["status"], run["temp_removed"]), ("matched", True))
        self.assert_private(self.reported(run), run["temp"])

    # PT1
    def test_a_too_long_system_temp_falls_back_to_tmp(self):
        long = self.base / ("t" * 60); long.mkdir(mode=0o700)
        with patch("runtime.tempfile.gettempdir", return_value=str(long)):
            parent = runtime.temp_parent()
        self.assertEqual(parent, os.path.abspath("/tmp"))

    # PT1, PT5
    def test_prechecks_use_and_remove_a_private_temp_too(self):
        (self.repo / precheck.NAME).write_text(json.dumps({"schema": 1, "total_seconds": 20, "checks": [
            {"name": "env", "argv": [sys.executable, "-c", REPORT], "timeout_seconds": 10}]}))
        self.git("add", precheck.NAME); self.git("-c", "commit.gpgsign=false", "commit", "-qm", "declare checks")
        rc, out, err = self.cli("prove", "--actor", "A", "--task", "T", "--", sys.executable, "-c", "print('ok')")
        self.assertEqual(rc, 0, err)
        check = next(r for r in self.store.state()["runs"].values() if r["mode"] == "precheck")
        self.assertEqual((check["status"], check["temp_removed"]), ("passed", True))
        self.assertEqual(Path(check["folder"]).parent, self.root / "runs")
        self.assert_private(self.reported(check), check["temp"])

    # PT1
    def test_selftest_children_get_and_lose_a_private_temp(self):
        output, seen = self.base / "selftest-output", {}
        real = selftest.subprocess.Popen

        def launch(argv, **options):
            seen.update(home=options["env"]["HOME"], tmp=options["env"]["TMPDIR"])
            return real([sys.executable, "-c", "pass"], **options)

        with patch.object(selftest.subprocess, "Popen", side_effect=launch):
            result = selftest.run(output, timeout=60)
        self.assertEqual((seen["home"], seen["tmp"]), (result["temp"], result["temp"]))
        self.assertLessEqual(len(result["temp"]), TEMP_LIMIT)
        self.assertFalse(Path(result["temp"]).is_relative_to(output))
        self.assertEqual(result["temp_removed"], True)
        self.assertFalse(os.path.lexists(result["temp"]))
        self.assertEqual(json.loads((output / "RESULT.json").read_text())["temp"], result["temp"])

    # PT1
    def test_offline_benchmark_turns_get_and_lose_a_private_temp(self):
        paths = benchmark.example(self.base / "example")
        result = benchmark.run(paths["manifest"], self.base / "bench")
        self.assertEqual(result["status"], "completed")
        for turn in result["turns"]:
            self.assertLessEqual(len(turn["temp"]), TEMP_LIMIT)
            self.assertEqual(turn["temp_removed"], True)
            self.assertFalse(os.path.lexists(turn["temp"]))
            self.assertFalse((Path(turn["folder"]) / "home").exists())

    # PT2, PT5
    def test_an_unsafe_squadra_tmp_is_refused_before_anything_is_recorded(self):
        marker = self.base / "ran"
        os.environ["SQUADRA_TMP"] = str(self.shared)
        self.assertGreater(len(str(self.shared)), 52, "a long folder gets both reasons")
        events, budget = len(self.store.events()), self.store.state()["runs"]
        rc, out, err = self.cli("prove", "--actor", "A", "--task", "T", "--", sys.executable, "-c",
                                "open(" + repr(str(marker)) + ", 'w')")
        self.assertNotEqual(rc, 0)
        self.assertIn("private_temp_refused: the run did not start", err)
        self.assertIn(str(self.shared) + "/sq-XXXXXXXX is " + str(len(str(self.shared)) + 12) + " characters, over 64", err)
        self.assertIn(str(self.shared) + ", which others can write", err)
        self.assertIn("SQUADRA_TMP=", err)
        self.assertFalse(marker.exists())
        # The command's refusal is the only record: a diagnostic, never a run.
        added = self.store.events()[events:]
        self.assertEqual([e["kind"] for e in added], ["diagnostic"])
        self.assertIn("private_temp_refused", added[0]["data"]["error"])
        self.assertEqual(self.store.state()["runs"], budget)
        self.assertFalse((self.root / "runs").exists() and any((self.root / "runs").iterdir()))
        self.assertEqual(list(self.shared.glob("sq-*")), [])

    # PT2, PT5
    def test_a_short_shared_squadra_tmp_is_refused_for_its_writers_alone(self):
        shared = Path(tempfile.mkdtemp(prefix="g", dir="/tmp")); self.addCleanup(os.rmdir, shared)
        shared.chmod(0o775)
        os.environ["SQUADRA_TMP"] = str(shared)
        events = len(self.store.events())
        with self.assertRaises(Error) as refused:
            execute(self.store, "A", "T", argv=[sys.executable, "-c", "pass"])
        self.assertEqual(str(refused.exception), "private_temp_refused: the run did not start; " + str(shared) +
                         "/sq-XXXXXXXX sits under " + str(shared) + ", which others can write. Fix: set SQUADRA_TMP "
                         "to a short private folder, for example SQUADRA_TMP=/tmp")
        self.assertEqual(len(self.store.events()), events)
        self.assertEqual(list(shared.iterdir()), [])

    # PT2, PT5
    def test_a_too_long_squadra_tmp_is_refused_with_its_length(self):
        # Under /tmp, so that length is the only reason wherever the suite runs.
        short = Path(tempfile.mkdtemp(prefix="q", dir="/tmp")); self.addCleanup(shutil.rmtree, short)
        long = short / ("l" * 60); long.mkdir(mode=0o700)
        os.environ["SQUADRA_TMP"] = str(long)
        events = len(self.store.events())
        with self.assertRaisesRegex(Error, r"private_temp_refused: the run did not start; .*sq-XXXXXXXX is \d+ "
                                           r"characters, over 64\. Fix: set SQUADRA_TMP"):
            execute(self.store, "A", "T", argv=[sys.executable, "-c", "pass"])
        self.assertEqual(len(self.store.events()), events)
        self.assertEqual(list(long.iterdir()), [])

    # PT1, PT2
    def test_a_squadra_tmp_inside_the_registry_is_refused_even_through_a_link(self):
        # A short private registry under /tmp, so that being inside it is the only reason.
        short = Path(tempfile.mkdtemp(prefix="r", dir="/tmp")); self.addCleanup(shutil.rmtree, short)
        self.use_registry(short / "reg")
        alias = Path(tempfile.mkdtemp(prefix="k", dir="/tmp")); alias.rmdir()
        alias.symlink_to(self.root, target_is_directory=True); self.addCleanup(alias.unlink)
        marker = self.base / "ran"
        for value in (str(self.root), str(alias)):
            os.environ["SQUADRA_TMP"] = value
            events = len(self.store.events())
            with self.assertRaises(Error) as refused:
                execute(self.store, "A", "T", argv=[sys.executable, "-c", "open(" + repr(str(marker)) + ", 'w')"])
            self.assertEqual(str(refused.exception), "private_temp_refused: the run did not start; " + value +
                             "/sq-XXXXXXXX is inside the registry " + str(self.store.root) + ". Fix: set SQUADRA_TMP to a "
                             "short private folder, for example SQUADRA_TMP=/tmp")
            self.assertEqual(len(self.store.events()), events)
            self.assertEqual(list(self.root.glob("sq-*")), [])
        self.assertFalse(marker.exists())
        # The declared checks refuse the same placement before any check is recorded.
        (self.repo / precheck.NAME).write_text(json.dumps({"schema": 1, "total_seconds": 20, "checks": [
            {"name": "env", "argv": [sys.executable, "-c", "pass"], "timeout_seconds": 10}]}))
        self.git("add", precheck.NAME); self.git("-c", "commit.gpgsign=false", "commit", "-qm", "declare checks")
        os.environ.pop("SQUADRA_TMP")
        self.assertEqual(self.cli("prove", "--actor", "A", "--task", "T", "--", sys.executable, "-c", "pass")[0], 0)
        os.environ["SQUADRA_TMP"] = str(alias)
        runs = self.store.state()["runs"]
        with self.assertRaisesRegex(Error, "^private_temp_refused: .* is inside the registry "):
            precheck.run(self.store, "A", self.store.state()["tasks"]["T"])
        self.assertEqual(self.store.state()["runs"], runs)

    # PT1, PT5
    def test_cleanup_reopens_folders_nobody_may_list_and_never_follows_a_link(self):
        outside = self.base / "outside"; outside.mkdir(); outside.chmod(0o500)
        self.addCleanup(outside.chmod, 0o700)
        os.environ["OUTSIDE"] = str(outside)
        result = execute(self.store, "A", "T", argv=[sys.executable, "-c", CLOSED])
        run = self.store.state()["runs"][result["id"]]
        self.assertEqual((run["status"], run["native_rc"], run["temp_removed"]), ("passed", 0, True),
                         run.get("temp_error"))
        self.assertNotIn("temp_error", run)
        self.assertFalse(os.path.lexists(run["temp"]))
        self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o500, "the link's target keeps its mode")

    # PT2
    def test_a_safe_short_squadra_tmp_is_used(self):
        mine = Path(tempfile.mkdtemp(prefix="q", dir="/tmp")); self.addCleanup(os.rmdir, mine)
        os.environ["SQUADRA_TMP"] = str(mine)
        result = execute(self.store, "A", "T", argv=[sys.executable, "-c", REPORT])
        self.assertEqual(result["status"], "passed")
        run = self.store.state()["runs"][result["id"]]
        self.assertEqual(Path(run["temp"]).parent, mine)
        self.assert_private(self.reported(run), run["temp"])


class Drift(unittest.TestCase):
    git = support.Workflow.git
    ack = support.Workflow.ack
    task = support.Workflow.task
    cli = PrivateTemp.cli

    def setUp(self):
        support.Workflow.setUp(self)
        self.ack()
        result = execute(self.store, "A", "T", argv=[sys.executable, "-c", "print('ok')"])
        self.assertEqual(result["status"], "passed")
        rc, out, err = self.cli("deliver", "--actor", "A", "--task", "T")
        self.assertEqual(rc, 0, err)

    # PT3, PT5
    def test_drift_names_an_added_untracked_file_and_a_changed_one(self):
        evidence = self.repo / "evidence" / "reader.log"; evidence.parent.mkdir(); evidence.write_text("notes\n")
        (self.repo / "code.py").write_text("VALUE = 2\n")
        rc, out, err = self.cli("check", "--actor", "C", "--task", "T")
        self.assertEqual(rc, 1)
        self.assertIn("Candidate drift; deliver/prove the new exact source. Moved since the delivery fingerprint: "
                      "changed code.py, added evidence/reader.log", err)

    # PT3
    def test_drift_names_a_removed_file_and_a_moved_head(self):
        (self.repo / "code.py").unlink()
        rc, out, err = self.cli("check", "--actor", "C", "--task", "T")
        self.assertIn("removed code.py", err)
        self.git("checkout", "--", "code.py")
        (self.repo / "other.py").write_text("X = 1\n"); self.git("add", "other.py")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "later")
        rc, out, err = self.cli("check", "--actor", "C", "--task", "T")
        self.assertRegex(err, r"Moved since the delivery fingerprint: HEAD [0-9a-f]{12} -> [0-9a-f]{12}, added other\.py")

    # PT3
    def test_drift_lists_twenty_paths_then_a_count(self):
        for n in range(DRIFT_PATHS + 5):
            (self.repo / ("extra-%02d.txt" % n)).write_text("x\n")
        rc, out, err = self.cli("check", "--actor", "C", "--task", "T")
        self.assertIn("added extra-%02d.txt, and 5 more paths" % (DRIFT_PATHS - 1), err)
        self.assertNotIn("extra-%02d.txt" % DRIFT_PATHS, err)

    # PT3
    def test_the_review_refusal_names_the_paths_too(self):
        self.store.append("ack", "R", {"message": self.store.state()["tasks"]["T"]["delivery"]["handoff"]}) \
            if self.store.state()["tasks"]["T"]["delivery"].get("handoff") else None
        (self.repo / "notes.md").write_text("reader notes\n")
        rc, out, err = self.cli("review", "--actor", "R", "--task", "T", "--stage", "code", "--verdict", "accepted",
                                "--report", str(self.report))
        self.assertEqual(rc, 1)
        self.assertIn("Candidate drift; deliver/prove the new exact source. Moved since the delivery fingerprint: "
                      "added notes.md", err)


if __name__ == "__main__":
    unittest.main()
