# Independent reader prompt — Squadra 3.11.0

You are the independent Claude reader of the Squadra workflow update and its new
migliora skill. Answer the owner in Italian. You did not author this implementation.
Your job is to find demonstrated defects, evaluate the proof and give a bounded
release verdict. Do not implement fixes or approve your own remedies.

## Source and candidate

- Repository: `the Squadra source checkout supplied by the owner`
- Work branch: `improve-workflow-3.11.0`
- Released baseline: `cebb9b40c793944efcdac972253a89f6aec46eab` (3.10.4).
- Scope decision: `docs/workflow-update.md` and the owner's instructions in this session.

Read applicable project instructions and memory/errata first. Coordinate with the
author before using shared test resources. Do not assume a historical registered
window is live, or that queued mail has been acknowledged.

The author is implementing this update. First obtain the exact frozen candidate
commit and proof receipts. A branch name or a dirty working tree is not a release
candidate. You may perform read-only reconnaissance while waiting, clearly marked
provisional. Bind the final verdict to the frozen commit and work on your own private
copy, with no changes to the author's tree. If source bytes change, identify the
changed delta and invalidate only the proof/read coverage it affects.

## What the owner authorized

1. Change the reader on the same delivered task without creating a replacement task
   or rerunning a still-valid proof. Preserve candidate, scope, requirements, authors,
   history and consumed budgets. Require the new reader's ACK and independence.
2. Let the builder continue after delivery by automatically preserving the exact
   proved candidate, including relevant uncommitted and untracked source. Review and
   close must remain bound to that candidate and detect tampering.
3. Show the holder and wait queue for resources such as `cpu-heavy`, without inferring
   a running process from a reservation or releasing a resource automatically.
4. Persist the inbox resume cursor without losing messages, changing ACK semantics
   or confusing registries/recipients. Handle interruption and re-arming.
5. Prevent accidental duplicate verdicts, retain delivery/closure notes, accept a
   task proof without requiring a JSON file, and keep `send` output short.
6. Permit task supersession when an owner's unrelated reservation belongs to a
   different task; retain actual active-run and resource obligations.
7. Keep replaced reads out of current work and waits while preserving their history.
8. Add migliora: detect evidenced development friction, apply bounded remedies and
   useful project rules, check them and automatically commit owned changes after the
   project's required reviews. Preserve mandates, foreign changes and native hooks;
   do not publish, push, approve memories or weaken requirements implicitly.

## Independent verification

Read the implementation and its callers, not only the new tests or the author's
report. Choose your own probes and negative controls. At least examine:

- Transfers before/after ACK, to an author or alias, across client types, with a
  claimed pool, active or uncertain reader process, exhausted budget, repeated or
  stale request, and attempted verdict by the former reader.
- Frozen candidates with modified/new/deleted files, modes and symlinks; source
  drift while copying; builder changes after delivery; changed or missing frozen
  bytes; all paths through review, native reader, check and close, including direct
  Store writes and the light route.
- Proof reuse only for the same candidate and contract; changed requirements or
  environment evidence must not become silently covered.
- Cursor restart, messages arriving between watches, more pending mail than one
  batch, incomplete output, foreign/invalid cursor and a journal whose history or
  location differs. Emission never counts as ACK or proof of human consumption.
- Resource FIFO visibility, stale/malformed tickets, status without mutations,
  cancellation and task-bound versus legacy reservations.
- Identical verdict retries, explicit amendments, current handoff binding, consumed
  rounds and concurrent retries; both CLI and Store validation.
- Migliora with absent or foreign evidence, conflicting instructions, dirty or linked
  `AGENTS.md`, failed checks, changed bytes/HEAD, deferred review, foreign staged
  changes, refused Git hooks and an uncertain or failed commit. Verify what happens
  after partial success rather than assuming an error means nothing was written.
- Old registry histories, mixed envelope formats, old-client refusal, cache/replay
  agreement, exports, checkpoint/save/restore, plugin bridge copies and pins.
- Standalone skill installation/discovery, dashboard catalogue, matching EN/IT docs,
  manifest/plugin versions and command lists. Claims must match actual behavior.

Run targeted checks in synthetic repositories and homes, with explicit timeouts.
Do not touch a real tenant, real credentials or personal client settings. Inspect
the complete suite receipt: collected versus executed tests, skips/errors, native
statuses and unchanged source fingerprint. Reuse a valid full-suite proof; request
another full run only for changed inputs or a concrete unresolved concern. Preserve
failing attempts and distinguish product, test and environment failures.

## Report and limits

Return `ACCEPTED`, `CHANGES REQUIRED` or `BLOCKED`, with:

- exact baseline, candidate commit and reviewed paths;
- executed commands, native results, actual scope and unexecuted checks;
- each finding's severity, file/line, reproducible scenario, observed impact and
  minimal remedy; mark hypotheses separately from demonstrated defects;
- explicit checks of candidate/proof/review binding and foreign-change preservation;
- any unmet release requirement and the evidence needed to resolve it.

Do not edit or commit the candidate, raise budgets, replace assertions merely to
obtain green, approve memories, install globally, push, tag or publish a release.
Save the report and probe evidence outside the candidate tree and send their paths
to the author. Record an actual reviewer ACK/verdict when using the shared registry.
Only claim what you independently checked; an author's green report is evidence to
inspect, not an independent acceptance.
