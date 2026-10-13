"""Read local plugin data with strict format and size checks."""
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
import base64
import errno
import ipaddress
import json
import os
import re
import stat
import unicodedata
from urllib.parse import urlsplit

NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,31}\Z")
FIELD_ID = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
KEY = re.compile(r"[0-9a-f]{64}\Z")
MANIFEST_BYTES = 64 * 1024
ICON_BYTES = 32 * 1024
PANEL_BYTES = 2 * 1024 * 1024
STATUSES = {"draft", "sent", "approved", "rejected", "withheld", "error"}


class Refused(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(ok, code="bad_value"):
    if not ok:
        raise Refused(code)


def check_directory(fd, private=False):
    """Shared policy: safe owned parents; private plugin installation directories."""
    info = os.fstat(fd)
    mode = stat.S_IMODE(info.st_mode)
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and (mode == 0o700 if private else not mode & 0o022), "unsafe_permissions")


def _open(parent, name, directory=False):
    try:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        if directory:
            flags |= os.O_DIRECTORY
        fd = os.open(name, flags, dir_fd=parent)
    except (AttributeError, NotImplementedError):
        raise Refused("io_error") from None
    except OSError as exc:
        code = "io_error"
        if exc.errno == errno.ENOENT:
            code = "not_found"
        elif exc.errno in (errno.ELOOP, errno.ENOTDIR):
            code = "not_directory" if directory else "not_regular"
            try:
                if stat.S_ISLNK(os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode):
                    code = "symlink"
            except OSError:
                pass
        raise Refused(code) from None
    try:
        mode = os.fstat(fd).st_mode
    except OSError:
        os.close(fd)
        raise Refused("io_error") from None
    if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
        os.close(fd)
        raise Refused("not_directory" if directory else "not_regular")
    return fd


@contextmanager
def _root(home):
    with ExitStack() as stack:
        try:
            fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY)
        except OSError:
            raise Refused("io_error") from None
        stack.callback(os.close, fd)
        check_directory(fd)
        for name in (".squadra", "plugins"):
            fd = _open(fd, name, directory=True)
            stack.callback(os.close, fd)
            check_directory(fd)
        yield fd


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "invalid_json")
        result[key] = value
    return result


def _constant(value):
    raise Refused("invalid_json")


def _read_bytes(parent, name, limit, check=None):
    fd = _open(parent, name)
    try:
        if check is not None:
            check(fd)
        require(os.fstat(fd).st_size <= limit, "too_large")
        body = bytearray()
        while len(body) <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        require(len(body) <= limit, "too_large")
        return bytes(body)
    except OSError:
        raise Refused("io_error") from None
    finally:
        os.close(fd)


def _read(parent, name, limit, check=None):
    body = _read_bytes(parent, name, limit, check)
    try:
        return json.loads(body.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise Refused("invalid_json") from None


def _icon(parent):
    body = _read_bytes(parent, "icon.png", ICON_BYTES)
    require(len(body) >= 33 and body[:8] == b"\x89PNG\r\n\x1a\n"
            and body[8:16] == b"\x00\x00\x00\rIHDR", "invalid_icon")
    width = int.from_bytes(body[16:20], "big")
    height = int.from_bytes(body[20:24], "big")
    require(1 <= width <= 256 and 1 <= height <= 256, "invalid_icon")
    return "data:image/png;base64," + base64.b64encode(body).decode("ascii")


def _fields(value, required, optional=()):
    require(type(value) is dict)
    require(set(value) <= set(required) | set(optional), "unknown_key")
    require(set(required) <= set(value))


def _text(value):
    require(type(value) is str)
    try:
        require(len(value.encode("utf-8")) <= 4096)
    except UnicodeError:
        raise Refused("bad_value") from None


def _bilingual(value):
    _fields(value, ("it", "en"))
    for text in value.values():
        _text(text)


def _schema(value):
    require(type(value["schema"]) is int and value["schema"] == 1, "bad_schema")


# Text-direction controls can disguise a plugin label.
BIDI_CONTROLS = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def _about_text(value, minimum, maximum):
    require(type(value) is str and minimum <= len(value) <= maximum)
    require(not BIDI_CONTROLS.intersection(value))
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise Refused("bad_value") from None


def _about_bilingual(value, minimum, maximum):
    _fields(value, ("it", "en"))
    for text in value.values():
        _about_text(text, minimum, maximum)


def _ip_literal(host):
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _homepage(value):
    _about_text(value, 1, 200)
    require(not any(c.isspace() or unicodedata.category(c) in ("Cc", "Cf") for c in value))
    require("\\" not in value and "#" not in value)
    try:
        url = urlsplit(value)
        require(url.scheme == "https" and url.hostname and url.username is None and url.password is None)
        require(not any(c in url.hostname for c in '%<>"^`{|}'))
        # A host is an IP address or dot-separated labels of letters, digits and inner hyphens; a port is 1–65535.
        require(_ip_literal(url.hostname) or all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                                                  for label in url.hostname.split(".")))
        require(url.port is None or 1 <= url.port <= 65535)
    except ValueError:
        raise Refused("bad_value") from None


