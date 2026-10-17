"""A coordinator scrub decision lets a leaked artifact file be removed without breaking the full audit."""
import contextlib
import copy
import http.client
import io
import json
from pathlib import Path
import threading
import time
import unittest

import test_squadra as support
from dashboard_test_support import make_server
from recovery import restore_check
from registry import Error, Store, apply, initial, snapshot
from squadra import main

ROOT = Path(__file__).resolve().parents[1]
LEAK = b"export SESSION_TOKEN=synthetic-leak-0000\n"


def decoded(text):
    """The JSON object a command prints before its table."""
    return json.JSONDecoder().raw_decode(text)[0]


class ArtifactScrub(unittest.TestCase):
    git = support.Workflow.git
    task = support.Workflow.task
    ack = support.Workflow.ack
    proof = support.Workflow.proof

    def setUp(self):
        support.Workflow.setUp(self)
        leak = self.base / "scope-leak.md"
        leak.write_bytes(LEAK)
        self.artifact = self.store.artifact(leak)
        self.sha = self.artifact["sha256"]
        self.path = self.root / "artifacts" / self.sha
        data = {"id": "LEAK", "title": "Task whose scope captured a token", "owner": "A", "repo": str(self.repo),
                "base": snapshot(self.repo)["head"], "scope": self.artifact, "proof": None, "expected": None,
                "depends": [], "full_gate": False}
        self.store.batch([("task", "C", data, None), ("message", "C", {"id": "assign-LEAK", "to": "A",
                          "task": "LEAK", "body": "Scoped work"}, None)])

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(["--root", str(self.root), *args])
        return rc, out.getvalue(), err.getvalue()

    def scrub(self, actor="C", sha=None):
        return self.run_cli("decision", "--actor", actor, "--reason", "Leaked session token",
                            "--scrub-artifact", sha or self.sha)

    def record_scrub(self):
        """Record the scrub through the Store, the path every writer shares; return its decision name."""
        event = self.store.append("decision", "C", {"reason": "Leaked session token", "tasks": ["LEAK"],
                                                    "scrub": {"sha256": self.sha, "tasks": ["LEAK"]}})
        return "dec-e" + str(next(e["seq"] for e in self.events() if e["id"] == event))

    def audit(self, root=None):
        store = Store(root or self.root)
        try:
            return store.audit()
        finally:
            store.close()

    def events(self):
        store = Store(self.root, read_only=True)
        try:
            return store.events()
        finally:
            store.close()

    def scrub_decisions(self):
        return [e for e in self.events() if e["kind"] == "decision" and "scrub" in e["data"]]

    def expected_report(self, decision, file="absent"):
        return [{"sha256": self.sha, "names": ["scope-leak.md"], "decision": decision, "tasks": ["LEAK"], "file": file}]

    def test_a_missing_artifact_without_a_scrub_decision_fails_the_full_audit(self):
        self.path.unlink()
        with self.assertRaisesRegex(Error, "Missing/changed artifact scope-leak.md"):
            self.audit()
        rc, _, err = self.run_cli("doctor")
        self.assertEqual(rc, 1)
        self.assertIn("Missing/changed artifact scope-leak.md", err)

    def test_scrub_decision_lets_audit_doctor_export_and_checkpoint_pass_and_report_it(self):
        self.path.unlink()
        with self.assertRaisesRegex(Error, "Missing/changed artifact scope-leak.md"):
            self.audit()
        rc, out, err = self.scrub()
        self.assertEqual(rc, 0, err)
        result = decoded(out)
        self.assertEqual(result["scrub"], {"sha256": self.sha, "tasks": ["LEAK"]})
        self.assertEqual(result["file"], "absent")
        [event] = self.scrub_decisions()
        self.assertEqual(event["kind"], "decision")
        self.assertEqual(event["actor"], "C")
        self.assertEqual(event["data"]["tasks"], ["LEAK"])
        self.assertEqual(result["decision"], "dec-e" + str(event["seq"]))
        report = self.expected_report(result["decision"])
        self.assertEqual(self.audit()["scrubbed"], report)
        rc, out, err = self.run_cli("doctor")
        self.assertEqual(rc, 0, err)
        doctor = decoded(out)
        self.assertEqual(doctor["integrity"], "passed")
        self.assertEqual(doctor["scrubbed"], report)
        self.assertIn("Published Squadra 3.10.1 fails its full audit", doctor["scrubbed_note"])
        rc, out, err = self.run_cli("export")
        self.assertEqual(rc, 0, err)
        self.assertEqual(decoded(out)["scrubbed"], report)
        self.assertTrue((self.root / "state.json").is_file())
        copy_root = self.base / "checkpoint"
        rc, out, err = self.run_cli("checkpoint", "--to", str(copy_root))
        self.assertEqual(rc, 0, err)
        self.assertEqual(decoded(out)["scrubbed"], report)
        self.assertFalse((copy_root / "artifacts" / self.sha).exists())
        self.assertEqual(self.audit(copy_root)["scrubbed"], report)

    def test_a_scrub_recorded_through_the_store_lets_the_full_audit_pass(self):
        self.path.unlink()
        decision = self.record_scrub()
        self.assertEqual(self.audit()["scrubbed"], self.expected_report(decision))

    def test_save_and_restore_check_pass_on_a_scrubbed_registry_and_report_it(self):
        self.path.unlink()
        report = self.expected_report(self.record_scrub())
        saved = self.base / "saved"
        rc, out, err = self.run_cli("save", "--actor", "C", "--to", str(saved), "--temporary")
        self.assertEqual(rc, 0, err)
        self.assertEqual(decoded(out)["scrubbed"], report)
        manifest = json.loads((saved / "MANIFEST.json").read_text())
        self.assertEqual(set(manifest["registry"]), {"event_count", "event_head"})
        self.assertEqual(manifest["scrubbed"], report)
        restored = restore_check(saved, self.base / "restored")
        self.assertEqual(restored["status"], "passed")
        self.assertEqual(restored["scrubbed"], report)

    def test_scrubbing_before_deleting_reports_the_file_until_it_is_gone(self):
        rc, out, err = self.scrub()
        self.assertEqual(rc, 0, err)
        result = decoded(out)
        self.assertEqual(result["file"], "present")
        self.assertEqual(result["next"], "Delete " + str(self.path) + " to complete the removal")
        self.assertEqual(self.audit()["scrubbed"], self.expected_report(result["decision"], "present"))
        self.path.unlink()
        self.assertEqual(self.audit()["scrubbed"], self.expected_report(result["decision"]))

    def test_a_changed_artifact_still_fails_even_when_scrubbed(self):
        rc, _, err = self.scrub()
        self.assertEqual(rc, 0, err)
        self.path.chmod(0o600)
        self.path.write_bytes(b"replaced bytes\n")
        with self.assertRaisesRegex(Error, "Missing/changed artifact scope-leak.md"):
            self.audit()
        rc, _, err = self.run_cli("doctor")
        self.assertEqual(rc, 1)
        self.assertIn("Missing/changed artifact scope-leak.md", err)

    def test_task_bindings_accept_only_the_scrubbed_absence(self):
        self.path.unlink()
        task = self.store.state()["tasks"]["LEAK"]
        with self.assertRaisesRegex(Error, "Missing/changed artifact scope-leak.md"):
            self.store.verify_artifacts(task)
        rc, _, err = self.scrub()
        self.assertEqual(rc, 0, err)
        checker = Store(self.root)
        try:
            self.assertTrue(checker.verify_artifacts(checker.state()["tasks"]["LEAK"]))
            other = checker.state()["tasks"]["T"]
            (self.root / "artifacts" / other["scope"]["sha256"]).unlink()
            with self.assertRaisesRegex(Error, "Missing/changed artifact scope.md"):
                checker.verify_artifacts(other)
        finally:
            checker.close()

    def test_a_non_coordinator_is_refused(self):
        self.path.unlink()
        for actor in ("A", "R"):
            rc, _, err = self.scrub(actor=actor)
            self.assertEqual(rc, 1)
            self.assertIn("Coordinator role required", err)
        self.assertEqual(self.scrub_decisions(), [])
        with self.assertRaisesRegex(Error, "Missing/changed artifact scope-leak.md"):
            self.audit()

    def test_an_unknown_or_malformed_digest_is_refused(self):
        rc, _, err = self.scrub(sha="0" * 64)
        self.assertEqual(rc, 1)
        self.assertIn("Scrub needs a registered artifact", err)
        rc, _, err = self.scrub(sha="not-a-digest")
        self.assertEqual(rc, 1)
        self.assertIn("64 lowercase hex characters", err)
        self.assertEqual(self.scrub_decisions(), [])

    def test_a_scrub_records_exactly_the_referencing_tasks_and_only_once(self):
        for scrub, message in (({"sha256": self.sha, "tasks": []}, "Scrub tasks must be the tasks that reference the artifact: LEAK"),
                               ({"sha256": self.sha}, "Scrub needs one artifact SHA-256 digest"),
                               ({"sha256": self.sha, "tasks": ["NOPE"]}, "Scrub tasks must be recorded task IDs"),
                               ({"sha256": "0" * 64, "tasks": []}, "Scrub needs a registered artifact")):
            with self.subTest(scrub=scrub), self.assertRaisesRegex(Error, message):
                self.store.append("decision", "C", {"reason": "Leaked session token", "scrub": scrub})
        rc, _, err = self.scrub()
        self.assertEqual(rc, 0, err)
        rc, _, err = self.scrub()
        self.assertEqual(rc, 1)
        self.assertIn("Artifact already scrubbed by an earlier decision", err)
        self.assertEqual(len(self.scrub_decisions()), 1)

    def test_a_source_manifest_cannot_be_scrubbed(self):
        support.Workflow.delivered(self)
        [delivery] = [e for e in self.events() if e["kind"] == "deliver"]
        sha = delivery["data"]["snapshot"]["manifest"]["sha256"]
        rc, _, err = self.scrub(sha=sha)
        self.assertEqual(rc, 1)
        self.assertIn("A source manifest cannot be scrubbed", err)

    def cli(self, *args):
        return self.run_cli(*args)[0]

    def test_the_dashboard_background_audit_does_not_latch_and_reads_answer_plainly(self):
        self.path.unlink()
        with self.dashboard() as get:
            self.assertEqual(self.settle(get), (409, {"error": "registry_invalid"}))
        decision = self.record_scrub()
        with self.dashboard() as get:
            status, body = self.settle(get)
            self.assertEqual(status, 200, body)
            self.assertEqual(body["audit"], {"status": "complete", "scrubbed": self.expected_report(decision)})
            status, headers, raw = get("/api/artifact/" + self.sha)
            self.assertEqual(status, 200, raw)
            self.assertEqual(headers["X-Squadra-Artifact"], "scrubbed")
            self.assertEqual(raw.decode(), "Documento rimosso con la decisione " + decision + ": il file è stato tolto "
                             "di proposito dal registro, che ne conserva solo l'impronta " + self.sha + ".")
            status, _, raw = get("/api/state")
            self.assertEqual(status, 200, raw)

    @contextlib.contextmanager
    def dashboard(self):
        server, token = make_server(self.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def get(route):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            try:
                connection.request("GET", route, headers={"X-Squadra-Token": token})
                response = connection.getresponse()
                return response.status, dict(response.getheaders()), response.read()
            finally:
                connection.close()
        try:
            yield get
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)

    def settle(self, get):
        deadline = time.monotonic() + 20
        while True:
            status, _, raw = get("/api/state")
            body = json.loads(raw)
            if status != 200 or body.get("audit", {}).get("status") != "running":
                return status, body
            self.assertLess(time.monotonic(), deadline, "The background audit must finish")
            time.sleep(.02)


