<a id="registro-dashboard-e-recupero"></a>

# Registry, dashboard and recovery

`registry.sqlite3` is authoritative, with WAL and synchronous FULL. Transactional
events, idempotent keys and a SHA-256 chain; content-addressed artifacts up to 16 MiB.
`state.json`, `events.jsonl`, `stato.md`, `squadra-posta.md` and
`posta/exchange-YYYY-MM-DD.md` are projections that `export` can regenerate.
Do not edit them by hand. A crash between commit and projection does not erase the event.

`doctor` verifies the chain/artifacts and flags ACKs, runs without receipts and uncertain
stops; `check` binds code, proofs, plan and review again. A valid log does not make a
window's report true. Identities are trusted within the same account; there are no
signatures or protection against someone who can rewrite the entire database.

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
handover and mail, run `doctor` and compare sources, WIP and reservations. Continue only
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

`checkpoint --to /new/directory` remains available: a SQLite backup, artifacts and
projections of the **registry only**. Do not copy an open SQLite database without its
WAL; do not delete WAL/SHM. `save` incorporates that checkpoint and adds the checkouts.
It does not automatically copy full conversations, client authentication, installed
dependencies or ignored files not explicitly included. Content-addressed logs are in
the registry; keep an explicit copy of any additional external evidence.

<a id="esclusioni-e-destinazione"></a>

## Exclusions and destination

Filters cover `.env*`, private keys/certificates, credential names and `PRIVATE KEY`
headers: untracked matches are excluded and listed; tracked matches or matches at HEAD
cause the package to be refused, because the bundle carries their history. This does
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
the report. You may add `--root /live-registry --actor coord` to record the receipt there
after successful verification.

Recovered authority and acceptances are **historical**: paths change, and the candidate
fingerprint includes the path. Do not silently remap a review; reread the mandate, mail
and receipts, reconcile sources/resources and wait for an explicit owner request before
`resume`. Restoration neither starts processes nor publishes. Checksums detect accidental
changes; they do not protect against someone who can also replace the manifest and checksums.

<a id="dashboard-e-versioni"></a>

## Dashboard and versions

`squadra dash --open` listens on 127.0.0.1, on a free port. The token is in the URL fragment;
the API requires a token and exact Host: recorded state and artifacts and plugin data projections.
Guarded POSTs for plugin settings and action selections write only the declared files.
No command or shell execution, no remote scripts or CDN. The disconnected view keeps the
last page with a warning. Ctrl-C stops that server, not the agents. Do not expose the port
or use it as a multi-user service.

`/api/state` reuses a verified snapshot while SQLite's data version and the database/WAL
file identity and change stamps are unchanged. Any change, including an older-row edit or
a replaced database, requires verification of the complete chain again; a verification
failure is reported rather than replaced with a cached success. Derived ages are never cached.
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
and exact-Host checks. They return only records of this registry, never arbitrary file paths.

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

`selftest --output /new/directory` keeps RESULT.json and logs; without the argument it uses
`~/.local/state/squadra/selftests/ID`. The overall timeout is 900 seconds (15 minutes), with
headroom for the full suite. Synthetic tests, no model quota or product gate. Node.js is
required: the dashboard's JavaScript contracts fail without Node; they are not skipped.
Comparative quality requires the separate harness in [benchmark.md](benchmark.md).
