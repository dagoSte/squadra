"""Save unfinished local work and verify its recovery in a separate folder."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from registry import Store, Error, VERSION, atomic, canonical, digest, git, now, require, snapshot

SECRET_NAMES = (".env*", "*.pem", "*.key", "*.p12", "id_rsa*", "id_ed25519*", ".netrc",
                "auth.json", ".credentials.json", "credentials*")
TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist")


def secret(path, body=b"", tracked=False):
    """A secret name or a private-key header; a tracked template is judged by its content only."""
    name = path.name.lower()
    return (not (tracked and name.endswith(TEMPLATE_SUFFIXES)) and any(fnmatch.fnmatch(name, p) for p in SECRET_NAMES)) or any(
        line.strip().startswith(b"-----BEGIN ") and b"PRIVATE KEY-----" in line for line in body.splitlines())


def inside(path, parent):
    return path == parent or parent in path.parents


def index(repo):
    return digest(git(repo, "ls-files", "--stage", "-z"))


def repositories(state, explicit):
    names = explicit or [state["workspace"]["project"], *[a["repo"] for a in state["agents"].values()],
                         *[t["repo"] for t in state["tasks"].values()]]
    result = []
    for name in names:
        path = Path(name).resolve(strict=True)
        root = Path(git(path, "rev-parse", "--show-toplevel").decode().strip()).resolve()
        if root not in result:
            result.append(root)
    return result


def supported(repo):
    require(not git(repo, "ls-files", "--stage").startswith(b"160000 ") and
            b"\n160000 " not in git(repo, "ls-files", "--stage"), "Submodule checkout not qualified")
    config = subprocess.run(["git", "config", "--get", "core.sparseCheckout"], cwd=repo, capture_output=True)
    require(config.stdout.strip().lower() not in {b"true", b"1"}, "Sparse checkout not qualified")
    attributes = git(repo, "ls-files", "--", "**/.gitattributes", ".gitattributes").decode().splitlines()
    require(not any(b"filter=lfs" in (repo / p).read_bytes() for p in attributes if (repo / p).is_file()),
            "Git LFS checkout not qualified")


def record_file(path, root):
    rel = str(path.relative_to(root))
    parts = Path(rel).parts
    frozen_git = (len(parts) >= 6 and parts[:2] == ('registry', 'candidates') and
                  len(parts[2]) == 64 and all(c in '0123456789abcdef' for c in parts[2]) and
                  parts[3:5] == ('repo', '.git'))
    require('.git' not in parts or frozen_git, "Git internals cannot be explicitly included")
    if path.is_symlink():
        target = os.readlink(path)
        require(not Path(target).is_absolute() and inside(path.resolve(), root), "Escaping or absolute symlink refused: " + rel)
        return {"path": rel, "kind": "symlink", "target": target, "sha256": digest(os.fsencode(target))}
    require(path.is_file(), "Only regular files and safe relative symlinks can be included: " + rel)
    body = path.read_bytes()
    return {"path": rel, "kind": "file", "mode": path.stat().st_mode & 0o777, "bytes": len(body), "sha256": digest(body)}


def included_files(repo, includes):
    result = set()
    for value in includes:
        path = Path(os.path.abspath(value))
        if not inside(path, repo):
            continue
        require(path.exists() or path.is_symlink(), "Missing explicitly included file")
        if path.is_dir() and not path.is_symlink():
            result.update(p for p in path.rglob("*") if p.is_file() or p.is_symlink())
        else:
            result.add(path)
    return result


LONG_GIT_SECONDS = 3600


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


def save(store, destination, repos=None, includes=None, temporary=False, declared_offsite=None):
    require(os.name != "nt", "Windows full recovery not qualified")
    destination = Path(destination).resolve()
    state = store.state()
    selected = repositories(state, repos)
    registered = repositories(state, None)
    require(not destination.exists(), "Save destination must be new")
    require(not any(inside(destination, r) for r in [*selected, *registered, store.root]), "Save outside all source/registry roots")
    require(temporary or not any(inside(destination, p) for p in [Path(tempfile.gettempdir()).resolve(), Path('/private/tmp')]),
            "Durable save cannot live in tmp; --temporary for tests")
    for value in includes or []:
        require(any(inside(Path(os.path.abspath(value)), r) for r in selected), "Include must belong to a selected repository")
    destination.mkdir(parents=True, mode=0o700)
    manifest = {"schema": 1, "version": VERSION, "utc": now(), "status": "building", "repositories": [],
                "scope": "explicit partial selection" if repos else "all registered repositories",
                "destination": "local checkpoint" if destination.stat().st_dev == store.root.stat().st_dev else "different filesystem, external disk unverified",
                "declared_offsite": declared_offsite, "offsite_verified": False,
                "limitations": "Current checkout/HEAD name and private-key-header filters only; no complete Git-history or registry secret scan. No upload. Restored approvals stay historical."}
    try:
        guards = []
        for n, repo in enumerate(selected):
            supported(repo); before = snapshot(repo, store.root); old_index = index(repo)
            extra = included_files(repo, includes or [])
            require(not any(inside(p, store.root) for p in extra), "Registry is captured separately, not as a source include")
            extra_before = {str(p.relative_to(repo)): record_file(p, repo) for p in extra}
            guards.append((repo, before, old_index, extra, extra_before))
            tracked = set(os.fsdecode(p) for p in git(repo, "ls-files", "-z").split(b'\0') if p)
            head_links = {os.fsdecode(row.split(b'\t',1)[1]): os.fsdecode(git(repo, "show", "HEAD:" + os.fsdecode(row.split(b'\t',1)[1])))
                          for row in git(repo,"ls-tree","-r","-z","HEAD").split(b'\0') if row.startswith(b"120000 ")}
            for rel, target in head_links.items():
                require(not Path(target).is_absolute() and inside(((repo/rel).parent/target).resolve(),repo),
                        "Escaping or absolute HEAD symlink refused: " + rel)
            for row in git(repo,"ls-files","--stage","-z").split(b'\0'):
                if row.startswith(b"120000 "):
                    rel=os.fsdecode(row.split(b'\t',1)[1]);sha=row.split(b' ',2)[1].decode()
                    target=os.fsdecode(git(repo,"cat-file","blob",sha))
                    require(not Path(target).is_absolute() and inside(((repo/rel).parent/target).resolve(),repo),
                            "Escaping or absolute index symlink refused: " + rel)
            head_files = set(os.fsdecode(p) for p in git(repo, "ls-tree", "-r", "--name-only", "-z", "HEAD").split(b'\0') if p)
            for rel in sorted(tracked | head_files):
                path = repo / rel
                if path.is_symlink(): record_file(path, repo)
                body = path.read_bytes() if path.is_file() and not path.is_symlink() else b""
                historical = git(repo, "show", "HEAD:" + rel) if rel in head_files else b""
                require(not secret(path, body, True) and not secret(path, historical, True), "Tracked secret candidate refused: " + rel)
            name = "repo-" + str(n); folder = destination / name; folder.mkdir(mode=0o700)
            git(repo, "bundle", "create", str(folder / "history.bundle"), "--all", "HEAD", timeout=LONG_GIT_SECONDS)
            git(repo, "bundle", "verify", str(folder / "history.bundle"), timeout=LONG_GIT_SECONDS)
            (folder / "index.patch").write_bytes(git(repo, "diff", "--cached", "--binary", "--no-ext-diff"))
            (folder / "worktree.patch").write_bytes(git(repo, "diff", "--binary", "--no-ext-diff"))
            untracked = {repo / os.fsdecode(p) for p in git(repo, "ls-files", "--others", "--exclude-standard", "-z").split(b'\0')
                         if p and not inside(repo / os.fsdecode(p), store.root)}
            copied, excluded = [], []
            for path in sorted((untracked | extra) - {repo / p for p in tracked}):
                row = record_file(path, repo)
                if secret(path, path.read_bytes() if row["kind"] == "file" else b""):
                    excluded.append(row["path"]); continue
                dest = folder / "files" / row["path"]; dest.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                if row["kind"] == "symlink": dest.symlink_to(row["target"])
                else: shutil.copy2(path, dest)
                copied.append(row)
            expected = {**before, "files": [r for r in before["files"] if r["path"] not in excluded]}
            expected.pop("sha256")
            manifest["repositories"].append({"folder": name, "original": str(repo), "head": before["head"],
                "branch": git(repo, "branch", "--show-current").decode().strip(), "snapshot": expected,
                "index_sha256": old_index, "files": copied, "excluded": excluded,
                "stash_entries": len(git(repo, "stash", "list").splitlines()),
                "extra": [r for p, r in extra_before.items() if p not in excluded and p not in untracked]})
            require(snapshot(repo, store.root) == before and index(repo) == old_index and
                    {str(p.relative_to(repo)): record_file(p, repo) for p in extra} == extra_before, "Torn source/index snapshot")
        report = {}
        store.checkpoint(destination / "registry", audit_source=False, report=report)
        if not report:
            check = Store(destination / "registry")
            try:
                events = check.audit()["events"]
                report = {"event_count": len(events), "event_head": events[-1]["hash"] if events else '0'*64}
            finally: check.close()
        scrubbed = report.pop("scrubbed", None)
        candidates = report.pop("candidates", None)
        if candidates is not None:
            manifest["candidates"] = candidates
        manifest["registry"] = report
        if scrubbed:
            manifest["scrubbed"] = scrubbed
        for repo, before, old_index, extra, extra_before in guards:
            require(snapshot(repo, store.root) == before and index(repo) == old_index and
                    {str(p.relative_to(repo)): record_file(p, repo) for p in extra} == extra_before,
                    "Torn source/index snapshot across registered repositories")
        manifest["status"] = "saved"
        atomic(destination / "MANIFEST.json", (canonical(manifest) + '\n').encode())
        atomic(destination / "CHECKSUMS.json", (canonical(inventory(destination)) + '\n').encode())
        return manifest
    except BaseException as exc:
        atomic(destination / "FAILED.json", (canonical({"status": "incomplete", "error": str(exc)}) + '\n').encode())
        raise


def verify_package(folder):
    folder = Path(folder).resolve(strict=True)
    require(not (folder / "CHECKSUMS.json").is_symlink(), "Checksum symlink refused")
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


def restore_refs(package, bundle, repo, saved_stashes=None):
    """Recreate the stash from the bundle and name every other ref the clone does not map."""
    stash, dropped = False, []
    for line in git(package, "bundle", "list-heads", str(bundle), timeout=LONG_GIT_SECONDS).decode().splitlines():
        sha, _, ref = line.partition(" ")
        if ref == "refs/stash":
            try:
                git(repo, "cat-file", "-e", sha + "^{commit}")
            except Error:
                dropped.append(ref)
                continue
            subject = git(repo, "log", "-1", "--format=%s", sha).decode().strip() or "restored stash"
            git(repo, "update-ref", "--create-reflog", "-m", subject, "refs/stash", sha)
            stash = True
        elif ref != "HEAD" and not ref.startswith(("refs/heads/", "refs/tags/")):
            dropped.append(ref)
    result = {"stash_restored": stash, "refs_not_restored": sorted(dropped)}
    if isinstance(saved_stashes, int):
        # A bundle keeps only the newest stash entry; older ones are named, not silently lost.
        result["stash_entries_not_restored"] = max(0, saved_stashes - int(stash))
    return result


def restore_check(package, destination):
    package = Path(package).resolve(strict=True)
    manifest = verify_package(package)  # Validate saved data before opening SQLite or Git.
    target = Path(destination).resolve()
    require(not target.exists() and not inside(target, package), "Restore destination must be new and outside package")
    target.mkdir(parents=True, mode=0o700)
    result = {"status": "failed", "utc": now(), "package": str(package), "repositories": [],
              "authority": "Historical only; reconcile paths/candidates/permissions before explicit resume"}
    try:
        shutil.copytree(package / "registry", target / "registry", symlinks=True)
        store = Store(target / "registry")
        try:
            checked = store.audit()
            events = checked["events"]
            require({"event_count": len(events), "event_head": events[-1]["hash"] if events else '0'*64} == manifest["registry"], "Registry head/count mismatch")
            result["registry_integrity"] = "passed"
            if checked["scrubbed"]:
                result["scrubbed"] = checked["scrubbed"]
        finally: store.close()
        for row in manifest["repositories"]:
            name = row["folder"]; require(Path(name).name == name and name.startswith("repo-"), "Invalid repository folder")
            source = package / name; repo = target / name
            bundle = source / "history.bundle"
            git(package, "clone", "--no-hardlinks", str(bundle), str(repo), timeout=LONG_GIT_SECONDS)
            git(repo, "checkout", "--detach", row["head"], timeout=LONG_GIT_SECONDS)
            restored_refs = restore_refs(package, bundle, repo, row.get("stash_entries"))
            for filename, extra in [("index.patch", ["--index"]), ("worktree.patch", [])]:
                patch = source / filename
                if patch.stat().st_size:
                    git(repo, "apply", *extra, str(patch), timeout=LONG_GIT_SECONDS)
            for entry in row["files"]:
                rel = entry["path"]; path = repo / rel
                require(not Path(rel).is_absolute() and '..' not in Path(rel).parts and inside(path.resolve(), repo), "Escaping restore file")
                path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                require(not path.exists() and not path.is_symlink(), "Restore file would overwrite tracked data")
                old = source / "files" / rel
                if entry["kind"] == "symlink": path.symlink_to(entry["target"])
                else: shutil.copy2(old, path)
                require(record_file(path, repo) == entry, "Restored untracked mode/content mismatch")
            actual = snapshot(repo)
            require(actual["head"] == row["head"] and actual["files"] == row["snapshot"]["files"] and index(repo) == row["index_sha256"], "Restored source/index mismatch")
            result["repositories"].append({"original": row["original"], "restored": str(repo), "head": row["head"], "verified": True,
                                           **restored_refs})
        frozen = Store(target / "registry")
        try:
            frozen.append("restore.freeze", "system", {"reason": "Historical restore; explicit reconciliation and owner resume required"})
            frozen.project()
            result["restored_paused"] = frozen.state()["paused"]
        finally: frozen.close()
        result["status"] = "passed"
        (target / "RESUME.md").write_text("Historical recovery copy. Do not replay assignments or trust old absolute candidate paths automatically. Read RESTORE-RESULT.json mappings, reconcile sources/resources and await explicit owner resume.\n")
        return result
    finally:
        atomic(target / "RESTORE-RESULT.json", (canonical(result) + '\n').encode())
