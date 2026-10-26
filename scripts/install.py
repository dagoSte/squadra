"""Install one frozen local version, preserving the old skill trees and launcher; --undo puts them back."""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import sys

import squadra  # noqa: F401  Imported first: it refuses a Python older than squadra.PYTHON.
from registry import Error, atomic, digest, git, now, require


def drop_shared_write(tree):
    """Remove group/other write across a frozen tree; the plugin Store refuses such folders whatever umask wrote them."""
    for folder, _, names in os.walk(tree):
        for path in [folder, *(os.path.join(folder, name) for name in names)]:
            mode = os.lstat(path).st_mode
            if mode & 0o022 and not stat.S_ISLNK(mode):
                os.chmod(path, stat.S_IMODE(mode) & ~0o022)


def install(source, home, backup):
    umask = os.umask(0o022)  # Nothing written here inherits a group-writable umask.
    try:
        return _install(source, home, backup)
    finally:
        os.umask(umask)


def launcher_body(version):
    return ("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -B " + shlex.quote(str(version / "scripts/squadra.py"))
            + ' "$@"\n').encode()


def _install(source, home, backup):
    source, home, backup = Path(source).resolve(), Path(home).resolve(), Path(backup).resolve()
    require(not git(source, "status", "--porcelain").strip(), "Commit the exact clean candidate before installation")
    sha = git(source, "rev-parse", "HEAD").decode().strip()
    require((source / "SKILL.md").is_file() and (source / "scripts/squadra.py").is_file(), "Incomplete skill source")
    files = [os.fsdecode(p) for p in git(source, "ls-files", "-z").split(b"\0") if p]
    require(all((source / p).is_file() and not (source / p).is_symlink() for p in files), "Install only regular tracked files")
    hashes = {p: digest((source / p).read_bytes()) for p in files}
    version = home / ".local/share/squadra/versions" / sha
    if version.exists():
        require(all((version / p).is_file() and digest((version / p).read_bytes()) == h for p, h in hashes.items()), "Existing version differs")
    else:
        version.mkdir(parents=True, mode=0o700)
        for p in files:
            dest = version / p; dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / p, dest)
        atomic(version / "SOURCE_COMMIT", (sha + "\n").encode())
    drop_shared_write(version)
    backup.mkdir(parents=True, mode=0o700, exist_ok=False)
    targets = [(home / ".codex/skills/squadra", "codex-squadra"),
               (home / ".claude/skills/squadra", "claude-squadra"),
               (home / ".cursor/skills/squadra", "cursor-squadra"),
               (home / ".local/bin/squadra", "launcher")]
    if (version / 'migliora/SKILL.md').is_file():
        targets.extend((home / client / 'skills/migliora', client.lstrip('.') + '-migliora')
                       for client in ('.codex', '.claude', '.cursor'))
    changed = []
    result = {"source": str(source), "commit": sha, "version": str(version), "started": now(),
              "backup": str(backup), "hashes": hashes, "targets": []}
    try:
        for target, label in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            old = backup / label
            existed = os.path.lexists(target)
            if existed:
                shutil.move(str(target), str(old))
            changed.append((target, old, existed))
            row = {"path": str(target), "old": str(old) if existed else None}
            if label == "launcher":
                body = launcher_body(version)
                atomic(target, body); target.chmod(0o755)
                row["sha256"] = digest(body)
            else:
                target.symlink_to(version / 'migliora' if label.endswith('-migliora') else version,
                                  target_is_directory=True)
            result["targets"].append(row)
        result.update(status="installed", ended=now(), live_protocol="Existing sessions retain their read version; no assignments or refs migrated")
        atomic(backup / "INSTALL.json", (json.dumps(result, indent=2) + "\n").encode())
    except BaseException:
        for target, old, existed in reversed(changed):
            if os.path.lexists(target):
                if target.is_symlink() or target.is_file(): target.unlink()
                else: shutil.rmtree(target)
            if existed and os.path.lexists(old): shutil.move(str(old), str(target))
        raise
    return result


TARGETS = ('.codex/skills/squadra', '.claude/skills/squadra', '.cursor/skills/squadra', '.local/bin/squadra',
           '.codex/skills/migliora', '.claude/skills/migliora', '.cursor/skills/migliora')


def written(target, version, sha256=None):
    """Whether target is still exactly the link or the launcher that the installation of version wrote. A launcher
    is compared byte for byte with its recorded hash (an older record: the launcher this interpreter writes)."""
    if target.is_symlink():
        return os.readlink(target) in (str(version), str(version / 'migliora'))
    if target.parent.name == 'bin' and target.is_file():
        return digest(target.read_bytes()) == (sha256 or digest(launcher_body(version)))
    return False


def undo(backup):
    """Undo one installation from its INSTALL.json: remove each link or launcher it wrote and put back what it
    moved into the backup. A target changed since then is kept, and its predecessor stays in the backup.
    UNDO.json in the backup records each target as it is undone, so a repeated undo touches it no more; a target
    whose predecessor is no longer in the backup is left as it is, since it may be that predecessor."""
    backup = Path(backup).resolve()
    record = json.loads((backup / "INSTALL.json").read_text())
    refusal = "Not an installation record: " + str(backup / "INSTALL.json")
    require(isinstance(record, dict) and record.get("status") == "installed" and isinstance(record.get("version"), str)
            and isinstance(record.get("targets"), list), refusal)
    log = backup / "UNDO.json"
    done = json.loads(log.read_text())["targets"] if os.path.lexists(log) else {}
    require(isinstance(done, dict) and all(isinstance(k, str) and v in ("restored", "removed") for k, v in done.items()),
            "Not an undo record: " + str(log))
    version = Path(record["version"])
    rows = []
    for row in reversed(record["targets"]):
        require(isinstance(row, dict) and {"path", "old"} <= set(row) <= {"path", "old", "sha256"}
                and isinstance(row["path"], str) and any(row["path"].endswith("/" + suffix) for suffix in TARGETS)
                and (row["old"] is None or isinstance(row["old"], str) and Path(row["old"]).parent == backup)
                and (row.get("sha256") is None or isinstance(row["sha256"], str)), refusal)
        target, old = Path(row["path"]), row["old"] and Path(row["old"])
        if row["path"] in done:
            status = "done"
        elif os.path.lexists(target) and not written(target, version, row.get("sha256")):
            status = "kept"
        elif old and not os.path.lexists(old):
            status = "missing"
        else:
            if os.path.lexists(target):
                target.unlink()
            if old:
                shutil.move(str(old), str(target)); status = "restored"
            else:
                status = "removed"
            done[row["path"]] = status
            atomic(log, (json.dumps({"targets": done}, indent=2) + "\n").encode())
        rows.append({"path": row["path"], "old": row["old"], "status": status})
    rows.reverse()
    return {"backup": str(backup), "version": str(version), "targets": rows,
            "backup_needed": any(r["old"] and os.path.lexists(r["old"]) for r in rows)}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", default=str(Path(__file__).resolve().parents[1]))
    p.add_argument("--home", default=str(Path.home()))
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--backup", help="new folder that receives what the installation replaces")
    mode.add_argument("--undo", metavar="BACKUP", help="undo the installation recorded in this backup folder")
    a = p.parse_args()
    try:
        print(json.dumps(undo(a.undo) if a.undo else install(a.source, a.home, a.backup), indent=2))
    except (Error, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr); raise SystemExit(1)
