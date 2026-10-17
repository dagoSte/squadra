"""Install one frozen local version, preserving both old skill trees and launcher."""
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
            if label == "launcher":
                body = "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -B " + shlex.quote(str(version / "scripts/squadra.py")) + ' "$@"\n'
                atomic(target, body.encode()); target.chmod(0o755)
            else:
                target.symlink_to(version, target_is_directory=True)
            result["targets"].append({"path": str(target), "old": str(old) if existed else None})
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


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", default=str(Path(__file__).resolve().parents[1]))
    p.add_argument("--home", default=str(Path.home()))
    p.add_argument("--backup", required=True)
    a = p.parse_args()
    try:
        print(json.dumps(install(a.source, a.home, a.backup), indent=2))
    except (Error, OSError) as exc:
        print(str(exc), file=sys.stderr); raise SystemExit(1)
