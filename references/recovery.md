<a id="registro-dashboard-e-recupero"></a>

# Registry, dashboard and recovery

`registry.sqlite3` is authoritative, with WAL and synchronous FULL. Transactional
events, idempotent keys and a SHA-256 chain; content-addressed artifacts up to 16 MiB.
Source-file inventories can be stored once in those artifacts: a reference retains
the original source fingerprint and resolves to the same ordered file list at
binding checks. Existing inline fingerprints and historical event bytes remain unchanged.

`state.json`, `events.jsonl`, `stato.md`, `squadra-posta.md` and
`posta/exchange-YYYY-MM-DD.md` are projections, not the authority. Read-only commands
write neither the registry nor its projections. Ordinary writes append new canonical
records to `events.jsonl` and update affected UTC days and bounded summaries; they
do not regenerate the complete state and mailbox on every command. In the live registry,
`state.json` and complete mail/history exports are refreshed by `export` (and by a recorded
`restore-check`); `checkpoint` and `save` write them into their copy, not into the live folder.
Use `squadra export --root /path/to/registry` before consuming complete exported files.
Do not edit projections by hand. A crash between commit and projection does not
erase the event, but can leave the files stale. Compare the export's recorded
sequence/head with the current Store view; a shorter valid prefix is not proof of
a complete current history. Receive or transport messages through the current
`inbox`, not by treating a complete mailbox export as a live queue.

`doctor` verifies the chain/artifacts and flags ACKs, runs without receipts and uncertain
stops; `check` binds code, proofs, plan and review again. A valid log does not make a
window's report true. Identities are trusted within the same account; there are no
signatures or protection against someone who can rewrite the entire database.

<a id="registry-verification"></a>

## What is verified, and when

The derived cache saves calculated state and indexes. Its proofs bind each requested
value to the recorded cache root; the anchor identifies the event through which the
cache has already been folded.

| Operation | Verified during that operation |
| --- | --- |
| Other ordinary commands, including `check` and an ordinary task's `close`; dashboard refresh after startup | The recorded anchor event and its hash, every newer event's hash and previous link, and authenticated proofs for each cache value or index read. |
| Quick resume check (`doctor --quick`) | The anchor, newer suffix and demanded cache values; no full historical/artifact audit or replay-root comparison. The output labels this limited scope. |
| Explicit `usage`/`balance` calculations, a lesson's source check, and the lesson hint after a failed proof | The complete event hash chain and the requested historical evidence. These reads do not perform a full artifact audit. |
| `doctor`, `export`, `checkpoint`, `save`, `restore-check`, and a release-gate task's `close` | The complete event chain and referenced artifacts/manifests, plus full replay to compare or rebuild the derived cache. |
| Dashboard startup | The ordinary verified path serves data while a full chain/artifact/replay audit runs in the background. A visible status marks the pending audit; failure stops data responses and removes the displayed data. |

Use read-only `doctor --quick` when resuming. Run `doctor` without that flag for
the complete audit. A full audit reports cache/journal disagreement as a mismatch:
default doctor exits nonzero and the dashboard refuses data. Explicit
`doctor --repair` rebuilds the stored derived cache and re-audits; it refuses a
root inside a saved package or checkpoint. Healthy doctor and quick doctor never
repair or publish. Cold reconstruction and release-gate audits happen outside
the exclusive write transaction; a concurrent change can require a retry.

The other ordinary reads above do **not** reverify events before the anchor, untouched cache leaves,
or that the cached root equals a complete replay of the reducer. A missing, foreign
or invalid cache falls back to verified reconstruction. Verification failure never
becomes a successful stale-cache response. These checks detect isolated corruption;
they do not authenticate a coordinated rewrite of the journal, anchor and cache by
someone with the same account.

Upgrade every window together before the first new-format write. New events bind
`format: 2` into their hash; legacy rows remain unchanged. Squadra 3.10.0 therefore
refuses at the first such event with an integrity error, including status, check and
close. This intentional refusal is not data loss: use the upgraded CLI, never bypass
the verifier. New doctor/export commands verify the complete mixed-format history. Complete exports, checkpoints and immutable packet
bytes retain their own identity: resolving source files does not rewrite an old event
or change an existing handoff hash.

<a id="stop-e-pacchetto-completo"></a>

