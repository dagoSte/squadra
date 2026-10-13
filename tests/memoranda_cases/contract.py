"""The SQ-PLUGINS contract v1, checked independently of the code that writes the panel."""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import stat
import urllib.parse

STATUSES = {"draft", "sent", "approved", "rejected", "withheld", "error"}
HEX = set("0123456789abcdef")


class Broken(AssertionError):
    pass


def _check(ok, why):
    if not ok:
        raise Broken(why)


def _load(path, limit):
    st = os.lstat(path)
    _check(stat.S_ISREG(st.st_mode), f"{path.name} is not a regular file")
    _check(st.st_size <= limit, f"{path.name} exceeds {limit} bytes")
    _check(stat.S_IMODE(st.st_mode) == 0o600, f"{path.name} is not 0600")

    def pairs(items):
        keys = [key for key, _ in items]
        _check(len(keys) == len(set(keys)), "duplicate key")
        return dict(items)

    def constant(_):
        raise Broken("non-finite number")

    return json.loads(path.read_bytes().decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)


def _text(value, where):
    _check(type(value) is str and len(value.encode("utf-8")) <= 4096, f"{where} is not a string of at most 4 KiB")


def _bilingual(value, where):
    _check(type(value) is dict and set(value) == {"it", "en"}, f"{where} is not exactly {{it, en}}")
    for language in ("it", "en"):
        _text(value[language], f"{where}.{language}")


MANIFEST_LIMIT = 64 * 1024
LOWER = set("abcdefghijklmnopqrstuvwxyz")
DIGITS = set("0123456789")


def validate_manifest(manifest, name):
    """plugin.json v1 with the CONTRACT EXTENSION (settings) and the about block (contract-plugin-about.md)."""
    _check(type(manifest) is dict and {"schema", "name", "title", "description"} <= set(manifest)
           <= {"schema", "name", "title", "description", "settings", "about", "actions"}, "plugin.json keys")
    _check(type(manifest["schema"]) is int and manifest["schema"] == 1, "plugin.json schema")
    _check(manifest["name"] == name, "plugin.json name")
    _bilingual(manifest["title"], "title")
    _bilingual(manifest["description"], "description")
    if "settings" in manifest:
        _settings(manifest["settings"])
    if "about" in manifest:
        _about(manifest["about"])
    if "actions" in manifest:
        actions = manifest["actions"]
        _check(type(actions) is list and len(actions) == 1, "one declared action")
        _check(type(actions[0]) is dict and set(actions[0]) == {"id", "label"} and actions[0]["id"] == "send",
               "action shape")
        _bilingual(actions[0]["label"], "send label")


