"""Measure small, textual changes against the task's original Git base."""
from fnmatch import fnmatchcase
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unicodedata

from registry import Error, canonical, digest, git, pool_claim, read_at_gate, require, snapshot

POLICY = {"version": "light-v1", "lines": 80, "files": 4, "profile": "P2"}
PROTECTED = (
    "scripts/*", "plugins/*", "assets/vendor/*", ".github/*", ".agents/*",
    ".codex/*", ".claude/*", ".cursor/*", ".claude-plugin/*", ".codex-plugin/*",
    ".gitignore", ".gitattributes", ".gitmodules", "version", "pyproject.toml",
    "requirements*.txt", "*.lock", "package-lock.json", "pnpm-lock.yaml",
    "yarn.lock", "makefile", "package*.json", "skill.md",
    "references/work.md", "references/start.md", "references/runtime.md",
    "references/recovery.md", "references/plugins.md", "references/plugin-authoring.md",
    "docs/plugin-authoring.it.md",
)
ALLOWED = ("readme.md", "readme.it.md", "docs/*.md", "references/*.md",
           "tests/*", "assets/dashboard.html")


def refuse(code, detail=""):
    raise Error(code + (": " + detail if detail else "") + ". Full route required.")


def contract(task):
    fields = {k: task.get(k) for k in ("id", "repo", "base", "scope", "proof", "expected",
                                      "requirements", "owner", "reviewer", "depends",
                                      "full_gate", "route", "light_policy")}
    for key in ("fix", "pool", "read_at_gate", "release_gate"):
        if key in task:
            fields[key] = task[key]
    return digest(canonical(fields).encode())


def check_contract(task):
    if task.get("route") == "light" and task.get("light_policy") != POLICY:
        refuse("light_policy_unsupported")
    if task.get("contract") != contract(task):
        refuse("light_contract_required", "task contract changed")


def normalized(path):
    if (not path or path.startswith("/") or "\\" in path or
            any(p in {"", ".", ".."} for p in path.split("/")) or
            any(unicodedata.category(c).startswith("C") for c in path)):
        refuse("light_unmeasurable", "invalid path")
    return unicodedata.normalize("NFC", path).casefold()


def protected(path):
    name = normalized(path)
    return (name.rsplit("/", 1)[-1] in {"agents.md", "claude.md"} or
            any(fnmatchcase(name, pattern) for pattern in PROTECTED) or
            not any(fnmatchcase(name, pattern) for pattern in ALLOWED))


def base_files(repo, base):
    tree = git(repo, "rev-parse", base + "^{tree}").decode().strip()
    entries = []
    for raw in git(repo, "ls-tree", "-rz", "--full-tree", tree).split(b"\0"):
        if raw:
            meta, path = raw.split(b"\t", 1)
            mode, kind, oid = meta.decode().split()
            if kind != "blob" or mode not in {"100644", "100755"}:
                refuse("light_unmeasurable", "base contains a non-regular entry")
            entries.append((os.fsdecode(path), mode, oid))
    run = subprocess.run(["git", "cat-file", "--batch"], cwd=repo,
                         input="".join(oid + "\n" for _, _, oid in entries).encode(),
                         capture_output=True, timeout=30)
    if run.returncode:
        refuse("light_unmeasurable", "base blobs unavailable")
    data, offset, files = run.stdout, 0, {}
    for path, mode, oid in entries:
        end = data.index(b"\n", offset)
        found, kind, size = data[offset:end].decode().split()
        require(found == oid and kind == "blob", "light_unmeasurable: base blob mismatch")
        start = end + 1; offset = start + int(size) + 1
        body = data[start:offset - 1]
        require(data[offset - 1:offset] == b"\n", "light_unmeasurable: truncated base blob")
        files[path] = (body, mode == "100755")
    return tree, files


