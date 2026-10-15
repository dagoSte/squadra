"""The small-change route keeps the original base, evidence and independent reader."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import unittest
import urllib.request
from unittest.mock import patch

import test_squadra as support
from registry import Error, apply, canonical, initial, snapshot, validate, requirement_matrix
from light_route import POLICY, contract, measure, protected
from runtime import execute
from squadra import bindings, deliver, parser


class LightRoute(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task
    ack = support.Workflow.ack
    cli = support.Workflow.cli

    def register(self, name="L", ack=True, **overrides):
        data = {"id": name, "title": "Small descriptive change", "owner": "A", "reviewer": "R",
                "repo": str(self.repo), "base": snapshot(self.repo)["head"],
                "scope": self.store.artifact(self.scope), "proof": [sys.executable, "-c", "print('proof')"],
                "requirements": [{"id": "R1", "text": "Preserve behavior"}], "depends": [],
                "full_gate": False, "expected": None, "route": "light", "light_policy": dict(POLICY)}
        data.update(overrides); data["contract"] = contract(data)
        self.store.batch([("task", "C", data, None), ("message", "C", {
            "id": "assign-" + name, "to": "A", "task": name, "body": "Read scope"}, None)])
        if ack:
            self.ack(name)
        return self.store.state()["tasks"][name]

    def change(self, lines=1, name="README.md"):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(f"Line {i}\n" for i in range(lines)))

    def proof(self, name="L", covers=None):
        t = self.store.state()["tasks"][name]
        return execute(self.store, "A", name, argv=t["proof"], covers=["R1"] if covers is None else covers)

    def review(self, verdict="accepted"):
        handoff = deliver(self.store, "A", "L")["handoff"]
        self.store.append("ack", "R", {"message": handoff})
        self.assertEqual(self.cli("review", "--actor", "R", "--task", "L", "--verdict", verdict,
                                  "--report", str(self.report), "--covers", "R1"), 0)

    def promote(self, actor="C", **overrides):
        old = self.store.state()["tasks"]["L"]
        data = {"task": "L", "route": "full", "scope": self.store.artifact(self.scope),
                "proof": [sys.executable, "-c", "print('full proof')"], "reason": "Needs full read",
                "previous_contract": old["contract"], "expected": None, "full_gate": False}
        data.update(overrides); data["contract"] = contract({**old, **data})
        self.store.append("route", actor, data)

    def test_added_plus_deleted_limits_below_equal_above(self):
        t = self.register()
        for count in (79, 80):
            self.change(count)
            self.assertEqual(measure(t)["lines"], count)
        self.change(81)
        with self.assertRaisesRegex(Error, "light_lines_exceeded"):
            measure(t)

    def test_file_limits_below_equal_above_including_empty_files(self):
        t = self.register()
        for count in range(1, 6):
            self.change(0, f"tests/file{count}.py")
            if count <= 4:
                self.assertEqual(measure(t)["files"], count)
            else:
                with self.assertRaisesRegex(Error, "light_files_exceeded"):
                    measure(t)

    def test_changes_are_cumulative_after_commit_and_include_staged_untracked_and_deletion(self):
        self.change(40); self.git("add", "README.md"); self.git("commit", "-qm", "text base")
        t = self.register()
        self.change(20); self.git("add", "README.md"); self.git("commit", "-qm", "intermediate")
        self.change(21); self.git("add", "README.md")
        self.change(1, "tests/new.py")
        measured = measure(t)
        self.assertEqual(measured["lines"], 20)
        self.assertEqual(measured["base"], t["base"])
        (self.repo / "README.md").unlink()
        self.assertEqual(measure(t)["lines"], 41)

    def test_original_base_not_current_head(self):
        t = self.register(); self.change(80); self.git("add", "README.md"); self.git("commit", "-qm", "first")
        self.change(81)
        with self.assertRaisesRegex(Error, "light_lines_exceeded"):
            measure(t)

    def test_replacement_counts_both_sides_whitespace_and_no_final_newline(self):
        self.change(); self.git("add", "README.md"); self.git("commit", "-qm", "text")
        t = self.register(); (self.repo / "README.md").write_bytes(b" Line 0")
        self.assertEqual(measure(t)["lines"], 2)

    def test_new_file_bytes_count_alongside_a_changed_tracked_file(self):
        self.change(); self.git("add", "README.md"); self.git("commit", "-qm", "text")
        t = self.register(); self.change(2); self.change(3, "tests/new.py")
        measured = measure(t)
        self.assertEqual(measured["lines"], 4)
        self.assertEqual(measured["files"], 2)

    def test_protected_profile_is_closed_and_matches_case_and_unicode(self):
        for name in ("scripts/registry.py", "scripts/anything.py", "scripts/release.sh", "plugins/x/a",
                     "tests/AGENTS.md", "docs/CLAUDE.md", "SKILL.md", "references/work.md",
                     "references/plugin-authoring.md", "docs/plugin-authoring.it.md", "package.json",
                     "a.lock", ".github/x", ".codex/config.toml", "unknown.py", "assets/logo.png",
                     "SCRIPTS/Registry.py", "Ｓcripts/x.py"):
            with self.subTest(name=name):
                self.assertTrue(protected(name))
        for name in ("README.md", "README.it.md", "docs/deep/example.md", "references/notes.md",
                     "tests/test_x.py", "assets/dashboard.html"):
            self.assertFalse(protected(name), name)

    def test_protected_delete_or_rename_never_hides_the_source(self):
        t = self.register()
        (self.repo / "code.py").rename(self.repo / "README.md")
        with self.assertRaisesRegex(Error, "light_protected_path"):
            measure(t)

    def test_binary_invalid_utf8_symlink_modes_and_unknown_base_refuse(self):
        self.change(); self.git("add", "README.md"); self.git("commit", "-qm", "base")
        t = self.register(); p = self.repo / "README.md"
        for data in (b"a\0b", b"\xff"):
            p.write_bytes(data)
            with self.assertRaisesRegex(Error, "light_unmeasurable"):
                measure(t)
        p.write_text("Line 0\n"); p.chmod(0o755)
        with self.assertRaisesRegex(Error, "light_unmeasurable"):
            measure(t)
        p.unlink(); p.symlink_to(self.scope)
        with self.assertRaisesRegex(Error, "light_unmeasurable"):
            measure(t)
        p.unlink(); self.change(2)
        t["base"] = "0" * 40; t["contract"] = contract(t)
        with self.assertRaisesRegex(Error, "light_unmeasurable"):
            measure(t)

    def test_case_and_nfc_alias_collision_refused(self):
        t = self.register(); self.change(1, "docs/é.md")
        actual = snapshot(self.repo)
        alias = deepcopy(actual["files"][-1]); alias["path"] = "docs/e\u0301.md"
        actual["files"].append(alias)
        with patch("light_route.snapshot", return_value=actual):
            with self.assertRaisesRegex(Error, "light_unmeasurable.*alias"):
                measure(t)

    def test_source_drift_while_measuring_refused(self):
        t = self.register(); self.change()
        original = snapshot(self.repo); changed = {**original, "sha256": "different"}
        with patch("light_route.snapshot", side_effect=[original, changed]):
            with self.assertRaisesRegex(Error, "light_unmeasurable.*unstable"):
                measure(t)

    def test_empty_and_dirty_registration_refused(self):
        t = self.register()
        with self.assertRaisesRegex(Error, "light_empty_change"):
            measure(t)
        self.change()
        with self.assertRaisesRegex(Error, "light_contract_required"):
            self.register("dirty")

    def test_registration_requires_exact_builtin_contract_reader_requirements_and_proof(self):
        for field, value in (("reviewer", None), ("requirements", []), ("proof", None), ("full_gate", True)):
            with self.subTest(field=field), self.assertRaisesRegex(Error, "light_contract_required"):
                self.register(**{field: value})
        with self.assertRaisesRegex(Error, "independent"):
            self.register(reviewer="A-alias")
        with self.assertRaisesRegex(Error, "light_policy_unsupported"):
            self.register(light_policy={**POLICY, "lines": 100})

    def test_no_proof_process_or_handoff_when_outside_limits(self):
        t = self.register(); self.change(81)
        real_popen, launched = subprocess.Popen, []
        def observe(argv, *args, **kwargs):
            if argv == t["proof"]:
                launched.append(argv)
            return real_popen(argv, *args, **kwargs)
        with patch("runtime.subprocess.Popen", side_effect=observe):
            with self.assertRaisesRegex(Error, "light_lines_exceeded"):
                self.proof()
            self.assertEqual(launched, [])
        with self.assertRaisesRegex(Error, "light_lines_exceeded"):
            deliver(self.store, "A", "L")
        self.assertFalse(self.store.state()["runs"])
        self.assertNotIn("delivery", self.store.state()["tasks"]["L"])

    def test_store_run_start_recomputes_instead_of_trusting_supplied_counts(self):
        t = self.register(); self.change(); before = snapshot(self.repo); measured = measure(t)
        self.change(81)
        record = {"id": "direct", "task": "L", "mode": "proof", "stage": "code", "before": snapshot(self.repo),
                  "contract": t["contract"], "argv": t["proof"], "expected_rc": 0, "covers": ["R1"],
                  "gate": False, "light": measured}
        with self.assertRaisesRegex(Error, "light_lines_exceeded"):
            self.store.append("run.start", "A", record)
        self.change(); record.update(before=before, light={**measured, "lines": 0})
        with self.assertRaisesRegex(Error, "measurement"):
            self.store.append("run.start", "A", record)

    def test_targeted_positive_lifecycle_packet_and_coverage(self):
        self.register(); self.change(); run = self.proof()
        self.assertEqual(run["status"], "passed")
        t = self.store.state()["tasks"]["L"]
        self.assertEqual(t["proofs"][-1]["light"]["lines"], 1)
        self.review()
        t = self.store.state()["tasks"]["L"]
        self.assertEqual(t["delivery"]["packet_data"]["light"]["policy"], POLICY)
        self.assertTrue(all(r["reviewed"] and r["proofs"] for r in requirement_matrix(t)))
        self.assertEqual(self.cli("close", "--actor", "C", "--task", "L"), 0)
        self.assertEqual(self.store.state()["tasks"]["L"]["status"], "done")

    def test_store_close_remeasures_after_review_and_refuses_pause(self):
        self.register(); self.change(); self.proof(); self.review()
        self.store.append("pause", "C", {"reason": "owner"})
        with self.assertRaisesRegex(Error, "paused"):
            self.store.append("close", "C", {"task": "L"})
        self.store.append("resume", "C", {"reason": "owner"}); self.change(81)
        with self.assertRaisesRegex(Error, "light_lines_exceeded"):
            self.store.append("close", "C", {"task": "L"})
        with self.assertRaises(Error):
            bindings(self.store, "L")

    def test_missing_ack_wrong_argv_covers_alias_and_handoff_refused(self):
        self.register(ack=False); self.change()
        with self.assertRaisesRegex(Error, "ACK"):
            self.proof()
        self.ack("L")
        with self.assertRaisesRegex(Error, "command differs"):
            execute(self.store, "A", "L", argv=[sys.executable, "-c", "print('wrong')"])
        self.proof(covers=[])
        with self.assertRaisesRegex(Error, "native.*proof"):
            deliver(self.store, "A", "L")
        self.proof(); result = deliver(self.store, "A", "L")
        t = self.store.state()["tasks"]["L"]
        data = {"task": "L", "stage": "code", "binding": t["candidate"]["sha256"], "contract": t["contract"],
                "verdict": "accepted", "covers": ["R1"], "report": self.store.artifact(self.report)}
        with self.assertRaisesRegex(Error, "ACK"):
            self.store.append("review", "R", data)
        self.store.append("ack", "R", {"message": result["handoff"]})
        with self.assertRaisesRegex(Error, "Author"):
            self.store.append("review", "A-alias", data)
        data["covers"] = []
        with self.assertRaisesRegex(Error, "cover every"):
            self.store.append("review", "R", data)

    def test_plan_second_verdict_and_read_start_refused_without_budget_reset(self):
        self.register(); self.change()
        with self.assertRaisesRegex(Error, "light_plan_requires_full"):
            self.store.append("plan", "A", {"task": "L", "artifact": self.store.artifact(self.scope)})
        self.proof(); self.review("changes-required")
        t = self.store.state()["tasks"]["L"]
        with self.assertRaisesRegex(Error, "light_read_round_exhausted"):
            self.store.append("review", "R", {"task": "L", "stage": "code", "binding": t["candidate"]["sha256"],
                "contract": t["contract"], "verdict": "accepted", "covers": ["R1"]})
        with self.assertRaisesRegex(Error, "light_read_round_exhausted"):
            self.proof()
        with self.assertRaisesRegex(Error, "light_read_round_exhausted"):
            self.store.append("run.start", "R", {"id": "read", "task": "L", "mode": "read", "stage": "code",
                "before": snapshot(self.repo), "contract": t["contract"], "covers": []})

    def test_only_one_automatic_read_start_even_before_verdict(self):
        self.register(); self.change(); self.proof()
        handoff = deliver(self.store, "A", "L")["handoff"]; self.store.append("ack", "R", {"message": handoff})
        t = self.store.state()["tasks"]["L"]
        data = {"id": "read1", "task": "L", "mode": "read", "stage": "code", "before": snapshot(self.repo),
                "contract": t["contract"], "covers": []}
        self.store.append("run.start", "R", data)
        self.store.append("run.end", "R", {"id": "read1", "status": "failed", "native_rc": 1})
        with self.assertRaisesRegex(Error, "light_read_round_exhausted"):
            self.store.append("run.start", "R", {**data, "id": "read2"})

    def test_promotion_keeps_history_and_rejects_old_proof_and_read(self):
        self.register(); self.change(); self.proof(); self.review("changes-required")
        old = self.store.state()["tasks"]["L"]
        with self.assertRaisesRegex(Error, "Coordinator"):
            self.promote("A")
        self.promote(); new = self.store.state()["tasks"]["L"]
        for key in ("base", "authors", "proofs", "rounds", "reviews"):
            self.assertEqual(old[key], new[key])
        self.assertNotEqual(old["contract"], new["contract"])
        with self.assertRaisesRegex(Error, "passing proof"):
            deliver(self.store, "A", "L")
        self.assertFalse(any(r["proofs"] or r["reviewed"] for r in requirement_matrix(new)))
        with self.assertRaisesRegex(Error, "one-way"):
            self.promote()
        self.proof(); self.review(); self.assertEqual(self.cli("close", "--actor", "C", "--task", "L"), 0)

    def test_store_success_receipt_cannot_change_contract_or_native_result(self):
        t = self.register(); self.change(); before = snapshot(self.repo)
        self.store.append("run.start", "A", {"id": "p", "task": "L", "mode": "proof", "stage": "code",
            "before": before, "contract": t["contract"], "argv": t["proof"], "expected_rc": 0,
            "light": measure(t), "covers": ["R1"]})
        for extra in ({"native_rc": 1}, {"contract": "old"}, {"covers": ["R1"]}, {"after": {}}):
            with self.subTest(extra=extra), self.assertRaises(Error):
                self.store.append("run.end", "A", {"id": "p", "status": "passed", "native_rc": 0, "after": before, **extra})
        self.store.append("run.end", "A", {"id": "p", "status": "passed", "native_rc": 0, "after": before})

    def test_direct_delivery_cannot_reuse_a_small_measurement_after_growth(self):
        self.register(); self.change(); self.proof(); deliver(self.store, "A", "L")
        data = self.store.state()["tasks"]["L"]["delivery"]
        self.change(81)
        with self.assertRaisesRegex(Error, "light_lines_exceeded"):
            self.store.append("deliver", "A", data)

    def test_store_close_does_not_trust_earlier_eligibility(self):
        self.register(); self.change(); self.proof(); self.review(); self.change(81)
        with self.assertRaisesRegex(Error, "light_lines_exceeded"):
            self.store.append("close", "C", {"task": "L"})

    def test_current_hash_cannot_replace_the_immutable_contract(self):
        t = self.register(); self.change(); original = measure(t)
        self.assertEqual(original["contract"], t["contract"])
        for field, value in (("base", snapshot(self.repo)["sha256"]), ("proof", ["different"]),
                             ("requirements", []), ("scope", {})):
            with self.subTest(field=field), self.assertRaisesRegex(Error, "light_contract_required"):
                measure({**t, field: value})

    def test_direct_review_cannot_certify_drift_or_a_changed_contract(self):
        self.register(); self.change(); self.proof()
        handoff = deliver(self.store, "A", "L")["handoff"]; self.store.append("ack", "R", {"message": handoff})
        t = self.store.state()["tasks"]["L"]
        data = {"task": "L", "stage": "code", "binding": t["candidate"]["sha256"], "contract": t["contract"],
                "verdict": "accepted", "covers": ["R1"], "report": self.store.artifact(self.report)}
        with self.assertRaisesRegex(Error, "contract"):
            self.store.append("review", "R", {**data, "contract": "not the contract"})
        self.change(2)
        with self.assertRaisesRegex(Error, "drift"):
            self.store.append("review", "R", data)

    def test_dependency_and_gate_reservation_remain_enforced(self):
        self.register(depends=["T"]); self.change()
        with self.assertRaisesRegex(Error, "Dependency"):
            self.proof()
        self.promote(full_gate=True)
        self.ack("T")
        state = self.store.state(); state["tasks"]["T"]["status"] = "done"
        t = state["tasks"]["L"]
        record = {"id": "gate", "task": "L", "mode": "proof", "stage": "code", "argv": t["proof"],
                  "before": snapshot(self.repo), "contract": t["contract"], "expected_rc": 0, "gate": True}
        with self.assertRaisesRegex(Error, "gate resource"):
            validate(state, "run.start", "A", record)

    def test_equivalent_task_does_not_reset_spent_review_budget(self):
        self.register(); self.change(); self.proof(); self.review("changes-required")
        (self.repo / "README.md").unlink()
        with self.assertRaisesRegex(Error, "equivalent task"):
            self.register("alias")

    def test_raw_diff_ignores_local_textconv_and_external_driver(self):
        self.change(); (self.repo / ".gitattributes").write_text("*.md diff=opaque\n")
        self.git("add", "."); self.git("commit", "-qm", "attributes")
        self.git("config", "diff.opaque.textconv", "false")
        self.git("config", "diff.external", "false")
        t = self.register(); self.change(81)
        self.assertEqual(measure(t)["lines"], 80)
        self.change(82)
        with self.assertRaisesRegex(Error, "light_lines_exceeded"):
            measure(t)

    def test_base_submodule_requires_full_route(self):
        oid = snapshot(self.repo)["head"]
        self.git("update-index", "--add", "--cacheinfo", "160000," + oid + ",vendor")
        self.git("commit", "-qm", "gitlink")
        data = {"base": oid, "repo": str(self.repo)}
        from light_route import base_files
        with self.assertRaisesRegex(Error, "light_unmeasurable"):
            base_files(data["repo"], snapshot(self.repo)["head"])

    def test_old_bridge_reads_light_registry_without_loading_write_helper(self):
        self.register(); self.change(); self.proof(); self.review()
        self.promote()
        recorded = self.store.events()
        # D8681: the old reader keeps legacy support but must refuse format-2 history.
        old = self.base / "old-bridge"; old.mkdir()
        root = Path(__file__).resolve().parents[1]
        fixture = root / "tests/fixtures/squadra-3.6.0"
        hashes = {"registry.py": "42146e9bf8adf88a46975b485ff8aa3bf192060b2c05a14dc586925e34cf51db",
                  "squadra.py": "df07e52470646dfcbad4ec19c5c7743dfc505bdbfc8b7d90c017b40b00877c27"}
        for name, expected in hashes.items():
            self.assertEqual(hashlib.sha256((fixture / name).read_bytes()).hexdigest(), expected, name)
        frozen = (fixture / "registry.py").read_bytes()
        (old / "registry.py").write_bytes(frozen)
        new = root / "plugins/playwright/bridge"
        def reader(resolve):
            return (
                "import sys, json; sys.path.insert(0, sys.argv[1]); from registry import Store; "
                "s=Store(sys.argv[2],read_only=True); t=" + resolve + "['tasks']['L']; "
                "print(json.dumps({'base':t['base'],'route':t['route'],'helper': 'light_route' in sys.modules})); s.close()")
        for folder in (old, new):
            script = reader("s.state()" if folder == old else "s.state(resolve=False)")
            result = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(folder), str(self.root)],
                                    capture_output=True, text=True, timeout=15)
            if folder == old:
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("integrity", result.stderr.lower())
                continue
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertFalse(data["helper"])
            self.assertEqual(data["base"], self.store.state()["tasks"]["L"]["base"])
            self.assertEqual(data["route"], "full")

        legacy = self.base / "legacy-reader-record"
        legacy.mkdir(); (legacy / "artifacts").mkdir()
        scope = self.store.state()["tasks"]["L"]["scope"]["sha256"]
        shutil.copy2(self.root / "artifacts" / scope, legacy / "artifacts" / scope)
        setup_events = [e for e in self.store.events() if e["kind"] in {"init", "agent"}
                        or e["kind"] == "task" and e["data"]["id"] == "L"]
        create = (
            "import sys,json; sys.path.insert(0,sys.argv[1]); from registry import Store; "
            "s=Store(sys.argv[2],create=True); "
            "[s.append(e['kind'],e['actor'],e['data'],e['id']) for e in json.load(sys.stdin)]; "
            "s.project(); s.close()")
        result = subprocess.run([sys.executable, "-I", "-B", "-c", create, str(old), str(legacy)],
                                input=json.dumps(setup_events), capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        legacy_before = (legacy / "events.jsonl").read_bytes()
        result = subprocess.run([sys.executable, "-I", "-B", "-c", reader("s.state()"), str(old), str(legacy)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "base": self.store.state()["tasks"]["L"]["base"], "route": "light", "helper": False})
        self.assertEqual((legacy / "events.jsonl").read_bytes(), legacy_before)

        old_cli = self.base / "old-cli"; old_cli.mkdir()
        shutil.copytree(fixture, old_cli / "scripts")
        copy = self.base / "old-reader-record"
        self.store.checkpoint(copy)
        before = (copy / "events.jsonl").read_bytes()
        state_before = (copy / "state.json").read_bytes()
        for command in ("status", "doctor"):
            result = subprocess.run([sys.executable, "-B", str(old_cli / "scripts/squadra.py"),
                                    "--root", str(copy), command], env=self.env,
                                    capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("integrity", (result.stdout + result.stderr).lower())
            self.assertNotIn("Unknown event kind", result.stderr)
            self.assertEqual((copy / "events.jsonl").read_bytes(), before)
            self.assertEqual((copy / "state.json").read_bytes(), state_before)
            positive = subprocess.run([sys.executable, "-B", str(old_cli / "scripts/squadra.py"),
                                       "--root", str(legacy), command], env=self.env,
                                      capture_output=True, text=True, timeout=30)
            self.assertEqual(positive.returncode, 0, positive.stdout + positive.stderr)
            self.assertNotIn("Unknown event kind", positive.stderr)
            if command == "doctor":
                self.assertEqual(json.loads(positive.stdout)["integrity"], "passed")
            self.assertEqual((legacy / "events.jsonl").read_bytes(), legacy_before)
        self.assertEqual(json.loads(state_before)["tasks"]["L"]["route"], "full")
        self.assertEqual(self.store.events(), recorded)
        self.assertEqual((old / "registry.py").read_bytes(), frozen)
        for name, expected in hashes.items():
            self.assertEqual(hashlib.sha256((fixture / name).read_bytes()).hexdigest(), expected)

    def test_promotion_has_bilingual_history_in_the_served_page(self):
        from dashboard_test_support import make_server
        self.register(); self.promote()
        server, token = make_server(self.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/api/state",
                                             headers={"X-Squadra-Token": token})
            with urllib.request.urlopen(request) as response:
                state = json.loads(response.read())
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        self.assertEqual(state["tasks"]["L"]["route_history"][0]["route"], "full")
        self.assertNotIn("route", state["comms"]["unknown_kinds"])
        fixture = self.base / "page-state.json"; fixture.write_text(json.dumps(state))
        root = Path(__file__).resolve().parents[1]
        node = shutil.which("node"); self.assertIsNotNone(node, "Node required; no skipped page proof")
        result = subprocess.run([node, str(root / "tests/light_route_contract.js"),
                                 str(root / "assets/dashboard.html"), str(fixture)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("light route page passed: it en", result.stdout)

    def test_old_tasks_replay_unchanged_and_future_policy_is_readable_but_refused(self):
        events = self.store.events(); state = initial()
        for event in events:
            apply(state, event)
        self.assertNotIn("route", state["tasks"]["T"])
        before = canonical(state)
        self.store.project()
        other = self.store.state(); other.pop("root")
        self.assertEqual(before, canonical(other))
        t = self.register(); t["light_policy"] = {**POLICY, "version": "light-v99"}; t["contract"] = contract(t)
        replay = deepcopy(self.store.state())
        apply(replay, {"kind": "task", "data": t, "actor": "C", "utc": "2026-01-01T00:00:00Z"})
        self.assertEqual(replay["tasks"]["L"]["light_policy"]["version"], "light-v99")
        replay["tasks"]["L"]["status"] = "ready"
        with self.assertRaisesRegex(Error, "light_policy_unsupported"):
            validate(replay, "start", "A", {"task": "L"})

    def test_cli_declaration_promotion_and_documented_command_table(self):
        proof = self.base / "proof.json"; proof.write_text(json.dumps([sys.executable, "-c", "print('scoped')"]))
        req = self.base / "requirements.json"; req.write_text(json.dumps([{"id": "R1", "text": "Keep behavior"}]))
        self.assertEqual(self.cli("task", "--actor", "C", "--id", "L", "--title", "Small", "--owner", "A",
            "--reviewer", "R", "--repo", str(self.repo), "--scope-file", str(self.scope), "--proof-file", str(proof),
            "--requirements-file", str(req), "--route", "light"), 0)
        self.ack("L")
        self.assertEqual(self.cli("route", "--actor", "C", "--task", "L", "--full", "--scope-file", str(self.scope),
            "--proof-file", str(proof), "--reason", "Broader impact", "--full-gate"), 0)
        self.assertTrue(self.store.state()["tasks"]["L"]["full_gate"])
        cli = parser(); choices = next(a.choices for a in cli._actions if hasattr(a, "choices") and isinstance(a.choices, dict))
        root = Path(__file__).resolve().parents[1]
        for name in ("README.md", "README.it.md"):
            text = (root / name).read_text()
            self.assertIn(str(len(choices)) + (" subcommands" if name == "README.md" else " sottocomandi"), text)
            self.assertIn("`route`", text)


if __name__ == "__main__":
    unittest.main()
