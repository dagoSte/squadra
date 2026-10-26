"""Save unfinished local work and verify its recovery in a separate folder."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

from registry import Store, Error, VERSION, atomic, canonical, digest, git, git_blobs, git_process, now, require, snapshot

SECRET_NAMES = (".env*", "*.pem", "*.key", "*.p12", "id_rsa*", "id_ed25519*", ".netrc",
                "auth.json", ".credentials.json", "credentials*", "*.pfx", "*.ppk", ".git-credentials",
                "*.jks", "*.keystore", "id_ecdsa*", "id_dsa*", ".npmrc", ".pypirc")
TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist")
KEY_HEADER = re.compile(rb'-{5}BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-{5}|PuTTY-User-Key-File-[0-9]+:')


class DestinationRefused(Error):
    """Invalid save destination, rejected before recording an allowance."""


def secret(path, body=b"", tracked=False):
    """A secret name or a private-key header; a tracked template is judged by its content only."""
    name = path.name.lower()
    return (not (tracked and name.endswith(TEMPLATE_SUFFIXES)) and any(fnmatch.fnmatch(name, p) for p in SECRET_NAMES)) or private_key(body)


def private_key(body):
    return KEY_HEADER.search(body) is not None


def index_rows(entries):
    rows = []
    for row in entries.split(b'\0'):
        if row:
            metadata, path = row.split(b'\t', 1)
            mode, sha, stage = metadata.decode('ascii').split()
            rows.append((os.fsdecode(path), mode, sha, stage))
    return rows


def head_rows(repo, revision='HEAD'):
    rows = {}
    for row in git(repo, 'ls-tree', '-r', '-z', revision).split(b'\0'):
        if row:
            metadata, path = row.split(b'\t', 1)
            mode, _, sha = metadata.decode('ascii').split()
            rows[os.fsdecode(path)] = (mode, sha)
    return rows


def blob_facts(repo, oids, links=(), hashes=()):
    """Keep scan results, link targets and allowance hashes, never a repository's blob bodies."""
    links, hashes = set(links), set(hashes)
    return {sha: {'private_key': private_key(body),
                  'target': os.fsdecode(body) if sha in links else None,
                  'sha256': digest(body) if sha in hashes else None}
            for sha, body in git_blobs(repo, oids)}


def checked_index(repo, *, details=False, names=()):
    """Check every staged blob in one batch, bound to the unchanged index."""
    entries = git(repo, 'ls-files', '--stage', '-z')
    checked = digest(entries); rows = index_rows(entries)
    head = git(repo, 'rev-parse', 'HEAD').decode().strip() if details else None
    heads = head_rows(repo, head) if details else {}
    names = set(names)
    links = {sha for _, mode, sha, _ in rows if mode == '120000'} | {
        sha for mode, sha in heads.values() if mode == '120000'}
    hashes = {sha for path, _, sha, _ in rows if path in names} | {
        sha for path, (_, sha) in heads.items() if path in names}
    facts = blob_facts(repo, [sha for _, mode, sha, _ in rows if mode != '160000'], links, hashes)
    for path, mode, sha, _ in rows:
        if mode != '160000':
            require(not facts[sha]['private_key'], 'Tracked secret candidate refused: ' + path +
                    '; private-key content in staged (index) blob')
    require(index(repo) == checked, 'Index changed during staged private-key check')
    return {'sha256': checked, 'entries': entries, 'rows': rows, 'head': head, 'heads': heads,
            'facts': facts, 'links': links, 'hashes': hashes} if details else checked


def allowed_files(repo, names, *, rows=None, heads=None, facts=None):
    """Collect exact name allowances using one metadata read and one blob batch."""
    entries = git(repo, 'ls-files', '--stage', '-z') if rows is None else None
    rows = index_rows(entries) if rows is None else rows
    heads = head_rows(repo) if heads is None else heads
    staged = {path: sha for path, _, sha, stage in rows if stage == '0'}
    for rel in names:
        path = repo / rel
        require(rel in staged and path.is_file() and path.resolve() == path,
                '--allow-name needs an exact tracked regular file: ' + rel)
    oids = [sha for rel in names for sha in
            ([staged[rel], heads[rel][1]] if rel in heads else [staged[rel]])]
    if facts is None:
        facts = blob_facts(repo, oids, hashes=oids)
    result = []
    for rel in names:
        body = (repo / rel).read_bytes(); staged_info = facts[staged[rel]]
        historical = facts[heads[rel][1]] if rel in heads else None
        require(not staged_info['private_key'], 'Private-key content refused in staged (index) blob: ' + rel)
        require(not private_key(body) and not (historical and historical['private_key']),
                'Private-key content refused in HEAD, index or working tree: ' + rel)
        result.append({'path': rel, 'sha256': digest(body), 'index_sha256': staged_info['sha256'],
                       'head_sha256': historical['sha256'] if historical else None})
    if entries is not None:
        require(index(repo) == digest(entries), 'Index changed during name allowance check')
    return result