class PublishedCompatibility(unittest.TestCase):
    def test_a_scrub_is_an_ordinary_decision_that_published_kinds_and_replay_accept(self):
        pin = json.loads((ROOT / "plugins/memoranda/bridge/squadra_memories/pin.json").read_text())
        published = next(e for e in pin["accepted"] if e["label"] == "squadra 3.10.1")
        self.assertIn("decision", published["kinds"])
        state = initial()
        for event in ({"kind": "init", "actor": "system", "data": {"name": "N", "project": "/p"}},
                      {"kind": "agent", "actor": "C", "data": {"id": "C", "identity": "c", "role": "coordinator"}}):
            apply(state, {**event, "utc": "2026-10-16T00:00:00+00:00", "seq": state["event_count"] + 1})
        plain_state, scrubbed_state = copy.deepcopy(state), copy.deepcopy(state)
        decision = {"kind": "decision", "actor": "C", "utc": "2026-10-16T00:00:01+00:00", "seq": 3,
                    "data": {"reason": "Leaked session token"}}
        apply(plain_state, decision)
        apply(scrubbed_state, {**decision, "data": {**decision["data"], "scrub": {"sha256": "a" * 64, "tasks": []}}})
        self.assertEqual(scrubbed_state["decisions"][-1].pop("scrub"), {"sha256": "a" * 64, "tasks": []})
        self.assertEqual(scrubbed_state, plain_state)


if __name__ == "__main__":
    unittest.main()