def line_counts(before, after, folder):
    for data in (before, after):
        if b"\0" in data:
            refuse("light_unmeasurable", "binary change")
        data.decode("utf-8", errors="strict")
    old, new = Path(folder) / "old", Path(folder) / "new"
    old.write_bytes(before); new.write_bytes(after)
    result = subprocess.run([
        "git", "-c", "core.autocrlf=false", "diff", "--no-index", "--numstat", "-z",
        "--no-ext-diff", "--no-textconv", "--no-renames", "--diff-algorithm=myers",
        "--no-indent-heuristic", "--", str(old), str(new)],
        cwd=folder, capture_output=True, timeout=30)
    require(result.returncode in (0, 1), "light_unmeasurable: raw diff failed")
    if not result.stdout:
        return 0, 0
    added, deleted, _ = result.stdout.split(b"\t", 2)
    return int(added), int(deleted)


def measure(task, candidate=None):
    check_contract(task)
    try:
        repo = Path(task["repo"])
        before = snapshot(repo)
        if candidate is not None and candidate != before:
            refuse("light_unmeasurable", "candidate changed")
        tree, base = base_files(repo, task["base"])
        current = {r["path"]: r for r in before["files"]}
        names = sorted(base.keys() | current.keys())
        aliases = {}
        for name in names:
            key = normalized(name)
            if key in aliases and aliases[key] != name:
                refuse("light_unmeasurable", "path alias collision")
            aliases[key] = name
        changes = []
        with tempfile.TemporaryDirectory(prefix="squadra-light-") as folder:
            for name in names:
                old, executable = base.get(name, (b"", False))
                row = current.get(name, {"kind": "deleted"})
                if row["kind"] not in {"file", "deleted"}:
                    refuse("light_unmeasurable", "non-regular entry")
                exists = row["kind"] == "file"
                if exists:
                    path = repo
                    for part in name.split("/"):
                        path = path / part
                        if stat.S_ISLNK(path.lstat().st_mode):
                            refuse("light_unmeasurable", "symlink traversal")
                    new = path.read_bytes()
                    if digest(new) != row["sha256"]:
                        refuse("light_unmeasurable", "unstable source")
                    if name in base and row["executable"] != executable:
                        refuse("light_unmeasurable", "mode change")
                else:
                    new = b""
                if name in base and exists and old == new:
                    continue
                if name not in base and not exists:
                    continue
                if protected(name):
                    refuse("light_protected_path", name)
                added, deleted = line_counts(old, new, folder)
                changes.append({"path": name, "added": added, "deleted": deleted})
        if snapshot(repo) != before:
            refuse("light_unmeasurable", "unstable source")
        lines = sum(row["added"] + row["deleted"] for row in changes)
        if not changes:
            refuse("light_empty_change")
        if len(changes) > POLICY["files"]:
            refuse("light_files_exceeded", f"{len(changes)} changed paths; maximum {POLICY['files']}")
        if lines > POLICY["lines"]:
            refuse("light_lines_exceeded", f"{lines} changed lines; maximum {POLICY['lines']}")
        return {"policy": dict(POLICY), "base": task["base"], "base_tree": tree,
                "contract": task["contract"], "candidate": before["sha256"],
                "files": len(changes), "lines": lines, "changes": changes}
    except Error as exc:
        if str(exc).startswith("light_"):
            raise
        raise Error("light_unmeasurable. Full route required.") from exc
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError) as exc:
        raise Error("light_unmeasurable. Full route required.") from exc


def register(task, state):
    check_contract(task)
    reader = task.get("reviewer") or task.get("pool") or task.get("read_at_gate") is True
    if (task.get("route") != "light" or not task.get("requirements") or not reader or
            task.get("full_gate") or not isinstance(task.get("scope"), dict) or
            not task["scope"].get("sha256") or not task["scope"].get("bytes") or
            not isinstance(task.get("proof"), list) or not task["proof"] or
            not all(isinstance(s, str) and s for s in task["proof"])):
        refuse("light_contract_required", "scope, requirements, reader and targeted argv required; no full gate")
    if (git(task["repo"], "rev-parse", "HEAD").decode().strip() != task["base"] or
            git(task["repo"], "status", "--porcelain=v1", "--untracked-files=all")):
        refuse("light_contract_required", "clean original base required")
    base_files(task["repo"], task["base"])
    for old in state["tasks"].values():
        same = all(old.get(k) == task.get(k) for k in ("repo", "base", "proof", "requirements"))
        same = same and old.get("scope", {}).get("sha256") == task["scope"]["sha256"]
        spent = any(old["rounds"].values()) or any(r["task"] == old["id"] and
                    r["mode"] in {"read", "build"} for r in state["runs"].values())
        if same and spent:
            refuse("light_contract_required", "equivalent task has spent budget; promote it instead")


