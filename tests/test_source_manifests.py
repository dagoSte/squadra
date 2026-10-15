import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import source_manifests as manifests


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode()


def sha(body):
    return hashlib.sha256(body).hexdigest()


def fingerprint(rows=None):
    rows = rows if rows is not None else [
        {"path": "bin/check", "kind": "file", "sha256": "a" * 64, "executable": True},
        {"path": "docs/é.md", "kind": "file", "sha256": "b" * 64, "executable": False},
        {"path": "old", "kind": "deleted"},
        {"path": "shortcut", "kind": "symlink", "sha256": "c" * 64},
    ]
    result = {"repo": "/synthetic/source", "head": "d" * 40, "files": rows}
    return {**result, "sha256": sha(canonical(result))}


class SourceManifests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "artifacts"
        self.folder.mkdir(mode=0o700)
        self.calls = []

    def writer(self, body, name, original):
        self.calls.append((body, name, original))
        path = self.folder / sha(body)
        if not path.exists():
            path.write_bytes(body)
            path.chmod(0o600)
        return {"sha256": sha(body), "bytes": len(body), "name": name, "original": original}

    def compact(self, value=None):
        return manifests.compact(fingerprint() if value is None else value, self.writer)

    def forged_reference(self, rows, *, raw=None):
        value = fingerprint(rows)
        body = canonical(rows) if raw is None else raw
        ref = self.writer(body, "source-files.json", "synthetic")
        return {"repo": value["repo"], "head": value["head"], "sha256": value["sha256"],
                "manifest": {**ref, "count": len(rows)}}

    def test_round_trip_preserves_original_digest_and_file_rows(self):
        original = fingerprint()
        frozen = copy.deepcopy(original)
        compact = self.compact(original)
        self.assertEqual(compact["sha256"], original["sha256"])
        self.assertNotEqual(compact["manifest"]["sha256"], original["sha256"])
        self.assertEqual(compact["manifest"]["count"], 4)
        self.assertNotIn("files", compact)
        self.assertEqual(manifests.resolve(compact, self.folder), original)
        self.assertEqual(original, frozen)
        self.assertEqual(stat.S_IMODE((self.folder / compact["manifest"]["sha256"]).stat().st_mode), 0o600)

    def test_empty_manifest_and_unicode_byte_order_are_valid(self):
        for rows in ([], [{"path": p, "kind": "deleted"}
                         for p in sorted(["é", "z", "Ω", "A"], key=os.fsencode)]):
            with self.subTest(rows=rows):
                original = fingerprint(rows)
                self.assertEqual(manifests.resolve(self.compact(original), self.folder), original)

    def test_same_file_list_deduplicates_across_heads_and_preserves_both_bindings(self):
        first = fingerprint()
        second = {key: value for key, value in first.items() if key != "sha256"}
        second["head"] = "e" * 40
        second["sha256"] = sha(canonical(second))
        one, two = self.compact(first), self.compact(second)
        self.assertEqual(one["manifest"]["sha256"], two["manifest"]["sha256"])
        self.assertNotEqual(one["sha256"], two["sha256"])
        self.assertEqual(len(list(self.folder.iterdir())), 1)
        self.assertEqual(manifests.resolve(one, self.folder), first)
        self.assertEqual(manifests.resolve(two, self.folder), second)

    def test_legacy_partial_inline_remains_accepted_without_io(self):
        for value in ({"sha256": "fixture"}, {"head": "x", "sha256": "fixture", "files": []},
                      {"repo": "/x", "head": "x", "sha256": "fixture", "files": []}, None):
            with self.subTest(value=value):
                self.assertIs(manifests.resolve(value, self.folder / "absent"), value)
                self.assertIs(manifests.compact(value, self.writer), value)
        self.assertEqual(self.calls, [])

    def test_compact_rejects_real_fingerprint_mismatch_and_bad_writer_receipt(self):
        original = fingerprint()
        bad = {**original, "sha256": "f" * 64}
        with self.assertRaisesRegex(manifests.ManifestError, "fingerprint digest"):
            self.compact(bad)
        def wrong(body, name, original):
            return {**self.writer(body, name, original), "sha256": "f" * 64}
        with self.assertRaisesRegex(manifests.ManifestError, "receipt mismatch"):
            manifests.compact(original, wrong)

    def test_existing_reference_is_not_rewritten_and_invalid_reference_is_not_legacy(self):
        value = self.compact()
        count = len(self.calls)
        self.assertIs(manifests.compact(value, self.writer), value)
        self.assertEqual(len(self.calls), count)
        with self.assertRaises(manifests.ManifestError):
            manifests.compact({"manifest": {}}, self.writer)

    def test_missing_and_tampered_artifact_refused(self):
        value = self.compact()
        path = self.folder / value["manifest"]["sha256"]
        body = path.read_bytes()
        path.unlink()
        with self.assertRaises(manifests.ManifestError):
            manifests.resolve(value, self.folder)
        path.write_bytes(body.replace(b"bin/check", b"bin/CHECK"))
        with self.assertRaisesRegex(manifests.ManifestError, "digest or size"):
            manifests.resolve(value, self.folder)
        path.write_bytes(body)
        self.assertEqual(manifests.resolve(value, self.folder), fingerprint())

    def test_artifact_digest_verified_independently_of_fingerprint_digest(self):
        value = self.compact()
        body = (self.folder / value["manifest"]["sha256"]).read_bytes()
        changed = copy.deepcopy(value)
        changed["manifest"]["sha256"] = "f" * 64
        (self.folder / ("f" * 64)).write_bytes(body)
        with self.assertRaisesRegex(manifests.ManifestError, "digest or size"):
            manifests.resolve(changed, self.folder)
        self.assertEqual(manifests.resolve(value, self.folder), fingerprint())

    def test_count_and_size_are_strict_integers_and_match_actual_content(self):
        value = self.compact()
        for field, bad_values in (("count", [True, -1, 3, 5, "4"]),
                                  ("bytes", [True, -1, 1, 2**24 + 1, "300", value["manifest"]["bytes"] - 1])):
            for bad in bad_values:
                with self.subTest(field=field, value=bad):
                    changed = copy.deepcopy(value)
                    changed["manifest"][field] = bad
                    with self.assertRaises(manifests.ManifestError):
                        manifests.resolve(changed, self.folder)

    def test_reconstructed_fingerprint_and_reference_shape_are_verified(self):
        original = self.compact()
        for change in ({"head": "different"}, {"repo": "/elsewhere"}, {"sha256": "f" * 64},
                       {"files": []}, {"extra": True}):
            with self.subTest(change=change):
                with self.assertRaises(manifests.ManifestError):
                    manifests.resolve({**original, **change}, self.folder)
        for change in ({"sha256": "../escape"}, {"sha256": "A" * 64}, {"extra": True},
                       {"name": ""}, {"original": "bad\0path"}):
            with self.subTest(change=change):
                changed = {**original, "manifest": {**original["manifest"], **change}}
                with self.assertRaises(manifests.ManifestError):
                    manifests.resolve(changed, self.folder)

    def test_rows_must_have_unique_byte_ordered_relative_paths(self):
        for paths in (["b", "a"], ["a", "a"], ["../outside"], ["/absolute"],
                      ["a/../b"], ["./a"], ["a//b"], ["a/"], [""], ["bad\0name"]):
            with self.subTest(paths=paths):
                value = self.forged_reference([{"path": p, "kind": "deleted"} for p in paths])
                with self.assertRaises(manifests.ManifestError):
                    manifests.resolve(value, self.folder)

    def test_row_kind_fields_and_executable_boolean_are_verified(self):
        good = fingerprint()["files"][0]
        for row in ({**good, "kind": "directory"}, {**good, "executable": 1},
                    {**good, "executable": "true"}, {**good, "sha256": "f"},
                    {**good, "extra": "ignored"}, {"path": "x", "kind": "file", "sha256": "a" * 64},
                    {"path": "x", "kind": "deleted", "sha256": "a" * 64},
                    {"path": "x", "kind": "symlink", "sha256": "a" * 64, "executable": False}):
            with self.subTest(row=row):
                with self.assertRaises(manifests.ManifestError):
                    manifests.resolve(self.forged_reference([row]), self.folder)

    def test_manifest_json_must_be_canonical_and_list_shaped(self):
        rows = fingerprint()["files"]
        for body in (json.dumps(rows, indent=2).encode(), canonical(rows) + b"\n", b"{}", b"[NaN]",
                     b"{broken", b"\xff"):
            with self.subTest(body=body[:20]):
                value = self.forged_reference(rows, raw=body)
                with self.assertRaises(manifests.ManifestError):
                    manifests.resolve(value, self.folder)

    def test_symlink_file_and_artifacts_directory_refused_with_regular_control(self):
        value = self.compact()
        path = self.folder / value["manifest"]["sha256"]
        real = Path(self.temp.name) / "body"
        path.rename(real)
        path.symlink_to(real)
        with self.assertRaises(manifests.ManifestError):
            manifests.resolve(value, self.folder)
        path.unlink()
        real.rename(path)
        alias = Path(self.temp.name) / "alias"
        alias.symlink_to(self.folder, target_is_directory=True)
        with self.assertRaises(manifests.ManifestError):
            manifests.resolve(value, alias)
        self.assertEqual(manifests.resolve(value, self.folder), fingerprint())

    def test_fifo_and_directory_artifact_refused_without_blocking(self):
        value = self.compact()
        path = self.folder / value["manifest"]["sha256"]
        path.unlink()
        os.mkfifo(path, mode=0o600)
        with self.assertRaises(manifests.ManifestError):
            manifests.resolve(value, self.folder)
        path.unlink()
        path.mkdir()
        with self.assertRaises(manifests.ManifestError):
            manifests.resolve(value, self.folder)

    def test_mixed_events_packet_and_state_round_trip_without_alias_changes(self):
        inline = fingerprint()
        compact = self.compact(inline)
        user = {"candidate": inline, "before": inline, "files": inline["files"]}
        run = {"id": "p", "before": inline, "after": compact, "result": user}
        challenge = {"candidate": inline, "runs": [], "user": user}
        packet = {"task": "t", "candidate": inline, "proofs": [run], "instructions": user,
                  "fix_result": challenge}
        original_packet = copy.deepcopy(packet)
        stored_packet = manifests.transform_packet(packet, lambda v: self.compact(v))
        self.assertNotIn("files", stored_packet["candidate"])
        self.assertNotIn("files", stored_packet["proofs"][0]["before"])
        self.assertNotIn("files", stored_packet["fix_result"]["candidate"])
        self.assertEqual(packet, original_packet)
        self.assertIs(stored_packet["instructions"], user)
        delivery = {"snapshot": compact, "packet_data": stored_packet}
        stored_challenge = {**challenge, "candidate": compact}
        state = {"tasks": {"t": {"candidate": compact, "proofs": [run], "delivery": delivery,
                                    "fix_result": stored_challenge}},
                 "runs": {"p": run}, "messages": {"m": user}, "challenges": [stored_challenge]}
        frozen = copy.deepcopy(state)
        materialized = manifests.materialize_known(state, self.folder)
        self.assertEqual(materialized["tasks"]["t"]["candidate"], inline)
        self.assertEqual(materialized["tasks"]["t"]["proofs"][0]["after"], inline)
        self.assertEqual(materialized["runs"]["p"]["after"], inline)
        self.assertEqual(materialized["tasks"]["t"]["fix_result"]["candidate"], inline)
        self.assertEqual(materialized["challenges"][0]["candidate"], inline)
        self.assertIs(materialized["tasks"]["t"]["delivery"]["packet_data"], stored_packet)
        self.assertIs(materialized["messages"], state["messages"])
        self.assertEqual(state, frozen)
        for kind in ("message", "output", "review", "diagnostic", "task", "decision"):
            self.assertIs(manifests.transform_event(kind, user, lambda v: self.compact(v)), user)
        self.assertIs(manifests.transform_event("deliver", delivery, lambda v: inline)["packet_data"], stored_packet)
        self.assertEqual(manifests.transform_event("run.start", run, lambda v: self.compact(v))["before"], compact)
        self.assertEqual(manifests.transform_event("run.end", run, lambda v: self.compact(v))["after"], compact)


if __name__ == "__main__":
    unittest.main()