## Stop and full recovery package

<a id="checkpoint-automatici-dellagente"></a>

### Automatic agent checkpoints

The skill requires these backups without a question or reminder from the owner.
They are actions performed by the agent through the CLI, not a daemon or a universal
monitor of Codex/Claude/Cursor memory.

| When | Action |
|---|---|
| Increment completed or review accepted | Registry `checkpoint`, to a new destination |
| Before publishing or changing sessions | `checkpoint`, handover report and receipt in the output |
| WIP to preserve, a stop or context at risk | `save` the registry and repositories, then `restore-check` |

A backup does not require a new test suite run. Use existing evidence when the candidate
is unchanged. Do not call a registry-only `checkpoint` complete: Git, staging, untracked
files and WIP are preserved by the `save` package. `save` requires the coordinator role.
A builder or reviewer saves its report and checkpoint, then sends the coordinator an
operational request for a full package; it does not assume the coordinator's identity.
The coordinator performs it within the already authorized scope, without asking the
owner for permission again.

```sh
squadra checkpoint --to /durable/path/checkpoint-001
squadra output --actor coord --file /evidence/checkpoint-update.md \
  --next "Continue from the saved point" --artifact /evidence/checkpoint-receipt.json
```

The second command assumes the agent has written those two files with the actual result
and path returned by the first. Do not invent the receipt. A checkpoint does not pause
work; an explicit stop from the owner also requires `pause`.

<a id="passaggio-a-un-contesto-pulito"></a>

### Handover to a clean context

Act before losing track: respond to an actual signal from the client or when you can
no longer reliably reconstruct the constraints. Do not publish context percentages
that the client does not expose. Compaction within the same session and opening a new
window are different events; a checkpoint is needed in both cases.

Before the change, keep a `HANDOVER.md` outside the sources containing:

- objective and task, role, actual identity, session and shared registry;
- clone, branch, SHA, staged/unstaged/untracked state and owned paths;
- owner decisions, blockers, skills read with their paths/hashes;
- proofs and reviews actually performed, limits, logs and findings still open;
- pending messages/ACKs, budgets already consumed, owned processes and resources;
- latest checkpoint/package, recovery verification and one concrete next step.

Save WIP with `save`, verify recovery and record the handover with `output
--artifact /evidence/HANDOVER.md`; use `send` to hand it over to the recipient. The package
captures repositories and the registry: an external handover remains in the registry
artifact, not in an `--include` outside the selected repositories.

If the tool supports a new session, the coordinator may start one to continue already
authorized work. Otherwise prepare the exact prompt and declare the handoff still
pending. The receiving session must actually acknowledge the message, read the mandate,
handover and mail, run `doctor --quick` and compare sources, WIP and reservations. Continue only
after an actual ownership handoff: no second writer or equivalent new tasks to reset
budgets.

Keep the identity if it is still the same window; a new session ID can be registered
with `agent --session` while preserving identity and role. A different window needs a
new seat: the registry refuses to rewrite the old seat's identity and offers no command
for automatically transferring task ownership. Do not bypass this limit with an alias;
retain the handoff and leave reconciliation to the coordinator. The previous builder
remains an author after the change and does not become an independent reviewer.
Other windows' tasks and squads with a frozen protocol are not restarted or migrated.

```sh
squadra pause --actor coord --reason "Owner requested a stop"
squadra doctor
# Reconcile owned processes and terminal receipts first:
squadra save --actor coord --to /durable/path/save-001
squadra restore-check --from /durable/path/save-001 --to /durable/path/restore-test-001
```

A pause prohibits new builds, reviews, proofs and deliveries. Do not terminate shared
services or windows belonging to another squad. A heartbeat or queued message does not
prove that a process stopped. A denied signal leaves `cleanup-unknown`, which requires
actual reconciliation.

`save` discovers all Git roots of the project, agents and tasks, deduplicating them.
It saves HEAD/branch and a verified Git bundle of all refs, **separate** binary patches
for staging and worktree, non-ignored untracked files, modes and relative symlinks
confined to the repository. `--include /repo/ignored-file` adds selected files, including
ignored ones; an explicit directory includes its files. A missing repo, submodules,
sparse checkout, LFS or links leading outside the repo cause the copy to be refused.
Use repeated `--repo /clone` only for a selection declared to be **partial**.