def allowed_file(repo, rel):
    return allowed_files(repo, [rel])[0]


def save_destination(store, state, destination, repos, temporary):
    destination = Path(destination).resolve()
    selected = repositories(state, repos)
    registered = repositories(state, None)
    if destination.exists():
        raise DestinationRefused("Save destination must be new")
    if any(inside(destination, r) for r in [*selected, *registered, store.root]):
        raise DestinationRefused("Save outside all source/registry roots")
    if not temporary and any(inside(destination, p) for p in [Path(tempfile.gettempdir()).resolve(), Path('/private/tmp')]):
        raise DestinationRefused("Durable save cannot live in tmp; --temporary for tests")
    return destination, selected


def allow_names(store, actor, destination, repos, names, reason, temporary=False):
    """Record a coordinator's exact, content-bound exception for one save destination."""
    state = store.state()
    require(state['agents'][actor]['role'] == 'coordinator', "Coordinator role required")
    require(len(repos) == 1 and isinstance(reason, str) and reason.strip(),
            "--allow-name requires one --repo and a nonblank --reason")
    require(len(set(names)) == len(names), "Duplicate --allow-name path")
    for name in names:
        require(name and not Path(name).is_absolute() and Path(name).as_posix() == name and
                not any(part in {'', '.', '..'} for part in name.split('/')) and
                not any(c in name for c in '*?[\\'), "--allow-name requires an exact relative path, never a pattern")
    destination, selected = save_destination(store, state, destination, repos, temporary)
    repo = selected[0]
    files = allowed_files(repo, names)
    allowance = {"repo": str(repo), "destination": str(destination), "files": files}
    decision = 'save-name-' + uuid.uuid4().hex
    store.append('decision', actor, {'id': decision, 'reason': reason, 'save_name_allowance': allowance})
    return decision


def inside(path, parent):
    return path == parent or parent in path.parents


def index(repo):
    return digest(git(repo, "ls-files", "--stage", "-z"))


def repositories(state, explicit):
    names = explicit or [state["workspace"]["project"], *[a["repo"] for a in state["agents"].values()],
                         *[t["repo"] for t in state["tasks"].values()]]
    result, seen = [], set()
    for name in names:
        path = Path(name).resolve(strict=True)
        if path in seen:
            continue
        seen.add(path)
        root = Path(git(path, "rev-parse", "--show-toplevel").decode().strip()).resolve()
        if root not in result:
            result.append(root)
    return result


def supported(repo, entries=None):
    entries = git(repo, 'ls-files', '--stage', '-z') if entries is None else entries
    require(not any(row.startswith(b'160000 ') for row in entries.split(b'\0')), 'Submodule checkout not qualified')
    config = git_process(repo, "config", "--get", "core.sparseCheckout")
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


