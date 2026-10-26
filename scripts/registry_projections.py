"""Incremental, reconstructible exports; SQLite events remain authoritative.

Only touched day cursors are read. A disk freshness marker describes its captured
instant: readers must call freshness(store) to compare against the current journal.
The small file anchors recover interrupted appends; they are not full-file audits.
"""
from contextlib import contextmanager, nullcontext
import hashlib
from itertools import chain, islice
import json
import os
from pathlib import Path
import stat
import sqlite3
import tempfile


SCHEMA = 1
ZERO = "0" * 64
ANCHOR_BYTES = 64 * 1024
DAY_HEADER = "# Squadra — daily exchanges / scambi del giorno UTC\n\nSQLite is authoritative / SQLite è autorevole.\n"
MAIL_HEADER = "# Squadra — shared mail / posta condivisa\n\nMessages and acknowledgements are separate entries / Messaggi e conferme sono voci distinte.\n"


class ProjectionError(ValueError):
    pass


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _pack(value):
    return _canonical({"value": value, "sha256": _digest(_canonical(value).encode())})


def _unpack(raw):
    try:
        packed = json.loads(raw)
        if (set(packed) != {"value", "sha256"}
                or packed["sha256"] != _digest(_canonical(packed["value"]).encode())):
            raise ValueError()
        return packed["value"]
    except (ValueError, TypeError, KeyError) as exc:
        raise ProjectionError("Invalid projection cursor") from exc


def _schema(db):
    db.execute("CREATE TABLE IF NOT EXISTS derived_projection_meta (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS derived_projection_files (path TEXT PRIMARY KEY, data TEXT NOT NULL)")


def _position(db):
    row = db.execute("SELECT seq,hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
    return {"seq": row[0], "head": row[1]} if row else {"seq": 0, "head": ZERO}


def _identity(store):
    row = store.db.execute("SELECT hash FROM events ORDER BY seq LIMIT 1").fetchone()
    return {"root": str(Path(store.root).resolve()), "first": row[0] if row else None}


def _meta(store):
    row = store.db.execute("SELECT data FROM derived_projection_meta WHERE id=1").fetchone()
    if row is None:
        raise ProjectionError("Missing projection cursor")
    value = _unpack(row[0])
    if (not isinstance(value, dict) or value.get("schema") != SCHEMA
            or value.get("identity") != _identity(store)
            or type(value.get("seq")) is not int or value["seq"] < 0
            or not isinstance(value.get("head"), str) or len(value["head"]) != 64):
        raise ProjectionError("Foreign or malformed projection cursor")
    return value


def _stamp(st):
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]


def _safe_fd(path, flags, mode=0o600):
    fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), mode)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd)
        raise ProjectionError("Projection must be a regular unlinked file")
    return fd


@contextmanager
def _locked(root):
    fd = _safe_fd(Path(root) / "projection.lock", os.O_RDWR | os.O_CREAT)
    try:
        if os.name == "nt":
            import msvcrt
            if not os.fstat(fd).st_size:
                os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        if os.name == "nt":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        os.close(fd)


def _atomic_parts(path, parts):
    """Replace an export without retaining another full byte buffer."""
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as out:
            name = out.name
            os.chmod(name, 0o600)
            for part in parts:
                out.write(part)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
        name = None
        if os.name != "nt":
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        if name is not None:
            os.unlink(name)


def _file_record(path):
    fd = _safe_fd(path, os.O_RDONLY)
    try:
        st = os.fstat(fd)
        start = max(0, st.st_size - ANCHOR_BYTES)
        os.lseek(fd, start, os.SEEK_SET)
        anchor = os.read(fd, st.st_size - start)
        return {"offset": st.st_size, "stamp": _stamp(st), "anchor_start": start,
                "anchor_size": len(anchor), "anchor_sha256": _digest(anchor)}
    finally:
        os.close(fd)


def _file_cursor(store, relative):
    row = store.db.execute("SELECT data FROM derived_projection_files WHERE path=?", (relative,)).fetchone()
    return None if row is None else _unpack(row[0])