def _about(value):
    _fields(value, ("publisher", "version", "tagline", "sections"), ("homepage",))
    if "homepage" in value:
        _homepage(value["homepage"])
    _about_text(value["publisher"], 1, 64)
    require(not any(unicodedata.category(c) == "Cc" for c in value["publisher"]))
    _about_text(value["version"], 1, 32)
    require(re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+-]*", value["version"]))
    _about_bilingual(value["tagline"], 1, 200)
    require(type(value["sections"]) is list and 1 <= len(value["sections"]) <= 8)
    ids = set()
    for section in value["sections"]:
        _fields(section, ("id", "title"), ("text", "items"))
        require(type(section["id"]) is str and re.fullmatch(r"[a-z][a-z0-9-]{0,31}", section["id"]) and section["id"] not in ids)
        ids.add(section["id"])
        _about_bilingual(section["title"], 1, 80)
        require("text" in section or "items" in section)
        if "text" in section:
            _about_bilingual(section["text"], 0, 2000)
        if "items" in section:
            require(type(section["items"]) is list and 1 <= len(section["items"]) <= 12)
            for item in section["items"]:
                _about_bilingual(item, 0, 300)


def _manifest(parent, name):
    value = _read(parent, "plugin.json", MANIFEST_BYTES)
    _fields(value, ("schema", "name", "title", "description"), ("settings", "about", "actions", "results"))
    _schema(value)
    require(value["name"] == name, "name_mismatch")
    _bilingual(value["title"])
    _bilingual(value["description"])
    if "about" in value:
        _about(value["about"])
    if "results" in value:
        results = value["results"]
        _fields(results, ("label",), ("empty", "status_labels"))
        _about_bilingual(results["label"], 1, 80)
        if "empty" in results:
            _about_bilingual(results["empty"], 1, 300)
        if "status_labels" in results:
            labels = results["status_labels"]
            _fields(labels, (), STATUSES)
            for label in labels.values():
                _about_bilingual(label, 1, 80)
    if "actions" in value:
        require(type(value["actions"]) is list and len(value["actions"]) <= 1)
        for action in value["actions"]:
            _fields(action, ("id", "label"))
            require(type(action["id"]) is str and NAME.fullmatch(action["id"]))
            _bilingual(action["label"])
    if "settings" in value:
        fields = value["settings"]
        require(type(fields) is list and 1 <= len(fields) <= 4)
        ids = set()
        for field in fields:
            _fields(field, ("id", "kind", "label"), ("help",))
            require(type(field["id"]) is str and FIELD_ID.fullmatch(field["id"]) and field["id"] not in ids)
            ids.add(field["id"])
            require(field["kind"] in ("url", "secret", "text"))
            for label in ("label", "help"):
                if label in field:
                    _bilingual(field[label])
                    require(all(len(s.encode("utf-8")) <= 1024 for s in field[label].values()))
    return value


def _references(value):
    require(type(value) is list and len(value) <= 8)
    for ref in value:
        _fields(ref, ("task",), ("run",))
        _about_text(ref["task"], 1, 200)
        require(not any(unicodedata.category(c).startswith("C") for c in ref["task"]))
        if "run" in ref:
            require(type(ref["run"]) is str and re.fullmatch(r"[0-9a-f]{32}", ref["run"]))


def _panel(value):
    _fields(value, ("schema", "updated", "summary", "items"), ("note",))
    _schema(value)
    _text(value["updated"])
    try:
        stamp = datetime.fromisoformat(value["updated"].replace("Z", "+00:00"))
        require("T" in value["updated"] and stamp.utcoffset() == timedelta(0))
    except (ValueError, OverflowError):
        raise Refused("bad_value") from None
    _bilingual(value["summary"])
    if "note" in value:
        _bilingual(value["note"])
    require(type(value["items"]) is list and len(value["items"]) <= 500)
    for item in value["items"]:
        _fields(item, ("id", "title", "text", "status", "meta"), ("selectable", "refs"))
        for field in ("id", "title", "text", "status"):
            _text(item[field])
        require(item["status"] in STATUSES)
        if "selectable" in item:
            require(type(item["selectable"]) is bool)
            require(not item["selectable"] or item["status"] == "draft")
        if "refs" in item:
            _references(item["refs"])
        require(type(item["meta"]) is list)
        for entry in item["meta"]:
            _fields(entry, ("label", "value"))
            _bilingual(entry["label"])
            _text(entry["value"])
    return value


def discover(home):
    result = {"plugins": [], "refused": []}
    try:
        with _root(home) as root:
            for name in sorted(os.listdir(root)):
                if name.startswith("."):
                    continue  # Ignore hidden files such as Finder metadata.
                try:
                    require(NAME.fullmatch(name), "bad_name")
                    fd = _open(root, name, directory=True)
                    try:
                        check_directory(fd)
                        manifest = _manifest(fd, name)
                        result["plugins"].append(manifest)
                        try:
                            manifest["icon"] = _icon(fd)
                        except Refused as exc:
                            if exc.code != "not_found":
                                result["refused"].append({"name": name, "code": exc.code})
                    finally:
                        os.close(fd)
                except Refused as exc:
                    result["refused"].append({"name": name.encode("utf-8", "replace").decode(), "code": exc.code})
    except Refused as exc:
        if exc.code != "not_found":
            result["refused"].append({"name": None, "code": exc.code})
    except OSError:
        result["refused"].append({"name": None, "code": "io_error"})
    return result


def panel_at(plugin, key):
    try:
        panels = _open(plugin, "panels", directory=True)
    except Refused as exc:
        if exc.code == "not_found":
            return None
        raise
    try:
        try:
            return _panel(_read(panels, key + ".json", PANEL_BYTES))
        except Refused as exc:
            if exc.code == "not_found":
                return None
            raise
    finally:
        os.close(panels)


def load_panel(home, name, key):
    require(type(name) is str and NAME.fullmatch(name), "bad_name")
    require(type(key) is str and KEY.fullmatch(key), "registry_invalid")
    with _root(home) as root, ExitStack() as stack:
        plugin = _open(root, name, directory=True)
        stack.callback(os.close, plugin)
        check_directory(plugin)
        _manifest(plugin, name)
        return panel_at(plugin, key)