def save(store, destination, repos=None, includes=None, temporary=False, declared_offsite=None, name_allowance=None):
    require(os.name != "nt", "Windows full recovery not qualified")
    state = store.state()
    destination, selected = save_destination(store, state, destination, repos, temporary)
    allowance = None
    if name_allowance is not None:
        decision = next((d for d in state['decisions'] if d.get('id') == name_allowance), None)
        require(decision is not None and state['agents'][decision['actor']]['role'] == 'coordinator',
                "Recorded coordinator allowance required")
        allowance = decision.get('save_name_allowance')
        require(allowance and allowance['destination'] == str(destination) and
                selected == [Path(allowance['repo'])], "Name allowance belongs to one exact save and repository")
    for value in includes or []:
        require(any(inside(Path(os.path.abspath(value)), r) for r in selected), "Include must belong to a selected repository")
    names = [row['path'] for row in allowance['files']] if allowance else []
    checked_indexes = {repo: checked_index(repo, details=True, names=names) for repo in selected}
    destination.mkdir(parents=True, mode=0o700)
    manifest = {"schema": 1, "version": VERSION, "utc": now(), "status": "building", "repositories": [],
                "scope": "explicit partial selection" if repos else "all registered repositories",
                "destination": "local checkpoint" if destination.stat().st_dev == store.root.stat().st_dev else "different filesystem, external disk unverified",
                "declared_offsite": declared_offsite, "offsite_verified": False,
                "limitations": "Live tracked files: names and private-key headers anywhere in HEAD/index blobs and regular working tree content (including JSON-escaped PEM/OpenSSH and armored PGP); untracked/include files: names and those headers in current regular-file content. Retained frozen candidates: private-key headers in every HEAD/index/working tree version refuse copying; filename checks apply only to untracked or changed working-tree rows against delivered HEAD, so unchanged committed names may pass. Candidate template exceptions require delivered HEAD membership. Live tracked source name-only refusals still require --allow-name. Earlier Git history, other refs including refs/stash, and registry artifacts are not scanned for secrets; the all-ref bundle can contain unchecked history. Runtime runs/ sidecars are not retained by the registry checkpoint. No upload. Restored approvals stay historical."}
    if allowance is not None:
        manifest['name_allowance'] = {"decision": name_allowance, **allowance}
    guards = []
    try:
        for n, repo in enumerate(selected):
            checked = checked_indexes[repo]
            supported(repo, checked['entries']); before = snapshot(repo, store.root); old_index = checked['sha256']
            require(before['head'] == checked['head'], 'HEAD changed after staged private-key check')
            extra = included_files(repo, includes or [])
            require(not any(inside(p, store.root) for p in extra), "Registry is captured separately, not as a source include")
            extra_before = {str(p.relative_to(repo)): record_file(p, repo) for p in extra}
            guards.append((repo, before, old_index, extra, extra_before))
            rows, heads, facts = checked['rows'], checked['heads'], checked['facts']
            tracked = {path for path, _, _, _ in rows}
            facts.update(blob_facts(repo, [sha for _, sha in heads.values() if sha not in facts],
                                    checked['links'], checked['hashes']))
            for rel, (mode, sha) in heads.items():
                if mode != '120000':
                    continue
                target = facts[sha]['target']
                require(not Path(target).is_absolute() and inside(((repo/rel).parent/target).resolve(),repo),
                        "Escaping or absolute HEAD symlink refused: " + rel)
            for rel, mode, sha, _ in rows:
                if mode == '120000':
                    target = facts[sha]['target']
                    require(not Path(target).is_absolute() and inside(((repo/rel).parent/target).resolve(),repo),
                            "Escaping or absolute index symlink refused: " + rel)
            head_files = heads.keys()
            allowed = {row['path']: row for row in allowance['files']} if allowance else {}
            require(allowed.keys() <= tracked, "Allowed tracked files changed before copy")
            verified = {row['path']: row for row in allowed_files(repo, list(allowed), rows=rows,
                                                                 heads=heads, facts=facts)}
            for rel in sorted(tracked | head_files):
                path = repo / rel
                if path.is_symlink(): record_file(path, repo)
                body = path.read_bytes() if path.is_file() and not path.is_symlink() else b""
                if rel in allowed:
                    require(verified[rel] == allowed[rel], "Allowed file changed before copy: " + rel)
                else:
                    require(not secret(path, body, True) and not (rel in heads and facts[heads[rel][1]]['private_key']),
                            "Tracked secret candidate refused: " + rel +
                            "; for a name-only refusal the coordinator can use save --repo ROOT --allow-name " +
                            rel + " --reason TEXT")
            name = "repo-" + str(n); folder = destination / name; folder.mkdir(mode=0o700)
            git(repo, "bundle", "create", str(folder / "history.bundle"), "--all", "HEAD", timeout=LONG_GIT_SECONDS)
            git(repo, "bundle", "verify", str(folder / "history.bundle"), timeout=LONG_GIT_SECONDS)
            staged_patch = git(repo, "diff", "--cached", "--binary", "--no-ext-diff")
            require(index(repo) == old_index, "Index changed after staged private-key check")
            (folder / "index.patch").write_bytes(staged_patch)
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
        atomic(destination / "MANIFEST.json", (canonical(manifest) + '\n').encode())
        complete = {**manifest, "status": "saved"}
        complete_body = (canonical(complete) + '\n').encode()
        checksums = inventory(destination)
        checksums['MANIFEST.json'].update(bytes=len(complete_body), sha256=digest(complete_body))
        atomic(destination / "CHECKSUMS.json", (canonical(checksums) + '\n').encode())
        atomic(destination / "MANIFEST.json", complete_body)
        return complete
    except BaseException as exc:
        atomic(destination / "FAILED.json", (canonical({"status": "incomplete", "error": str(exc)}) + '\n').encode())
        raise


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
            clone_result = git_process(package, "clone", "--no-hardlinks", str(bundle), str(repo),
                                 local_only=True, timeout=LONG_GIT_SECONDS)
            require(clone_result.returncode == 0, "Cannot clone saved Git bundle: " + clone_result.stderr.decode(errors='replace')[-300:])
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
