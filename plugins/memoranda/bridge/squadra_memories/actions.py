"""Read a user selection under ledger → action locking; never create or execute a selection."""
from contextlib import contextmanager
import datetime
import fcntl
import json
import os
import re
import stat
import time

from . import connector
from .source import Refusal

LIMIT = 64 * 1024
LOCK_WAIT = 3.0


def _constant(value):
    raise ValueError("non-finite JSON")


def _private(st):
    return (stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid()
            and stat.S_IMODE(st.st_mode) == 0o600 and st.st_nlink == 1)


def _directory(parent, name):
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    st = os.fstat(fd)
    if st.st_uid != os.getuid() or st.st_mode & 0o022:
        os.close(fd)
        raise Refusal("E-ACTION")
    return fd


def _file(parent, name, limit):
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    except FileNotFoundError:
        return None, None
    try:
        st = os.fstat(fd)
        if not _private(st) or st.st_size > limit:
            raise Refusal("E-ACTION")
        body = bytearray()
        while len(body) <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        if len(body) > limit:
            raise Refusal("E-ACTION")
        return fd, json.loads(body.decode("utf-8"), object_pairs_hook=connector._unique, parse_constant=_constant)
    except BaseException:
        os.close(fd)
        raise


def _request(data):
    if not (type(data) is dict and set(data) == {"schema", "key", "ids", "requested"}
            and type(data["schema"]) is int and data["schema"] == 1
            and isinstance(data["key"], str) and re.fullmatch(r"[0-9a-f]{64}", data["key"])
            and type(data["ids"]) is list and 1 <= len(data["ids"]) <= 500
            and all(isinstance(v, str) and re.fullmatch(r"[1-9][0-9]{0,18}:(?:0|[1-9][0-9]{0,18})", v)
                    for v in data["ids"]) and len(set(data["ids"])) == len(data["ids"])
            and isinstance(data["requested"], str)
            and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", data["requested"])):
        raise Refusal("E-ACTION")
    datetime.datetime.strptime(data["requested"], "%Y-%m-%dT%H:%M:%SZ")
    return data


class Selection:
    """One admitted request; its open inode distinguishes even an equal-second replacement."""

    def __init__(self, home, registry_id):
        self.home, self.registry_id = home, registry_id
        self.folder = self.plugin = self.lock = self.original = None
        self.data = None

    @contextmanager
    def locked(self):
        deadline = time.monotonic() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Refusal("E-ACTION-BUSY") from None
                time.sleep(0.025)
        try:
            yield
        finally:
            fcntl.flock(self.lock, fcntl.LOCK_UN)

    def __enter__(self):
        try:
            self.folder = os.open(self.home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            for name in (*connector.PLUGIN, "actions"):
                if name == "actions":
                    self.plugin = os.dup(self.folder)
                try:
                    child = _directory(self.folder, name)
                except FileNotFoundError:
                    return self
                os.close(self.folder)
                self.folder = child
            self.lock = os.open("send.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                                0o600, dir_fd=self.folder)
            if not _private(os.fstat(self.lock)):
                raise Refusal("E-ACTION")
            with self.locked():
                self.original, data = _file(self.folder, "send.json", LIMIT)
                if self.original is not None:
                    self.data = _request(data)
                    if self.data["key"] != self.registry_id:
                        raise Refusal("E-ACTION")
            return self
        except (OSError, ValueError, UnicodeError, RecursionError):
            self.__exit__()
            raise Refusal("E-ACTION") from None
        except BaseException:
            self.__exit__()
            raise

    def __exit__(self, *exc):
        for name in ("original", "lock", "folder", "plugin"):
            fd = getattr(self, name)
            if fd is not None:
                os.close(fd)
                setattr(self, name, None)

    def selectable(self):
        """Only the last persisted panel can authorize an id; fresh eligibility is checked separately."""
        folder = fd = None
        try:
            folder = _directory(self.plugin, "panels")
            fd, data = _file(folder, self.registry_id + ".json", 2 * 1024 * 1024)
            if (fd is None or type(data) is not dict or type(data.get("schema")) is not int
                    or data["schema"] != 1 or type(data.get("items")) is not list):
                raise Refusal("E-ACTION")
            rows = data["items"]
            ids = [row["id"] for row in rows if type(row) is dict and isinstance(row.get("id"), str)]
            if len(rows) > 500 or len(ids) != len(rows) or len(set(ids)) != len(ids):
                raise Refusal("E-ACTION")
            for row in rows:
                if (row.get("status") not in ("draft", "sent", "approved", "rejected", "withheld", "error")
                        or ("selectable" in row and (type(row["selectable"]) is not bool
                            or (row["selectable"] and row["status"] != "draft")))):
                    raise Refusal("E-ACTION")
            return {row["id"] for row in rows if row.get("status") == "draft" and row.get("selectable") is True}
        except (OSError, ValueError, UnicodeError, RecursionError):
            raise Refusal("E-ACTION") from None
        finally:
            for opened in (fd, folder):
                if opened is not None:
                    os.close(opened)

    def clear(self):
        fd = None
        try:
            with self.locked():
                fd, current = _file(self.folder, "send.json", LIMIT)
                if fd is None:
                    return
                _request(current)
                before, after = os.fstat(self.original), os.fstat(fd)
                if ((before.st_dev, before.st_ino) == (after.st_dev, after.st_ino) and current == self.data):
                    os.unlink("send.json", dir_fd=self.folder)
                    os.fsync(self.folder)
        except (OSError, ValueError, UnicodeError, RecursionError):
            raise Refusal("E-ACTION") from None
        finally:
            if fd is not None:
                os.close(fd)


def complete(ids, candidates, ledger):
    eligible = {connector.item_name(c) for c in candidates}
    for name in ids:
        status = (ledger["items"].get(name) or {}).get("status")
        if status in ("sending", "uncertain") or (name in eligible and status in connector.PROPOSABLE):
            return False
    return True