def _save_file(store, relative, value):
    store.db.execute("INSERT INTO derived_projection_files VALUES (?,?) "
                     "ON CONFLICT(path) DO UPDATE SET data=excluded.data", (relative, _pack(value)))


class JournalRollback(ProjectionError):
    """The exports hold events the journal no longer has: an older database was restored or its WAL was lost."""


def _journal_tail(store, root, record):
    """Refuse when events.jsonl past its cursor holds an event the journal lacks or records with another hash.

    Projections are appended only after the journal commits, so an interrupted append leaves journal events
    or a partial line past the cursor. Anything else means the journal lost the tail the exports still hold,
    and truncating or regenerating them would erase the only copy. No cursor means the whole file is checked."""
    offset = record["offset"] if isinstance(record, dict) and type(record.get("offset")) is int and record["offset"] >= 0 else 0
    try:
        fd = _safe_fd(Path(root) / "events.jsonl", os.O_RDONLY)
    except (OSError, ProjectionError):
        return
    lost = []
    with os.fdopen(fd, "rb") as source:
        source.seek(offset)
        for line in source:
            if not line.endswith(b"\n"):
                break
            try:
                event = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if not isinstance(event, dict) or type(event.get("seq")) is not int or not isinstance(event.get("hash"), str):
                continue
            row = store.db.execute("SELECT hash FROM events WHERE seq=?", (event["seq"],)).fetchone()
            if row is None or row[0] != event["hash"]:
                lost.append(event["seq"])
    if lost:
        raise JournalRollback(
            f"events.jsonl holds {len(lost)} event(s) from #{lost[0]} to #{lost[-1]} that the journal "
            f"(now at #{_position(store.db)['seq']}) lacks or records with another hash: an older registry.sqlite3 "
            "was restored or its -wal was lost. The projections are kept unchanged; restore the newer database, "
            "or move events.jsonl, squadra-posta.md and posta/ aside to accept the loss.")


def _recover(path, record):
    try:
        if (not isinstance(record, dict) or type(record.get("offset")) is not int or record["offset"] < 0
                or type(record.get("anchor_start")) is not int or record["anchor_start"] < 0
                or type(record.get("anchor_size")) is not int or not 0 <= record["anchor_size"] <= ANCHOR_BYTES
                or record["anchor_start"] + record["anchor_size"] != record["offset"]
                or not isinstance(record.get("stamp"), list) or len(record["stamp"]) != 5
                or record["stamp"][2] != record["offset"]):
            raise ProjectionError("Malformed projection file cursor")
        fd = _safe_fd(path, os.O_RDWR)
        try:
            st = os.fstat(fd)
            if _stamp(st) == record["stamp"]:
                return False
            if ([st.st_dev, st.st_ino] != record["stamp"][:2] or st.st_size <= record["offset"]):
                raise ProjectionError("Projection file differs from its cursor")
            os.lseek(fd, record["anchor_start"], os.SEEK_SET)
            anchor = os.read(fd, record["anchor_size"])
            if len(anchor) != record["anchor_size"] or _digest(anchor) != record["anchor_sha256"]:
                raise ProjectionError("Committed projection tail changed")
            os.ftruncate(fd, record["offset"])
            os.fsync(fd)
            return True
        finally:
            os.close(fd)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ProjectionError("Missing or mismatched projection file") from exc


