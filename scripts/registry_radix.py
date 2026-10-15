"""Authenticated current-state index. The caller owns the SQLite transaction.

Roots must be bound to verified registry metadata by the caller. ``root()`` only
reads the stored hint. Point reads authenticate their path, not untouched leaves;
``validate_all`` additionally detects unreferenced rows. Enumeration is ordered by
the SHA-256 of the logical key, not by the logical key. No historical roots remain
usable after an update. Use a single SQLite read snapshot for related accesses.
"""

import hashlib
import json


class IntegrityError(ValueError):
    pass


_HEX = frozenset("0123456789abcdef")
_MAX_COUNT = 2**63 - 1


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _key_digest(key):
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _hex(value, length):
    return isinstance(value, str) and len(value) == length and set(value) <= _HEX


def _root(value):
    if (not isinstance(value, dict) or set(value) != {"sha256", "count"}
            or not _hex(value["sha256"], 64)
            or type(value["count"]) is not int
            or not 0 <= value["count"] <= _MAX_COUNT):
        raise IntegrityError("Malformed radix root")
    return value


def create_schema(conn):
    # execute(), unlike executescript(), does not implicitly commit the caller.
    conn.execute("CREATE TABLE IF NOT EXISTS derived_radix_nodes ("
                 "namespace TEXT NOT NULL, digest TEXT NOT NULL, data TEXT NOT NULL, "
                 "PRIMARY KEY(namespace, digest)) WITHOUT ROWID")
    conn.execute("CREATE TABLE IF NOT EXISTS derived_radix_roots ("
                 "namespace TEXT PRIMARY KEY, digest TEXT NOT NULL, count INTEGER NOT NULL)")


