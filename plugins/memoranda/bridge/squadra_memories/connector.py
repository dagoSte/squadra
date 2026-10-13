"""Proposes collected memory candidates to memoranda.dev over MCP and records every outcome in a local ledger.

Propose-only: approval belongs to the subscriber's policy for the scope, manual by default. Nothing here claims
exactly-once delivery: an uncertain outcome is never resent automatically, and an explicitly authorised resend can
still duplicate a request whose completion was never observed. Remote strings never reach a stream, the ledger or the
panel: error codes pass a closed allowlist, and identifiers must match the core's exact formats.
"""
from __future__ import annotations

import datetime
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import stat
import string
import time
import urllib.error
import urllib.parse
import urllib.request

from .source import Refusal
from .storage import output_directory

PROTOCOL_VERSION = "2025-03-26"
MAX_RESPONSE = 1 << 20
LEDGER_LIMIT = 4 * 1024 * 1024
PACE = 2.4
MAX_WAIT = 120
CONTENT_LIMIT = 1500
CONFIDENCE = {"sure": 0.8, "likely": 0.65}
TOKEN_CHARS = frozenset(string.ascii_letters + string.digits + "._~-")
TENANT_CHARS = frozenset(string.ascii_letters + string.digits + "._-")
SCOPE_CHARS = frozenset(string.ascii_lowercase + string.digits + "._-")
HEX = frozenset("0123456789abcdef")
ULID = frozenset("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
REMOTE_CODES = {
    "duplicate_waiting": "E-DUPLICATE-WAITING", "proposal_queue_full": "E-QUEUE-FULL",
    "scope_queue_full": "E-SCOPE-QUEUE-FULL", "invalid_arguments": "E-INVALID-ARGUMENTS",
    "policy_invalid": "E-POLICY-INVALID", "not_found": "E-NOT-FOUND", "internal": "E-REMOTE-INTERNAL",
}
REASON_CODES = frozenset({"duplicate", "insufficient_evidence", "incorrect", "out_of_scope", "superseded", "sensitive"})
PROPOSABLE = (None, "resend")
KEY_SOURCES = ("environment", "token_file", "saved")
PLUGIN = (".squadra", "plugins", "memoranda")
SETTINGS_LIMIT = 16 * 1024


class Stop(Exception):
    """The run stops; nothing was written remotely by the request that raised it."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class Uncertain(Stop):
    """A write whose outcome cannot be known."""


class Later(Exception):
    """The server asked to be called again after a number of seconds; the request was not processed."""

    def __init__(self, seconds):
        super().__init__("later")
        self.seconds = seconds


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def valid_id(value, prefix):
    return isinstance(value, str) and len(value) == len(prefix) + 26 and value.startswith(prefix) \
        and set(value[len(prefix):]) <= ULID


def _stamp(value):
    if not isinstance(value, str) or len(value) > 40 or not value.endswith("Z") or "T" not in value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    return parsed if parsed.utcoffset() == datetime.timedelta(0) else None


class Key:
    """An agent key. Its secret is reachable only through `header()`; repr and str never show it."""

    __slots__ = ("_token", "key_id_sha256")

    def __init__(self, token, key_id_sha256):
        self._token = token
        self.key_id_sha256 = key_id_sha256

    def header(self):
        return "Bearer " + self._token

    def __repr__(self):
        return "Key(<hidden>)"

    __str__ = __repr__


def _plugin_dir(home, *more, code="E-SETTINGS"):
    """`$HOME/.squadra/plugins/memoranda` (and any subfolder): every part a real folder. None when a part is missing;
    a symlink or a foreign file type anywhere below `.squadra` refuses with `code`."""
    folder = Path(home)
    for part in PLUGIN + more:
        folder = folder / part
        try:
            st = os.lstat(folder)
        except FileNotFoundError:
            return None
        except OSError:
            raise Refusal(code) from None
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise Refusal(code)
    return folder


def _read_private(path, limit, code):
    """The exact bytes of a regular, owner-only file of at most `limit` bytes, never through a final symlink; None when
    absent. A loose mode, a foreign owner, a larger size or another file type refuses with `code`."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        raise Refusal(code) from None
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077 \
            or st.st_size > limit:
        raise Refusal(code)
    try:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC), "rb") as handle:
            return handle.read(limit + 1)
    except OSError:
        raise Refusal(code) from None


