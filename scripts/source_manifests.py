"""Resolve immutable source file manifests without depending on the registry reducer."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path


MAX_BYTES = 16 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_INLINE = {"repo", "head", "files", "sha256"}
_REFERENCED = {"repo", "head", "sha256", "manifest"}
_REFERENCE = {"sha256", "name", "original", "bytes", "count"}


class ManifestError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise ManifestError(message)


def _canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ManifestError("Source manifest cannot be encoded") from exc


def _sha256(value):
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _digest(body):
    return hashlib.sha256(body).hexdigest()


def _header(value):
    _require(isinstance(value.get("repo"), str) and bool(value["repo"])
             and "\0" not in value["repo"], "Invalid source repository")
    _require(isinstance(value.get("head"), str) and bool(value["head"])
             and "\0" not in value["head"], "Invalid source head")
    _require(_sha256(value.get("sha256")), "Invalid source fingerprint")


def _files(rows):
    _require(isinstance(rows, list), "Source manifest must contain a file list")
    previous = None
    for row in rows:
        _require(isinstance(row, dict), "Invalid source file row")
        path = row.get("path")
        _require(isinstance(path, str) and bool(path) and "\0" not in path
                 and not path.startswith("/")
                 and all(part not in {"", ".", ".."} for part in path.split("/")),
                 "Invalid source file path")
        try:
            order = os.fsencode(path)
        except (UnicodeError, ValueError) as exc:
            raise ManifestError("Invalid source file path encoding") from exc
        _require(previous is None or previous < order,
                 "Source file paths must be unique and byte ordered")
        previous = order
        kind = row.get("kind")
        expected = {"path", "kind"}
        if kind == "file":
            expected |= {"sha256", "executable"}
            _require(type(row.get("executable")) is bool, "Invalid executable flag")
        elif kind == "symlink":
            expected.add("sha256")
        else:
            _require(kind == "deleted", "Invalid source file kind")
        _require(set(row) == expected, "Invalid source file row fields")
        if kind != "deleted":
            _require(_sha256(row.get("sha256")), "Invalid source file digest")


def _inline(value):
    _require(isinstance(value, dict) and set(value) == _INLINE,
             "Invalid inline source fingerprint")
    _header(value)
    _files(value["files"])
    body = _canonical({key: value[key] for key in ("repo", "head", "files")})
    _require(_digest(body) == value["sha256"], "Source fingerprint digest mismatch")


def _reference(value):
    _require(isinstance(value, dict) and set(value) == _REFERENCED,
             "Invalid referenced source fingerprint")
    _header(value)
    ref = value["manifest"]
    _require(isinstance(ref, dict) and set(ref) == _REFERENCE,
             "Invalid source manifest reference")
    _require(_sha256(ref.get("sha256")), "Invalid source manifest digest")
    _require(type(ref.get("bytes")) is int and 2 <= ref["bytes"] <= MAX_BYTES,
             "Source manifest exceeds size bound")
    _require(type(ref.get("count")) is int and 0 <= ref["count"] <= MAX_BYTES,
             "Invalid source file count")
    _require(all(isinstance(ref.get(key), str) and bool(ref[key])
                 and "\0" not in ref[key] for key in ("name", "original")),
             "Invalid source manifest label")
    return ref


def compact(value, write_artifact):
    """Replace a genuine inline file list; write_artifact has Store.artifact_bytes's API.

    Legacy partial fingerprints used by older callers remain inline. A referenced
    value is never downgraded to that compatibility path.
    """
    if isinstance(value, dict) and "manifest" in value:
        _reference(value)
        return value
    if not (isinstance(value, dict) and _INLINE <= set(value)
            and _sha256(value.get("sha256"))):
        return value
    _inline(value)
    body = _canonical(value["files"])
    _require(len(body) <= MAX_BYTES, "Source manifest exceeds size bound")
    ref = write_artifact(body, "source-files.json",
                         "generated-source-manifest:" + value["sha256"])
    _require(isinstance(ref, dict), "Invalid source artifact receipt")
    ref = {**ref, "count": len(value["files"])}
    result = {key: value[key] for key in ("repo", "head", "sha256")}
    result["manifest"] = ref
    _reference(result)
    _require(ref["sha256"] == _digest(body) and ref["bytes"] == len(body),
             "Source artifact receipt mismatch")
    return result


def _read(ref, artifacts_root):
    """Open the digest under a directory descriptor; neither final path may be a link."""
    root_fd = file_fd = None
    try:
        root_fd = os.open(Path(artifacts_root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        file_fd = os.open(ref["sha256"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                          dir_fd=root_fd)
        before = os.fstat(file_fd)
        _require(stat.S_ISREG(before.st_mode) and before.st_size == ref["bytes"],
                 "Missing, changed or nonregular source manifest")
        chunks, length = [], 0
        while length <= ref["bytes"]:
            block = os.read(file_fd, min(64 * 1024, ref["bytes"] + 1 - length))
            if not block:
                break
            chunks.append(block)
            length += len(block)
        after = os.fstat(file_fd)
        _require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                  before.st_ctime_ns) ==
                 (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
                  after.st_ctime_ns), "Source manifest changed while reading")
        body = b"".join(chunks)
        _require(len(body) == ref["bytes"] and _digest(body) == ref["sha256"],
                 "Source manifest digest or size mismatch")
        return body
    except OSError as exc:
        raise ManifestError("Cannot read source manifest: " + type(exc).__name__) from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if root_fd is not None:
            os.close(root_fd)


def resolve(value, artifacts_root):
    """Resolve and verify a referenced fingerprint; preserve legacy inline values."""
    if not isinstance(value, dict) or "manifest" not in value:
        return value
    ref = _reference(value)
    body = _read(ref, artifacts_root)
    try:
        rows = json.loads(body)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ManifestError("Invalid source manifest JSON") from exc
    _files(rows)
    _require(len(rows) == ref["count"], "Source manifest count mismatch")
    _require(_canonical(rows) == body, "Source manifest must be canonical JSON")
    result = {key: value[key] for key in ("repo", "head", "sha256")}
    result["files"] = rows
    _inline(result)
    return result


def transform_run(value, transform):
    """Visit the two source bindings of a run, never its arbitrary result payload."""
    result = dict(value)
    for key in ("before", "after"):
        if key in value:
            result[key] = transform(value[key])
    return result


def transform_packet(value, transform):
    """Use before hashing a NEW packet, or for an explicit comparison view only."""
    result = dict(value)
    if "candidate" in value:
        result["candidate"] = transform(value["candidate"])
    if "proofs" in value:
        result["proofs"] = [transform_run(run, transform) for run in value["proofs"]]
    if value.get("fix_result") is not None:
        result["fix_result"] = transform_event("challenge", value["fix_result"], transform)
    return result


def transform_event(kind, value, transform):
    """Visit declared event bindings, preserving stored packet bytes and user data."""
    if kind == 'replace':
        return {**value, 'operations': [{**op, 'data': transform_event(op['kind'], op['data'], transform)}
                                      for op in value['operations']]}
    if kind in {"run.start", "run.end"}:
        return transform_run(value, transform)
    if kind in {'reassign', 'replacement.pool'} and 'packet_data' in value:
        return {**value, 'packet_data': transform_packet(value['packet_data'], transform)}
    if kind == "deliver" and "snapshot" in value:
        return {**value, "snapshot": transform(value["snapshot"])}
    if kind == "challenge" and "candidate" in value:
        return {**value, "candidate": transform(value["candidate"])}
    return value


def transform_task(value, transform):
    result = dict(value)
    if "candidate" in value:
        result["candidate"] = transform(value["candidate"])
    if "proofs" in value:
        result["proofs"] = [transform_run(run, transform) for run in value["proofs"]]
    if "delivery" in value:
        result["delivery"] = transform_event("deliver", value["delivery"], transform)
    if value.get("fix_result") is not None:
        result["fix_result"] = transform_event("challenge", value["fix_result"], transform)
    return result


def materialize_task(value, artifacts_root):
    """Resolve a demanded task's bindings; its immutable packet stays as recorded."""
    return transform_task(value, lambda fingerprint: resolve(fingerprint, artifacts_root))


def materialize_known(state, artifacts_root):
    """Full/export compatibility view; ordinary reads should materialize one entity."""
    result = dict(state)
    transform = lambda fingerprint: resolve(fingerprint, artifacts_root)
    if "tasks" in state:
        result["tasks"] = {key: transform_task(value, transform)
                           for key, value in state["tasks"].items()}
    if "runs" in state:
        result["runs"] = {key: transform_run(value, transform)
                          for key, value in state["runs"].items()}
    if "challenges" in state:
        result["challenges"] = [transform_event("challenge", value, transform)
                                for value in state["challenges"]]
    return result