def _append(path, parts):
    fd = _safe_fd(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
    tail = b""
    try:
        for part in parts:
            view = memoryview(part)
            while view:
                written = os.write(fd, view)
                if not written:
                    raise OSError("Projection append made no progress")
                view = view[written:]
            tail = part[-ANCHOR_BYTES:] if len(part) >= ANCHOR_BYTES else (tail + part)[-ANCHOR_BYTES:]
        os.fsync(fd)
        st = os.fstat(fd)
        return {"offset": st.st_size, "stamp": _stamp(st), "anchor_start": st.st_size - len(tail),
                "anchor_size": len(tail), "anchor_sha256": _digest(tail)}
    finally:
        os.close(fd)


def render_daily(event):
    """Readable bounded text, with the immutable event digest for complete detail."""
    data = event["data"]
    parts = [f"\n## {event['utc']} · {event['actor']} · {event['kind']} · #{event['seq']}\n"]
    if event["kind"] == "message" and data.get("info") is True:
        parts.append("\nINFO · No ACK required / Nessuna conferma richiesta\n")
    budget = 64 * 1024
    for key in ("task", "id", "to", "message", "status", "stage", "verdict", "title", "body", "text", "summary", "reason", "next"):
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, (str, int, float, bool)):
            text = str(value)
            encoded = text.encode("utf-8")
            if len(encoded) > budget:
                text = encoded[:max(0, budget - 80)].decode("utf-8", "ignore") + "\n[Full text in source event / Testo completo nell'evento sorgente]"
            parts.append(f"\n{key}: {text}\n")
            budget -= len(text.encode("utf-8"))
            if budget <= 0:
                break
    refs, pending, visited = [], [data], 0
    while pending and visited < 512 and len(refs) < 16:
        value = pending.pop()
        visited += 1
        if isinstance(value, dict):
            if {"sha256", "name", "bytes"} <= value.keys():
                refs.append(f"Artifact / Artefatto: {str(value['name'])[:200]} · {str(value['sha256'])[:64]} · {str(value['bytes'])[:30]} bytes")
            pending.extend(v for k, v in islice(value.items(), 64) if k != "files" and isinstance(v, (dict, list)))
        elif isinstance(value, list):
            pending.extend(value[:16])
    parts.extend("\n" + ref + "\n" for ref in refs)
    parts.append(f"\nFull event / Evento completo: events.jsonl · #{event['seq']} · SHA-256 {event['hash']}\n")
    return "".join(parts)


def _day(event):
    day = event["utc"][:10]
    if len(day) != 10 or day[4] != "-" or day[7] != "-" or not day.replace("-", "").isdigit():
        raise ProjectionError("Invalid event day")
    return "posta/exchange-" + day + ".md"