def _key_text(source, token_file, home):
    """One source's key text, or None when that source is not given. The saved secret keeps its exact bytes."""
    try:
        if source == "environment":
            return os.environ.get("MEMORANDA_API_KEY", "").strip() or None
        if source == "token_file":
            if not token_file:
                return None
            body = _read_private(token_file, 4096, "E-KEY")
            if body is None:
                raise Refusal("E-KEY")
            return body.decode("ascii").strip()
        folder = _plugin_dir(home, "secrets", code="E-KEY") if home is not None else None
        body = _read_private(folder / "key", 4096, "E-KEY") if folder is not None else None
        return None if body is None else body.decode("ascii")
    except UnicodeError:
        raise Refusal("E-KEY") from None


def load_key(token_file=None, home=None):
    """The first given of MEMORANDA_API_KEY, `--token-file`, then the secret saved from the squadra panel. The first
    source present decides; a bad one refuses, it never falls through. Shape `mrdk_<32 hex key id>_<secret>`."""
    for source in KEY_SOURCES:
        text = _key_text(source, token_file, home)
        if text is not None:
            break
    else:
        raise Refusal("E-KEY")
    if not 5 < len(text) <= 4096 or not set(text) <= TOKEN_CHARS or not text.startswith("mrdk_"):
        raise Refusal("E-KEY")
    key_id, separator, secret = text[5:].partition("_")
    if len(key_id) != 32 or not set(key_id) <= HEX or not separator or not secret:
        raise Refusal("E-KEY")
    return Key(text, hashlib.sha256(key_id.encode("ascii")).hexdigest())


def _unique(pairs):
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate key")
    return dict(pairs)


def saved_settings(home):
    """What the squadra panel saved, read-only: the endpoint if it is valid, and whether a key is saved. Never raises;
    anything unusable reads as unset. W never writes settings.json or secrets/."""
    state = {"endpoint": None, "key": False}
    try:
        folder = _plugin_dir(home)
        body = _read_private(folder / "settings.json", SETTINGS_LIMIT, "E-SETTINGS") if folder is not None else None
        if body is None:
            return state
        data = json.loads(body.decode("utf-8"), object_pairs_hook=_unique)
        if not isinstance(data, dict) or set(data) != {"schema", "updated", "values", "secrets"} \
                or type(data["schema"]) is not int or data["schema"] != 1 or not isinstance(data["values"], dict) \
                or not isinstance(data["secrets"], list):
            return state
        endpoint = data["values"].get("endpoint")
        if isinstance(endpoint, str):
            parse_endpoint(endpoint)
            state["endpoint"] = endpoint
        if "key" in data["secrets"]:
            secrets = _plugin_dir(home, "secrets")
            state["key"] = secrets is not None and stat.S_ISREG(os.lstat(secrets / "key").st_mode)
    except (Refusal, OSError, ValueError, UnicodeError, RecursionError):
        pass
    return state


def parse_endpoint(endpoint):
    """HTTPS `/t/<tenant>/mcp` without credentials, query or fragment; plain HTTP only on loopback (tests)."""
    try:
        url = urllib.parse.urlsplit(endpoint)
        url.port
    except (ValueError, TypeError):
        raise Refusal("E-ENDPOINT") from None
    loopback = url.hostname in ("localhost", "127.0.0.1", "::1")
    parts = url.path.split("/")
    if (url.scheme != "https" and not (url.scheme == "http" and loopback)) or not url.hostname or url.username \
            or url.password or url.query or url.fragment or any(ord(c) <= 32 for c in endpoint) or len(parts) != 4 \
            or parts[0] or parts[1] != "t" or parts[3] != "mcp" or not 0 < len(parts[2]) <= 128 \
            or not set(parts[2]) <= TENANT_CHARS:
        raise Refusal("E-ENDPOINT")
    return endpoint, parts[2]