class Index:
    def __init__(self, conn, namespace):
        if not isinstance(namespace, str) or not namespace:
            raise ValueError("A nonempty namespace is required")
        self.conn, self.namespace = conn, namespace
        self.empty = {"sha256": self._hash("empty"), "count": 0}

    def _hash(self, *parts):
        return _digest(["squadra-radix-v1", self.namespace, *parts])

    def root(self):
        row = self.conn.execute(
            "SELECT digest, count FROM derived_radix_roots WHERE namespace=?",
            (self.namespace,)).fetchone()
        return dict(self.empty) if row is None else _root({"sha256": row[0], "count": row[1]})

    def _checked_root(self, root):
        root = _root(root)
        if root["count"] == 0 and root != self.empty:
            raise IntegrityError("Invalid empty radix root")
        return root

    def _description(self, node):
        if not isinstance(node, dict):
            raise IntegrityError("Malformed radix node")
        if node.get("kind") == "leaf":
            if (set(node) != {"kind", "path", "key", "value"}
                    or not isinstance(node["key"], str)
                    or not _hex(node["path"], 64)
                    or node["path"] != _key_digest(node["key"])):
                raise IntegrityError("Malformed radix leaf")
            digest = self._hash("leaf", node["path"], node["key"], _digest(node["value"]))
            return {"sha256": digest, "count": 1}
        if node.get("kind") != "branch" or set(node) != {"kind", "path", "children"}:
            raise IntegrityError("Malformed radix branch")
        path, children = node["path"], node["children"]
        if (not isinstance(path, str) or len(path) >= 64 or set(path) - _HEX
                or not isinstance(children, dict) or not 2 <= len(children) <= 16):
            raise IntegrityError("Invalid compressed radix prefix")
        count = 0
        for digit, child in children.items():
            if not _hex(digit, 1) or _root(child)["count"] == 0:
                raise IntegrityError("Invalid radix child")
            count += child["count"]
        if count > _MAX_COUNT:
            raise IntegrityError("Radix count overflow")
        return {"sha256": self._hash("branch", len(path), path, children), "count": count}

    def _load(self, root, prefix=""):
        self._checked_root(root)
        if root["count"] == 0:
            raise IntegrityError("Empty child")
        row = self.conn.execute(
            "SELECT data FROM derived_radix_nodes WHERE namespace=? AND digest=?",
            (self.namespace, root["sha256"])).fetchone()
        if row is None:
            raise IntegrityError("Missing radix node")
        try:
            node = json.loads(row[0])
            if _canonical(node) != row[0] or self._description(node) != root:
                raise IntegrityError("Radix node commitment mismatch")
        except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError) as exc:
            raise IntegrityError("Invalid radix node") from exc
        if not node["path"].startswith(prefix):
            raise IntegrityError("Radix child outside its committed prefix")
        return node

    def get(self, key, expected_root, default=None):
        if not isinstance(key, str):
            raise TypeError("Logical keys must be strings")
        path, root, prefix = _key_digest(key), self._checked_root(expected_root), ""
        while root["count"]:
            node = self._load(root, prefix)
            if node["kind"] == "leaf":
                if node["path"] == path and node["key"] != key:
                    raise IntegrityError("Logical key hash collision")
                return node["value"] if node["key"] == key else default
            if not path.startswith(node["path"]):
                return default
            prefix = path[:len(node["path"]) + 1]
            root = node["children"].get(prefix[-1], self.empty)
        return default

    def _walk(self, root, prefix=""):
        if not self._checked_root(root)["count"]:
            return
        node = self._load(root, prefix)
        yield root["sha256"], node
        if node["kind"] == "branch":
            for digit, child in sorted(node["children"].items()):
                yield from self._walk(child, node["path"] + digit)

    def items(self, expected_root):
        """Authenticate every returned leaf and subtree count; exhaust the iterator."""
        count = 0
        for _, node in self._walk(expected_root):
            if node["kind"] == "leaf":
                count += 1
                yield node["key"], node["value"]
        if count != expected_root["count"]:
            raise IntegrityError("Radix enumeration count mismatch")

    def validate_all(self, expected_root, reject_orphans=True):
        seen, leaves = set(), 0
        for digest, node in self._walk(expected_root):
            if digest in seen:
                raise IntegrityError("Duplicate radix subtree")
            seen.add(digest)
            leaves += node["kind"] == "leaf"
        if leaves != expected_root["count"]:
            raise IntegrityError("Radix enumeration count mismatch")
        if reject_orphans:
            actual = {row[0] for row in self.conn.execute(
                "SELECT digest FROM derived_radix_nodes WHERE namespace=?", (self.namespace,))}
            if seen != actual:
                raise IntegrityError("Unreferenced radix nodes")
        return True

    @staticmethod
    def _common(a, b):
        i = 0
        while i < min(len(a), len(b)) and a[i] == b[i]:
            i += 1
        return a[:i]

    def _change(self, key, value, expected_root, deleting):
        if not isinstance(key, str):
            raise TypeError("Logical keys must be strings")
        expected_root = self._checked_root(expected_root)
        if self.root() != expected_root:
            raise IntegrityError("Stale radix root")
        path, pending, obsolete = _key_digest(key), {}, set()

        def remember(node):
            root = self._description(node)
            pending[root["sha256"]] = _canonical(node)
            return root

        def leaf():
            return remember({"kind": "leaf", "path": path, "key": key, "value": value})

        def split(node_path, old, new):
            prefix = self._common(node_path, path)
            return remember({"kind": "branch", "path": prefix, "children": {
                node_path[len(prefix)]: old, path[len(prefix)]: new}})

        def change(old, prefix=""):
            if not old["count"]:
                return old if deleting else leaf()
            node = self._load(old, prefix)
            if node["kind"] == "leaf":
                if node["path"] == path and node["key"] != key:
                    raise IntegrityError("Logical key hash collision")
                if node["key"] != key:
                    return old if deleting else split(node["path"], old, leaf())
                new = self.empty if deleting else leaf()
            elif not path.startswith(node["path"]):
                return old if deleting else split(node["path"], old, leaf())
            else:
                digit = path[len(node["path"])]
                children = dict(node["children"])
                child = change(children.get(digit, self.empty), node["path"] + digit)
                if child["count"]:
                    children[digit] = child
                else:
                    children.pop(digit, None)
                new = (next(iter(children.values())) if len(children) == 1 else
                       remember({"kind": "branch", "path": node["path"], "children": children}))
            if new != old:
                obsolete.add(old["sha256"])
            return new

        result = change(expected_root)
        if result == expected_root:
            return dict(result)
        # All accessed old paths have passed authentication before any SQL write.
        for digest, data in pending.items():
            row = self.conn.execute(
                "SELECT data FROM derived_radix_nodes WHERE namespace=? AND digest=?",
                (self.namespace, digest)).fetchone()
            if row is not None and row[0] != data:
                raise IntegrityError("Existing radix commitment has different bytes")
        self.conn.executemany("INSERT OR IGNORE INTO derived_radix_nodes VALUES (?,?,?)",
                              ((self.namespace, digest, data) for digest, data in pending.items()))
        self.conn.executemany("DELETE FROM derived_radix_nodes WHERE namespace=? AND digest=?",
                              ((self.namespace, digest) for digest in obsolete - pending.keys()))
        self.conn.execute("INSERT INTO derived_radix_roots VALUES (?,?,?) "
                          "ON CONFLICT(namespace) DO UPDATE SET digest=excluded.digest,count=excluded.count",
                          (self.namespace, result["sha256"], result["count"]))
        return dict(result)

    def set(self, key, value, expected_root):
        return self._change(key, value, expected_root, False)

    def delete(self, key, expected_root):
        return self._change(key, None, expected_root, True)
