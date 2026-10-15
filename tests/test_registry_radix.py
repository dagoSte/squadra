import copy
import json
import random
import sqlite3
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import registry_radix as radix


class RadixTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        radix.create_schema(self.conn)
        self.index = radix.Index(self.conn, "tasks")
        self.root = self.index.root()

    def put(self, key, value):
        self.root = self.index.set(key, value, self.root)

    def data(self, digest):
        return json.loads(self.conn.execute(
            "SELECT data FROM derived_radix_nodes WHERE namespace='tasks' AND digest=?",
            (digest,)).fetchone()[0])

    def replace_data(self, digest, node):
        self.conn.execute("UPDATE derived_radix_nodes SET data=? WHERE namespace='tasks' AND digest=?",
                          (radix._canonical(node), digest))

    def forged_node(self, node):
        """A self-consistent root still must authenticate its descendants."""
        root = self.index._description(node)
        self.conn.execute("INSERT OR REPLACE INTO derived_radix_nodes VALUES ('tasks',?,?)",
                          (root["sha256"], radix._canonical(node)))
        return root

    def test_empty_absence_sentinel_and_json_values(self):
        sentinel = object()
        self.assertIs(self.index.get("missing", self.root, sentinel), sentinel)
        self.assertEqual(list(self.index.items(self.root)), [])
        for number, value in enumerate([None, False, 0, 1.25, "è", [], {}, {"a": [2, "b"]}]):
            self.put(str(number), value)
            self.assertEqual(self.index.get(str(number), self.root, sentinel), value)
        self.assertIs(self.index.get("absent", self.root, sentinel), sentinel)
        self.assertTrue(self.index.validate_all(self.root))

    def test_random_differential_order_independent_root_and_bounded_rows(self):
        rng, expected = random.Random(4817), {}
        for step in range(750):
            key = f"task-{rng.randrange(60)}"
            if rng.randrange(4):
                value = {"step": step, "values": [rng.randrange(200), None]}
                expected[key] = value
                self.put(key, value)
            else:
                expected.pop(key, None)
                self.root = self.index.delete(key, self.root)
            self.assertEqual(dict(self.index.items(self.root)), expected, step)
            self.assertEqual(self.root["count"], len(expected), step)
            self.assertTrue(self.index.validate_all(self.root))
            count = self.conn.execute("SELECT count(*) FROM derived_radix_nodes").fetchone()[0]
            self.assertLessEqual(count, max(0, 2 * len(expected) - 1), step)
        other = sqlite3.connect(":memory:")
        self.addCleanup(other.close)
        radix.create_schema(other)
        index, root = radix.Index(other, "tasks"), None
        root = index.root()
        keys = list(expected)
        rng.shuffle(keys)
        for key in keys:
            root = index.set(key, expected[key], root)
        self.assertEqual(root, self.root)
        for key in keys:
            self.root = self.index.delete(key, self.root)
        self.assertEqual(self.root, self.index.empty)
        self.assertTrue(self.index.validate_all(self.root))

    def test_compressed_prefix_divergence_and_missing_child_prove_absence(self):
        paths = {"a": "abc" + "0" * 61, "b": "abc" + "1" * 61,
                 "outside": "abd" + "0" * 61, "inside": "abc" + "2" * 61}
        with patch.object(radix, "_key_digest", paths.__getitem__):
            self.put("a", 1)
            self.put("b", 2)
            self.assertEqual(self.data(self.root["sha256"])["path"], "abc")
            sentinel = object()
            self.assertIs(self.index.get("outside", self.root, sentinel), sentinel)
            self.assertIs(self.index.get("inside", self.root, sentinel), sentinel)
            self.put("outside", 3)
            self.assertEqual(self.data(self.root["sha256"])["path"], "ab")
            self.assertEqual(dict(self.index.items(self.root)), {"a": 1, "b": 2, "outside": 3})
            self.root = self.index.delete("outside", self.root)
            self.assertEqual(self.data(self.root["sha256"])["path"], "abc")
            self.assertTrue(self.index.validate_all(self.root))

    def test_missing_leaf_and_changed_value_are_not_absence(self):
        self.put("a", {"value": 4})
        digest, node = self.root["sha256"], self.data(self.root["sha256"])
        altered = copy.deepcopy(node)
        altered["value"]["value"] = 5
        self.replace_data(digest, altered)
        with self.assertRaises(radix.IntegrityError):
            self.index.get("a", self.root)
        self.conn.execute("DELETE FROM derived_radix_nodes")
        with self.assertRaises(radix.IntegrityError):
            self.index.get("absent", self.root)
        self.conn.execute("INSERT INTO derived_radix_nodes VALUES ('tasks',?,?)",
                          (digest, radix._canonical(node)))
        self.assertEqual(self.index.get("a", self.root), {"value": 4})

    def test_changed_branch_prefix_count_and_child_are_refused(self):
        for key in ["a", "b", "c"]:
            self.put(key, key)
        digest, original = self.root["sha256"], self.data(self.root["sha256"])
        for kind in ["prefix", "count", "child", "missing-child"]:
            with self.subTest(kind=kind):
                node = copy.deepcopy(original)
                digit = next(iter(node["children"]))
                if kind == "prefix":
                    node["path"] += "e"
                elif kind == "count":
                    node["children"][digit]["count"] += 1
                elif kind == "child":
                    node["children"][digit]["sha256"] = "0" * 64
                else:
                    del node["children"][digit]
                self.replace_data(digest, node)
                with self.assertRaises(radix.IntegrityError):
                    list(self.index.items(self.root))
        self.replace_data(digest, original)
        self.assertEqual(dict(self.index.items(self.root)), {k: k for k in ["a", "b", "c"]})

    def test_self_consistent_wrong_child_prefix_and_count_still_refused(self):
        self.put("a", 1)
        self.put("b", 2)
        original = self.data(self.root["sha256"])
        node = copy.deepcopy(original)
        digits = list(node["children"])
        node["children"][digits[0]], node["children"][digits[1]] = (
            node["children"][digits[1]], node["children"][digits[0]])
        forged = self.forged_node(node)
        with self.assertRaises(radix.IntegrityError):
            self.index.get("a", forged)
        with self.assertRaises(radix.IntegrityError):
            list(self.index.items(forged))
        node = copy.deepcopy(original)
        node["children"][digits[0]]["count"] += 1
        forged = self.forged_node(node)
        with self.assertRaises(radix.IntegrityError):
            list(self.index.items(forged))
        self.assertEqual(dict(self.index.items(self.root)), {"a": 1, "b": 2})

    def test_false_absence_needs_authenticated_children_and_root_count(self):
        self.put("a", 1)
        self.put("b", 2)
        digest, original = self.root["sha256"], self.data(self.root["sha256"])
        node = copy.deepcopy(original)
        digit = radix._key_digest("a")[len(node["path"])]
        replacement = next(c for c in "0123456789abcdef" if c not in node["children"])
        node["children"][replacement] = node["children"].pop(digit)
        self.replace_data(digest, node)
        with self.assertRaises(radix.IntegrityError):
            self.index.get("a", self.root)
        self.replace_data(digest, original)
        wrong_count = {**self.root, "count": self.root["count"] + 1}
        with self.assertRaises(radix.IntegrityError):
            self.index.get("a", wrong_count)
        self.assertEqual(self.index.get("a", self.root), 1)

    def test_omitted_child_row_cannot_shorten_enumeration(self):
        self.put("a", 1)
        self.put("b", 2)
        leaf = next(digest for digest, node in self.index._walk(self.root) if node.get("key") == "b")
        self.conn.execute("DELETE FROM derived_radix_nodes WHERE digest=?", (leaf,))
        with self.assertRaises(radix.IntegrityError):
            dict(self.index.items(self.root))
        self.assertEqual(self.index.get("a", self.root), 1)

    def test_single_child_branch_is_not_compressed_canonical_state(self):
        self.put("a", 1)
        node = {"kind": "branch", "path": "", "children": {
            radix._key_digest("a")[0]: self.root}}
        with self.assertRaises(radix.IntegrityError):
            forged = self.forged_node(node)
            self.index.get("a", forged)
        self.assertEqual(self.index.get("a", self.root), 1)

    def test_malformed_nodes_and_roots_fail_closed(self):
        self.put("a", 1)
        digest, original = self.root["sha256"], self.data(self.root["sha256"])
        for raw in ["{", "null", "[]", '{"x":NaN}', radix._canonical(original) + " "]:
            with self.subTest(raw=raw):
                self.conn.execute("UPDATE derived_radix_nodes SET data=?", (raw,))
                with self.assertRaises(radix.IntegrityError):
                    self.index.get("a", self.root)
        self.replace_data(digest, original)
        for root in [{"sha256": "0" * 64, "count": 0},
                     {"sha256": digest, "count": True}, {"sha256": digest, "count": -1},
                     {"sha256": digest, "count": 2**63}, {"sha256": digest}, None]:
            with self.subTest(root=root), self.assertRaises(radix.IntegrityError):
                self.index.get("a", root)
        self.assertEqual(self.index.get("a", self.root), 1)

    def test_namespace_substitution_and_hash_collision_refused(self):
        self.put("a", 1)
        other = radix.Index(self.conn, "messages")
        with self.assertRaises(radix.IntegrityError):
            other.get("a", self.root)
        with patch.object(radix, "_key_digest", return_value="a" * 64):
            index = radix.Index(self.conn, "collisions")
            root = index.set("a", 1, index.root())
            with self.assertRaises(radix.IntegrityError):
                index.set("b", 2, root)
            with self.assertRaises(radix.IntegrityError):
                index.get("b", root)
            self.assertEqual(index.get("a", root), 1)
            self.assertEqual(index.root(), root)
            self.assertTrue(index.validate_all(root))

    def test_external_root_stale_writes_and_orphan_detection(self):
        initial = self.root
        self.put("a", 1)
        with self.assertRaises(radix.IntegrityError):
            self.index.set("b", 2, initial)
        self.conn.execute("INSERT INTO derived_radix_nodes VALUES ('tasks',?,?)", ("f" * 64, "{}"))
        self.assertEqual(self.index.get("a", self.root), 1)
        self.assertTrue(self.index.validate_all(self.root, reject_orphans=False))
        with self.assertRaises(radix.IntegrityError):
            self.index.validate_all(self.root)

    def test_point_lookup_and_update_do_not_read_unrelated_leaf(self):
        self.put("a", 1)
        self.put("b", 2)
        trace = []
        self.conn.set_trace_callback(trace.append)
        self.assertEqual(self.index.get("a", self.root), 1)
        selects = [sql for sql in trace if sql.startswith("SELECT data")]
        self.assertEqual(len(selects), 2)
        self.assertFalse(any("ORDER BY" in sql for sql in trace))
        b_leaf = next(digest for digest, node in self.index._walk(self.root)
                      if node.get("key") == "b")
        self.conn.execute("UPDATE derived_radix_nodes SET data='{}' WHERE digest=?", (b_leaf,))
        self.put("a", 3)
        self.assertEqual(self.index.get("a", self.root), 3)
        with self.assertRaises(radix.IntegrityError):
            self.index.get("b", self.root)

    def test_schema_and_updates_leave_transaction_control_to_caller(self):
        self.conn.execute("BEGIN")
        radix.create_schema(self.conn)
        self.put("a", 1)
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertEqual(self.index.root(), self.index.empty)
        self.assertEqual(list(self.index.items(self.index.root())), [])


if __name__ == "__main__":
    unittest.main()
