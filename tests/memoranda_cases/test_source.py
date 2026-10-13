"""T6, T7, T9: the registry is read without writing; refusals create nothing; unknown kinds refuse."""
import datetime
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
import unittest
import uuid
import pwd
import signal
from pathlib import Path

import fx

ITEM = {"type": "trap", "sentence": "A slow write is not a failed write.", "certainty": "sure",
        "private_content": "no"}

LIVE_WRITER = r"""
import sys, time, types
from pathlib import Path
scripts, root, ready, release = sys.argv[1:5]
path = Path(scripts) / "registry.py"
module = types.ModuleType("live_writer_registry")
exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
store = module.Store(root)
store.append("output", "coord", {"task": "T1", "text": "live writer", "next": "-", "artifacts": []})
Path(ready).write_text("ready")
deadline = time.monotonic() + 300
while not Path(release).exists() and time.monotonic() < deadline:
    time.sleep(0.05)
store.close()
"""


class Source(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.info = fx.info()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.reg = fx.copy_registry(self.tmp)
        fx.declare(self.reg, "b1", [{**ITEM, "evidence": [{"event": self.info["review_seq"]}]}])

    def tearDown(self):
        fx.remove(self.tmp)

    def test_t6_a_read_only_registry_is_read_and_left_unchanged(self):
        self.assertNotEqual(os.geteuid(), 0, "permission denial cannot be measured as root")
        fx.set_modes(self.reg.root, 0o500, 0o400)
        try:
            before = fx.inventory(self.reg.root)
            run = fx.collect(self.reg, self.tmp / "out")
            after = fx.inventory(self.reg.root)
        finally:
            fx.set_modes(self.reg.root, 0o700, 0o600)
        self.assertEqual(run.rc, 0, run.stderr)
        self.assertEqual(len(run.doc["candidates"]), 1)
        self.assertEqual(before, after)
        self.assertEqual([name for name in after if name.endswith(("-wal", "-shm"))], [])

    def test_t6_a_live_wal_is_read_and_left_unchanged(self):
        ready, release = self.tmp / "ready", self.tmp / "release"
        last = self.reg.last_seq()
        child = subprocess.Popen([sys.executable, "-B", "-c", LIVE_WRITER, fx.scripts(), self.reg.root, ready, release],
                                 env=fx.env())
        try:
            deadline = time.monotonic() + 120
            while not ready.exists():
                if child.poll() is not None or time.monotonic() > deadline:
                    raise fx.FixtureError("live writer did not start")
                time.sleep(0.05)
            if not (self.reg.root / "registry.sqlite3-wal").exists():
                raise fx.FixtureError("live writer left no WAL")
            before = fx.inventory(self.reg.root)
            run = fx.collect(self.reg, self.tmp / "out")
            after = fx.inventory(self.reg.root)
        finally:
            release.write_text("go")
            child.wait(timeout=120)
        self.assertEqual(run.rc, 0, run.stderr)
        self.assertEqual(run.doc["binding"]["last_seq"], last + 1)
        self.assertEqual(before, after)

    def test_t7_refused_locations_and_code_create_nothing(self):
        with self.subTest("out-root inside the registry"):
            before = fx.inventory(self.reg.root)
            run = fx.collect(self.reg, self.reg.root / "artifacts")
            self.assertEqual(run.rc, 3)
            self.assertTrue(run.stderr.startswith("E-OUTPUT:"), run.stderr)
            self.assertEqual(fx.inventory(self.reg.root), before)
        with self.subTest("out-root inside the bridge"):
            before = sorted(p.name for p in (fx.ROOT / "squadra_memories").iterdir())
            run = fx.collect(self.reg, fx.ROOT / "squadra_memories")
            self.assertEqual(run.rc, 3)
            self.assertTrue(run.stderr.startswith("E-OUTPUT:"), run.stderr)
            self.assertEqual(sorted(p.name for p in (fx.ROOT / "squadra_memories").iterdir()), before)
        with self.subTest("output already present"):
            first = fx.collect(self.reg, self.tmp / "twice")
            self.assertEqual(first.rc, 0, first.stderr)
            again = fx.collect(self.reg, self.tmp / "twice")
            self.assertEqual(again.rc, 3)
            self.assertTrue(again.stderr.startswith("E-OUTPUT:"), again.stderr)
            self.assertEqual([p.name for p in (self.tmp / "twice").iterdir()], [first.folder.name])

    def fabricate(self, kind):
        """An event of a kind no accepted squadra writes, chained with the pinned code on this test's own copy."""
        module = fx.pinned_module()
        with sqlite3.connect(self.reg.root / "registry.sqlite3") as db:
            previous = db.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()[0]
            data = {"task": "T1", "reason": "a kind no accepted squadra knows"}
            event = {"id": uuid.uuid4().hex, "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                     "kind": kind, "actor": "coord", "data": data, "previous": previous}
            digest = module.digest(module.canonical(event).encode())
            db.execute("INSERT INTO events(id,utc,kind,actor,data,previous,hash) VALUES(?,?,?,?,?,?,?)",
                       (event["id"], event["utc"], kind, "coord", module.canonical(data), previous, digest))
        db.close()

    def test_t9_a_registry_carrying_a_supersede_event_is_read_verified(self):
        fx.task(self.reg, "T2")
        fx.task(self.reg, "T3")
        self.reg.cli("supersede", "--actor", "coord", "--task", "T2", "--by", "T3", "--reason", "T3 replaces T2")
        supersede = self.reg.last_seq("supersede")
        run = fx.collect(self.reg, self.tmp / "out")
        self.assertEqual(run.rc, 0, run.stderr)
        self.assertEqual(run.doc["binding"]["last_seq"], supersede)
        self.assertEqual(run.doc["binding"]["squadra"]["label"], "squadra 3.7.0")
        self.assertEqual(len(run.doc["candidates"]), 1)

    def test_t9b_a_fabricated_unknown_kind_is_refused_whole(self):
        self.fabricate("zz-unknown")
        run = fx.collect(self.reg, self.tmp / "out")
        self.assertEqual(run.rc, 3)
        self.assertTrue(run.stderr.startswith("E-PIN:"), run.stderr)
        self.assertEqual(list((self.tmp / "out").iterdir()), [])

    def test_every_pin_entry_matches_its_supported_build(self):
        from squadra_memories.source import load_pin, load_registry
        entry = load_registry()[1]
        self.assertIn(entry, load_pin()['accepted'])
        module = fx.pinned_module()
        for kind in entry['kinds']:
            try:
                module.validate(module.initial(), kind, 'x', {})
            except Exception as error:
                self.assertNotEqual(str(error), 'Unknown event kind', kind)
        with self.assertRaises(Exception) as error:
            module.validate(module.initial(), 'zz-unknown', 'x', {})
        self.assertEqual(str(error.exception), 'Unknown event kind')

    def observe(self, out_root, ready=None):
        fx.install(fx.synthetic_home())
        out_root.mkdir(mode=0o700, exist_ok=True)
        record = self.tmp / "record.json"
        env = {**fx.env(), **({"SQMEM_OBSERVE_READY": str(ready)} if ready else {})}
        argv = [sys.executable, "-B", str(fx.CASES / "_observe.py"), str(record), "collect",
                "--registry", str(self.reg.root), "--out-root", str(out_root)]
        return subprocess.Popen(argv, cwd=fx.ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True), record

    def test_the_registry_copy_never_enters_the_out_root(self):
        out_root = Path(os.path.realpath(self.tmp)) / "out"
        child, record = self.observe(out_root)
        stdout, stderr = child.communicate(timeout=600)
        self.assertEqual(child.returncode, 0, stderr)
        events = json.loads(record.read_text())
        registry = os.path.realpath(self.reg.root)
        copies = [path for kind, path in events if kind == "write" and path.endswith("registry.sqlite3")]
        self.assertTrue(copies)
        inside = [path for kind, path in events if kind in ("write", "sqlite3.connect")
                  and path.startswith(str(out_root) + os.sep)]
        self.assertEqual(sorted(os.path.basename(path) for path in inside),
                         ["candidates.en.md", "candidates.it.md", "candidates.json"])
        self.assertEqual([path for kind, path in events if path.startswith(registry + os.sep)], [])

    def test_an_interrupted_collect_leaves_nothing_behind(self):
        out_root = self.tmp / "out"
        ready = self.tmp / "ready"
        child, record = self.observe(out_root, ready)
        try:
            deadline = time.monotonic() + 120
            while not ready.exists():
                if child.poll() is not None or time.monotonic() > deadline:
                    raise fx.FixtureError("the collector never reached its copy")
                time.sleep(0.05)
            copy = Path(ready.read_text())
            child.send_signal(signal.SIGTERM)
            stdout, stderr = child.communicate(timeout=120)
        finally:
            if child.poll() is None:
                child.kill()
        self.assertEqual(child.returncode, 130, stderr)
        self.assertTrue(stderr.startswith("E-INTERRUPTED:"), stderr)
        self.assertFalse(copy.parent.parent.exists(), copy)
        self.assertEqual(list(out_root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