HEAD, files, index and inclusions are compared before/after, including across all
repositories after the registry checkpoint. A source that changes produces an incomplete
package with `FAILED.json`, not a green backup. The destination must be new and outside
the sources and registry. `/tmp` is allowed only with `--temporary` for tests; choose a
durable directory for work.

`checkpoint --to /new/directory` remains available: a SQLite backup, all referenced
artifacts/source manifests and complete projections of the **registry only**, with
checksums. The package is self-contained; it does not depend on the live registry's
files remaining in place. Do not copy an open SQLite database without its
WAL; do not delete WAL/SHM. `save` incorporates that checkpoint and adds the checkouts.
It does not automatically copy full conversations, client authentication, installed
dependencies or ignored files not explicitly included. Content-addressed logs are in
the registry; keep an explicit copy of any additional external evidence.

<a id="esclusioni-e-destinazione"></a>

## Exclusions and destination

Filters cover `.env*`, private keys/certificates, credential names and `PRIVATE KEY`
headers: untracked matches are excluded and listed; tracked matches or matches at HEAD
cause the package to be refused, because the bundle carries their history. A tracked
template ending in `.example`, `.sample`, `.template` or `.dist` (such as `.env.example`)
is judged by its content only: a `PRIVATE KEY` header still refuses it. This does
not find every secret: **there is no complete scan of Git history or registry artifacts**.
Inspect the data before sharing it.

`MANIFEST.json` records tool/version/UTC, repos, fingerprints, inclusions/exclusions,
event head and count, limits and destination provenance. Same `st_dev`: local checkpoint.
Different `st_dev`: different filesystem, external disk unverified.
`--declared-offsite "..."` remains a declaration; `offsite_verified` remains false.
No automatic upload. Protection against disk failure requires a separate copy.

<a id="prova-di-ripristino-e-ripresa"></a>

## Restore check and resumption

`restore-check` verifies inventory/checksums, added/missing files and confinement
**before** opening Git or SQLite. In a new destination, it restores a clone from the
bundle, HEAD, index and worktree patches, files/modes/symlinks; it compares the expected
contents and staging. It verifies the copied registry's chain, artifacts, count and head.
It writes `RESTORE-RESULT.json` and the path mapping in `RESUME.md`, without changing the
original package or live registry. After verification, it adds a tool `restore.freeze`
receipt only to the recovered copy, pausing it; the verified historical head remains in
the report. With `--actor coord`, the receipt is recorded after successful verification in
the live registry, chosen with `--root` or `SQUADRA_ROOT` like every registry command; any
other actor is refused before anything is restored.

Recovered authority and acceptances are **historical**: paths change, and the candidate
fingerprint includes the path. Do not silently remap a review; reread the mandate, mail
and receipts, reconcile sources/resources and wait for an explicit owner request before
`resume`. Restoration neither starts processes nor publishes. Checksums detect accidental
changes; they do not protect against someone who can also replace the manifest and checksums.

<a id="removing-a-leaked-artifact"></a>

## Removing a leaked artifact

Artifacts are files the registry keeps by their SHA-256 digest under `<root>/artifacts`.
When one records a secret by mistake (a token, a key, a captured environment), the
coordinator removes the file without breaking the registry:

1. Find the digest: it is the file name under `artifacts/`, and the record that cites it
   (a task scope, a report, an output) shows it.
2. Delete `<root>/artifacts/<digest>`.
3. Record `squadra decision --actor coord --scrub-artifact <digest> --reason "Leaked session token"`.

Steps 2 and 3 work in either order. The decision is an ordinary `decision` event with a
`scrub` field naming the digest and the tasks that reference it; only a coordinator can
record it, and only for a digest that a recorded event registered. A source-file manifest
cannot be scrubbed, because the registry resolves recorded snapshots from it.

From then on the full audit (`doctor`, `export`, `checkpoint`, `save`, `restore-check`, a
release gate's `close` and the dashboard's background audit) accepts the missing file and
reports it as `scrubbed`, with its decision. Every other missing file still fails, and so
does a scrubbed file that is present but changed: the decision covers absence only. The
dashboard answers a scrubbed artifact with a plain «Scrubbed by decision …», in the
workspace language, instead of its content.

