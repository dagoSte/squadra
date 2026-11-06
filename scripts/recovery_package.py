"""Package-level recovery helpers shared by recovery, candidates and storage.

Leaf module: it imports only the standard library and registry, never recovery, frozen_candidate,
storage_policy, storage_reuse or storage_compact, so those modules can depend on it without a cycle.
"""
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re

from registry import digest, require

SECRET_NAMES = (".env*", "*.pem", "*.key", "*.p12", "id_rsa*", "id_ed25519*", ".netrc",
                "auth.json", ".credentials.json", "credentials*", "*.pfx", "*.ppk", ".git-credentials",
                "*.jks", "*.keystore", "id_ecdsa*", "id_dsa*", ".npmrc", ".pypirc")
TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist")
KEY_HEADER = re.compile(rb'-{5}BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-{5}|PuTTY-User-Key-File-[0-9]+:')


def secret(path, body=b"", tracked=False):
    """A secret name or a private-key header; a tracked template is judged by its content only."""
    name = path.name.lower()
    return (not (tracked and name.endswith(TEMPLATE_SUFFIXES)) and any(fnmatch.fnmatch(name, p) for p in SECRET_NAMES)) or private_key(body)


def private_key(body):
    return KEY_HEADER.search(body) is not None


def inside(path, parent):
    return path == parent or parent in path.parents


def file_sha256(path):
    """Stream large bundles instead of holding them in memory."""
    with open(path, 'rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def record_file(path, root):
    rel = str(path.relative_to(root))
    parts = Path(rel).parts
    frozen_git = (len(parts) >= 6 and parts[:2] == ('registry', 'candidates') and
                  len(parts[2]) == 64 and all(c in '0123456789abcdef' for c in parts[2]) and
                  parts[3:5] == ('repo', '.git'))
    require(not any(p.lower() == '.git' for p in parts) or frozen_git, "Git internals cannot be explicitly included")
    require(all(not p.is_symlink() for p in path.parents if p.is_relative_to(root)),
            "Symlinked parent directory refused: " + rel)
    if path.is_symlink():
        target = os.readlink(path)
        require(not Path(target).is_absolute() and inside(path.resolve(), root), "Escaping or absolute symlink refused: " + rel)
        return {"path": rel, "kind": "symlink", "target": target, "sha256": digest(os.fsencode(target))}
    require(path.is_file(), "Only regular files and safe relative symlinks can be included: " + rel)
    body = path.read_bytes()
    return {"path": rel, "kind": "file", "mode": path.stat().st_mode & 0o777, "bytes": len(body), "sha256": digest(body)}


def volatile(rel, size):
    """SQLite's shared-memory file, and an empty write-ahead log, appear when a package registry is only read."""
    return rel == "registry/registry.sqlite3-shm" or (rel == "registry/registry.sqlite3-wal" and size == 0)


def inventory(folder):
    result = {}
    for path in sorted(folder.rglob("*")):
        if path == folder / "CHECKSUMS.json":
            continue
        if path.is_file() or path.is_symlink():
            rel = str(path.relative_to(folder))
            if not path.is_symlink() and volatile(rel, path.stat().st_size):
                continue
            result[rel] = record_file(path, folder)
    return result


def verify_package(folder):
    folder = Path(folder).resolve(strict=True)
    require((folder / "MANIFEST.json").is_file(),
            "Expected a save package from squadra save, not a registry checkpoint; MANIFEST.json is missing")
    require(not (folder / "CHECKSUMS.json").is_symlink(), "Checksum symlink refused")
    require((folder / "CHECKSUMS.json").is_file(), "Save package missing regular CHECKSUMS.json")
    expected = json.loads((folder / "CHECKSUMS.json").read_text())
    require(isinstance(expected, dict), "Checksum inventory must be a mapping")
    for rel in expected:
        require(not Path(rel).is_absolute() and '..' not in Path(rel).parts, "Escaping checksum path")
    expected = {rel: row for rel, row in expected.items()
                if not (isinstance(row, dict) and volatile(rel, row.get("bytes")))}
    require(inventory(folder) == expected, "Package missing, added, escaping or tampered file")
    manifest = json.loads((folder / "MANIFEST.json").read_text())
    require(manifest.get("schema") == 1 and manifest.get("status") == "saved", "Incomplete/unsupported package")
    return manifest
