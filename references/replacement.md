# Replacing windows during a milestone

Introduced in 3.11.1. Upgrade all windows
together before using it. Replacement, service waits and onboarding settings write
journal format 4: 3.11.0 and earlier builds refuse that format instead of ignoring the
new state. Keep the registry backup and use the refreshed reader to resume it.

## Discover and preview

```sh
squadra census --root REGISTRY --unregistered
squadra replace --root REGISTRY --actor coord --old codex-old --new codex-new \
  --reason 'Old window exhausted its token budget' --session NEW_SESSION --dry-run
```

Codex candidates come from session metadata whose cwd matches the project, its Git
worktrees or registered repository roots. Registered sessions are excluded. This
does not read conversations, prove liveness or select a session automatically.
For a new Codex or Claude seat without `--session`, replace returns candidates and
changes nothing. Verify the candidate and repeat with its session ID. Claude's
live peer API belongs to the coordinator client; when available, export its peer
list as a JSON array of `{ "session": "ID", "cwd": "/project" }` records and use
`census --unregistered --peers-file peers.json`. A missing peer source is reported
as a limitation, not an empty live census. Cursor uses manual delivery.

`--dry-run` lists the entire registry plan, generated messages and artifact
references without writing events, artifacts, projections or queue messages.

## Apply a handover

Repeat the preview command without `--dry-run`. `--client codex|claude|cursor`
selects the client for new seats; otherwise the old seat's client is retained.
Known replacement seats retain their registered session and client. A reader role
on a builder window uses a `<new>-read` seat with the same identity, never a new
independent author identity.

One append-only `replace` event registers the new seats, stands by all old session
aliases, creates successor tasks and reassigns reads. Successors keep the original
scope after a `taken over from <old>` header, repository, proof command, previous
authors and consumed build/read budgets. Their base is the repository's current
HEAD; they require their own proof. A required plan keeps its exact artifact and
requires fresh independent plan approval, even if the predecessor's plan was accepted.
Previous verdicts remain on the predecessor linked by `taken_over_from`; consumed
rounds are not reset. Repeated takeovers keep this gate. Current dependencies follow
the successors. Affected light contracts are rebound only after final reader and
dependency declarations; old proof bindings no longer count, and light-policy limits
remain unchanged.
Delivered reads retain the proved candidate and frozen copy, but need fresh ACKs.
An author or session alias cannot become the independent reader. Existing
cross-client rules and recorded exceptions still apply.

Pending mail to the replaced windows stays in history with its replacement link;
it leaves active ACK waits without a fabricated ACK. Each new window receives one
onboarding digest containing the registry, identity, project rules and assignments.
Assignment and review messages retain their own required ACKs in the inbox.

- `--carry useful` (default) includes pending ordinary context, excluding presence
  pings, generated assignments/handoffs and messages about already superseded tasks.
- `--carry all` also includes ordinary context about already superseded tasks.
- `--carry none` sends onboarding and assignments without copied pending context.

Stop/resume/pause messages identified by those ID prefixes or the first word of
their body are never carried. Classification is deterministic, not a semantic
summary: inspect the dry-run when messages use other wording.

Configure project instructions through the existing settings file, alongside its
wait thresholds: `{"alive":300,"onboarding_template":"Read AGENTS.md and the task scope; ACK before starting."}`.
The template is literal UTF-8 text, at most 8192 bytes; it grants no authorization.

## Safety and delivery receipts

The command refuses recent ACKs (the configured `alive` window) and live or
unverified runs unless `--force` accompanies the required nonblank reason.
Force never kills a process or declares uncertainty resolved. Verified absent
processes with no terminal receipt or results are abandoned under their run lease;
unresolved runs remain visible and block execution of their successor tasks until
reconciled. Reservations belonging to the identities or sessions of live/uncertain
runs remain held, including reservations owned by their role aliases: unrelated work
cannot acquire them. Reconcile those runs to a verified terminal state before explicitly
releasing their reservations. The replacement releases reservations of leaving windows
only when no unresolved run of that identity/session can still use them.

Atomicity covers the registry. Native client queues are external: delivery happens
after commit, with deterministic message IDs and separate attempt/result receipts.
Codex uses `codex-queue` when the session is known. Claude and Cursor retain a
manual delivery instruction; their peer tools must be used by the coordinator.
A queue receipt is not an ACK. Repeating the same replacement is idempotent and
does not resend attempted onboarding. It resumes delivery intents that have never been attempted. If an attempt is interrupted, inspect its receipt and
current inbox before explicitly retrying `transport --force`; a crash after
enqueue can leave an uncertain delivery and must not trigger a blind retry.

`send --id NAME` now prints a new suffixed ID if NAME already exists. Always use
the printed ID. Transport refuses retired messages and messages predating the
recipient's current session registration without `--force`. Re-registering the
same session does not make its existing mail stale.

## External limits and partial saves

```sh
squadra wait --root REGISTRY --actor codex-new --until 19:30 --cause 429
squadra wait --root REGISTRY --actor codex-new --until 2026-10-26T19:30:00+01:00 --cause outage
```

This records an observation; it does not sleep, retry a request or stop work.
Status, board and dashboard show the service wait until its deadline. HH:MM means
the next occurrence in the caller's local time; explicit ISO times require an
offset. The record stays in history after expiry. Squadra cannot observe HTTP
errors hidden inside an interactive Codex window. Automatic 429/5xx capture is
not provided: a client-side hook can invoke this command with its known retry time.

`save --repo ROOT` validates the selected repositories and destination containment.
Missing unselected registered repositories appear as MANIFEST warnings, so removed
worktrees do not prevent a partial save. A missing selected repository still fails.
A partial save does not recover omitted repositories.

## hotskills, 26 October 2026

The reported incident needed about twenty manual commands to replace four old
Codex identities with three windows and stand by eleven old seats. The following
IDs and session values are illustrative, not reconstructed incident identifiers.
Assume four old windows with 3, 3, 3 and 2 registered seats respectively.

```sh
squadra census --root REGISTRY --unregistered
squadra replace --root REGISTRY --actor coord \
  --map old-a=new-a,old-b=new-a,old-c=new-b,old-d=new-c \
  --session new-a=VERIFIED_SESSION_A --session new-b=VERIFIED_SESSION_B \
  --session new-c=VERIFIED_SESSION_C --client codex \
  --reason 'Owner closed the exhausted windows and opened replacements' --dry-run
# Inspect the plan, then run the same command without --dry-run.
squadra board --root REGISTRY
squadra inbox --root REGISTRY --agent new-a
```

The map produces three onboarding digests, stands by all eleven old seats and
shows `new-a (replaces old-a, old-b)`. The new windows read and ACK their actual
assignment/review messages before starting. Add `--force` only after reconciling
the recent-answer/live-run warning and retain that reason in the audit trail.
The selftest includes this synthetic 4→3/11-seat case, single/map replacements,
independence, digest filtering, idempotency, dry-run, transport, save and waits.