BIDI = {0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ICON_LIMIT = 32 * 1024


def homepage(value):
    """contract-plugin-link-logo.md: https only, a host, ≤ 200 characters, no userinfo or fragment, no whitespace,
    control or bidi character."""
    _check(type(value) is str and 0 < len(value) <= 200, "homepage length")
    _check(not any(c.isspace() or ord(c) < 32 or ord(c) == 127 or ord(c) in BIDI for c in value),
           "homepage holds whitespace, control or bidi characters")
    try:
        url = urllib.parse.urlsplit(value)
        url.port
    except ValueError:
        raise Broken("homepage is not a URL") from None
    _check(url.scheme == "https" and bool(url.hostname), "homepage is not https with a host")
    _check(url.username is None and url.password is None and "@" not in url.netloc, "homepage holds userinfo")
    _check(not url.fragment and "#" not in value, "homepage holds a fragment")


def check_icon(path):
    """An optional icon.png: regular file, never a symlink, PNG signature, ≤ 32 KiB, IHDR width and height 1–256."""
    st = os.lstat(path)
    _check(stat.S_ISREG(st.st_mode), "icon.png is not a regular file")
    _check(st.st_size <= ICON_LIMIT, "icon.png exceeds 32 KiB")
    body = Path(path).read_bytes()
    _check(body.startswith(PNG_SIGNATURE) and len(body) >= 24 and body[12:16] == b"IHDR", "icon.png is not a PNG")
    width, height = int.from_bytes(body[16:20], "big"), int.from_bytes(body[20:24], "big")
    _check(1 <= width <= 256 and 1 <= height <= 256, "icon.png is not 1–256 px a side")
    return width, height


def _sized(value, where, low, high):
    """Exactly {it, en}, each a string of low…high characters."""
    _check(type(value) is dict and set(value) == {"it", "en"}, f"{where} is not exactly {{it, en}}")
    for language in ("it", "en"):
        _check(type(value[language]) is str and low <= len(value[language]) <= high,
               f"{where}.{language} is not {low}–{high} characters")


def _about(about):
    _check(type(about) is dict and {"publisher", "version", "tagline", "sections"} <= set(about)
           <= {"publisher", "version", "tagline", "sections", "homepage"}, "about keys")
    if "homepage" in about:
        homepage(about["homepage"])
    publisher, version = about["publisher"], about["version"]
    _check(type(publisher) is str and 1 <= len(publisher) <= 64
           and not any(ord(c) < 32 or ord(c) == 127 for c in publisher), "about publisher")
    _check(type(version) is str and 1 <= len(version) <= 32 and version[0] in LOWER | DIGITS | {c.upper() for c in LOWER}
           and set(version) <= LOWER | DIGITS | {c.upper() for c in LOWER} | set(".+-"), "about version")
    _sized(about["tagline"], "about tagline", 1, 200)
    sections = about["sections"]
    _check(type(sections) is list and 1 <= len(sections) <= 8, "about must hold 1 to 8 sections")
    ids = []
    for section in sections:
        _check(type(section) is dict and {"id", "title"} <= set(section) <= {"id", "title", "text", "items"},
               "about section keys")
        _check("text" in section or "items" in section, "an about section needs text or items")
        name = section["id"]
        _check(type(name) is str and 1 <= len(name) <= 32 and name[0] in LOWER
               and set(name) <= LOWER | DIGITS | {"-"}, "about section id")
        ids.append(name)
        _sized(section["title"], f"section {name} title", 1, 80)
        if "text" in section:
            _sized(section["text"], f"section {name} text", 0, 2000)
        if "items" in section:
            items = section["items"]
            _check(type(items) is list and 1 <= len(items) <= 12, f"section {name} must hold 1 to 12 items")
            for item in items:
                _sized(item, f"section {name} item", 0, 300)
    _check(len(ids) == len(set(ids)), "about section ids are not unique")


def _settings(fields):
    """The CONTRACT EXTENSION: 1–4 fields {id, kind, label, help?}, ids unique, help ≤ 1 KiB per language."""
    _check(type(fields) is list and 1 <= len(fields) <= 4, "settings must hold 1 to 4 fields")
    ids = []
    for field in fields:
        _check(type(field) is dict and {"id", "kind", "label"} <= set(field) <= {"id", "kind", "label", "help"},
               "setting keys")
        name = field["id"]
        _check(type(name) is str and 1 <= len(name) <= 32 and name[0] in "abcdefghijklmnopqrstuvwxyz"
               and set(name) <= set("abcdefghijklmnopqrstuvwxyz0123456789_"), "setting id")
        _check(field["kind"] in ("url", "secret", "text"), "setting kind")
        _bilingual(field["label"], f"setting {name} label")
        if "help" in field:
            _bilingual(field["help"], f"setting {name} help")
            for language in ("it", "en"):
                _check(len(field["help"][language].encode("utf-8")) <= 1024, f"setting {name} help over 1 KiB")
        ids.append(name)
    _check(len(ids) == len(set(ids)), "setting ids are not unique")


def check(home, name, key):
    """Every rule of the contract for one plugin folder and one panel; returns (manifest, panel)."""
    _check(0 < len(name) <= 32 and name[0].isalnum() and set(name) <= set("abcdefghijklmnopqrstuvwxyz0123456789-"),
           "bad plugin name")
    _check(len(key) == 64 and set(key) <= HEX, "bad panel key")
    folder = Path(home) / ".squadra"
    for part in ("plugins", name, "panels"):
        st = os.lstat(folder)
        _check(stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode), f"{folder} is not a real folder")
        folder = folder / part
    _check(stat.S_ISDIR(os.lstat(folder).st_mode), "panels is not a real folder")
    manifest = _load(folder.parent / "plugin.json", MANIFEST_LIMIT)
    validate_manifest(manifest, name)
    if os.path.lexists(folder.parent / "icon.png"):
        check_icon(folder.parent / "icon.png")
    panel = _load(folder / f"{key}.json", 2 * 1024 * 1024)
    _check({"schema", "updated", "summary", "items"} <= set(panel) <= {"schema", "updated", "summary", "items", "note"},
           "panel keys")
    _check(type(panel["schema"]) is int and panel["schema"] == 1, "panel schema")
    _text(panel["updated"], "updated")
    moment = datetime.datetime.fromisoformat(panel["updated"].replace("Z", "+00:00"))
    _check("T" in panel["updated"] and moment.utcoffset() == datetime.timedelta(0), "updated is not ISO UTC")
    _bilingual(panel["summary"], "summary")
    if "note" in panel:
        _bilingual(panel["note"], "note")
    _check(type(panel["items"]) is list and len(panel["items"]) <= 500, "items")
    for index, item in enumerate(panel["items"]):
        _check(type(item) is dict and {"id", "title", "text", "status", "meta"} <= set(item)
               <= {"id", "title", "text", "status", "meta", "selectable"}, f"item {index} keys")
        if "selectable" in item:
            _check(type(item["selectable"]) is bool and (not item["selectable"] or item["status"] == "draft"),
                   "selectable only for draft")
        for field in ("id", "title", "text", "status"):
            _text(item[field], f"item {index}.{field}")
        _check(item["status"] in STATUSES, f"item {index} status")
        _check(type(item["meta"]) is list, f"item {index} meta")
        for entry in item["meta"]:
            _check(type(entry) is dict and set(entry) == {"label", "value"}, f"item {index} meta keys")
            _bilingual(entry["label"], f"item {index} meta label")
            _text(entry["value"], f"item {index} meta value")
    return manifest, panel