def validate_event(state, kind, task, data):
    """Check routed writes inside the Store transaction, not only in the CLI."""
    check_contract(task)
    if kind in {"start", "run.start", "deliver", "review", "close"}:
        require(not state["paused"], "Workspace paused")
        assignment = state["messages"].get("assign-" + task["id"], {})
        require(assignment.get("ack", {}).get("agent") == task["owner"] if assignment.get("ack") else False,
                "Assignment has no recipient ACK")
    if task["route"] == "light":
        require(not (read_at_gate(task) and ((kind == "review" and data.get("stage") == "code") or
                                              (kind == "run.start" and data.get("mode") == "read"))),
                "read_at_gate_no_own_read: this task is read at the release gate, not on its own")
        if kind == "plan" or (kind in {"review", "run.start"} and data.get("stage") == "plan"):
            refuse("light_plan_requires_full")
        reads = [r for r in state["runs"].values()
                 if r["task"] == task["id"] and r["mode"] == "read"]
        if ((kind == "review" and task["rounds"]["code"] >= 1) or
                (kind == "run.start" and (task["rounds"]["code"] >= 1 or
                 (data["mode"] == "read" and len(reads) >= 1))) or
                (kind in {"start", "deliver"} and task["rounds"]["code"] >= 1)):
            refuse("light_read_round_exhausted")
    if kind in {"review", "run.start", "deliver"}:
        require(data.get("contract") == task["contract"], "Task contract changed; new proof and read required")
    if kind == "run.start" and data["mode"] == "read":
        delivery = task.get("delivery", {})
        require(delivery.get("handoff") or (delivery.get("handoffs") and pool_claim(state, task)),
                "Exact delivery handoff required before read")
        from registry import candidate_checks
        candidate_checks(task, snapshot(task["repo"]), require_review=False)
    if kind == "run.start" and data["mode"] == "proof":
        require(data.get("before") == snapshot(task["repo"]), "Proof source changed before start")
        require(data.get("argv") == task["proof"] and data.get("expected_rc") == 0,
                "Proof command differs from agreed task contract")
        if task["route"] == "light":
            require(data.get("light") == measure(task, data.get("before")), "Light proof measurement changed")
    if kind in {"deliver", "review", "close"}:
        current = snapshot(task["repo"])
        if task["route"] == "light":
            measured = measure(task, current)
            if kind == "deliver":
                # A read-at-gate delivery has no reader, so no packet: only its own measurement.
                require(data.get("light") == measured and (read_at_gate(task) or
                        data.get("packet_data", {}).get("light") == measured),
                        "Light delivery measurement changed")
        from registry import candidate_checks
        candidate_checks(task, current, require_review=kind == "close" and not read_at_gate(task),
                         delivering=kind == "deliver")


def promotion(state, task, data):
    require(task.get("route") == "light", "Only one-way light to full promotion is supported")
    require(task["status"] != "done" and not task.get("superseded_by"), "Task already closed or superseded")
    require(not state["paused"], "Workspace paused")
    require(not any(r["task"] == task["id"] and r["status"] == "running" for r in state["runs"].values()),
            "Wait for the task's active run before promotion")
    require(isinstance(data.get("reason"), str) and data["reason"].strip(), "Promotion reason required")
    require(isinstance(data.get("scope"), dict) and data["scope"].get("sha256") and data["scope"].get("bytes"),
            "New full scope required")
    require(isinstance(data.get("proof"), list) and data["proof"] and
            all(isinstance(s, str) and s for s in data["proof"]), "New full proof argv required")
    require(data.get("previous_contract") == task.get("contract"), "Promotion contract changed")
    require(data.get("route") == "full" and data.get("contract") == contract({**task, **data}),
            "Invalid full route contract")