def valid_scope(scope):
    if not isinstance(scope, str) or not 0 < len(scope) <= 200:
        return False
    return all(part and part[0] not in "._-" and set(part) <= SCOPE_CHARS for part in scope.split("/"))


def default_scope(name):
    slug, dash = [], False
    for char in (name or "").lower():
        if char in SCOPE_CHARS and char not in "._-":
            slug.append(char)
            dash = False
        elif slug and not dash:
            slug.append("-")
            dash = True
    text = "".join(slug).strip("-")[:120] or "registry"
    return "squadra/" + text


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _seconds(value):
    return int(value) if isinstance(value, str) and value.isdigit() and len(value) <= 6 else None


class Client:
    """Plain JSON-RPC over POST, as the hosted reference client harvest.py: no redirects, no proxies, bounded reads."""

    def __init__(self, endpoint, key, timeout):
        self.endpoint, self.key, self.timeout = endpoint, key, timeout
        self.session, self.sequence, self.ready = None, 0, False
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def _post(self, body, write):
        headers = {"Authorization": self.key.header(), "Content-Type": "application/json",
                   "Accept": "application/json"}
        if self.session:
            headers.update({"Mcp-Session-Id": self.session, "MCP-Protocol-Version": PROTOCOL_VERSION})
        request = urllib.request.Request(self.endpoint, data=json.dumps(body).encode("utf-8"), headers=headers,
                                         method="POST")
        failure = Uncertain if write else Stop
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return response.read(MAX_RESPONSE + 1), response.headers.get("Mcp-Session-Id")
        except urllib.error.HTTPError as error:
            status = error.code
            wait = _seconds(error.headers.get("Retry-After")) if error.headers else None
            error.close()
            if status in (401, 403):
                raise Stop("E-AUTH") from None
            if status in (429, 503) and wait is not None:
                raise Later(wait) from None
            if status == 429:
                raise Stop("E-RATE-LIMITED") from None
            raise failure("E-SERVER-BUSY" if status == 503 else "E-RESPONSE") from None
        except (OSError, http.client.HTTPException, ValueError):
            raise failure("E-NETWORK") from None

    def call(self, method, params, write=False):
        self.sequence += 1
        data, session = self._post({"jsonrpc": "2.0", "id": self.sequence, "method": method, "params": params}, write)
        failure = Uncertain("E-RESPONSE") if write else Stop("E-RESPONSE")
        if len(data) > MAX_RESPONSE:
            raise failure
        try:
            envelope = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise failure from None
        if not isinstance(envelope, dict) or envelope.get("jsonrpc") != "2.0" or envelope.get("id") != self.sequence:
            raise failure
        if "error" in envelope:
            raise Uncertain("E-RPC") if write else Stop("E-RPC")
        result = envelope.get("result")
        if not isinstance(result, dict):
            raise failure
        if method == "initialize":
            if not isinstance(session, str) or not 0 < len(session) <= 512 or not set(session) <= TOKEN_CHARS:
                raise Stop("E-RESPONSE")
            self.session = session
        return result

    def open(self):
        if self.ready:
            return
        result = self.call("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                          "clientInfo": {"name": "squadra-memories", "version": "1"}})
        if result.get("protocolVersion") != PROTOCOL_VERSION:
            raise Stop("E-RESPONSE")
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}, False)
        self.ready = True

    def tool(self, name, arguments, write=False):
        result = self.call("tools/call", {"name": name, "arguments": arguments}, write)
        payload = result.get("structuredContent")
        if not isinstance(payload, dict):
            raise Uncertain("E-RESPONSE") if write else Stop("E-RESPONSE")
        return payload


def _private_ledger_file(fd):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise Refusal("E-LEDGER")
    return info


