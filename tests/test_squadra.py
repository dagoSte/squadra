"""Check workflows, recovery and CLI adapters without model calls."""
import concurrent.futures
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from registry import Store, Error, digest, snapshot
from runtime import cli_command, execute, validate_reply
from squadra import main, bindings, deliver
from dashboard_test_support import make_server


class Workflow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="squadra-tests-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "repo with spaces"; self.repo.mkdir()
        self.home = self.base / "home"; self.home.mkdir()
        self.env = os.environ | {"HOME": str(self.home), "GIT_CONFIG_NOSYSTEM": "1"}
        for args in [("init", "-q"), ("config", "user.name", "Synthetic"), ("config", "user.email", "test@example.invalid")]:
            self.git(*args)
        (self.repo / "code.py").write_text("VALUE = 1\n")
        self.git("add", "."); self.git("-c", "commit.gpgsign=false", "commit", "-qm", "baseline")
        self.root = self.base / "record"
        self.scope = self.base / "scope.md"; self.scope.write_text("Preserve VALUE; synthetic contract.\n")
        self.report = self.base / "report.md"; self.report.write_text("Independent examined contract and source.\n")
        self.store = Store(self.root, create=True); self.addCleanup(self.store.close)
        self.store.append("init", "system", {"name": "Synthetic team", "project": str(self.repo), "version": "2.0.0",
                          "limits": {"plan": 3, "code": 2, "build": 3}})
        for name, role, identity, client in [("C", "coordinator", "coord", "manual"),
                   ("A", "builder", "author", "codex"), ("R", "reviewer", "outside", "claude"),
                   ("A-alias", "reviewer", "author", "claude")]:
            self.store.append("agent", name, {"id": name, "role": role, "identity": identity,
                              "client": client, "repo": str(self.repo), "skills": [], "session": ""})
        self.task()

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, env=self.env, stderr=subprocess.PIPE)

    def task(self, name="T", owner="A", deps=None, full_gate=False):
        data = {"id": name, "title": "Synthetic change", "owner": owner, "repo": str(self.repo),
                "base": snapshot(self.repo)["head"], "scope": self.store.artifact(self.scope),
                "proof": None, "expected": None, "depends": deps or [], "full_gate": full_gate}
        self.store.batch([("task", "C", data, None), ("message", "C", {"id": "assign-" + name,
                          "to": owner, "task": name, "body": "Scoped work"}, None)])

    def ack(self, name="T", owner="A"):
        self.store.append("ack", owner, {"message": "assign-" + name})

    def cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(["--root", str(self.root), *args])

    def proof(self, actor="A", task="T", **kwargs):
        return execute(self.store, actor, task, argv=[sys.executable, "-c", "print('contract passed')"], **kwargs)

    def delivered(self):
        self.ack(); self.assertEqual(self.proof()["status"], "passed")
        self.assertEqual(self.cli("deliver", "--actor", "A", "--task", "T"), 0)

    def test_assignment_needs_recipient_ack(self):
        with self.assertRaisesRegex(Error, "ACK"):
            self.store.append("start", "A", {"task": "T"})
        self.ack(); self.store.append("start", "A", {"task": "T"})
        self.assertEqual(self.store.state()["tasks"]["T"]["status"], "working")

    def test_transport_receipt_does_not_become_ack(self):
        self.store.append("transport", "C", {"message": "assign-T", "delivered": True})
        self.assertIsNone(self.store.state()["messages"]["assign-T"]["ack"])
        with self.assertRaises(Error): self.store.append("ack", "R", {"message": "assign-T"})

    def test_atomic_task_assignment_rolls_back_on_bad_recipient(self):
        before = len(self.store.events())
        with self.assertRaises(Error): self.task("bad", owner="missing")
        self.assertEqual(len(self.store.events()), before)

    def test_idempotent_event_cannot_duplicate_or_change_effect(self):
        data = {"reason": "Owner pause"}
        self.store.append("pause", "C", data, "stable")
        n = len(self.store.events()); self.store.append("pause", "C", data, "stable")
        self.assertEqual(len(self.store.events()), n)
        with self.assertRaises(Error): self.store.append("resume", "C", data, "stable")

    def test_concurrent_events_have_no_loss_and_valid_chain(self):
        def worker(number):
            s = Store(self.root)
            try:
                for i in range(8): s.append("diagnostic", "system", {"worker": number, "index": i})
            finally: s.close()
        before = len(self.store.events())
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(worker, range(6)))
        self.assertEqual(len(self.store.events()), before + 48)
        self.assertEqual(self.store.state()["event_count"], before + 48)

    def test_uncommitted_process_crash_does_not_damage_record(self):
        before = len(self.store.events())
        code = "import sqlite3,sys,os;c=sqlite3.connect(sys.argv[1]);c.execute('BEGIN IMMEDIATE');c.execute(\"INSERT INTO events(id,utc,kind,actor,data,previous,hash) VALUES('crash','','diagnostic','system','{}','','')\");os._exit(7)"
        q = subprocess.run([sys.executable, "-c", code, str(self.store.path)], env=self.env)
        self.assertEqual(q.returncode, 7)
        self.assertEqual(len(self.store.events()), before)

    def test_projection_can_be_rebuilt_after_commit_gap(self):
        self.ack(); self.assertFalse((self.root / "state.json").exists())
        self.store.project()
        self.assertEqual(json.loads((self.root / "state.json").read_text())["tasks"]["T"]["status"], "ready")
        self.assertIn("ACK", (self.root / "squadra-posta.md").read_text())

    def test_append_only_and_integrity_failure_are_detected(self):
        with self.assertRaises(sqlite3.IntegrityError): self.store.db.execute("DELETE FROM events")
        self.store.db.execute("DROP TRIGGER no_update")
        self.store.db.execute("UPDATE events SET data='{}' WHERE seq=1")
        with self.assertRaisesRegex(Error, "integrity"): self.store.events()

    def test_checkpoint_restores_events_and_immutable_artifacts(self):
        self.ack(); self.store.project()
        target = self.base / "checkpoint"
        self.store.checkpoint(target)
        restored = Store(target)
        try:
            self.assertEqual(restored.state()["tasks"], self.store.state()["tasks"])
            self.assertTrue(restored.verify_artifacts())
        finally: restored.close()
        with self.assertRaises(Error): self.store.checkpoint(target)

    def test_corrupt_artifact_blocks_verification(self):
        scope = self.store.state()["tasks"]["T"]["scope"]
        (self.root / "artifacts" / scope["sha256"]).write_text("tampered")
        with self.assertRaises(Error): self.store.verify_artifacts()

    def test_author_alias_does_not_gain_independent_review(self):
        self.delivered()
        self.assertEqual(self.cli("review", "--actor", "A-alias", "--task", "T", "--verdict", "accepted", "--report", str(self.report)), 1)
        self.assertEqual(self.store.state()["tasks"]["T"]["reviews"], [])

    def test_proof_deliver_independent_review_and_close_journey(self):
        self.delivered()
        self.assertEqual(self.cli("close", "--actor", "C", "--task", "T"), 1)
        self.assertEqual(self.cli("review", "--actor", "R", "--task", "T", "--verdict", "accepted", "--report", str(self.report)), 0)
        self.assertEqual(self.cli("close", "--actor", "C", "--task", "T"), 0)
        self.assertEqual(self.store.state()["tasks"]["T"]["status"], "done")

    def handoff_body(self, name):
        data = {"id": name, "title": "Synthetic change", "owner": "A", "reviewer": "R", "repo": str(self.repo),
                "base": snapshot(self.repo)["head"], "scope": self.store.artifact(self.scope),
                "proof": None, "expected": None, "depends": [], "full_gate": False}
        self.store.batch([("task", "C", data, None), ("message", "C", {"id": "assign-" + name,
                          "to": "A", "task": name, "body": "Scoped work"}, None)])
        self.ack(name); self.assertEqual(self.proof(task=name)["status"], "passed")
        handoff = deliver(self.store, "A", name)["handoff"]
        return self.store.state()["messages"][handoff]["body"]

    def test_handoff_names_the_working_tree_when_its_commit_lacks_the_change(self):
        head = snapshot(self.repo)["head"]
        self.assertTrue(self.handoff_body("V").startswith("Read exact candidate " + head + " for V.\nImmutable packet "))
        (self.repo / "code.py").write_text("VALUE = 2\n"); (self.repo / "new.py").write_text("NEW = 1\n")
        current = snapshot(self.repo, self.root); body = self.handoff_body("W")
        self.assertIn("the working tree of " + str(self.repo) + " matching snapshot " + current["sha256"] +
                      ", NOT commit " + head + ", which lacks 2 uncommitted or untracked paths: code.py, new.py.\n", body)
        self.assertNotIn("Read exact candidate " + head, body)

    def test_changed_code_invalidates_proof_and_review(self):
        self.delivered()
        self.assertEqual(self.cli("review", "--actor", "R", "--task", "T", "--verdict", "accepted", "--report", str(self.report)), 0)
        (self.repo / "new.py").write_text("UNREVIEWED = True\n")
        with self.assertRaisesRegex(Error, "drift"): bindings(self.store, "T")

    def test_native_zero_with_mutated_source_is_not_valid_proof(self):
        self.ack()
        result = execute(self.store, "A", "T", argv=[sys.executable, "-c", "from pathlib import Path;Path('code.py').write_text('changed')"])
        self.assertEqual(result["native_rc"], 0); self.assertEqual(result["status"], "failed")
        self.assertEqual(self.cli("deliver", "--actor", "A", "--task", "T"), 1)

    def test_negative_probe_cannot_qualify_delivery(self):
        self.ack()
        result = execute(self.store, "R", "T", mode="probe", expected_rc=1, argv=[sys.executable, "-c", "raise AssertionError('controlled semantic refusal')"])
        self.assertEqual(result["status"], "matched")
        self.assertEqual(self.store.state()["tasks"]["T"]["proofs"], [])
        self.assertEqual(self.cli("deliver", "--actor", "A", "--task", "T"), 1)

    def test_required_full_gate_cannot_use_targeted_proof(self):
        self.task("G", full_gate=True); self.ack("G")
        self.assertEqual(self.proof(task="G")["status"], "passed")
        self.assertEqual(self.cli("deliver", "--actor", "A", "--task", "G"), 1)

    def test_gate_resource_is_exclusive_even_for_same_actor(self):
        self.ack()
        self.store.append("resource.acquire", "A", {"name": "gate", "reason": "frozen candidate"})
        with self.assertRaises(Error): self.store.append("resource.acquire", "C", {"name": "gate", "reason": "another gate"})
        data = {"id": "first", "task": "T", "mode": "proof", "gate": True}
        self.store.append("run.start", "A", data)
        with self.assertRaisesRegex(Error, "already running"):
            self.store.append("run.start", "A", data | {"id": "second"})

    def test_dependencies_require_actual_closure(self):
        self.task("dependent", deps=["T"]); self.ack("dependent")
        with self.assertRaisesRegex(Error, "Dependency"):
            self.store.append("start", "A", {"task": "dependent"})

    def test_review_budget_has_a_real_stop(self):
        self.delivered()
        for verdict in ["changes-required", "accepted"]:
            self.assertEqual(self.cli("review", "--actor", "R", "--task", "T", "--verdict", verdict, "--report", str(self.report)), 0)
        self.assertEqual(self.cli("review", "--actor", "R", "--task", "T", "--verdict", "accepted", "--report", str(self.report)), 1)

    def test_changed_plan_invalidates_approval(self):
        self.assertEqual(self.cli("plan", "--actor", "C", "--task", "T", "--file", str(self.scope)), 0)
        self.assertEqual(self.cli("review", "--actor", "R", "--task", "T", "--stage", "plan", "--verdict", "accepted", "--report", str(self.report)), 0)
        self.ack(); self.scope.write_text("new unreviewed scope")
        with self.assertRaisesRegex(Error, "plan changed"):
            self.proof()

    def test_pause_blocks_new_work_until_explicit_resume(self):
        self.ack(); self.store.append("pause", "C", {"reason": "owner stop"})
        with self.assertRaisesRegex(Error, "paused"): self.store.append("start", "A", {"task": "T"})
        self.store.append("resume", "C", {"reason": "owner explicitly resumed"})
        self.store.append("start", "A", {"task": "T"})

    def test_timeout_is_saved_and_not_a_proof(self):
        self.ack()
        result = execute(self.store, "A", "T", argv=[sys.executable, "-c", "import time;time.sleep(20)"], timeout=1)
        self.assertEqual(result["status"], "timeout")
        self.assertIsNotNone(result["native_rc"])
        self.assertEqual(self.store.state()["runs"][result["id"]]["status"], "timeout")

    @unittest.skipIf(os.name == 'nt', 'POSIX process-group contract')
    def test_timeout_stops_term_ignoring_descendant_after_leader_exits(self):
        self.ack()
        heartbeat = self.base / 'child-heartbeat'
        child = "import signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);p=Path(" + repr(str(heartbeat)) + ");\nwhile True:p.write_text(str(time.monotonic()));time.sleep(.05)"
        parent = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c'," + repr(child) + "]);time.sleep(30)"
        result = execute(self.store, 'A', 'T', argv=[sys.executable, '-c', parent], timeout=1)
        self.assertEqual(result['status'], 'timeout')
        self.assertTrue(heartbeat.is_file(), 'positive control: descendant actually ran')
        old = heartbeat.read_text();time.sleep(.3)
        self.assertEqual(heartbeat.read_text(), old, 'receipt must follow termination of owned descendants')

    def test_pause_cancels_owned_run_and_keeps_terminal_receipt(self):
        self.ack()
        ready = self.base / 'pause-child-ready'
        errors = []
        def pause():
            deadline = time.monotonic() + 10
            while not ready.is_file() and time.monotonic() < deadline:
                time.sleep(.02)
            if not ready.is_file():
                errors.append('Synthetic child never started'); return
            other = Store(self.root)
            try: other.append("pause", "C", {"reason": "owner pause"})
            finally: other.close()
        t = threading.Thread(target=pause); t.start()
        program = "import time;from pathlib import Path;Path(" + repr(str(ready)) + ").write_text('ready');time.sleep(20)"
        result = execute(self.store, "A", "T", argv=[sys.executable, "-c", program])
        t.join()
        self.assertEqual(errors, [], 'Positive control: actual child readiness precedes pause')
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(self.store.state()["runs"][result["id"]]["status"], "interrupted")

    def test_output_is_saved_before_table_and_survives_reopen(self):
        self.assertEqual(self.cli("output", "--actor", "A", "--task", "T", "--file", str(self.report), "--next", "Independent review"), 0)
        other = Store(self.root)
        try:
            self.assertEqual(other.state()["outputs"][-1]["next"], "Independent review")
            self.assertIn("Independent review", (self.root / "stato.md").read_text())
        finally: other.close()

    def test_legacy_import_does_not_create_acceptance_or_assignments(self):
        legacy = self.base / "legacy"; legacy.mkdir()
        (legacy / "manifest.json").write_text('{"status":"accepted"}')
        before = self.store.state()
        self.assertEqual(self.cli("legacy", "--actor", "C", "--path", str(legacy)), 0)
        after = self.store.state()
        self.assertEqual(after["tasks"], before["tasks"])
        self.assertEqual(after["messages"], before["messages"])
        self.assertEqual(after["legacy"][str(legacy)]["trust"], "reported-unverified")

    def test_doctor_does_not_claim_unknown_run_stopped(self):
        self.ack(); self.store.append("run.start", "A", {"id": "orphan", "task": "T", "mode": "proof"})
        self.assertEqual(self.cli("doctor"), 0)
        self.assertEqual(self.store.state()["runs"]["orphan"]["status"], "running")

    def test_root_argument_can_follow_subcommand(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["status", "--root", str(self.root)]), 0)

    def test_failed_artifact_capture_keeps_native_terminal_result(self):
        self.ack()
        with patch.object(self.store, "artifact", side_effect=Error("synthetic capture failure")):
            result = self.proof()
        self.assertEqual(result["native_rc"], 0)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.store.state()["runs"][result["id"]]["status"], "failed")

    def test_signal_denial_keeps_terminal_receipt_and_uncertain_stop(self):
        self.ack()
        with patch('runtime.terminate', side_effect=PermissionError('synthetic signal denial')):
            result = self.proof()
        self.assertEqual(result['native_rc'], 0)
        self.assertEqual(result['status'], 'cleanup-unknown')
        saved = self.store.state()['runs'][result['id']]
        self.assertEqual(saved['cleanup'], 'not verified')
        self.assertEqual(saved['status'], 'cleanup-unknown')
        self.assertTrue(saved['artifacts'])
        self.assertEqual(self.cli('deliver', '--actor', 'A', '--task', 'T'), 1)

    def test_wrong_collection_cannot_be_green_on_native_zero(self):
        self.ack()
        # The contract expects ten, while the instrument reports nine.
        state = self.store.state()["tasks"]["T"]
        self.task("Count"); self.ack("Count")
        original = self.store.state
        def changed():
            s = original(); s["tasks"]["Count"]["expected"] = 10; return s
        with patch.object(self.store, "view", side_effect=changed):
            result = execute(self.store, "A", "Count", argv=[sys.executable, "-c", "print('9 passed in 0.1s')"])
        self.assertEqual(result["native_rc"], 0); self.assertEqual(result["status"], "failed")

    def test_projection_concurrency_does_not_lose_latest_event(self):
        def worker(i):
            s = Store(self.root)
            try:
                s.append("diagnostic", "system", {"i": i}); s.project()
            finally: s.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(worker, range(8)))
        self.assertEqual(json.loads((self.root / "state.json").read_text())["event_count"], len(self.store.events()))

    def test_actual_fake_cli_journeys_for_both_clients(self):
        self.ack()
        executable = self.base / "fake-client"
        response = {"verdict": "changes-required", "summary": "Concrete synthetic refusal", "findings": ["VALUE remains 1"], "coverage": ["code.py"], "limitations": []}
        executable.write_text("#!" + sys.executable + "\nimport sys,json,pathlib\n"
            "if '--version' in sys.argv: print('synthetic-cli 1');sys.exit(0)\n"
            "prompt=sys.stdin.read();assert len(prompt)>65536\n"
            "reply=" + repr(response) + "\n"
            "session='12345678-1234-4567-8123-123456789abc'\n"
            "if 'exec' in sys.argv:\n"
            " pathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_text(json.dumps(reply))\n"
            " print(json.dumps({'type':'thread.started','thread_id':session}))\n"
            " print(json.dumps({'type':'turn.completed','usage':{'input_tokens':2}}))\n"
            "else: print(json.dumps({'type':'result','subtype':'success','session_id':session,'structured_output':reply}))\n")
        executable.chmod(0o700)
        for client in ["codex", "claude"]:
            agent = self.store.state()["agents"]["R"]
            self.store.append("agent", "R", agent | {"client": client})
            result = execute(self.store, "R", "T", mode="read", prompt="x" * 100000,
                             executable=str(executable), timeout=10)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["response"]["verdict"], "changes-required")
            self.assertEqual(result["cli_version"], "synthetic-cli 1")
            self.assertEqual(result["observed_models"], [])
        self.assertEqual(self.store.state()["tasks"]["T"]["reviews"], [])
        with self.assertRaisesRegex(Error, "budget"):
            execute(self.store, "R", "T", mode="read", prompt="third", executable=str(executable))

    def test_native_zero_without_model_completion_stays_failed(self):
        self.ack()
        executable = self.base / "empty-client"
        executable.write_text("#!" + sys.executable + "\nprint('{}')\n")
        executable.chmod(0o700)
        result = execute(self.store, "R", "T", mode="read", prompt="inspect", executable=str(executable))
        self.assertEqual(result["native_rc"], 0); self.assertEqual(result["status"], "failed")

    def test_latest_plan_refusal_blocks_builder(self):
        self.ack()
        self.assertEqual(self.cli("plan", "--actor", "C", "--task", "T", "--file", str(self.scope)), 0)
        for verdict in ["accepted", "changes-required"]:
            self.assertEqual(self.cli("review", "--actor", "R", "--task", "T", "--stage", "plan", "--verdict", verdict, "--report", str(self.report)), 0)
        with self.assertRaisesRegex(Error, "approval"):
            execute(self.store, "A", "T", mode="build", prompt="implement")


