# Building a squadra plugin

This guide is for developers who want their tool's data in the squadra dashboard.
It builds a small working plugin, then shows how to test it on your own machine and
how to publish it. The exact contract (every field, size limit and refusal code) is
[Local data plugins](plugins.md). This guide summarises it and links to the section
that governs each rule.

1. [What a plugin is](#what-a-plugin-is)
2. [Files and names](#files-and-names)
3. [A minimal plugin, step by step](#a-minimal-plugin)
4. [Settings and secrets](#settings-and-secrets)
5. [The optional action and selectable drafts](#the-optional-action)
6. [AGENTS.md: telling the coordinator what to run](#agents-md)
7. [Safety and limits checklist](#safety-checklist)
8. [Testing locally](#testing-locally)
9. [Publishing your plugin](#publishing)

<a id="what-a-plugin-is"></a>
## 1. What a plugin is

A squadra plugin is **data, not code**: a folder of files in the HOME of the user who
runs `squadra dash`.

- **Your producer** is a program you write and distribute. It writes a manifest and
  one data panel per registry as JSON files.
- **The dashboard** reads those files, validates them strictly and shows them on a
  plugin page: title, an optional About page with your logo and homepage, your items
  with status badges, an optional Settings card and an optional action button.
- **Your consumer** (optional) is another program you write. It acts on the one thing
  the dashboard can save for you: the user's choice of drafts for your action.

The dashboard and the CLI never run plugin code; agents run it only as your
[AGENTS.md](#agents-md) says. The dashboard does not execute or import anything a
plugin ships and makes no network request for it: no scripts, no HTML, no remote
images. Its only writes into your folder are the values the user saves for the
settings you declared and the selection the user saves for your action, with their
lock files. It never sends anything and never starts your consumer.

A plugin is not:

- **an extension of squadra's registry.** Status badges are labels. They approve
  nothing, record no registry event and do not prove that anything ran.
- **a web page.** Panel content is plain text; HTML, Markdown and URLs in it are not
  interpreted. The only link is the HTTPS homepage you declare.
- **something squadra starts.** The user runs your programs, or the coordinator window
  runs the commands your `AGENTS.md` names, within the permissions the user has
  already given it ([section 6](#agents-md)).

<a id="files-and-names"></a>
## 2. Files and names

```text
$HOME/.squadra/plugins/<name>/
├── plugin.json         you: the manifest (required)
├── icon.png            you: optional logo
├── AGENTS.md           you: optional instructions for the coordinator window
├── panels/<key>.json   your producer: one panel per registry
├── settings.json       the dashboard: saved non-secret settings
├── secrets/<id>        the dashboard: one file per saved secret
├── .settings.lock      the dashboard: the settings lock
└── actions/            the dashboard: the saved selection and its lock
```

- `<name>` matches `^[a-z0-9][a-z0-9-]{0,31}$` (lowercase letters, digits and
  hyphens, at most 32 characters) and must equal the manifest's `name`. Otherwise the
  plugin is refused with `bad_name` or `name_mismatch`.
- `$HOME` is the HOME of the user who runs `squadra dash`. A dashboard started by an
  agent with a private HOME reads the plugins in that HOME, not in yours.
- Every path component from `.squadra` down must be a real directory or a regular
  file. Symbolic links, FIFOs and directories in place of files are refused.
- Keep the plugin folder owned by the user and not writable by group or others
  (`chmod 700` does it). Settings and actions are refused with `unsafe_permissions`
  otherwise. The example reader also refuses a group- or other-writable HOME,
  `.squadra` or `plugins`; `chmod go-w` on them is enough when `mkdir` made them so.
- Write each JSON file completely and replace it atomically: write a temporary file
  in the same folder, then rename it over the old one. The dashboard refuses partial
  or malformed files; it never repairs them.

Details: [Files and registry identity](plugins.md#files-and-registry-identity).

<a id="a-minimal-plugin"></a>
## 3. A minimal plugin, step by step

The example plugin is `release-notes`. It shows a project's release notes as items,
so the user can review the drafts from the dashboard.

### Step 1: create the folder

```sh
mkdir -p "$HOME/.squadra/plugins/release-notes"
chmod go-w "$HOME/.squadra" "$HOME/.squadra/plugins"
chmod 700 "$HOME/.squadra/plugins/release-notes"
```

### Step 2: write `plugin.json`

```json
{
  "schema": 1,
  "name": "release-notes",
  "title": {"it": "Note di rilascio", "en": "Release notes"},
  "description": {
    "it": "Le note di rilascio del progetto, da rivedere prima della pubblicazione.",
    "en": "The project's release notes, to review before publication."
  },
  "about": {
    "publisher": "Example Labs",
    "version": "1.0.0",
    "homepage": "https://example.com/release-notes",
    "tagline": {
      "it": "Le note di rilascio accanto al lavoro della squadra",
      "en": "Release notes next to the squad's work"
    },
    "sections": [
      {
        "id": "purpose",
        "title": {"it": "A cosa serve", "en": "What it does"},
        "text": {
          "it": "Mostra le note di rilascio del progetto per il registro aperto.",
          "en": "Shows the project's release notes for the open registry."
        }
      },
      {
        "id": "data",
        "title": {"it": "Quali dati usa", "en": "What data it uses"},
        "items": [
          {"it": "Legge solo file locali", "en": "Reads local files only"},
          {"it": "Scrive un pannello per registro", "en": "Writes one panel per registry"}
        ]
      }
    ]
  }
}
```

- `schema` is the integer `1`. `name`, `title` and `description` are required.
- Every bilingual object has exactly two members: `it` (Italian) and `en` (English).
  The dashboard shows the one that matches the page language.
- `about` is optional. It gives the plugin an About page: publisher, a version label,
  a tagline and one to eight sections, each with a paragraph, a list or both.
  Everything in it is your own description, not a verified identity.
- `about.homepage` is the only clickable link the dashboard shows. It opens in a new
  tab when the user clicks it; the dashboard never fetches it.
- Unknown fields at any level, duplicate keys and out-of-range values refuse the whole
  manifest. The file is at most 64 KiB.

Limits for each field: [JSON schemas](plugins.md#json-schemas-version-1) and
[Optional about metadata](plugins.md#optional-about-metadata).

### Step 3: add a logo (optional)

Save a PNG as `icon.png` next to `plugin.json`: at most 32 KiB, width and height each
1–256 pixels. The dashboard embeds the file's own bytes in the page, beside the plugin
in the menu and on its page; nothing is fetched. A missing icon is normal. An invalid
one leaves the plugin listed without a logo and reports the refusal code. Details:
[Homepage and local icon](plugins.md#homepage-and-local-icon).

### Step 4: find the registry key

A registry is the folder that holds a squad's record (`registry.sqlite3`,
`events.jsonl` and the other files squadra writes there). The dashboard shows the
panel for the registry it has open, and finds it by **key**: the lowercase 64-hex
`hash` of the registry's **event #1**. Appending events never changes it. The key is
not the folder path and not the hash of the latest event.

For this local example, your producer reads it from the first record of the registry's
`events.jsonl`, the line with `"seq": 1`. This is a regenerable projection, not a verified
identity: it may be stale or altered. Use this shortcut only with a trusted local
registry; the sequence and hex checks below do not verify the event chain:

```sh
head -n 1 /path/to/registry/events.jsonl
```

The dashboard's own value is `plugins.key` in its `/api/state` response;
[section 8](#testing-locally) shows how to compare the two. With a wrong key the page
shows «No data for this registry». The exact rule:
[Files and registry identity](plugins.md#files-and-registry-identity).

### Step 5: write the panel

Save it as `panels/<key>.json`, with the key from step 4:

```json
{
  "schema": 1,
  "updated": "2026-10-12T09:30:00Z",
  "summary": {"it": "Note: 2, bozze: 1", "en": "Notes: 2, drafts: 1"},
  "note": {
    "it": "Dati scritti dal produttore di release-notes",
    "en": "Data written by the release-notes producer"
  },
  "items": [
    {
      "id": "note-102",
      "title": "Faster start-up",
      "text": "The dashboard opens in under a second.",
      "status": "draft",
      "meta": [{"label": {"it": "Incarico", "en": "Task"}, "value": "T12"}]
    },
    {
      "id": "note-101",
      "title": "Italian help text",
      "text": "Every help line has an Italian translation.",
      "status": "approved",
      "meta": []
    }
  ]
}
```

- `updated` is an ISO timestamp with `T` and an explicit UTC offset (`Z` or
  `+00:00`). `note` is the only optional member.
- Each item has exactly `id`, `title`, `text`, `status` and `meta`, plus the optional
  `selectable` of [section 5](#the-optional-action). `meta` is a list, possibly
  empty, of objects with exactly a bilingual `label` and a string `value`.
- `title`, `text` and meta values are single strings, shown as written and not
  translated.
- `status` is one of `draft`, `sent`, `approved`, `rejected`, `withheld` and `error`;
  the dashboard shows it as a translated badge. Use it to say where each item stands
  in *your* process.
- Keep every `id` stable across updates: the dashboard then keeps the user's place on
  the same card.
- At most 500 items and 2 MiB per panel; each string at most 4096 UTF-8 bytes.

Details: [JSON schemas](plugins.md#json-schemas-version-1).

### Step 6: automate it with a producer

A producer writes the same panel from your tool's data. This one needs only Python's
standard library:

```python
#!/usr/bin/env python3
"""release-notes producer: write this registry's panel for the squadra dashboard."""
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PLUGIN = Path.home() / ".squadra" / "plugins" / "release-notes"


def registry_key(registry):
    """Local-example key from a trusted projection; this does not verify identity."""
    with open(Path(registry) / "events.jsonl", encoding="utf-8") as events:
        first = json.loads(events.readline())
    if first.get("seq") != 1 or not re.fullmatch(r"[0-9a-f]{64}", str(first.get("hash"))):
        raise SystemExit("not a squadra registry: " + str(registry))
    return first["hash"]


def write_json(path, value):
    """Replace path with a complete file in one atomic step."""
    body = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")  # created 0600
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(body)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    except BaseException:
        os.unlink(temp)
        raise
    folder = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(folder)
    finally:
        os.close(folder)


def collect():
    """Your tool's data. Keep every id stable across runs."""
    return [
        {"id": "note-102", "title": "Faster start-up",
         "text": "The dashboard opens in under a second.", "status": "draft",
         "meta": [{"label": {"it": "Incarico", "en": "Task"}, "value": "T12"}]},
        {"id": "note-101", "title": "Italian help text",
         "text": "Every help line has an Italian translation.", "status": "approved",
         "meta": []},
    ]


def main(registry):
    items = collect()
    drafts = sum(item["status"] == "draft" for item in items)
    panel = {
        "schema": 1,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": {"it": f"Note: {len(items)}, bozze: {drafts}",
                    "en": f"Notes: {len(items)}, drafts: {drafts}"},
        "items": items,
    }
    panels = PLUGIN / "panels"
    panels.mkdir(mode=0o700, exist_ok=True)
    write_json(panels / (registry_key(registry) + ".json"), panel)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: producer.py <registry folder>")
    main(sys.argv[1])
```

Run it whenever the data behind it changes; for a squad that usually means at start,
at resume and after each task closes ([section 6](#agents-md) lets the coordinator
window do this for the user):

```sh
python3 producer.py /path/to/registry
```

Where the data comes from is up to you. A producer may also read the registry's
`events.jsonl` and `state.json` and its registered artifacts, for example the optional
[«worth remembering» declarations](work.md#da-ricordare-at-close) that agents attach at
close. It only reads: squadra's CLI is the only writer in the registry folder. Apart
from the event #1 key, those formats are not part of the plugin contract, so test
against the squadra version you support.

### Step 7: see it in the dashboard

```sh
squadra dash --root /path/to/registry --open
```

With the `/plugin` install, run `python3 <skill folder>/scripts/squadra.py` in place
of `squadra`. Choose your plugin in the **Plugins** menu group. With `about`, the page
has an Overview tab (your About text), a Settings tab when you declare settings, and a
Memories tab with your panel items. Without `about`, the settings card and the items
share one page. The dashboard refreshes every two seconds, so a rewritten panel
appears without reloading the page.

<a id="settings-and-secrets"></a>
## 4. Settings and secrets

A plugin can declare 1–4 settings, which the user fills in on its Settings card. Add a
`settings` member to `plugin.json`:

```json
"settings": [
  {"id": "endpoint", "kind": "url",
   "label": {"it": "Indirizzo del servizio", "en": "Service address"},
   "help": {"it": "Inizia con https://", "en": "Starts with https://"}},
  {"id": "api_key", "kind": "secret",
   "label": {"it": "Chiave API", "en": "API key"},
   "help": {"it": "Resta su questo computer e non viene più mostrata",
            "en": "It stays on this computer and is never shown again"}},
  {"id": "channel", "kind": "text",
   "label": {"it": "Canale", "en": "Channel"}}
]
```

- `id` matches `^[a-z][a-z0-9_]{0,31}$` and is unique. Note the underscore: setting
  IDs take `_`, while plugin and action names take `-`.
- `kind` is `url`, `secret` or `text`. `label` is required, `help` optional; each
  language at most 1024 UTF-8 bytes.
- Values the dashboard accepts: a URL is HTTPS with a host, at most 2048 characters,
  without userinfo, fragment, whitespace, control characters or backslashes; a secret
  is 1–4096 printable ASCII characters without spaces; a text is at most 256
  characters without control or format characters.
- The dashboard checks the values only. It never opens a URL or contacts your service.

**Where the dashboard keeps them.** In the plugin folder, in the HOME of the user who
runs `squadra dash`:

- `settings.json` (0600), for example
  `{"schema":1,"updated":"…","values":{"endpoint":"https://…"},"secrets":["api_key"]}`.
  `values` holds only non-secret fields; `secrets` lists the IDs of saved secrets,
  never their values.
- `secrets/<id>` (0600, in a 0700 folder): the exact bytes of one secret, without a
  newline.
- `.settings.lock`: the lock the dashboard holds while it saves.

They are plaintext local files, not an encrypted vault. Your programs read them and
never write them: saving is the dashboard's job.

**How your program reads them.** Walk from a trusted HOME with directory descriptors,
opening one component at a time without following symbolic links. The example checks
that HOME, `.squadra`, `plugins` and the plugin directory are owned by the user and
not group/other-writable; `secrets` and `actions` must be 0700, files 0600 with one
link. It refuses unsafe permissions rather than repairing them. Hold a shared lock
on `.settings.lock` while reading to exclude concurrent saves. The consumer in
[section 5](#the-optional-action) implements these checks. A missing lock means no
saved configuration is available to this example, not proof of a successful save.

**After a failed save or a lost reply.** Each file is atomic on its own; there is no
atomic multi-file snapshot. A failure after writing a new secret but before publishing
`settings.json` can leave the old endpoint paired with the new secret even after the
lock is released. The reader can notice missing/unsafe files or invalid data, and the
dashboard can report `save_failed` or `settings_invalid`, but a structurally valid
mixed pair is not detectable from the timestamp or the secret's «set» flag. A lost
reply also leaves the outcome unknown; `read_settings` is not a coherence check.

On any such signal, stop sending and leave the request pending. Re-read the dashboard's
status, then have the user deliberately resubmit or clear **all affected fields
together**, including re-entering the secret (blank keeps it). Repair unsafe paths or
malformed metadata locally first. Do not automatically retry or use the key to try
unconfirmed endpoints. Before resuming, the plugin must establish that the endpoint
and credential belong together through its own authorized configuration checks or
explicit user confirmation; if it cannot, it must abstain. A successful re-save removes
the known partial-save condition, but the file reader alone cannot prove that an
arbitrary previously saved pair is coherent.

**Never echo a secret.** Keep it out of panel items, logs, error messages, command
lines, chat and registry artifacts (artifacts are permanent and cannot be removed).
Hand it to your service in memory only. The dashboard never shows a saved secret
again; it shows only whether one is set.

**Keep setting IDs and kinds stable across versions.** Saved values are checked
against the declaration on every read. Removing a field that has a saved value, or
changing its kind, makes the saved settings unreadable (`settings_invalid`) until the
files are repaired by hand.

Details, including the save behaviour and every refusal code:
[Declared settings](plugins.md#declared-settings).

<a id="the-optional-action"></a>
## 5. The optional action and selectable drafts

A plugin can declare **at most one** action. The dashboard turns it into a button that
saves the user's choice of drafts, and does nothing else.

**Declare it** in `plugin.json`. The `id` follows the plugin-name rule; the `label` is
the button text:

```json
"actions": [{"id": "send", "label": {"it": "Invia per revisione", "en": "Send for review"}}]
```

**Mark the drafts the user may choose** with `"selectable": true` in the panel (in the
producer, add `"selectable": True` to the draft that `collect()` returns):

```json
{
  "id": "note-102",
  "title": "Faster start-up",
  "text": "The dashboard opens in under a second.",
  "status": "draft",
  "selectable": true,
  "meta": [{"label": {"it": "Incarico", "en": "Task"}, "value": "T12"}]
}
```

`selectable: true` is allowed **only** with `"status": "draft"`. On any other status,
or as a value that is not a boolean, it refuses the whole panel. A draft can be chosen
only if its `id` appears once in the panel.

**What the dashboard saves.** The user ticks drafts and presses the button. The
dashboard saves the request as `actions/<id>.json`, here `actions/send.json`:

```json
{"schema":1,"key":"<event-1 hash>","ids":["note-102"],"requested":"2026-10-12T09:45:00Z"}
```

A different choice replaces the pending request; the same choice again keeps it as it
is; «Cancel request» deletes it. The request belongs to the registry whose key it
carries. While it is pending, a choice made from another registry's dashboard is
refused with `registry_mismatch`, so consume requests promptly. The dashboard itself
never sends, runs, approves or contacts anything.

**The consumer protocol.** Your consumer acts on the request in five steps:

1. Open `actions/<id>.lock` without following links and take an exclusive
   `fcntl.flock`. Never create, delete or replace that lock: the dashboard creates it.
   A request file without its lock is invalid; stop and report.
2. Read and check the request, keep it as a snapshot, check that its `key` is the key
   of the registry you are working for, then release the lock.
3. Do the work outside the lock, for example send the drafts to your service.
4. Take the lock again, re-read the request and delete it **only if `key`,
   `requested` and the ordered `ids` still equal your snapshot**; then fsync the
   `actions` folder. If they differ, the user made a newer choice: leave it pending.
5. Run the producer, so the items show the outcome (`sent`, `error`, …) and the ones
   already sent stop being selectable.

This consumer follows the protocol and reads the settings of
[section 4](#settings-and-secrets). It sits next to the producer and imports from it;
`deliver` is where your own sending code goes:

```python
#!/usr/bin/env python3
"""release-notes consumer: send the drafts the user chose, then clear the request."""
import fcntl
import json
import os
import stat
import sys
from pathlib import Path

from producer import PLUGIN, registry_key

ACTION = "send"
SNAPSHOT = ("key", "requested", "ids")
FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
FOLDER = FILE | os.O_DIRECTORY


def open_directory(parent, name, private_mode=False):
    """Open one component; secret/action directories require exactly 0700."""
    fd = os.open(name, FOLDER, dir_fd=parent)
    try:
        info = os.fstat(fd)
        mode = stat.S_IMODE(info.st_mode)
        if info.st_uid != os.getuid() or (mode != 0o700 if private_mode else mode & 0o022):
            raise SystemExit("unsafe directory: stop and report")
        return fd
    except BaseException:
        os.close(fd)
        raise


def open_plugin():
    """HOME is the trusted anchor; never resolve an absolute plugin path."""
    fd = open_directory(None, Path.home())
    try:
        for name in (".squadra", "plugins", PLUGIN.name):
            child = open_directory(fd, name)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def private(fd, name):
    """Refuse anything but a regular 0600 file with one link, owned by this user."""
    info = os.fstat(fd)
    if not (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1):
        raise SystemExit(f"unsafe {name}: stop and report")


def read_private(folder, name, limit):
    fd = os.open(name, FILE, dir_fd=folder)
    try:
        private(fd, name)
        body = b""
        while len(body) <= limit:
            chunk = os.read(fd, limit + 1 - len(body))
            if not chunk:
                break
            body += chunk
    finally:
        os.close(fd)
    if len(body) > limit:
        raise SystemExit(f"{name} is too large: stop and report")
    return body


def read_settings():
    """The saved non-secret values and the API key, read under the settings lock."""
    plugin = open_plugin()
    try:
        try:
            lock = os.open(".settings.lock", FILE, dir_fd=plugin)
        except FileNotFoundError:
            return {}, None  # nothing saved yet
        try:
            private(lock, ".settings.lock")
            fcntl.flock(lock, fcntl.LOCK_SH)
            saved = json.loads(read_private(plugin, "settings.json", 16 * 1024))
            api_key = None
            if "api_key" in saved["secrets"]:  # IDs only, never values
                secrets = open_directory(plugin, "secrets", private_mode=True)
                try:
                    api_key = read_private(secrets, "api_key", 4096).decode("ascii")
                finally:
                    os.close(secrets)
            return saved["values"], api_key
        finally:
            os.close(lock)
    finally:
        os.close(plugin)


def read_request(actions):
    """The saved request, or None when there is none."""
    try:
        request = json.loads(read_private(actions, ACTION + ".json", 64 * 1024))
    except FileNotFoundError:
        return None
    if set(request) != {"schema", *SNAPSHOT} or request["schema"] != 1:
        raise SystemExit("unexpected request: stop and report")
    return request


def deliver(ids, values, api_key):
    """Require a confirmed endpoint/key pair before sending these drafts.
    Make it idempotent: skip IDs already
    sent and record every outcome, so a repeated request sends nothing twice."""
    raise NotImplementedError("send the drafts to your service here")


def consume(registry):
    key = registry_key(registry)
    plugin = open_plugin()
    try:
        try:
            actions = open_directory(plugin, "actions", private_mode=True)
        except FileNotFoundError:
            return "no request"
    finally:
        os.close(plugin)
    try:
        try:
            lock = os.open(ACTION + ".lock", FILE, dir_fd=actions)
        except FileNotFoundError:
            if read_request(actions) is not None:
                raise SystemExit("request without its lock: stop and report")
            return "no request"
        try:
            private(lock, ACTION + ".lock")
            fcntl.flock(lock, fcntl.LOCK_EX)
            snapshot = read_request(actions)
            fcntl.flock(lock, fcntl.LOCK_UN)
            if snapshot is None:
                return "no request"
            if snapshot["key"] != key:
                return "the request belongs to another registry; left alone"
            deliver(snapshot["ids"], *read_settings())  # outside the lock
            fcntl.flock(lock, fcntl.LOCK_EX)
            current = read_request(actions)
            if current is None:
                return "sent; the request had already been cancelled"
            if any(current[field] != snapshot[field] for field in SNAPSHOT):
                return "sent; a newer choice stays pending"
            os.unlink(ACTION + ".json", dir_fd=actions)
            os.fsync(actions)
            return "sent; request cleared"
        finally:
            os.close(lock)  # closing also releases the lock
    finally:
        os.close(actions)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: consumer.py <registry folder>")
    print(consume(sys.argv[1]))
```

**Make sends idempotent.** Cancelling or replacing a request cannot take back work a
consumer has already started, and a consumer can stop between sending and deleting the
request, so the same IDs can come back. Record each item's outcome in your own storage
and skip the items already sent. A saved request is the user's intent, never proof
that anything was sent.

Details: [Action requests](plugins.md#action-requests) and
[Files and consumer protocol](plugins.md#files-and-consumer-protocol).

<a id="agents-md"></a>
## 6. AGENTS.md: telling the coordinator what to run

The user should not have to run your programs by hand. Ship
`$HOME/.squadra/plugins/<name>/AGENTS.md` with plain instructions for the squad's
coordinator window. The coordinator reads it at start and resume and runs only the
commands it specifies, within the permissions the user has already granted: your
producer after a task closes, your consumer when the dashboard shows a pending
request. Then it reports the outcome to the user.

The file counts only if it is a **regular file owned by the user, not writable by
group or others, and not a symbolic link** (`chmod 600 AGENTS.md` is enough). Without
a valid file, a request stays pending and the coordinator says so. The dashboard never
reads, writes or runs this file.

The file never widens permissions. It never authorizes installation, push,
publication or decisions reserved for the user; for anything beyond running that
plugin's producer or consumer, the coordinator stops and reports. So keep it to those
two commands, and put installation and upgrades in your documentation for the user.
Your installer writes the real install folder in place of `<install folder>`:

````markdown
# release-notes: instructions for the coordinator window

This file never widens permissions and never authorizes installation, push,
publication or decisions reserved for the user. For anything beyond running this
plugin's producer or consumer, stop and report.

Use the open squadra registry's absolute folder for `<registry>`; shell-quote it.

At start, at resume and after every task close, refresh the panel:

```sh
python3 "<install folder>/producer.py" <registry>
```

When `$HOME/.squadra/plugins/release-notes/actions/send.json` exists (the dashboard
shows a pending «Send for review» request), run:

```sh
python3 "<install folder>/consumer.py" <registry>
```

It leaves another registry's request alone. Report its output to the user, including
refusals, then refresh the panel. Never print the API key or ask for it in chat: the
user saves it in the dashboard's Settings.
````

The rule itself: [Agent instructions](plugins.md#agent-instructions).

<a id="safety-checklist"></a>
## 7. Safety and limits checklist

| Area | Check before you publish | Contract |
| --- | --- | --- |
| Manifest | ≤64 KiB; `schema` is the integer 1; `name` equals the folder; no unknown fields or duplicate keys | [JSON schemas](plugins.md#json-schemas-version-1) |
| Panel | ≤2 MiB and ≤500 items; strings ≤4096 UTF-8 bytes; `updated` in UTC; stable item IDs | [JSON schemas](plugins.md#json-schemas-version-1) |
| Text | Plain text only: HTML, Markdown and URLs are shown, not interpreted | [JSON schemas](plugins.md#json-schemas-version-1) |
| About | Lengths count characters; publisher ≤64, version ≤32 (letters, digits, `.`, `+`, `-`), tagline ≤200, 1–8 sections | [Optional about metadata](plugins.md#optional-about-metadata) |
| Text direction | The bidi controls U+202A–U+202E and U+2066–U+2069 are refused in every `about` string; the homepage refuses all control and format characters. Keep them out of panels too | [Homepage and local icon](plugins.md#homepage-and-local-icon) |
| Homepage | `https`, a host, ≤200 characters; no userinfo, fragment (`#`), whitespace or backslash. Never fetched; opened only by the user's click | [Homepage and local icon](plugins.md#homepage-and-local-icon) |
| Images | Only the local `icon.png`: ≤32 KiB, 1–256 pixels per side. No remote images | [Homepage and local icon](plugins.md#homepage-and-local-icon) |
| Settings | 1–4 fields; secrets never in panels, logs, command lines or the registry | [Declared settings](plugins.md#declared-settings) |
| Action | At most one; `selectable` only on drafts; a request carries 1–500 IDs | [Action requests](plugins.md#action-requests) |
| Files | Regular files, no symbolic links, atomic replacement; plugin folder not writable by group or others | [Files and registry identity](plugins.md#files-and-registry-identity) |
| Statuses | Labels only: they approve nothing and record nothing in the registry | [Local data plugins](plugins.md) |

Every refusal code and its meaning: [HTTP contract](plugins.md#http-contract).

<a id="testing-locally"></a>
## 8. Testing locally

Test with a synthetic HOME and a throwaway registry, so your real installation and
your squads stay untouched. From the folder that holds your plugin files:

```sh
TRY="$(mktemp -d)"
PLUGIN_DIR="$TRY/home/.squadra/plugins/release-notes"
mkdir -p "$TRY/project" "$PLUGIN_DIR"
chmod go-w "$TRY/home" "$TRY/home/.squadra" "$TRY/home/.squadra/plugins"
chmod 700 "$PLUGIN_DIR"
cp plugin.json "$PLUGIN_DIR/"    # and icon.png, AGENTS.md if you ship them
squadra --root "$TRY/registry" init --name "Plugin test" --project "$TRY/project" --temporary
HOME="$TRY/home" python3 producer.py "$TRY/registry"
HOME="$TRY/home" squadra --root "$TRY/registry" dash --open
```

**Read what the dashboard read.** `dash` prints its address,
`http://127.0.0.1:<port>/#<token>`. From another terminal, ask for your plugin with
that token:

```sh
curl -s -H "X-Squadra-Token: <token>" "http://127.0.0.1:<port>/api/plugin/release-notes"
```

The answer is `{"name": "release-notes", "key": "…", "panel": {…}}`. Its `key` is the
dashboard's registry key and must equal what your producer used; `"panel": null` means
there is no panel file for that key. A refused panel answers `{"error": "<code>"}`
instead.

**What a problem looks like on the page.**

| You see | It means |
| --- | --- |
| «release-notes: refused (unknown_key)» under the Plugins group | The manifest broke a rule; the code names which ([HTTP contract](plugins.md#http-contract)) |
| «No data for this registry» | The manifest is valid, but there is no panel for this registry's key |
| «Plugin data unavailable or refused.» | The panel file was refused; the API answer above gives its code |
| No Settings card, or an unavailable one | No `settings` declared, or the saved files cannot be read safely |

**Check without a browser.** The dashboard's validator is plain Python in the skill's
`scripts/` folder. It is an internal module, not a stable interface, but it is handy in
your own tests:

```sh
HOME="$TRY/home" python3 -c 'import os, sys; sys.path.insert(0, "<skill folder>/scripts"); import plugins; print(plugins.discover(os.environ["HOME"]))'
```

A valid installation prints the manifest under `plugins` and an empty `refused` list.
A manifest with a misspelt field prints
`{'plugins': [], 'refused': [{'name': 'release-notes', 'code': 'unknown_key'}]}`.
`plugins.load_panel(home, "release-notes", key)` returns the panel, returns `None`
when it is absent, or raises `plugins.Refused` whose `code` names the rule.

**Test the action.** Declare the action and mark a draft selectable, tick it in the
Memories tab and press the button: `actions/send.json` appears under `$TRY/home`. Run
your consumer with `HOME="$TRY/home"`; the request disappears and, after the producer
runs, the page shows the outcome. To see the compare-and-delete at work, make a
different choice while your `deliver` is still running: the newer request stays
pending.

When you are done, delete `$TRY`.

<a id="publishing"></a>
## 9. Publishing your plugin

squadra 3.5.1 has no plugin store, plugin catalogue or plugin installer. You
distribute your plugin yourself, and each user installs it on their own machine.

- **Ship it from your own repository or package**: the producer, the optional
  consumer and an install step (a script, a package-manager recipe or clear manual
  steps).
- **What the installer does.** It creates `$HOME/.squadra/plugins/<name>/` (0700) in
  the HOME of the user who runs `squadra dash`, writes `plugin.json`, `icon.png` and
  `AGENTS.md` there (0600, each replaced atomically) and installs your programs where
  your platform expects them. It leaves `settings.json`, `secrets/` and `actions/` to
  the dashboard.
- **Document what it writes**: every path, what each program reads (which registry
  files, which settings), what it sends and where, and how to uninstall. Removing the
  plugin folder also deletes the settings and secrets the user saved for it; say so.
- **Version it.** Put the release in `about.version` (a label of letters, digits,
  `.`, `+` and `-`; it is not checked as a semantic version) and tag releases in your
  repository. Keep item IDs, setting IDs and kinds, and the action ID stable from one
  version to the next. An upgrade replaces `plugin.json` atomically; the dashboard
  picks it up at its next refresh.
- **Free or paid is your choice.** squadra is released under the MIT licence, and that
  licence does not restrict your plugin: your plugin is your own work under your own
  licence, open source or proprietary, free or paid. A plugin talks to squadra only
  through the files described here, so it does not need squadra's code. If you do
  copy squadra code into your package, keep squadra's copyright and permission notice
  with that copy, as the [licence](../LICENSE) requires.
- **Do not claim endorsement.** Do not present your plugin as made, reviewed or
  approved by squadra or its author. The `publisher` you write is shown as your own
  description, not a verified identity.
- **Keep secrets out of panels**, and tell users which settings are secret and what
  your service keeps of what it receives.

Back to the [plugin section of the README](../README.md#plugins).