class Ledger:
    """`ledger-<event-1 hash>.json` beside the collector's output, held under an exclusive lock for a whole run."""

    def __init__(self, out_root, registry_id):
        self.path = Path(out_root) / f"ledger-{registry_id}.json"
        self.lock_path = Path(out_root) / f"ledger-{registry_id}.lock"
        self.registry_id = registry_id
        self.data, self._fd, self._directory = None, None, None
        self._locked = False

    def __enter__(self):
        self._directory = output_directory(self.path.parent)
        try:
            try:
                self._fd = os.open(self.lock_path.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
                                   | os.O_NONBLOCK, 0o600, dir_fd=self._directory)
                _private_ledger_file(self._fd)
            except OSError:
                raise Refusal("E-LEDGER") from None
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._locked = True
            except OSError:
                raise Refusal("E-BUSY") from None
            self.data = read_ledger(self.path, parent=self._directory)
            changed = False
            for entry in (self.data or {}).get("items", {}).values():
                if entry.get("status") == "sending":
                    entry.update(status="uncertain", error_code="E-INTERRUPTED", updated=now_iso())
                    changed = True
            if changed:
                self.save()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc):
        if self._fd is not None:
            try:
                if self._locked:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd, self._locked = None, False
        if self._directory is not None:
            os.close(self._directory)
            self._directory = None

    def bind(self, binding):
        if self.data is None:
            self.data = {"schema": 1, "registry_id": self.registry_id, "binding": binding, "scope_policy": "manual",
                         "accepted_scope_policy": None, "decided_cursor": None, "items": {}}
            self.save()
        elif self.data.get("binding") != binding:
            raise Refusal("E-BINDING")

    def record_intent(self):
        self.save()

    def save(self):
        body = (json.dumps(self.data, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8")
        if len(body) > LEDGER_LIMIT:
            raise Refusal("E-LEDGER-SIZE")
        temporary = f".{self.path.name}.{os.getpid()}.tmp"
        try:
            with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                                   0o600, dir_fd=self._directory), "wb") as out:
                out.write(body)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, self.path.name, src_dir_fd=self._directory, dst_dir_fd=self._directory)
            os.fsync(self._directory)
        except OSError:
            try:
                os.unlink(temporary, dir_fd=self._directory)
            except FileNotFoundError:
                pass
            raise Refusal("E-LEDGER") from None