def _folder(root):
    path = root / "posta"
    path.mkdir(mode=0o700, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise ProjectionError("Unsafe projection directory")


def _mail(event):
    return event["kind"] in {"message", "ack", "transport"}


def _freshness(authority, meta):
    events = {"seq": meta["seq"], "head": meta["head"]} if meta else None
    full = meta.get("full_export") if meta else None
    return {"schema": SCHEMA, "authority": authority, "events": events, "full_export": full,
            "events_current": events == authority, "full_export_current": full == authority,
            "captured_only": True}


def freshness(store):
    """No writes; compare committed export positions with the journal now."""
    authority = _position(store.db)
    try:
        meta = _meta(store)
    except (ProjectionError, sqlite3.OperationalError):
        meta = None
    return _freshness(authority, meta)


def _save_meta(store, meta):
    store.db.execute("INSERT INTO derived_projection_meta VALUES (1,?) "
                     "ON CONFLICT(id) DO UPDATE SET data=excluded.data", (_pack(meta),))


def _marker(store):
    _atomic_parts(Path(store.root) / "projection-freshness.json", [(_canonical(freshness(store)) + "\n").encode()])


def _marker_matches(store):
    try:
        fd = _safe_fd(Path(store.root) / "projection-freshness.json", os.O_RDONLY)
        try:
            raw = os.read(fd, 4097)
        finally:
            os.close(fd)
        return len(raw) <= 4096 and json.loads(raw) == freshness(store)
    except (OSError, ValueError, TypeError):
        return False


def _regenerate(store, canonical, previous=None):
    root = Path(store.root)
    _journal_tail(store, root, None)
    events = store.events()
    _folder(root)
    store.db.execute("DELETE FROM derived_projection_files")
    _atomic_parts(root / "events.jsonl", ((canonical(event) + "\n").encode() for event in events))
    _atomic_parts(root / "squadra-posta.md", chain([MAIL_HEADER.encode()], (
        render_daily(event).encode() for event in events if _mail(event))))
    daily = {}
    for event in events:
        daily.setdefault(_day(event), []).append(event)
    for relative, rows in daily.items():
        _atomic_parts(root / relative, chain([DAY_HEADER.encode()], (render_daily(event).encode() for event in rows)))
    for relative in ["events.jsonl", "squadra-posta.md", *daily]:
        _save_file(store, relative, _file_record(root / relative))
    position = {"seq": events[-1]["seq"], "head": events[-1]["hash"]} if events else {"seq": 0, "head": ZERO}
    return {"schema": SCHEMA, "identity": _identity(store), **position,
            "full_export": previous.get("full_export") if previous else None}


def publish(store, canonical=None, summary=None):
    if getattr(store, "read_only", False):
        raise ProjectionError("Read-only stores do not publish projections")
    if store.db.in_transaction:
        raise ProjectionError("Publish requires a completed journal transaction")
    canonical = canonical or _canonical
    root = Path(store.root)
    with _locked(root):
        _folder(root)
        _schema(store.db)
        store.db.execute("BEGIN IMMEDIATE")
        changed, rebuilt, meta = False, False, None
        try:
            try:
                meta = _meta(store)
                _journal_tail(store, root, _file_cursor(store, "events.jsonl"))
                suffix = store.events_since(meta["seq"], meta["head"])
                touched = {"events.jsonl", "squadra-posta.md"} | {_day(event) for event in suffix}
                records = {relative: _file_cursor(store, relative) for relative in touched}
                for relative, record in records.items():
                    path = root / relative
                    if record is None:
                        if relative in {"events.jsonl", "squadra-posta.md"} or path.exists() or path.is_symlink():
                            raise ProjectionError("Missing file cursor")
                    else:
                        if _recover(path, record):
                            _save_file(store, relative, _file_record(path))
                            changed = True
            except JournalRollback:
                raise
            except Exception:
                # The full verifier remains authoritative: its failure propagates.
                meta = _regenerate(store, canonical, meta)
                changed, rebuilt, suffix = True, True, []
            if suffix:
                _folder(root)
                chunks = {"events.jsonl": [(canonical(event) + "\n").encode() for event in suffix]}
                mail = [render_daily(event).encode() for event in suffix if _mail(event)]
                if mail:
                    chunks["squadra-posta.md"] = mail
                for event in suffix:
                    relative = _day(event)
                    chunks.setdefault(relative, [DAY_HEADER.encode()] if records[relative] is None else []).append(render_daily(event).encode())
                for relative, parts in chunks.items():
                    _save_file(store, relative, _append(root / relative, parts))
                meta.update(seq=suffix[-1]["seq"], head=suffix[-1]["hash"])
                changed = True
            if changed:
                _save_meta(store, meta)
            store.db.execute("COMMIT")
        except BaseException:
            store.db.execute("ROLLBACK")
            raise
        if changed or not _marker_matches(store):
            _marker(store)
        if changed and summary is not None:
            summary()
        return {**freshness(store), "changed": changed, "rebuilt": rebuilt}


def mark_full_export(store, seq, head, *, locked=False):
    """Call after explicit full export; never infer that state.json is current."""
    if getattr(store, "read_only", False):
        raise ProjectionError("Read-only stores do not mark exports")
    if store.db.in_transaction:
        raise ProjectionError("Mark export requires a completed journal transaction")
    with nullcontext() if locked else _locked(store.root):
        _folder(Path(store.root))
        _schema(store.db)
        store.db.execute("BEGIN IMMEDIATE")
        try:
            events = store.events()
            if seq:
                anchor = next((event for event in events if event["seq"] == seq), None)
                if anchor is None or anchor["hash"] != head:
                    raise ProjectionError("Full export anchor mismatch")
            elif head != ZERO:
                raise ProjectionError("Full export empty anchor mismatch")
            position = {"seq": seq, "head": head}
            meta = {"schema": SCHEMA, "identity": _identity(store), **position,
                    "full_export": position}
            paths = {"events.jsonl", "squadra-posta.md"} | {_day(event) for event in events if event["seq"] <= seq}
            store.db.execute("DELETE FROM derived_projection_files")
            for relative in paths:
                _save_file(store, relative, _file_record(Path(store.root) / relative))
            _save_meta(store, meta)
            store.db.execute("COMMIT")
        except BaseException:
            store.db.execute("ROLLBACK")
            raise
        _marker(store)
    return freshness(store)
