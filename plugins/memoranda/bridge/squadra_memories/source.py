"""Reads a squadra registry without writing to it, through squadra's own pinned code.

The live database is never opened with SQLite: a read-only SQLite connection creates `-wal`/`-shm` beside a
quiescent registry and rewrites `-shm` on a live one. The database and its WAL are copied byte for byte into a private
folder, and squadra's pinned `registry.py` verifies the copy.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import types

MAX_ARTIFACT = 16 * 1024 * 1024
HEX = frozenset("0123456789abcdef")
ATTEMPTS = 3


class Refusal(Exception):
    """A refusal with one fixed code; it never carries registry content."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def load_pin():
    return json.loads(Path(__file__).with_name("pin.json").read_text(encoding="utf-8"))


def locate_scripts(explicit=None):
    """Use the bundled reader unless a caller explicitly names another pinned build."""
    return Path(explicit) if explicit else Path(__file__).resolve().parents[1]


def load_registry(scripts):
    """Hash the exact reader bytes before execution; external builds also need their pinned commit."""
    path = Path(scripts) / "registry.py"
    try:
        body = path.read_bytes()
        sha = hashlib.sha256(body).hexdigest()
        entry = next((e for e in load_pin()["accepted"] if e["registry_py_sha256"] == sha), None)
        if entry is None:
            raise Refusal("E-PIN")
        if path.resolve().parent != Path(__file__).resolve().parents[1]:
            commit = (Path(scripts).parent / "SOURCE_COMMIT").read_text(encoding="utf-8").strip()
            if commit != entry["source_commit"]:
                raise Refusal("E-PIN")
    except (OSError, ValueError):
        raise Refusal("E-PIN") from None
    module = types.ModuleType("squadra_registry_pinned")
    module.__file__ = str(path)
    exec(compile(body, str(path), "exec"), module.__dict__)
    return module, entry


def _signature(path):
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    return (st.st_ino, st.st_size, st.st_mtime_ns, stat.S_IFMT(st.st_mode))


def _copy(source, target):
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as reader:
        st = os.fstat(reader.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise Refusal("E-SNAPSHOT")
        digest = hashlib.sha256()
        with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as writer:
            for chunk in iter(lambda: reader.read(1 << 20), b""):
                digest.update(chunk)
                writer.write(chunk)
    return (st.st_ino, st.st_size, st.st_mtime_ns, stat.S_IFREG), digest.hexdigest()


def snapshot(root, target):
    """Copies `registry.sqlite3` and its WAL; a copy during which either file changed is discarded and retried."""
    db, wal = root / "registry.sqlite3", root / "registry.sqlite3-wal"
    if os.path.lexists(root / "registry.sqlite3-journal"):
        raise Refusal("E-SNAPSHOT")
    for _ in range(ATTEMPTS):
        before = (_signature(db), _signature(wal))
        if before[0] is None or before[0][3] != stat.S_IFREG or (before[1] and before[1][3] != stat.S_IFREG):
            raise Refusal("E-SNAPSHOT")
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(mode=0o700)
        try:
            copied_db, db_sha = _copy(db, target / "registry.sqlite3")
            copied_wal, wal_sha = _copy(wal, target / "registry.sqlite3-wal") if before[1] else (None, None)
        except FileNotFoundError:
            continue
        except OSError:
            raise Refusal("E-SNAPSHOT") from None
        if before == (_signature(db), _signature(wal)) == (copied_db, copied_wal):
            return {"db": db_sha, "wal": wal_sha}
    raise Refusal("E-DRIFT")


def verified_events(module, entry, folder):
    """The copy's events, chain-verified by squadra's own code; any kind the pin does not know is refused."""
    try:
        store = module.Store(folder, read_only=True)
    except Exception:
        raise Refusal("E-CHAIN") from None
    try:
        events = store.events()
        check = store.db.execute("PRAGMA quick_check").fetchone()[0]
    except Exception:
        raise Refusal("E-CHAIN") from None
    finally:
        store.close()
    if check != "ok" or not events or events[0]["kind"] != "init":
        raise Refusal("E-CHAIN")
    known = set(entry["kinds"])
    if any(event["kind"] not in known for event in events):
        raise Refusal("E-PIN")
    return events


class Artifacts:
    """The registry's content-addressed store; only registered digests are read, and every read is verified."""

    def __init__(self, root, refs):
        self.root = Path(root)
        self.refs = refs

    def registered(self, sha):
        return isinstance(sha, str) and sha in self.refs

    def read(self, sha):
        ref = self.refs[sha]
        folder = self.root / "artifacts"
        try:
            if not stat.S_ISDIR(os.lstat(folder).st_mode):
                raise Refusal("E-ARTIFACT")
            if len(sha) != 64 or not set(sha) <= HEX:
                raise Refusal("E-ARTIFACT")
            fd = os.open(folder / sha, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError:
            raise Refusal("E-ARTIFACT") from None
        with os.fdopen(fd, "rb") as reader:
            st = os.fstat(reader.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_size != ref["bytes"] or st.st_size > MAX_ARTIFACT:
                raise Refusal("E-ARTIFACT")
            body = reader.read(MAX_ARTIFACT + 1)
        if len(body) != ref["bytes"] or hashlib.sha256(body).hexdigest() != sha:
            raise Refusal("E-ARTIFACT")
        return body