def read_ledger(path, *, parent=None):
    """Read an owned, private regular descriptor under a checked directory; absent is None."""
    path = Path(path)
    owned = parent is None
    if owned:
        parent = output_directory(path.parent)
    try:
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=parent)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as handle:
            st = _private_ledger_file(handle.fileno())
            if st.st_size > LEDGER_LIMIT:
                raise Refusal("E-LEDGER-SIZE")
            data = json.loads(handle.read(LEDGER_LIMIT + 1).decode("utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise Refusal("E-LEDGER") from None
    finally:
        if owned:
            os.close(parent)
    if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("items"), dict):
        raise Refusal("E-LEDGER")
    return data


def item_name(candidate):
    return f"{candidate['declared_by']['seq']}:{candidate['item']}"


def arguments(candidate, scope, registry_id, refs):
    """The propose arguments of decision D-CONNECTOR-MAPPING; no path, `who` or objection is ever sent."""
    evidence = []
    for entry in candidate["evidence"]:
        if "event" in entry:
            evidence.append({"kind": "conversation", "conv_id": "squadra-" + registry_id[:16], "turn": entry["event"],
                             "excerpt_hash": "sha256:" + entry["hash"]})
        else:
            name = refs.get(entry["artifact"], {}).get("name")
            if not isinstance(name, str) or not name or "/" in name or name in (".", "..") \
                    or any(ord(c) < 32 for c in name):
                name = "artifact-" + entry["artifact"][:16]
            evidence.append({"kind": "document", "path": "squadra/" + name, "page": 1,
                             "content_hash": "sha256:" + entry["artifact"]})
    return {"scope": scope, "content": candidate["sentence"], "evidence": evidence,
            "confidence": CONFIDENCE[candidate["certainty"]], "tags": ["squadra", candidate["type"]]}


def _waiting_id(message):
    if not isinstance(message, str) or len(message) > 512 or ": " not in message:
        return None
    tail = message.rsplit(": ", 1)[1].strip()
    return tail if valid_id(tail, "prp_") else None


def send(ledger, client, candidates, scope, registry_id, refs, sleep=time.sleep, clock=time.monotonic):
    """Proposes every eligible candidate once; returns None when the run completed, else the code that stopped it."""
    items = ledger.data["items"]
    eligible = [c for c in candidates if (items.get(item_name(c)) or {}).get("status") in PROPOSABLE]
    if not eligible:
        return None
    if ledger.data["scope_policy"] == "automatic" and ledger.data.get("accepted_scope_policy") is None:
        return "E-SCOPE-POLICY"
    client.open()
    last = None
    for candidate in eligible:
        name = item_name(candidate)
        content = candidate["sentence"]
        if len(content) > CONTENT_LIMIT:
            items[name] = {"status": "error", "error_code": "E-TOO-LONG", "attempts": 0, "updated": now_iso(),
                           "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}
            ledger.save()
            continue
        request = arguments(candidate, scope, registry_id, refs)
        previous, retried = items.get(name), False
        while True:
            if last is not None:
                sleep(max(0.0, last + PACE - clock()))
            items[name] = {**(previous or {}), "status": "sending", "updated": now_iso(),
                           "attempts": (previous or {}).get("attempts", 0) + 1,
                           "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}
            ledger.record_intent()
            last = clock()
            try:
                payload = client.tool("propose", request, write=True)
            except Later as later:
                _restore(items, name, previous)
                ledger.save()
                if retried or later.seconds > MAX_WAIT:
                    return "E-RATE-LIMITED"
                retried = True
                sleep(later.seconds)
                continue
            except Uncertain as unsure:
                items[name].update(status="uncertain", error_code=unsure.code, updated=now_iso())
                ledger.save()
                return "E-UNCERTAIN"
            except Stop as stop:
                _restore(items, name, previous)
                ledger.save()
                return stop.code
            break
        entry = items[name]
        if "error" in payload:
            error = payload["error"] if isinstance(payload["error"], dict) else {}
            remote = error.get("code")
            code = (REMOTE_CODES.get(remote) if isinstance(remote, str) else None) or "E-REMOTE"
            if code == "E-DUPLICATE-WAITING":
                adopted = _waiting_id(error.get("message"))
                if adopted is None:
                    entry.update(status="uncertain", error_code="E-RESPONSE", updated=now_iso())
                    ledger.save()
                    return "E-UNCERTAIN"
                entry.update(status="sent", proposal_id=adopted, adopted="duplicate_waiting", updated=now_iso())
                ledger.save()
                continue
            if code in ("E-QUEUE-FULL", "E-SCOPE-QUEUE-FULL"):
                _restore(items, name, previous)
                ledger.save()
                return code
            entry.update(status="error", error_code=code, updated=now_iso())
            ledger.save()
            continue
        proposal = payload.get("proposal_id")
        redacted = payload.get("redacted")
        flags = {"redacted": (type(redacted) is int and redacted > 0) or redacted is True,
                 "auto_refusal": payload.get("auto_refusal") is not None}
        if not valid_id(proposal, "prp_") or payload.get("status") not in ("pending", "committed"):
            entry.update(status="uncertain", error_code="E-RESPONSE", updated=now_iso())
            ledger.save()
            return "E-UNCERTAIN"
        if payload["status"] == "pending":
            entry.update(status="sent", proposal_id=proposal, updated=now_iso(), **flags)
            ledger.save()
            continue
        memory = payload.get("memory_id")
        entry.update(status="approved", proposal_id=proposal, by_policy=True, updated=now_iso(),
                     memory_id=memory if valid_id(memory, "mem_") else None, **flags)
        ledger.data["scope_policy"] = "automatic"
        ledger.save()
        stop_after_commit = ledger.data.get("accepted_scope_policy") is None
        if stop_after_commit:
            return "E-SCOPE-POLICY"
    return None


def _restore(items, name, previous):
    if previous is None:
        items.pop(name, None)
    else:
        items[name] = previous


def status(ledger, client, scope, now=None):
    """Refreshes sent items from the caller's decided proposals; only known, validated ids are matched, and an id
    updates every item holding it (two declarations can share one waiting proposal)."""
    items = ledger.data["items"]
    known = {}
    for name, e in items.items():
        if e.get("status") == "sent" and valid_id(e.get("proposal_id"), "prp_"):
            known.setdefault(e["proposal_id"], []).append(name)
    if not known:
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    floor = now - datetime.timedelta(days=29)
    cursor = _stamp((ledger.data.get("decided_cursor") or {}).get("decided_since"))
    since = max(cursor - datetime.timedelta(hours=1), floor) if cursor else floor
    request = {"decided_since": since.strftime("%Y-%m-%dT%H:%M:%SZ"), "scope": scope, "limit": 200}
    client.open()
    latest = None
    for _ in range(1000):
        payload = client.tool("review_queue", request)
        if "error" in payload:
            error = payload["error"] if isinstance(payload["error"], dict) else {}
            remote = error.get("code")
            return (REMOTE_CODES.get(remote) if isinstance(remote, str) else None) or "E-REMOTE"
        decisions, truncated = payload.get("decisions"), payload.get("truncated")
        if not isinstance(decisions, list) or type(truncated) is not bool:
            return "E-RESPONSE"
        for decision in decisions:
            if not isinstance(decision, dict) or not isinstance(decision.get("proposal_id"), str):
                continue
            decided = _stamp(decision.get("decided_at"))
            if decided and (latest is None or decided > latest):
                latest = decided
            outcome = decision.get("outcome")
            for name in known.get(decision["proposal_id"], ()):
                entry = items[name]
                if outcome == "approved":
                    memory = decision.get("memory_id")
                    entry.update(status="approved", memory_id=memory if valid_id(memory, "mem_") else None)
                elif outcome == "merged":
                    merged = decision.get("merged_into")
                    entry.update(status="merged", merged_into=merged if valid_id(merged, "mem_") else None)
                elif outcome == "rejected":
                    code = decision.get("reason_code")
                    entry.update(status="rejected",
                                 reason_code=code if isinstance(code, str) and code in REASON_CODES else None)
                else:
                    continue
                entry["updated"] = now_iso()
        if not truncated:
            break
        following = payload.get("next")
        if not isinstance(following, dict) or _stamp(following.get("decided_since")) is None \
                or not valid_id(following.get("after"), "prp_"):
            return "E-RESPONSE"
        request = {"decided_since": following["decided_since"], "after": following["after"], "scope": scope,
                   "limit": 200}
    else:
        return "E-RESPONSE"
    if latest:
        ledger.data["decided_cursor"] = {"decided_since": latest.strftime("%Y-%m-%dT%H:%M:%SZ")}
    ledger.save()
    return None


def resolve(ledger, name, proposal=None, abandon=False, resend=False):
    """A person's explicit disposition of one uncertain item."""
    entry = (ledger.data or {}).get("items", {}).get(name)
    if entry is None:
        raise Refusal("E-ITEM")
    if entry.get("status") != "uncertain":
        raise Refusal("E-NOT-UNCERTAIN")
    if proposal is not None:
        if not valid_id(proposal, "prp_"):
            raise Refusal("E-ID")
        entry.update(status="sent", proposal_id=proposal)
        action = "proposal"
    elif abandon:
        entry["status"] = "abandoned"
        action = "abandon"
    else:
        entry["status"] = "resend"
        action = "resend"
    entry.update(resolution={"action": action, "by": "explicit", "at": now_iso()}, updated=now_iso())
    ledger.save()