class Adapters(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.response = {"verdict": "accepted", "summary": "Inspected", "findings": [], "coverage": ["code.py"], "limitations": []}
        self.session = "12345678-1234-4567-8123-123456789abc"

    def codex(self, events=None, response=None):
        (self.folder / "stdout.log").write_text("\n".join(json.dumps(e) for e in events or [
            {"type": "thread.started", "thread_id": self.session}, {"type": "turn.completed", "usage": {"input_tokens": 4}}]))
        (self.folder / "reply.txt").write_text(json.dumps(response if response is not None else self.response))

    def test_codex_completion_and_structured_result(self):
        self.codex(); self.assertEqual(validate_reply("codex", self.folder)["actual_session"], self.session)

    def test_started_or_failed_turn_is_not_success(self):
        for events in [[{"type": "thread.started", "thread_id": self.session}],
                       [{"type": "thread.started", "thread_id": self.session}, {"type": "turn.failed"}, {"type": "turn.completed"}]]:
            self.codex(events)
            with self.assertRaises(Error): validate_reply("codex", self.folder)

    def test_empty_malformed_and_contradictory_results_refused(self):
        for response in [{}, self.response | {"findings": ["material defect"]}, self.response | {"coverage": []}]:
            self.codex(response=response)
            with self.assertRaises(Error): validate_reply("codex", self.folder)

    def test_revise_is_valid_execution_but_not_approval(self):
        self.codex(response=self.response | {"verdict": "changes-required", "findings": ["concrete defect"]})
        self.assertEqual(validate_reply("codex", self.folder)["response"]["verdict"], "changes-required")

    def test_claude_result_and_array_envelope(self):
        result = {"type": "result", "subtype": "success", "session_id": self.session, "structured_output": self.response}
        for value in [result, [{"type": "system"}, result]]:
            (self.folder / "stdout.log").write_text(json.dumps(value))
            self.assertEqual(validate_reply("claude", self.folder)["response"], self.response)

    def test_claude_failure_and_missing_session_refused(self):
        for value in [{"type": "result", "subtype": "error_during_execution"},
                      {"type": "result", "subtype": "success", "structured_output": self.response}]:
            (self.folder / "stdout.log").write_text(json.dumps(value))
            with self.assertRaises(Error): validate_reply("claude", self.folder)

    def test_review_permissions_and_explicit_model(self):
        codex = cli_command("codex", "read", self.folder, "requested-model")
        self.assertIn("read-only", codex); self.assertIn("--ignore-user-config", codex)
        self.assertIn("requested-model", codex)
        claude = cli_command("claude", "read", self.folder)
        self.assertIn("Read,Glob,Grep", claude); self.assertIn("--strict-mcp-config", claude)
        self.assertNotIn("--dangerously-skip-permissions", claude)

    def test_real_dashboard_script_boots_and_preserves_offline_view(self):
        node = shutil.which('node')
        if not node: self.fail('Node is required: the dashboard JS contract was not measured')
        source = SCRIPTS.parent
        q = subprocess.run([node, str(source / 'tests/dashboard_contract.js'),
                            str(source / 'assets/dashboard.html')], capture_output=True, text=True, timeout=10)
        self.assertEqual(q.returncode, 0, q.stderr)
        self.assertIn('dashboard contract passed', q.stdout)


class Dashboard(unittest.TestCase):
    setUp = Workflow.setUp
    git = Workflow.git
    task = Workflow.task
    def test_dashboard_token_host_and_no_write_boundary(self):
        import urllib.request
        import urllib.error
        server, token = make_server(self.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        url = "http://127.0.0.1:" + str(server.server_port)
        try:
            with self.assertRaises(urllib.error.HTTPError) as e: urllib.request.urlopen(url + "/api/state")
            self.assertEqual(e.exception.code, 401)
            request = urllib.request.Request(url + "/api/state", headers={"X-Squadra-Token": token})
            with urllib.request.urlopen(request) as result:
                state = json.load(result); self.assertEqual(state["workspace"]["name"], "Synthetic team")
                self.assertEqual(result.headers["Cache-Control"], "no-store")
            bad_host = urllib.request.Request(url + "/api/state", headers={"Host": "attacker.invalid", "X-Squadra-Token": token})
            with self.assertRaises(urllib.error.HTTPError) as e: urllib.request.urlopen(bad_host)
            self.assertEqual(e.exception.code, 403)
            post = urllib.request.Request(url + "/api/state", data=b"{}", headers={"X-Squadra-Token": token})
            with self.assertRaises(urllib.error.HTTPError) as e: urllib.request.urlopen(post)
            self.assertEqual(e.exception.code, 405)
        finally:
            server.shutdown(); server.server_close(); thread.join()


class Installation(unittest.TestCase):
    setUp = Workflow.setUp
    git = Workflow.git
    task = Workflow.task

    def candidate(self):
        (self.repo / 'SKILL.md').write_text('---\nname: squadra\ndescription: Synthetic\n---\n')
        (self.repo / 'scripts').mkdir()
        (self.repo / 'scripts/squadra.py').write_text('print("synthetic installed")\n')
        self.git('add', '.'); self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'candidate')
        for name in ['.codex', '.claude']:
            dest = self.home / name / 'skills/squadra'; dest.mkdir(parents=True)
            (dest / 'OLD').write_text(name)

    def test_installs_both_clients_same_version_preserving_old(self):
        from install import install
        self.candidate()
        result = install(self.repo, self.home, self.base / 'backup')
        for name in ['.codex', '.claude']:
            skill = self.home / name / 'skills/squadra'
            self.assertEqual(skill.resolve(), Path(result['version']))
        self.assertTrue((self.base / 'backup/claude-squadra/OLD').is_file())
        q = subprocess.run([str(self.home / '.local/bin/squadra')], capture_output=True, text=True, env=self.env)
        self.assertEqual(q.returncode, 0); self.assertIn('synthetic installed', q.stdout)
        self.assertTrue((self.base / 'backup/INSTALL.json').is_file())

    def test_install_failure_restores_original_trees(self):
        from install import install
        self.candidate()
        with patch.object(Path, 'symlink_to', side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError): install(self.repo, self.home, self.base / 'backup')
        for name in ['.codex', '.claude']:
            self.assertEqual((self.home / name / 'skills/squadra/OLD').read_text(), name)

    def test_dirty_source_cannot_be_installed(self):
        from install import install
        self.candidate(); (self.repo / 'code.py').write_text('unreviewed')
        with self.assertRaisesRegex(Error, 'clean candidate'):
            install(self.repo, self.home, self.base / 'backup')


if __name__ == "__main__":
    unittest.main()
