"""Synthetic registries built with the real squadra launcher in temporary folders. A fixture failure raises
FixtureError, which unittest reports as an error, never as an assertion failure."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import uuid

CASES = Path(__file__).resolve().parent
RELEASE = Path(os.environ.get('SQMEM_TEST_RELEASE', CASES.parents[1])).resolve()
ROOT = Path(os.environ.get('SQMEM_TEST_BRIDGE', RELEASE / 'plugins/memoranda/bridge')).resolve()
BUNDLE = ROOT.parent
sys.path.insert(0, str(ROOT))
REPORT = b"# Code read\n\nAccepted for the fixture.\n"
FORM = "da-ricordare/1"


class FixtureError(Exception):
    pass


def synthetic_home():
    """Every test process gets its own HOME; the real ~/.squadra is never written by a test."""
    home = Path(tempfile.gettempdir()) / f"sqmem-home-{os.getpid()}"
    home.mkdir(mode=0o700, exist_ok=True)
    return home


def env(home=None):
    values = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0",
                  HOME=str(home or synthetic_home()))
    values.pop("MEMORANDA_API_KEY", None)
    for role in ("AUTHOR", "COMMITTER"):
        values[f"GIT_{role}_NAME"] = "fixture"
        values[f"GIT_{role}_EMAIL"] = "fixture@example.invalid"
    return values


def sh(argv, cwd=None, timeout=300):
    done = subprocess.run([str(a) for a in argv], cwd=cwd, env=env(), capture_output=True, text=True, timeout=timeout)
    if done.returncode:
        raise FixtureError(f"{' '.join(map(str, argv[:4]))} rc={done.returncode}: {done.stderr[-600:]}")
    return done.stdout


def launcher():
    return str(RELEASE / 'scripts/squadra.py')


def scripts():
    return RELEASE / 'scripts'


def install(home):
    import importlib.util
    spec = importlib.util.spec_from_file_location('fixture_plugin_store', RELEASE / 'scripts/plugin_store.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not (Path(home) / '.squadra/plugins/memoranda/.store-receipt.json').exists():
        module.install(Path(home), 'memoranda', RELEASE)


def pinned_module():
    path = scripts() / "registry.py"
    module = types.ModuleType("fixture_registry")
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


class Reg:
    def __init__(self, root):
        self.root = Path(root)

    def cli(self, *args):
        return sh([sys.executable, "-B", launcher(), "--root", self.root, *args])

    def events(self):
        with open(self.root / "events.jsonl", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def last_seq(self, kind=None):
        return [e for e in self.events() if kind is None or e["kind"] == kind][-1]["seq"]

    def event(self, seq):
        return next(e for e in self.events() if e["seq"] == seq)


def _build(home):
    proj, files, reg = home / "proj", home / "files", Reg(home / "reg")
    proj.mkdir()
    files.mkdir()
    sh(["git", "init", "-q", "-b", "main", proj])
    (proj / "README.md").write_text("fixture\n")
    sh(["git", "-C", proj, "add", "README.md"])
    sh(["git", "-C", proj, "commit", "-q", "-m", "base"])
    (files / "scope.md").write_text("Fixture scope.\n")
    (files / "req.json").write_text(json.dumps([{"id": "F-1", "text": "Fixture requirement"}]))
    (files / "proof.json").write_text(json.dumps(["python3", "-c", "pass"]))
    (files / "report.md").write_bytes(REPORT)
    reg.cli("init", "--temporary", "--name", "fixture", "--project", proj)
    for seat, identity, role in [("coord", "id-coord", "coordinator"), ("coord2", "id-coord2", "coordinator"),
                                 ("b1", "id-b1", "builder"), ("b1-alias", "id-b1", "builder"),
                                 ("b2", "id-b2", "builder"), ("r1", "id-r1", "reviewer")]:
        reg.cli("agent", "--id", seat, "--identity", identity, "--role", role, "--client", "manual", "--repo", proj)
    reg.cli("task", "--id", "T1", "--title", "Fixture task", "--actor", "coord", "--owner", "b1", "--repo", proj,
            "--scope-file", files / "scope.md", "--proof-file", files / "proof.json", "--reviewer", "r1",
            "--requirements-file", files / "req.json")
    reg.cli("ack", "--actor", "b1", "--message", "assign-T1")
    reg.cli("decision", "--actor", "coord", "--reason", "Fixture decision", "--channel", "typed-here",
            "--owner-words", "Use X over Y", "--id", "D1", "--task", "T1")
    reg.cli("prove", "--actor", "b1", "--task", "T1", "--covers", "F-1", "--", "python3", "-c", "pass")
    delivered, _ = json.JSONDecoder().raw_decode(reg.cli("deliver", "--actor", "b1", "--task", "T1"))
    reg.cli("ack", "--actor", "r1", "--message", delivered["handoff"])
    reg.cli("review", "--actor", "r1", "--task", "T1", "--stage", "code", "--verdict", "accepted",
            "--report", files / "report.md", "--covers", "F-1")
    reg.cli("close", "--actor", "coord", "--task", "T1")
    events = reg.events()
    info = {"d1_seq": next(e["seq"] for e in events if e["kind"] == "decision" and e["data"].get("id") == "D1"),
            "review_seq": next(e["seq"] for e in events if e["kind"] == "review"),
            "report_sha": hashlib.sha256(REPORT).hexdigest(), "report_bytes": len(REPORT),
            "proj": str(proj), "scope": str(files / "scope.md")}
    (home / "info.json").write_text(json.dumps(info))


def base():
    """The base registry, built once per temporary folder and reused by every test process that shares it."""
    key = hashlib.sha256(Path(__file__).read_bytes() + str(scripts()).encode()).hexdigest()[:16]
    home = Path(tempfile.gettempdir()) / f"sqmem-fixture-{key}"
    if not (home / "READY").is_file():
        shutil.rmtree(home, ignore_errors=True)
        home.mkdir(mode=0o700)
        _build(home)
        (home / "READY").write_text("ok\n")
    return home


def info():
    return json.loads((base() / "info.json").read_text())


def copy_registry(folder):
    target = Path(folder) / "reg"
    shutil.copytree(base() / "reg", target, symlinks=True)
    return Reg(target)


def clone(reg, folder):
    target = Path(folder) / f"reg-{uuid.uuid4().hex[:8]}"
    shutil.copytree(reg.root, target, symlinks=True)
    return Reg(target)


def _post(reg, actor, payload, line):
    folder = Path(tempfile.mkdtemp())
    (folder / "da-ricordare.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (folder / "line.txt").write_text(line + "\n", encoding="utf-8")
    reg.cli("output", "--actor", actor, "--task", "T1", "--file", folder / "line.txt", "--next", "-",
            "--artifact", folder / "da-ricordare.json")
    shutil.rmtree(folder)
    return reg.last_seq("output")


def declare(reg, actor, items, answer="yes"):
    return _post(reg, actor, {"form": FORM, "task": "T1", "answer": answer, "items": items},
                 f"Da ricordare: {'sì' if answer == 'yes' else 'no'} — {len(items)}")


def reply(reg, actor, seq, item, verdict, why=None, private="no"):
    payload = {"form": FORM, "replies_to": seq, "item": item, "verdict": verdict}
    if private is not None:
        payload["private_content"] = private
    if why is not None:
        payload["why"] = why
    return _post(reg, actor, payload, f"Da ricordare: risposta {verdict} a #{seq}")


def task(reg, task_id, owner="b2"):
    """An open task made with the real CLI, for supersession."""
    facts = info()
    reg.cli("task", "--id", task_id, "--title", "Fixture task " + task_id, "--actor", "coord", "--owner", owner,
            "--repo", facts["proj"], "--scope-file", facts["scope"])
    return reg.last_seq("task")


class Run:
    def __init__(self, done, out_root):
        self.rc, self.stdout, self.stderr = done.returncode, done.stdout, done.stderr
        self.out_root = Path(out_root)
        made = [p for p in self.out_root.iterdir()] if self.out_root.is_dir() else []
        self.folder = made[0] if len(made) == 1 and made[0].name.startswith("collect-") else None
        self.doc = self.texts = None
        if self.rc == 0 and self.folder:
            self.texts = {name: (self.folder / name).read_text(encoding="utf-8")
                          for name in ("candidates.json", "candidates.it.md", "candidates.en.md")}
            self.doc = json.loads(self.texts["candidates.json"])

    def everything(self):
        files = [p.read_bytes().decode("utf-8", "replace") for p in self.out_root.rglob("*") if p.is_file()]
        return "\n".join(files + [self.stdout, self.stderr])


def collect(reg, out_root, home=None, install_bundle=True):
    if install_bundle:
        install(home or synthetic_home())
    out_root = Path(out_root)
    out_root.mkdir(mode=0o700, exist_ok=True)
    argv = [sys.executable, "-B", "-m", "squadra_memories", "collect", "--registry", reg.root,
            "--out-root", out_root]
    done = subprocess.run([str(a) for a in argv], cwd=ROOT, env=env(home), capture_output=True, text=True, timeout=600)
    return Run(done, out_root)


TOKEN = "mrdk_" + "0123456789abcdef" * 2 + "_SENTINEL-SECRET-0f9e8d7c"
SECRET = "SENTINEL-SECRET-0f9e8d7c"


def connector_argv(command, reg, out_root, *extra, endpoint=None):
    argv = [sys.executable, "-B", "-m", "squadra_memories", command, "--registry", reg.root, "--out-root", out_root]
    if endpoint:
        argv += ["--endpoint", endpoint, "--timeout", "3"]
    return [str(a) for a in argv + list(extra)]


def connector_env(home, token=TOKEN):
    values = env(home)
    if token is not None:
        values["MEMORANDA_API_KEY"] = token
    return values


def run_connector(command, reg, out_root, home, *extra, endpoint=None, token=TOKEN):
    install(home)
    Path(out_root).mkdir(mode=0o700, exist_ok=True)
    done = subprocess.run(connector_argv(command, reg, out_root, *extra, endpoint=endpoint), cwd=ROOT,
                          env=connector_env(home, token), capture_output=True, text=True, timeout=600)
    return done


def request(reg, home, ids, requested="2026-10-11T12:00:00Z", key=None):
    """Synthetic user's atomic selection; follows the shared server lock, never used by production."""
    import fcntl
    folder = Path(home) / ".squadra/plugins/memoranda/actions"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(folder / "send.lock", "a") as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        target = folder / "send.json"
        if ids is None:
            target.unlink(missing_ok=True)
        else:
            value = {"schema": 1, "key": key or reg.event(1)["hash"], "ids": ids, "requested": requested}
            staging = folder / (".request-" + uuid.uuid4().hex)
            with os.fdopen(os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
                json.dump(value, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staging, target)
        return target


def choose_drafts(reg, home):
    """Explicit selection setup for legacy connector tests. An empty separate ledger yields a stale draft
    panel deliberately: the connector's own durable eligibility checks must still resist repeated selections.
    Raw run_connector stays raw, for tests proving no-request/no-selection behavior."""
    with tempfile.TemporaryDirectory() as tmp:
        run = collect(reg, tmp, home=home)
        if run.rc:
            raise FixtureError("selection preview failed: " + run.stderr)
    document = json.loads((Path(home) / ".squadra/plugins/memoranda/panels" /
                           (reg.event(1)["hash"] + ".json")).read_text())
    ids = [row["id"] for row in document["items"] if row.get("selectable") is True]
    if ids:
        request(reg, home, ids)
    return ids


def files_text(*folders):
    texts = []
    for folder in folders:
        for path in Path(folder).rglob("*"):
            if path.is_file() and not path.is_symlink():
                texts.append(path.read_bytes().decode("utf-8", "replace"))
    return "\n".join(texts)


def inventory(root):
    """Every path under root: type, mode, size, inode, mtime_ns and content digest."""
    rows = {}
    for folder, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(folder) / name
            st = os.lstat(path)
            digest = hashlib.sha256(path.read_bytes()).hexdigest() if stat.S_ISREG(st.st_mode) else None
            rows[str(path.relative_to(root))] = (stat.S_IFMT(st.st_mode), stat.S_IMODE(st.st_mode), st.st_size,
                                                 st.st_ino, st.st_mtime_ns, digest)
    return rows


def set_modes(root, folder_mode, file_mode):
    for folder, dirs, files in os.walk(root, topdown=False):
        for name in files:
            os.chmod(Path(folder) / name, file_mode)
        for name in dirs:
            os.chmod(Path(folder) / name, folder_mode)
    os.chmod(root, folder_mode)


def remove(folder):
    for path in [Path(folder), *Path(folder).rglob("*")]:
        if path.is_dir() and not path.is_symlink():
            os.chmod(path, 0o700)
    shutil.rmtree(folder, ignore_errors=True)


def write_settings(home, endpoint=None, key=None):
    """The saved state as squadra's server writes it (CONTRACT EXTENSION): settings.json 0600, and each secret in
    secrets/<id>, folder 0700, file 0600, exact bytes. W itself never writes these files."""
    install(home)
    folder = Path(home)
    for part in (".squadra", "plugins", "memoranda"):
        folder = folder / part
        folder.mkdir(mode=0o700, exist_ok=True)
    data = {"schema": 1, "updated": "2026-10-11T12:00:00Z", "values": {"endpoint": endpoint} if endpoint else {},
            "secrets": ["key"] if key is not None else []}
    path = folder / "settings.json"
    path.unlink(missing_ok=True)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
        handle.write(json.dumps(data))
    if key is not None:
        (folder / "secrets").mkdir(mode=0o700, exist_ok=True)
        secret = folder / "secrets" / "key"
        secret.unlink(missing_ok=True)
        with os.fdopen(os.open(secret, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as handle:
            handle.write(key.encode("ascii"))
    return folder


def saved_files(home):
    """Type, mode, inode, size, mtime and digest of settings.json and every file under secrets/."""
    folder = Path(home) / ".squadra" / "plugins" / "memoranda"
    paths = [folder / "settings.json"] + sorted((folder / "secrets").glob("*"))
    rows = {}
    for path in paths:
        if os.path.lexists(path):
            st = os.lstat(path)
            rows[path.name] = (stat.S_IFMT(st.st_mode), stat.S_IMODE(st.st_mode), st.st_ino, st.st_size,
                               st.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
    return rows