The event chain is untouched: the digest stays, and text recorded inline in events (a
brief, a message, an output's text, a decision reason) stays in the history. Copies made
before (checkpoints, saves, other disks) still hold the file; delete it there too, and
rotate the leaked secret anyway.

Published Squadra 3.10.1 does not know the `scrub` field. It replays the decision as an
ordinary one, but its full audit, its dashboard, and its `check` or `close` of a task that
references the scrubbed artifact still fail on the missing file until that window updates.

<a id="dashboard-e-versioni"></a>

## Dashboard and versions

`squadra dash --open` listens on 127.0.0.1, on a free port. The token is in the URL fragment;
the API requires a token and exact Host: recorded state and artifacts and plugin data projections.
A script sends the token in the `X-Squadra-Token` header, for example
`curl -H "X-Squadra-Token: <token>" http://127.0.0.1:<port>/api/state`; without the token
the API answers 401, and with any other Host 403.
Guarded POSTs for plugin settings and action selections write only the declared files.
No command or shell execution, no remote scripts or CDN. The disconnected view keeps the
last page with a warning. Ctrl-C stops that server, not the agents. Do not expose the port
or use it as a multi-user service.

Dashboard startup runs the complete chain, artifact and replay audit in the background.
The first view uses the ordinary authenticated state path and verifies the raw event
chain needed for historical charts. Until the full audit completes, the page shows
“Full registry verification in progress”. A failed audit latches an integrity error:
the server refuses further data and the page removes its displayed data. Cold first
response and time to completed audit are separate measurements.
Later reads use the anchor, suffix and requested-cache proofs in the
[verification table](#registry-verification); they do not rescan every older event
after each append. A missing, foreign or invalid cache requires verified
reconstruction, and a verification failure is reported instead of a cached success.
Derived ages are never cached.
The response returns, using the same read-time clock,
waits (`waits`), presence (`presence`), the decision graph (`graph`), each task's steps
(`progress`) and communications (`comms`, at most the 500 most recent, without message
bodies). The page opens on Summary, has Communications and Decisions tabs and the presence
panel in Windows; the IT / EN button changes the language of the entire page and remembers
it only in that browser. Ages and deadlines are computed at read time: opening the page
neither writes events nor changes any window's availability.

The page requests `X-Squadra-View: compact`: only the source-file inventories inside task
and run fingerprints are omitted from recurring responses. Fingerprint metadata, registered
artifacts, legacy file lists and raw Timeline events remain. Without that header, the API
returns the complete state. `X-Squadra-Search` carries the URI-encoded search text; matches
against complete task/run records, including omitted paths and hashes, are returned in
`search` with the query and the matching task/run IDs. The page waits for the matching reply
before replacing its search results. The technical-data buttons read `/api/detail/task` or
`/api/detail/run` with the URI-encoded record ID in `X-Squadra-Record`, under the same token
and Host checks (`127.0.0.1`, `localhost` or `[::1]` with the dashboard's port; any other Host is
refused). They return only records of this registry, never arbitrary file paths.
`/api/forecast` returns, under the same checks, the comparison `squadra forecast --task`
prints for the task whose URI-encoded ID is in `X-Squadra-Record`; an unknown task gets
409 `unknown_task`. The page does not call it.

A changed registry is read again only from its new events: records they did not change are reused
with their requirements, fix status and compact form, and the reply equals a full rebuild. An idle
poll with the same head answers «not modified» without recomputing waits, presence or activity until
one of them can cross a threshold. The page sends `X-Squadra-Delta: 1`; when the server still holds
the reply named by the page's token, a changed poll returns only what differs from it (`delta`), and
a changed search alone, before any threshold or 15-minute boundary, returns only the new results.
A held reply's `server_time` is the instant its ages were computed, and the page ages them from there
to the reply's current time. Otherwise, and for every client that does not ask, the whole reply. The complete view (no `X-Squadra-View`) is refused with
`full_state_too_large` when it would materialize more than 200,000 source-inventory entries.

Initial load and page reload show a loading message; later refreshes keep the current view
with a small updating indicator. Each GET has a 15-second deadline covering the response
body as well as connection/headers. Failure leaves a message and a Retry action. State requests
never overlap; regular polling waits two seconds after completion, while a changed search or
an explicit retry can request the next state sooner. Hidden tabs suspend new
state polls and refresh when visible again. Read dialogs keep their complete text, while
truncation measurement is grouped and limited to visible content.

`legacy --actor coord --path /old/package` preserves **historical, unconfirmed** copies,
without generating ACKs or tasks. Active squads remain on their frozen protocol/version;
a new installation does not migrate them.

`selftest --output /new/directory` keeps RESULT.json and logs outside the Squadra source;
without the argument it uses `~/.local/state/squadra/selftests/ID`. It runs the suite with
`tools/parallel_tests.py`, which fails when a collected test did not run; `--timeout SECONDS`
(default 1800) bounds the whole run and each test module. An interrupted run reports how
many tests its finished modules completed, out of those collected. Synthetic tests, no model
quota or product gate. Node.js is required: the dashboard's JavaScript contracts fail
without Node; they are not skipped.
Comparative quality requires the separate harness in [benchmark.md](benchmark.md).

<a id="dashboard-page"></a>

## The dashboard page, view by view

*Moved from the README, word for word; the README keeps the summary, the views table and the safety paragraph.*

`squadra dash --open` opens on the **Summary** (Quadro). One sentence and four tiles say where the work stands: tasks closed, in progress, to fix or stuck, and active windows (with how many are in stand-by). **Needs you** (Serve a te) appears only when a question, an exhausted budget or a pause is recorded for the owner. Then one card per task: its brief title and six recorded steps, who builds and who reads («Builds: Claude · Reads: another Claude window»), the latest event and the next step the registry expects, and, apart, the next step a window declared, until a later decision or brief answers it. Each card opens the task's **story** in time order: brief, plans and their reads, proofs, deliveries, questions, decisions and messages, with documents opened by name. Below come the five **Trends** charts; closed and replaced tasks, the five-question overview and the technical tiles are folded at the bottom. Identifiers stay in «Technical details», which stays open with your reading position while events arrive; an empty field is never printed.

- **Menu and top bar.** A side menu, collapsible to icons, becomes a drawer on a phone (☰; Esc, × or a choice closes it). The top bar holds the project, search, IT / EN, the theme (Automatic, Light, Dark), the **Sounds** switch and the registry connection. Language and theme are remembered in this browser only, and the page still works when storage is refused. Recorded words are never translated; «How to read this page» explains every term.
- **Five charts:** tasks by state; how far each task has gone (a red cross marks a step reached with changes required); activity in the last 24 hours or 14 days, counted by the server in quarter hours and shown in your time zone (or in UTC, with a note), run heartbeats excluded; proofs passed and failed; reads and verdicts within each task's budget. Each states its result in a sentence and can show its data as a table; ✓, ✗, ■ and empty circles carry the meaning, never colour alone.
- **Tables** for Tasks, Windows, Runs and Mail sort, filter and search 100 rows at a time; identifiers stay in each row's «Details», and keyboard focus survives a refresh. **Long texts** anywhere show a few lines and a «Read» button that opens the whole text, as text and never as markup, in a dialog that Esc closes.

![The «Read» dialog: a long task text opened in full; Esc or the close button returns to the table](../docs/images/dashboard-read-dialog-en.png)

- **Sounds and notices.** A short chime and «Step done: <task title>» when a task closes («Steps done: N» for several); a rising melody and «Work complete: no open task» when the last one does. Notices fade after a few seconds, can be closed, stack at most three and appear even with sounds off. Sounds are on by default and play only after your first click in the page («Audio after a click in the page.»), never as it opens, and a hidden tab may skip one. The browser generates them, with no audio files and no network request.
- **Task links** on every card open its decisions, messages, checks, reviews, results and communications, selecting the task by its recorded identity even when titles repeat; a banner explains the context, **Back to the task** reopens its history and **Show the complete view** removes the context.
- **Footer:** «Local view, read-only except plugin settings and saved selections · no actions on clients.», and the credit «Developed by Stefano D'Agostino · GitHub», linking to [the project on GitHub](https://github.com/dagoSte/squadra).
- **Look and contents.** Cream and paper surfaces with ink text, a dark espresso theme, highlighter orange as the only accent, Geist type, colour-blind-safe status colours and WCAG AA in both themes. Reboot 5.3.8, 40 Bootstrap Icons 1.13.1 (MIT) and two typefaces (SIL Open Font License 1.1) are copied byte for byte and served by `squadra dash` itself ([files, versions, licences and hashes](../assets/vendor/VENDOR.md)). No Bootstrap JavaScript, nothing from the network: the Content-Security-Policy adds only `font-src 'self'`.

![The Tasks view with proof, read and linked documents](../docs/images/dashboard-tasks-en.png)

![The Windows view: the team and the presence panel with one window in stand-by](../docs/images/dashboard-presence-en.png)

*Two windows confirmed the coordinator's call; one did not and was put in stand-by. Role labels demonstrate the protocol, not a model's quality; registration, heartbeat and answers do not prove that a process is running.*

**Presence.** The presence panel names each window by role and task (the brief's first line, otherwise the recorded title) and explains calls, replies and stand-by in plain words, keeping a missing reply and an explicit coordinator hold apart. The team panel groups the roles of one window; registered presence is not a claim that a process is working right now.

**Communications** answers «who is talking to whom, and where does it stand?». Filters stay on top (Period, How many, Task, Window, Search, «Reset filters»). «Who talks to whom» has one row per pair of windows, counted on the whole registry; selecting one filters the timeline. The timeline draws one arrow per message with a plain sentence, such as «The coordinator (Claude) wrote to the reviewer (Codex) about the task titled “Pagina web delle chiavi per i clienti” — sent, not read yet.», alongside presence calls and stand-by. Sent is not read and queued is not delivered: only a recorded confirmation proves reading. States are written in words with their own line style; a message text opens only when you ask; refreshes keep your position, open rows and filters.

![Communications filtered to one pair: the selected row filters the timeline](../docs/images/dashboard-comms-pair-en.png)

Every screenshot exists in both languages:

| View | English | Italian |
|---|---|---|
| Summary | [EN](../docs/images/dashboard-overview-en.png) | [IT](../docs/images/dashboard-overview-it.png) |
| Summary, dark theme | [EN](../docs/images/dashboard-overview-dark-en.png) | [IT](../docs/images/dashboard-overview-dark-it.png) |
| Phone | [EN, dark](../docs/images/dashboard-phone-en.png) | [IT, menu](../docs/images/dashboard-phone-it.png) |
| Task story | [EN](../docs/images/dashboard-story-en.png) | [IT](../docs/images/dashboard-story-it.png) |
| Read dialog | [EN](../docs/images/dashboard-read-dialog-en.png) | [IT](../docs/images/dashboard-read-dialog-it.png) |
| Tasks | [EN](../docs/images/dashboard-tasks-en.png) | [IT](../docs/images/dashboard-tasks-it.png) |
| Windows and presence | [EN](../docs/images/dashboard-presence-en.png) | [IT](../docs/images/dashboard-presence-it.png) |
| Communications | [EN](../docs/images/dashboard-comms-en.png) | [IT](../docs/images/dashboard-comms-it.png) |
| Communications, one pair | [EN](../docs/images/dashboard-comms-pair-en.png) | [IT](../docs/images/dashboard-comms-pair-it.png) |
| Decisions | [EN](../docs/images/dashboard-decisions-en.png) | [IT](../docs/images/dashboard-decisions-it.png) |
| Reads | [EN](../docs/images/dashboard-reads-en.png) | [IT](../docs/images/dashboard-reads-it.png) |
| Usage | [EN](../docs/images/dashboard-usage-en.png) | [IT](../docs/images/dashboard-usage-it.png) |
| Balance | [EN](../docs/images/dashboard-balance-en.png) | [IT](../docs/images/dashboard-balance-it.png) |
| System skills | [EN](../docs/images/dashboard-system-skills-en.png) | [IT](../docs/images/dashboard-system-skills-it.png) |
| Plugin page | [EN](../docs/images/dashboard-plugins-en.png) | [IT](../docs/images/dashboard-plugins-it.png) |
| Plugin settings | [EN](../docs/images/dashboard-plugin-settings-en.png) | [IT](../docs/images/dashboard-plugin-settings-it.png) |
| Plugin memories | [EN](../docs/images/dashboard-plugin-memories-en.png) | [IT](../docs/images/dashboard-plugin-memories-it.png) |
| Plugin store | [EN](../docs/images/dashboard-plugin-store-en.png) | [IT](../docs/images/dashboard-plugin-store-it.png) |

| View (Italian name) | What you can inspect |
|---|---|
| Summary (Quadro) | Status, tiles, Needs you, task cards and stories, waits and presence, Trends; folded below, the overview, technical tiles, reservations and recent decisions. |
| Windows (Finestre) | Windows named in words: role, tasks, latest output, skills, next step; identity, session, model and skill hashes in Details; then the presence panel. |
| Tasks (Incarichi) | Each task under its brief title: status, builder and reviewer, latest proof, next step; description, dependencies, reviews, requirements and artifacts in Details. |
| Mail (Posta) | Who wrote to whom about which task, the start of the message, ACK and delivery. |
| Communications (Comunicazioni) | Who talks to whom and why, live, in plain words. |
| Decisions (Decisioni) | The decision graph, with details, task filter and zoom. |
| Runs (Prove) | Task, who, kind, result, duration; command, native result, logs and timeout or cancel state in Details. |
| Outputs (Output) | Saved updates and declared next steps. |
| Timeline (Cronologia) | The latest 120 events; use `export` for complete history in `events.jsonl` and check its recorded sequence/head against the current Store view. |
| History (Storico) | Old records imported as historical, unverified copies. |

**Words used below.**

| Term | Meaning |
|---|---|
| ACK | The recipient's explicit confirmation; a queued message is not yet confirmed. |
| Candidate / review | A fixed version of the work / a check by someone who did not write it. |
| ID / hash | A technical name for a record / a fingerprint of the exact contents checked. |
| Log / exit | A command's recorded output / its termination code. Read both together. |
| Checkpoint / WIP | A copy of the registry for recovery / unfinished code changes. A checkpoint alone does not copy the code. |
| UTC / PID / cwd | Universal time / process number / working directory. Technical data, not proof of activity. |
| Brief | A short plain-words description of the project or of a task, written by the coordinator; a task's brief gives it its name on the page. |
| Ping / stand-by | A presence call asking a window to confirm it still answers / the coordinator's record that a window gets no new assignments for now. Stand-by stops nothing that is running. |

<a id="checkpoint-details"></a>

## Checkpoint, save, restore and handover: the README summary

*Moved from the README, word for word.*

- **`checkpoint`** copies **only the registry**, its artifacts and projections. **`save`**, coordinator only, adds every registered Git root (history bundles, HEAD, branch, index and worktree patches, untracked files) into a new destination outside registry and sources (`--temporary` for throwaway ones); `--repo` makes a declared partial package and `--include` adds ignored files inside a selected repository. Sources that change meanwhile refuse a green package. Builders and readers ask the coordinator for it within the existing scope, without asking the owner again.
- **`restore-check`** verifies checksums before opening anything, restores into a new folder and compares the result; the restored copy is paused, old acceptances are historical, nothing restarts by itself, and `--actor coord` records the receipt in the live registry (`--root` or `SQUADRA_ROOT`); any other actor is refused before restoring.
- **When context fills up**, on a real client signal or when constraints can no longer be reliably reconstructed, the agent saves a handover **before continuing**: task, identity, branch and SHA, open findings, pending ACKs, budgets, resources and one exact next step. It never invents token percentages. A fresh session ACKs, runs `doctor` and reconciles sources and resources before writing; a prepared prompt does not prove that a window started. The same window keeps its identity and updates `agent --session`; a different window needs a new seat. There is **no command that moves a task to another owner**: keep one writer, never duplicate a task to erase a budget, and earlier builders stay authors.
- **A pause** blocks new work, reviews, runs and deliveries and keeps consultation and recovery, while a checkpoint alone pauses nothing. Stop only the processes you own; resume only on the owner's word, after reconciling processes and state. Other squads are never migrated or resumed.

Always write through the CLI, never into the generated files. `export` regenerates complete state, event and mail/history projections; `status` and `inbox` read current work and messages without rewriting those exports. `doctor` checks the complete chain and artifacts and reports pending ACKs or uncertain runs; the [verification table](#registry-verification) distinguishes this from ordinary reads. Integrity is not proof that claims are true, nor cryptographic authentication of identities. `legacy --actor coord --path /old/packet` imports records as **historical, unverified copies**, never touching the originals, creating tasks or ACKs, or granting acceptance.
