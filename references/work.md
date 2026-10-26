# Task, proof, review and output

Ordinary command output shows the board, every owner request, wait counts by kind
and at most five grouped waits. Waits of the same kind and task share a line with
their count and oldest age. Assignments and current delivery/read handoffs come
first, then active-window pings and process alerts; age orders each priority.
Unflagged historical notices still require ACK and remain in the counts. Messages
sent with `--info` do not. A table retains up to five open tasks'
next steps and the windows/roles holding them, with an explicit remainder.
Use `squadra status --all --root
REGISTRY` for the complete previous text view and every wait. Ordinary `stato.md`
is a bounded current summary; `squadra export` refreshes its complete projection.
JSON results and dashboard data retain their complete contents.

## Forecast from comparable work

`squadra forecast --task TASK --root REGISTRY` verifies and reads an existing
registry without exporting it or writing an event. Task creation, briefing and
start also print this comparison. Follow [the previsione guide](skills/previsione.md)
for cohort selection, the three-sample minimum and elapsed-time interpretation.
The coordinator follows the suggested route or records a reason to deviate;
the forecast itself cannot change a route, reviewer, budget or permission.
The System skills page explains the automatic trigger and shows **Computed on
demand**, because no historical activation counter is recorded for forecasts.

## Balance of windows and models

`squadra balance [--task TASK] [--reference FILE] --root REGISTRY` reads the registry
and the Usage collector's numbers without writing anything. Task creation, briefing and
start also print a short balance: the first window for building and for reading, each
with its reason and the number of candidates, without tokens or quota, and the
`squadra balance --task` command that prints the whole ranking. Follow
[the bilanciamento guide](skills/bilanciamento.md) for the order, the missing-data rule,
the quota slots and the Claude estimate. The coordinator follows the suggestion or records
a reason; the advice cannot change an owner, reviewer, route, budget or permission.

`status`, `stato.md`, `census` and the dashboard's overview also show a **Composition**
line: the Claude and Codex windows that can build or read, counted from the registered
windows (the coordinator's window and windows in stand-by left out), with the source named.
`census` adds the processes it sees, never counted as windows, and says when its scan is
partial; before any registration it counts those processes and says so. The advice follows
the cross-model rule below: with one client only, open a window of the other model or record a
`--same-client-reason` for each read (with only Codex, a Claude window still coordinates);
with an imbalance such as 2 Claude and 1 Codex, the lone Codex window reads everything and
becomes the bottleneck, so give it a build lane read by a Claude window or open another
Codex window; with a balanced squad, pair each builder with a reader of the other model,
listed for the newest open tasks that record no same-client reason; in the
[final block](#final-block) that is the ledger's planned reader of the other model for the
builder's delta. The coordinator gives this advice to the owner when it introduces itself,
at every resume and when a window joins or leaves. It assigns nothing and refuses nothing.

<a id="triage-in-una-pagina"></a>
## Triage on one page

At task creation, on a finding and when the scope changes, the builder proposes;
the coordinator settles it and records the reason. The non-author reader checks
the choice in the assigned read; this does not add a per-lane gate before the
default final block. Keep the answers and choice in one line of the scope or report.
This is a judgment about effects: no regex, path pattern, score or parser
turns these answers into automatic permissions.

| Italiano | English |
|---|---|
| 1. Tocca sicurezza, dati dei clienti, chiavi, permessi, integrità delle prove o azioni difficili da annullare? | Does it affect security, customer data, keys, permissions, evidence integrity or actions hard to undo? |
| 2. Conosciamo tutto l'effetto e possiamo annullare la modifica senza danni? | Do we understand its full effect and can we undo it without harm? |
| 3. È una correzione circoscritta, senza scelte di progetto, dimostrabile con una prova mirata? | Is it a bounded fix, with no design choice, demonstrated by a focused check? |
| 4. Quale requisito richiesto o controllo obbligatorio rischia di non essere rispettato? | Which required behavior or mandatory check could be left unmet? |
| 5. Un errore potrebbe restare nascosto o far prendere una decisione sbagliata? | Could an error stay hidden or lead to the wrong decision? |

**At creation:**

- **Fix now with proof:** Q1 no, effect known and reversible, Q3 yes. The builder
  fixes within the confirmed scope; a non-author checks the bounded diff and proof
  in the final block, or before standalone closure. Tests alone are not enough;
  no separate plan ceremony unless required by a mandate.
- **One code read:** a bounded ordinary behavior or choice, Q1 no, effect known
  and reversible. Scope, bound proof and an independent read of the changed
  contracts and callers in the assigned block; relevant checks.
- **Full path:** Q1 yes **or unknown**, Q2 unresolved, material hidden harm under
  Q5, or an existing mandate. Use the required full proof, a non-author read and
  relevant probes; a full gate where required. A plan read before construction
  is an option on the owner's word or for a lane touching a trust boundary;
  an already required plan still needs acceptance. Q4 does not authorize omitting
  a requirement or check.

**On a finding:**

- **Block:** only a grave finding: demonstrated harm to security, permissions,
  data, evidence or essential safe operation. Missing or invalid mandatory
  security evidence also prevents acceptance. Preserve the counterexample;
  the consequence matters, not an old label.
- **Fix now, demonstrate it:** a bounded remedy with no protected impact and a
  precise expected result. The builder fixes it; the non-author checks the new
  diff and original case within the authorized budget.
- **Record for later:** in final-block mode, collect unresolved non-grave findings
  in the single carry-over decision described below and create their tasks at the
  next milestone's kickoff. For a standalone task, record the candidate, effect,
  owner, observable trigger or deadline and acceptance check in **its own task
  in the registry** (`task`, scope and proof), or in **a `decision --task ID`
  naming the task that owns it**. Link the entry from the report: the work stays
  visible and open until closed, not merely buried in prose. If deferring a
  binding requirement, the coordinator records the authorized scope disposition
  before the current closure; do not mark it satisfied or defer required safety.

`Choice: fix now with proof; Q1 no, Q2 yes, Q3 yes, Q4 none, Q5 no; reason: typo with no operational effect; builder proposes, reader confirms on the candidate.`

A new protected impact stops dependent actions; do not invent a prior plan after
the fact. A final report covers all retained requirements. In the structured
runner, `findings` contains blockers: an `accepted` response with nonempty
`findings` is refused. Authorized nonblocking follow-ups go in the report/
`limitations`, with links to the registered entries, without hiding them.

<a id="final-block"></a>
## Default: one final block per milestone

This is Rule 7's default for coordinated lanes. Standalone tasks and owner-selected
per-lane reads keep their own proof/read/close sequence; a plan read can precede
building on the owner's word or for a lane that touches a trust boundary.

1. **Set the seams before building.** The coordinator writes the shared formats,
   interfaces, fixed limits and owned files per lane. Relay a seam question to the
   affected lanes and record the answer as a decision linked to those tasks;
   private agreement between two builders is not the shared contract.
2. **Merge proven deliveries.** Each lane ACKs, builds on its own tree, proves its
   exact committed head and delivers. The coordinator checks the proof, merges the
   delivered bytes and resolves conflicts. There is no per-lane read. Keep a delta
   ledger: range/head, author, owned files, proof, coordinator/merge-resolution
   changes and planned Claude/Codex readers for every delta.
3. **Freeze and read together.** Tag the integration `<milestone>-rcN`. Register
   final-read tasks on that exact candidate, with scopes partitioned by author:
   every delta and seam gets both a Claude and a Codex non-author read. Include
   coordinator commits, merge resolutions, version, pins, mirrors and release docs.
   Each actual recipient ACKs its handoff before reading a frozen private copy.
   A coordinator may use a fresh-context subagent for a non-author read, supplied
   with the candidate, scope and raw evidence; it verifies the subagent's main
   claims before recording its own verdict. Keep the real identity: a fresh context
   or alias never makes the coordinator's own work independently reviewable.
4. **Use one fix round.** Collect all findings, then assign bounded fix tasks on
   branches from the frozen candidate. Deliver each with its normal green proof
   and red-to-green evidence in its task output: original base, exact remedy,
   frozen test/probe bytes, assertion outcomes and log paths. Distinguish setup
   errors, skips and timeouts from an assertion failure. Do not register a
   `challenge` agreement for a fix covered by the re-read block. That marks it as a
   defect fix that cannot be superseded by an ordinary read task and pins a host
   interpreter. Already declared defect fixes retain all their obligations; this
   workflow is not a waiver or a way to replace them with ordinary tasks.
5. **Re-read the fix deltas once.** Merge their proven deliveries, freeze the next
   candidate and open one non-author re-read block for the fix deltas and any new
   integration resolutions. Keep accepted reads on the unchanged work. Only grave
   findings block; an unresolved grave finding keeps the gate closed.
6. **Defer non-grave findings explicitly.** Record all unresolved non-grave findings
   in one ordinary deferral decision linked to the release read tasks, naming each
   source finding, intended owner, acceptance check and next-milestone kickoff.
   At that kickoff, create each carry-over task with those fields and link it back
   to the decision and source report. This does not mark a binding requirement met
   or silently defer required safety evidence. Do not use `--defer-read` for findings:
   that option names one unread light task and one release gate.
7. **Prove the release head.** Whole-suite proof uses `tools/parallel_tests.py` when
   present, with collected equal to executed. Required runtime/native proofs bind
   the exact release-head sources, rather than only lane heads. Check both readers'
   coverage against the ledger, record the accepted scope and close the read tasks.
   Completion grants no authority to publish.

**Represent the block with existing tasks.** Final-read tasks name the lanes/fixes
they cover and the paired reader's scope. Supersede each covered open lane/fix with
its responsible final-read task; the ledger retains the second reader's coverage.
Do not close an ordinary unread lane as though it had its own accepted read. The
replacement must be registered later and must not depend, even indirectly, on the
task it replaces. Reconcile runs, reservations and open questions first. Supersession
preserves history, failed proofs and spent budgets; it never resets a read budget.

```sh
# REL-CODEX already exists, covers LANE-A and does not depend on it.
squadra supersede --actor coord --task LANE-A --by REL-CODEX --reason "Covered in the final block; paired Claude scope is in the delta ledger"
```

`--read-at-gate` remains light-only. `--gate-covers` is for its outstanding tasks,
not a general coverage flag for full lanes; `--release-gate` does not enforce the
whole delta ledger or choose the two readers. The coordinator checks that coverage
and the exact proof bindings. This is an agent workflow, not a registry format or
CLI change. On the owner's word, record the former per-lane read plus final fix
block, or individual corrective re-reads, instead.

<a id="lettori-disponibili-lavoro-parallelo-e-capacità"></a>
## Available readers, parallel work and capability

When creating a read task, use `task --reviewer` to choose an available non-author reader by
real identity; an alias does not create independence. **Codex writes and Claude reads;
Claude writes and Codex reads** (owner, 2026-10-23): the read that counts comes from the
other model, and a same-client read only adds to it. For a Claude or Codex owner, `task`
refuses a reader of the owner's client and names both clients, unless
`--same-client-reason TEXT` records why, for example because no window of the other model is
available; `check` and `close` then need an accepted read of the current candidate from the
other client, or that recorded reason. The rule binds tasks recorded by the 3.10.4 `task`
command (they carry `cross_client`); tasks recorded earlier are never re-judged, and work by
a manual or Cursor seat, whose model is not known, is not judged by it. A task read at a release
gate is judged on the gate's reader ([Read at the gate](#read-at-gate)). In the
[final block](#final-block) every delta already gets a Claude and a Codex read, and the one
that counts is the read by the other model than the delta's author. A final-read task whose
reader shares its owner's model, such as the Claude read of a Claude coordinator's
integration, names its paired read of the other model in `--same-client-reason`. Readers
of different tasks work at the same time and may run alongside construction on other trees.
One builder per tree; reads on frozen copies, dependencies respected, shared
gates/ports/databases still reserved. Do not wait for a single reader when another
suitable identity is available; do not reassign by changing state or reset the
budget with an equivalent task.

The handoff generated by `deliver` starts the already assigned read: the reader
ACKs it and begins, without another "go" from the coordinator. Delivery neither
invokes the client nor proves it is awake. In final-block mode, gather corrections
into the single fix/re-read round above. For an individual corrective read, a change
suspends the read on the frozen copy: the builder fixes, a new proof/delivery,
actual ACK, then a reread of the delta and counterexample. **A narrow corrective
reread is still a read within the same budget**; spent rounds stay spent.

Before assigning browser or installation work, the window demonstrates actual
capability with a `probe` receipt in the relevant context and destination path.
The probe uses synthetic/private resources and already authorized actions: it
does not implicitly install or publish. For the browser, set timeouts for the
**DevTools connection**, each protocol call and the entire step; preserve exit
status, errors and cleanup of owned processes only. Isolated DOM checks are not
evidence of a real browser. Without a capable window, declare the step **not run**,
cite the last valid evidence and its limits (or state that none exists), and
refer the limit to the coordinator. **Do not give the owner commands to run.**
Delegation and capability do not override actual permissions; no sandbox bypass
or falsely claimed execution.

<a id="domande-al-proprietario"></a>
## Questions for the owner

Prepare `choices.json` as a list of at least two objects with distinct IDs:

```json
[
  {"id":"A","label":"Continue","say":"Continue within scope","buys":"Completion","costs":"One check"},
  {"id":"B","label":"Stop","say":"Stop and save"}
]
```

```sh
squadra ask --actor coord --id Q1 --question "How should we proceed?" --options-file /evidence/choices.json --recommended A --task T1 --waiting builder --evidence /evidence/report.md
# After the owner's actual response:
squadra decision --actor coord --reason "Owner decision" --answers Q1 --choice A --channel typed-here --owner-words "Continue within scope"
# If the question has become obsolete, instead of answering it:
squadra decision --actor coord --reason "Question superseded" --withdraws Q1
```

`--channel relayed` explicitly preserves an answer reported by another window.
Answering and withdrawing are mutually exclusive and cannot be repeated. Earlier
decisions without a linked question stay historical and do not close questions
through similar wording.

Link each decision to its source so the Decisions graph traces it to the result:

```sh
squadra decision --actor coord --id D-SCOPE --task T1 --follows ask:Q1 --follows decision:D-BEFORE --reason "Relayed instruction" --owner-words "Old keys remain readable for one month" --channel relayed
```

`--id` is optional: without it, the decision is named `dec-e<event number>` (a
reserved form). `--task` names existing tasks; `--follows` always requires the
`ask:` or `decision:` prefix and resolves once, when recorded: reusing a name
later does not move the link. An owner instruction without a registered question
may have `--channel typed-here|relayed` with nonempty `--owner-words`; without
`--channel`, it remains "channel not recorded". Do not use instructions, mentions
or `--follows` to close a question: use `--answers` or `--withdraws`.
No link is inferred from text, mail or proximity in time. The Who's waiting view
and next step per task depend on events, not on `output.next`, which remains a
window's statement.

Prepare `scope.md` outside the delivered code: expected behavior, owned paths,
dependencies, mandatory checks, resources and publication boundaries.
Prepare `proof.json` with exact argv, for example `["python3", "-m", "pytest", "tests/test_fix.py", "-q"]`.
Probe/log paths must stay outside the candidate: a test writing into sources
invalidates the proof. Git-ignored files are not part of the fingerprint: declare
relevant external configurations/dependency locks separately. Submodules require
explicit evidence.

```sh
squadra task --actor coord --id T1 --owner builder --reviewer reader --title "Fix the behavior" --repo /checkout/builder --scope-file /evidence/scope.md --proof-file /evidence/proof.json
squadra inbox --agent builder
squadra ack --actor builder --message assign-T1
squadra start --actor builder --task T1
```

Use `send --info` for progress and FYI notices that need no confirmation: they
remain visible as information without an ACK wait. Requests, assignments, review
handoffs, presence challenges and messages to unregistered recipients still need
confirmation and cannot use this flag. A voluntary ACK of information is a real
sign of life, never a substitute for a required assignment, review or ping ACK.
Existing messages keep their original receipt contract; old readers may still
show an unnecessary wait for new information messages until they are upgraded.

`inbox` prints a JSON list of the messages addressed to the named `--agent`
names, oldest first, each with its full body, sender, task, receipts and ACK. By
default it keeps it short: every message still waiting for an ACK (the same
waits that `status` and the follower count) plus the latest ten messages. When
older messages are left out, one line on standard error says how many and how
to see them. `--message ID` prints only that message, refused unless it is
addressed to one of the names; `--all` prints the whole history.

After assignment, explain the task in plain words: the first line becomes its
dashboard title, followed by why it exists, who it is for and when it will be
finished (UTF-8, at most 8192 bytes).

```sh
squadra brief --actor coord --task T1 --file /evidence/t1.md
```

If an open task becomes obsolete, register a new one with `task ... --supersedes T0`.
T0 leaves the next steps and its waits, keeps its history, and tasks depending on
T0 remain blocked: nothing is cancelled, released or reassigned. The command
refuses replacement while T0 has a run in progress or a stop awaiting verification,
while T0's owner holds a reservation, or while a question on T0 is open; a task
cannot depend on the task it replaces.

If the new task already exists because work moved there before the replacement
was recorded, the coordinator alone records it afterwards:

```sh
squadra supersede --actor coord --task T0 --by T1 --reason "Work moved to T1 by recorded decision"
```

Effects and refusals are the same as for `--supersedes`; additionally T1 must have
been registered after T0, must not depend on T0 (directly or through other tasks),
and the reason cannot be empty. T0's reads, failed proofs and spent budget stay
in its history; the Decisions graph draws the same replacement arrow from T0
to T1, taken only from the registered event.

For an optional or already required plan read before construction:

```sh
squadra plan --actor coord --task T1 --file /evidence/plan.md
squadra review --actor reader --task T1 --stage plan --verdict accepted --report /evidence/plan-read.md
```

The review must match the latest plan bytes. A later `changes-required` invalidates
a previous acceptance. Keep the original file; if it changes, register and reread
the new version. Budgets are set at init (`--plan-rounds`, `--review-rounds`);
automatic construction allows at most three attempts. Do not create an equivalent
new task to evade them. `decision` preserves a choice; it does not automatically
increase counters. An extension requires a newly, explicitly authorized scope;
all earlier findings are preserved.

For a standalone task or owner-selected per-lane read, after the change with frozen
sources (coordinated lanes use the [final block](#final-block)):

```sh
squadra prove --actor builder --task T1 --timeout 120 -- python3 -m pytest tests/test_fix.py -q
squadra deliver --actor builder --task T1
# The reader confirms the actual ID returned by deliver, then reads the frozen copy.
squadra ack --actor reader --message "review-ID-RETURNED-BY-DELIVER"
squadra review --actor reader --task T1 --verdict accepted --report /evidence/code-read.md
squadra check --actor coord --task T1
squadra close --actor coord --task T1
```

The argv for `prove` must match the agreed command. `--expected N` on the task
checks the test count in recognized pytest/unittest summaries; it refuses a
missing count. Other runners require their own explicit check. `--full-gate`
requires a gate-labelled proof: first `resource acquire --actor builder --name
gate --reason ...`, then `prove --gate`, finally `resource release` after the
terminal receipt. The label does not prove the command is the whole suite:
the coordinator must agree on the correct command and project reservations.

`prove`, a probe, the quick checks, `selftest` and an offline benchmark give their process
HOME and TMPDIR in a private folder made for that run (`sq-` and eight characters, mode
0700) in the system temporary folder, never under the registry: Unix socket paths stop near
104 bytes, and a registry deep under a shared folder such as MAMP's `htdocs` gave paths of
146 characters under folders others can write. `SQUADRA_TMP` chooses another parent folder.
Before anything starts and before any run is recorded, a folder path over 64 characters,
one under a folder others can write that is not a root-owned sticky folder like `/tmp`, or
one inside the registry, even through a link, is refused with the path and the fix
(`SQUADRA_TMP=<a short private folder>`). Cleanup first reopens to its owner any folder the
process left unreadable, and never follows a link. Logs, metadata and the lease stay in
`<registry>/runs/<id>/`; the run record names the private folder and whether it was removed
when the run ended, whatever its outcome. Claude and Codex turns keep the caller's HOME, where
the client keeps its sign-in.

### One execution for the two final reads

When final-read tasks bind the same repository, exact source snapshot, proof argv
and expected test count, one physical proof can serve them both. First verify
that **each reader's actual executable** lists `--also` in `prove --help`;
the version string alone does not distinguish refreshed builds. Otherwise prove
each task separately. Deliveries, handoff ACKs, independent reads, checks and
closes remain separate for each task.

```sh
squadra prove --help
squadra resource acquire --actor builder --name gate --reason "Both final reads"
squadra prove --actor builder --task REL-CODEX --also REL-CLAUDE --gate --timeout 2400 \
  --covers RF1 --covers RF2 -- python3 -B tools/parallel_tests.py
squadra resource release --actor builder --name gate --reason "Run ended"
squadra deliver --actor builder --task REL-CODEX
squadra deliver --actor builder --task REL-CLAUDE
```

Repeat `--also` for additional tasks. `--covers` must cover every requirement of
every served task (the example assumes RF1/RF2 are the complete union). Before
starting, every member's assignment, dependencies, plan, route and proof contract
must qualify. One run ID, process, native result and source binding are retained;
each task sees its own coverage. A failed, interrupted or changed-source run
cannot qualify any member. The secondary handoff names the physical run and its
primary task. Older builds still read and audit the registry, but see the extra
task as unproven and refuse its delivery, formal read, check or close.

### Parallel whole-suite proof

For this repository, the coordinator may agree on
`["python3", "-B", "tools/parallel_tests.py"]` as the whole-suite proof argv.
The serial `python3 -B -m unittest discover -s tests -v` remains available; a
task's registered argv must still match its proof exactly. The parallel runner
uses unittest discovery, including `load_tests`, and runs modules in fresh
processes with separate synthetic HOME, CODEX_HOME, CLAUDE_CONFIG_DIR and temporary
directories. It compares collected and executed IDs including multiplicity;
missing tests, worker crashes and skips cannot yield a passing complete proof.

The default is `min(cpu count, 8)` concurrent modules; `--jobs N` overrides it.
Each module and the discovery process has a **300-second timeout**, overridden
with `--timeout SECONDS`. A timed-out module is named with its elapsed time and
fails the run. Any modules that require serial execution are listed in the
runner's `SERIAL_MODULES` mapping with a reason and announced before execution.
There is no silent serial retry. Module fixtures stay together.

Logs, structured receipts and profiles are kept in a temporary directory printed
at startup, or a new `--output DIRECTORY` outside the source tree. Python runs
with bytecode writes disabled. Each module's output retains failing test names
and tracebacks; the final summary gives collected, executed, failed, errors,
skipped and wall seconds. A test is counted as executed when unittest starts it;
a crash after that point still fails through its native exit or missing receipt.
Keep the logs as evidence and inspect the summary, not only the exit code.

### Quick checks before proof and delivery

A repository can declare quick checks in one tracked file at its root,
`.squadra-precheck.json`. `prove` and `deliver` run every declared check on the
exact candidate before the proof process starts or the delivery and handoff are
written. Without the file nothing changes.

```json
{"schema": 1, "total_seconds": 30, "checks": [
  {"name": "pin-label", "argv": ["python3", "-B", "tests/precheck_checks.py", "pin-label"], "timeout_seconds": 10}
]}
```

- `argv` is a list run without a shell, from the repository root, with a private
  HOME and TMPDIR. A shell string is refused.
- Each check has its own `timeout_seconds` (1–60). `total_seconds` (1–120) is a
  deadline that starts before any precheck work: no check starts after it, and a
  running check is stopped at it. Cleanup, the last snapshot and the record finish
  afterwards; the reported `seconds` include them.
- Names are unique (`a-z`, `0-9`, `.`, `_`, `-`). There are at most 32 checks, the
  file is at most 16 KiB, and duplicate or unknown keys are refused.
- Each check is recorded like a probe, as a run of mode `precheck`: argv, exit
  code, logs, seconds, and the candidate fingerprint before and after. A precheck
  never counts as a proof.
- A check that exits nonzero, times out, cannot start, or changes the source
  refuses the command with `precheck_failed`, naming every failing check. An
  untracked, ignored, symlinked or malformed declaration refuses with
  `precheck_invalid` and is never skipped.
- After the checks pass, `prove` runs the proof only if the source is still the
  one that was checked. Otherwise it refuses with `precheck_failed: source changed
  after the checks`, before any run or process.

The optional `review --findings-file findings.json` records observations for the
**Reads** dashboard view. The file is a UTF-8 JSON array, at most 16 KiB and 32
items; each item has exactly `severity` (`grave`, `minor`, `info`) and `title`
(one nonblank line, 1–200 characters, no control characters). Duplicate JSON
keys and unknown fields are refused. Use safe summaries, never secrets or copied
private content. For example:

```json
[{"severity":"minor","title":"The example omits the retry limit"}]
```

Omitting the option leaves observations unrecorded; `[]` explicitly declares
none. It does not change the verdict, closure rules or runtime reply schema.
Old registries and frozen protocols need no migration or backfill. The dashboard
counts recorded observations, not unique or unresolved defects. A changed plan
or delivery after a bound changes-required read is a chronological revision,
not proof of causation or a successful fix. “Accepted with conditions” remains
unrecorded because the protocol has only three verdicts. Reports and task stories
retain the evidence behind each count.

The review records a non-author report bound to the current fingerprint; it does
not magically run tests. The reviewer may use `probe --actor reader --task T1
--expected-rc 0 -- argv...` for independent probes, or `--expected-rc 1` for a
negative control. Expected native 1 is called `matched`, not a passing product
proof. Do not put mutants in the frozen checkout: use isolated copies and preserve
the original probe before modifying it. Report limits, collection, assertions/
errors/skips, configuration and provenance. Reproduce the original issue on the
exact remedy.

One substantive output per completed step: a result, decision, blocker or handoff
that adds information. Routine progress comes from the registry. Do not duplicate
generated assignments or handoffs with manual "ready" messages; an existing proof
does not need a second narrative. ACKs, decisions, pauses and receipts stay explicit.

```sh
squadra output --actor builder --task T1 --file /evidence/update.md --next "Candidate reread" --artifact /evidence/report.md
```

The command saves the complete text, UTC, author, task, content-addressed artifacts,
next step and receipt. Show a compact summary of registered facts when useful,
without an output for every chat turn. The dashboard shows the details.
The reviewer also uses the ID of the task read for its output.

Mail: `send --actor coord --to builder --body-file /evidence/message.md --id M1`
saves a message. `ack --actor builder --message M1` is a separate recipient action.
`transport --via manual` provides the exact handoff without pretending delivery
or wakeup, and records `delivered: null`: unconfirmed, not failed. `--via codex-queue`
requires a known session ID and checks the local CLI; native 0 means queued, not read.
`send` and `transport` print short JSON receipts without the board; full transport
evidence stays in the journal. Read-only JSON commands, including `candidate status`
and `lesson --list`, print one JSON document on stdout; `status` keeps its human board.
**To forward.** A Codex window has no follower of its own, so mail to a Codex seat
waits on the coordinator until a transport reports `delivered: true` or the recipient
ACKs: with failed or unconfirmed attempts, or with none at all (a review handoff that
`deliver` records as sent by the coordinator, an assignment, a ping). After 120 seconds
from the first attempt, or from the message when there was none, it is listed first
as «To forward», naming the coordinators; the coordinator relays it with
`transport --via codex-queue`. Mail to Claude, Cursor or manual windows is never «to
forward»: they follow their own mail, so a transport could not end the wait, and it
stays an ordinary ACK wait. The dashboard's Communications tab shows the same
events in words (who wrote to whom, about which task, sent, queued, acknowledged
or late against the `ack` threshold); a message's text opens only on request,
and no read is inferred from text. Do not send email/Slack or use external channels
without the owner's authorization. Shared mail is enough across mixed clients.

## Following mail without a native queue

Every Claude window and every other window without a native queue, including the
coordinator, starts a follower when joining or resuming. Name all its roles with
repeated `--agent`; the coordinator includes `coord`. The follower leaves the journal
and its projections read-only and saves its cursor separately under
`$XDG_STATE_HOME/squadra/inbox` (default `~/.local/state/squadra/inbox`). Re-arm the
same command without manually copying an event number.

In Claude Code, arm **Monitor** with a timeout longer than the command's timeout:

```json
{"command":"squadra --root /shared/registry inbox --agent claude-build --agent claude-review --follow --timeout 1740","description":"mail for claude-build, claude-review","timeout_ms":1800000}
```

A background Bash command with `run_in_background` and `--until-first` has no
30-minute limit: it exits after the first batch that shows mail and wakes the window.
Read the full mail and acknowledge it, then re-arm the same command:

```sh
squadra --root /shared/registry inbox --agent claude-build --agent claude-review --follow --until-first
```

Use `--cursor-file PATH` to choose a private cursor file, `--since EVENT` to override
its starting point or `--no-cursor` to track the cursor manually. The saved cursor
binds the registry path, recipients and the event hash. A foreign cursor or mismatched recorded hash anchor
is refused. Each batch is saved only after its output was flushed; interruption
before that point may repeat a message. No cursor proves consumption or an ACK.

On the first run, pending mail is shown oldest first, at most ten in the first
batch with a `more` line for the remainder. The saved position never skips mail
that was not printed; subsequent polls or a restarted follower drain that remainder.
On resume it prints new matching mail, including messages sent between watches.
On restart a `pending` row lists the count and IDs still needing ACK, including previously printed mail. This row does not wake `--until-first`; only a new message does. Message lines carry `kind: "message"`, creation
`event`, sender, recipient, task, ID, `info`, `ack_needed`, a bounded preview and the
root-bound `ack_command`. Read full content with `inbox --agent NAME --message ID`,
then explicitly ACK when required.

The follower polls every two seconds, releases views before sleeping and verifies
new events from its last hash anchor. It has no periodic output ticks. SIGINT,
SIGTERM and a timeout exit 0; a registry/cursor error exits nonzero without a journal
diagnostic. `--since` and `--timeout` require `--follow`; `--all` and `--message`
do not apply to it. A stopped follower is not proof of an empty mailbox; status cannot tell whether it
is still running: Monitor or
an actively watched background process must wake the window, which re-arms after
restart or compaction. Redirection alone does not wake an agent. Stop only your own
processes. The follower saves no ACKs or transport receipts.

## Worth remembering at close

An optional declaration by the closing agent, without changing the `output`
protocol or starting a proposal. If a memory service is in use, declare at close
and do not propose the same item through another channel.

**Da ricordare.** Questo incarico ha prodotto qualcosa che vale oltre la sua chiusura: una decisione e chi l'ha presa, un vincolo, una trappola già pagata? Se sì, una frase per ciascuna, la prova (numero di evento o rapporto registrato) e quanto ne sei sicuro (sicuro / probabile). La frase contiene segreti, dati personali o percorsi privati? Se sì, riscrivila senza; se non puoi, non scrivere la frase: indica solo la prova e `private_content: yes`.

**Worth remembering.** Did this task produce something that matters beyond its close: a decision and who took it, a constraint, a trap already paid for? If yes, one sentence each, the proof (event number or registered report) and how sure you are (sure / likely). Does the sentence contain secrets, personal data or private paths? If so, rewrite it without them; if you cannot, do not write the sentence: give only the evidence and `private_content: yes`.

Name the file **exactly `da-ricordare.json`**, attached to the closing agent's
output with `--artifact /evidence/da-ricordare.json`. The command's path is local:
do not copy it into the content. The registry retains artifacts permanently;
secrets, personal data and private paths written by mistake cannot be removed.
Refer only to event numbers or hashes of already registered reports.

The public schema is `form: "da-ricordare/1"`, `task` (task ID), `answer` (`yes`
or `no`), `items` (list; empty with `no`). Each item contains:
`type` (`decision`, `constraint`, `trap`), `certainty` (`sure`, `likely`),
`private_content` (`no`, `yes`) and `evidence` (list of `{ "event": <positive number> }`
or `{ "artifact": <SHA-256 of the registered report> }`). With `private_content: no`,
add `sentence`, one sentence of at most 1500 characters; a decision also states
`who`. With `private_content: yes`, **omit `sentence`**: keep only type, certainty,
private flag and evidence. Do not put private content in other fields. These
examples are synthetic: use evidence that is actually registered.

```json
{
  "form": "da-ricordare/1",
  "task": "T1",
  "answer": "yes",
  "items": [
    {"type": "decision", "sentence": "Use a bounded queue.", "who": "owner", "certainty": "sure", "private_content": "no", "evidence": [{"event": 12}]},
    {"type": "trap", "certainty": "likely", "private_content": "yes", "evidence": [{"event": 15}]}
  ]
}
```

```json
{"form": "da-ricordare/1", "task": "T2", "answer": "no", "items": []}
```

This is a declaration, not evidence of sending or approval. A consumer may
produce drafts; the user's explicit selection is a separate request.

<a id="candidato-lettore-e-copertura-dei-requisiti"></a>
## Candidate, reader and requirement coverage

`task --reviewer reader` selects an already registered reader whose identity
differs from the authors' and, for a Claude or Codex author, whose client is the other
model's (see above). `deliver` saves the candidate, content-addressed packet
with scope/plan/proofs and `review-...` message in one transaction. The command
returns that ID. The recipient ACKs it before the code review or `read` turn.
The message names the commit only when the working tree equals it; otherwise it names
the working-tree snapshot and the uncommitted or untracked paths the commit lacks.
A new fingerprint requires a new handoff and ACK. **Reader evidence lives outside the
candidate tree**. New CLI tasks copy the exact proved source, including relevant
uncommitted and untracked files, into the registry at delivery. The builder may then
continue; `review`, `check` and `close` measure the frozen copy and refuse tampering.
Legacy tasks continue measuring their live checkout and refuse candidate drift.
Ignored dependencies still need their own evidence. Checkpoint and save include the
frozen candidates; restored readers resolve them relative to the restored registry. Repeating the same packet does
not duplicate events. New proofs change the packet and require a new message/ACK,
even when returning to an earlier candidate. Without `--reviewer`, the manual
protocol remains.

`advance --actor builder --task T1` reconciles an existing delivery: it can create
the missing handoff for the same proven candidate; the coordinator can inspect
an existing handoff. It does not build, approve, assign or invoke models.

For an explicit matrix, `requirements.json` is a JSON list:

```json
[{"id":"R1","text":"The valid request works"},
 {"id":"R2","text":"The invalid request is refused"}]
```

Pass it to `task --requirements-file /evidence/requirements.json`. During `prove`
and `review`, repeat `--covers R1 --covers R2`. A passing native proof covers only
the current fingerprint; the reviewer declares coverage separately. Delivery
and closure refuse missing requirements; unknown IDs are refused before the
proof starts. The dashboard shows the requirement, proof IDs/results and read.
Test sufficiency needs independent judgment: `--covers` alone does not establish it.

<a id="reviewer-pool"></a>
## Reviewer pool

Repeat `--reviewer` to name 2 to 4 eligible readers instead of one. Each member must
be a registered reviewer or coordinator, not in stand-by, and of an identity other
than the owner's; one invalid member refuses the whole registration
(`review_pool_member`), and five or more refuse it as `review_pool_size`. One
`--reviewer` is the single-reader task exactly as before.

`deliver` saves one immutable packet and one handoff message per member, all named
after that packet. The first valid ACK claims the read: another member's ACK, code
review or read run on the same packet is refused with `review_claimed` and changes
nothing; only the claimant's verdict counts toward check and close. Repeating the same
packet (`deliver` or `advance`) keeps its ACKs and claim; a new packet (new bytes or
new proofs) starts unclaimed, and an ACK on an earlier packet claims nothing. Budgets
and history are kept. Delivery skips a member in stand-by: it gets no handoff of that
packet, the result names it in `skipped_standby`, and the others can claim the read.
Only a pool whose members are all in stand-by is refused. Changing availability stays a
coordinator action, and a member back from stand-by reads from the next packet. Until
the claim, waits show one entry listing the members holding a handoff; nothing is
reassigned, retried or released by the wait.
Plan reads are not tied to a handoff and work as before.

```sh
squadra task --actor coord --id T2 --title "Retry limit" --owner builder --repo /work/repo \
  --scope-file /evidence/t2.md --reviewer reader-a --reviewer reader-b --reviewer reader-c
squadra deliver --actor builder --task T2          # returns three handoffs, one per member
squadra ack --actor reader-b --message "review-<packet>-reader-b"   # reader-b claims the read
squadra ack --actor reader-a --message "review-<packet>-reader-a"   # refused: review_claimed
squadra review --actor reader-b --task T2 --verdict accepted --report /evidence/t2-read.md
squadra check --actor coord --task T2
squadra close --actor coord --task T2
```

<a id="attese-e-finestre-ancora-da-registrare"></a>
## Waits and windows not yet registered

```sh
squadra send --actor coord --to-new expected-reader --body-file /evidence/handoff.md --id M-EXPECTED
# The actual window registers with that ID, then ACKs M-EXPECTED.
squadra settings --actor coord --file /evidence/thresholds.json
```

`--to-new` creates an expected recipient without assigning it an identity. It
refuses an already registered name; after registration, only the recipient may
ACK. `thresholds.json` is an object containing only the selected keys from `ack`,
`delivered`, `reviewed`, `heartbeat`, `alive`, `to-forward`, `plan`; values are
nonnegative integers in **seconds**, 0 for an immediate warning. The first five
defaults are 900, 3600, 1800, 1800, 300; `to-forward` defaults to 120 and `plan`
to 3600. The latest settings event replaces custom values. A settings write by
a window on the published 3.10.1 restores both additional thresholds to their
defaults, because that writer records neither optional value. This is intentional
compatibility behavior.
`cleanup-unknown` is always flagged. CLI, doctor and API calculate age at read
time; projections do not retain an age that appears current. A warning is not
proof of liveness and performs no actions.

<a id="output-della-squadra"></a>
## Squad outputs

Omit `--task` when the text concerns coordination of the area, for example the
arrival of an outside reviewer. Previous outputs do not change their association.
Each write appends new event records and updates affected UTC days in
`posta/exchange-YYYY-MM-DD.md` and bounded summaries. Complete `state.json` and
mail/history exports are refreshed by `export`, `checkpoint` or `save`, not by
every command. Do not create authoritative copies per clone or edit projections.
A commit can precede its projection update: compare the exported sequence/head
with the current Store view before treating a file as complete. Use the current
`inbox` for reception and transport; an old complete mailbox is not a live queue.
`squadra export` rebuilds the complete projections from verified authority.

### Registry read boundaries

Ordinary commands verify the anchor event and its hash, the newer event hashes and
links, and authenticated proofs for the cache values/indexes they read. They do not
reverify pre-anchor events, untouched leaves, or the cached root against complete
reducer replay. At startup/resume use read-only `doctor --quick` for explicitly
limited anchor, suffix and demanded-cache checks. Full verification runs in
`doctor` without `--quick`, `export`, `checkpoint`, `save`,
`restore-check` and release-gate `close`. Dashboard startup performs the same full
audit in the background, on a private copy of the registry: ordinary verified data is marked pending until completion,
and an audit failure stops data responses and removes the displayed view. A missing, foreign or
invalid cache also falls back to verified reconstruction. See the exact
[verification table](recovery.md#registry-verification). Read-only commands do not
write the registry or projections. A full audit reports cache/journal disagreement
as a mismatch, not a successful fallback. Default doctor refuses it; explicit
`doctor --repair` rebuilds derived tables and re-audits, but refuses saved packages
and checkpoint folders; it also copies the [write-ahead log](recovery.md#registry-wal)
back into the database. The dashboard never repairs: its audit fails closed.
Cold-cache computation and release-gate auditing happen before the short write
transaction; concurrent changes may produce a plain retryable refusal.
Coordinate every writer's upgrade before new
source-manifest references are used; unsupported older tools must refuse them.

<a id="keep-alive-e-stand-by"></a>
## Keep-alive and stand-by

```sh
squadra census
squadra ping --actor coord --all
squadra ping --actor coord --to builder
# Replace PING-ID with the returned ID; forward every message to its recipients.
squadra transport --actor coord --message PING-ID --via codex-queue
squadra ack --actor builder --message PING-ID
squadra ping --actor coord --sweep
# Explicit stand-by and return, with a recorded reason:
squadra standby --actor coord --agent builder --reason "Window unavailable"
squadra standby --actor coord --agent builder --resume --reason "Availability confirmed"
```

`ping --all`, coordinator only, calls each other active identity once; it excludes those in stand-by.
A pending ping is reused without moving its deadline. **The sweep acts only on a
ping with a recorded delivery** (a transport that reported `delivered: true`, which
today only the Codex queue gives): a window cannot answer a call that never reached
it. A ping to a Codex window that is not delivered is listed «to forward» instead; a
window that receives mail only through its follower or by hand is never put in
stand-by by the sweep, so record that with `standby --reason` when you know it. To recall a window in
stand-by, use `--to`: it creates a fresh call linked to that stand-by. The ACK of
that call allows a return at the coordinator's next `ping --sweep`. An old ACK,
an alias or a repeated `agent` command does not reactivate the window. The CLI
performs no implicit transport: record and forward the returned messages.

`alive` is the ping response threshold in seconds, default 300; `ack` continues
to measure mail generally. The `settings` file replaces previous custom
thresholds: include those you want to retain, for example
`{"ack": 900, "alive": 300}`. Omitting a key restores its default. Age and deadline
are calculated at read time; changing `alive` also applies to pending calls.
Persistent projections retain events, not ages.

Only the coordinator changes availability with `standby` or `ping --sweep`,
recording an event. The sweep rechecks the response, call and threshold inside
the transaction: an earlier read does not decide the current state. All roles
of the same identity share stand-by. New executor or reader assignments and
new review handoffs are refused until the return is recorded. Status and the
dashboard distinguish windows in stand-by.

Stand-by **does not** cancel processes, release resources, transfer roles or
reassign work. Return **does not** restart anything. No response is not proof
that a process ended. No timer or dashboard read changes state. The census
remains a partial snapshot, to be supplemented with the clients.

**Waits on a window in stand-by are counted, not listed.** Mail to a window in
stand-by leaves «Who is waiting» in `status`, `stato.md` and the dashboard: its
ACK and to-forward waits become one line per window, for example «3 messages to
core-A on stand-by (Window closed)», in Italian «3 messaggi a core-A in stand-by
(Window closed)». The old heartbeats of that window's runs fold into the same line
(«1 run of core-A, state uncertain»), and the runs themselves stay listed with
their uncertain result: a run without a receipt is never declared stopped. This
is computed at read time and records nothing. The messages stay without ACK:
an ACK from the window still closes one, the pending-ACK count and the mail
filter still include them, and `standby --resume` (or a return through the
sweep) brings their waits back. Pool handoffs and task steps are not folded.
To silence a window that is gone for good, put it in stand-by with a reason, even
when it has not answered for days: no ping is needed for an explicit `standby`.

Earlier registries can be read without migration. After new presence events,
all writing windows must use the same updated version: an earlier CLI does not
enforce the new constraints. This does not authorize installation or migration
of an already active squad.

<a id="light-route"></a>
## Light route for a small change

After the same question-based triage, the coordinator may use `task --route light`.
The task must start at a clean Git base, with a nonempty scope, requirements,
configured independent reader and exact proof argv. These form an immutable
contract under **light-v1: 80 added plus deleted lines, 4 files, profile P2**.
Equality is allowed; 81 lines or 5 files refuse. Tests, documentation, comments and
whitespace count. Git raw Myers diff counts renames as deletion plus addition,
without textconv, external diff or whitespace exemptions. Intermediate commits do
not change the original base. There is no flag to enlarge these limits.

P2 permits README.md/README.it.md, Markdown under docs/ or references/,
tests/** and assets/dashboard.html, except the following protected names:
all scripts/**, plugins/**, assets/vendor/**, client/CI directories
(.github, .agents, .codex, .claude, .cursor, .claude-plugin, .codex-plugin),
.gitignore/.gitattributes/.gitmodules, VERSION, pyproject.toml, requirements*.txt,
*.lock, package-lock.json, pnpm-lock.yaml, yarn.lock, Makefile, package*.json,
AGENTS.md/CLAUDE.md at any depth, SKILL.md,
references/{work,start,runtime,recovery,plugins,plugin-authoring}.md and
docs/plugin-authoring.it.md. Patterns match whole relative paths (`*` includes
slashes), with NFC/case-folded comparison. Aliases/collisions, unknown paths,
symlinks/submodules (including in the base), type/mode changes, non-UTF-8/binary
changes, unavailable bases, unstable snapshots and no-op changes require full.

No plan or plan read on light; use the agreed targeted proof and one code-read
round. ACKs, non-author identity, native proof on unchanged exact bytes and the
same contract, requirement covers, dependencies, pauses, resources and budgets
remain. Required browser or other domain evidence remains too. The refreshed
status example changed 23 added/deleted lines in two files including its test:
`["python3","-B","-m","unittest","discover","-s","tests","-p","test_dashboard_loading.py","-v"]`
would run its six targeted tests, but would not replace the required browser
measurement. Release final reads and the owner's explicit typed publication
permission never relax. No minification to qualify, and no path/size threshold
replaces judgment about security or irreversible impact.

The central validator remeasures before a proof process, at deliver, check and
close, also for direct Store events; packets retain the verified measurement.
Refusals use `light_lines_exceeded`, `light_files_exceeded`,
`light_protected_path`, `light_unmeasurable`, `light_empty_change`,
`light_contract_required`, `light_policy_unsupported`,
`light_plan_requires_full` or `light_read_round_exhausted`, with a native nonzero
exit and “Full route required”. No proof process or handoff follows a refusal.
Both automated read starts and recorded code verdicts allow at most one round.
A changes-required verdict stops light work; it is not another light fix cycle.

Only the coordinator may promote an open light task:

```sh
squadra route --actor coord --task T1 --full --scope-file /evidence/full-scope.md --proof-file /evidence/full-proof.json --reason "Broader impact"
```

Use `--full-gate` if that scope requires a reserved gate; `--expected` updates an
agreed collection count (otherwise the count is retained). Promotion keeps base,
authors, all old proofs/reads and spent counters. It changes the contract, clears
current delivery/plan, and requires fresh proof and independent read; the normal
triage determines whether a new plan is needed. There is no demotion. Exhausted
budgets still stop work; neither promotion nor a fresh equivalent task can reset
them. Exact same-base/scope/proof/requirements aliases with spent budgets refuse;
semantic disguises are still the coordinator's responsibility, not a classifier.

No-route tasks and historical projections keep their existing behavior. Unknown
future light policies are readable but cannot qualify work. A frozen squad must
coordinate every writer's explicit upgrade before opting in: older CLI versions
ignore new route metadata, so mixed-version enforcement is not supported, and a
3.6.x window still shows a task promoted to the full route as light. No
launcher, installation or workspace version is changed automatically. The change
that implements this protocol itself takes the full route and full suite.

<a id="read-at-gate"></a>
### Read at the gate

A light task may be registered with `--read-at-gate` and no `--reviewer`. It keeps
every light-v1 rule above; it simply closes without its own code read, once its
assignment is ACKed, its native proof on the exact bytes covers every requirement and,
for a defect fix, its challenge has matched. A defect fix closed by a recorded exception
needs a reader for that exception: promote it to the full route for its own read
(`read_at_gate_fix_exception_needs_read`). It cannot have its own read
(`read_at_gate_no_own_read`); a full-route task, or a light task after promotion,
always has its own read (`read_at_gate_requires_light`). Until a gate reads it, status,
board, the dashboard and the complete `stato.md` written by `squadra export` show it as
**closed — read at the gate**, never as read, with its own count.

A release gate is an ordinary task the coordinator registers with `--release-gate`,
usually the integration of the batch. Its `check` and `close` refuse, naming each task,
while any outstanding read-at-gate task lacks the gate's current accepted code read
covering it from a reader who is not its author (`gate_read_missing`), or while a path
that task changed does not have its exact delivered bytes in the gate's candidate
(`gate_bytes_missing`). The gate reader lists what the read covers with
`--gate-covers`; a task that is not outstanding, whose author is the reader or, under the
cross-model rule, whose author's model is the reader's, is refused (`gate_covers_invalid`).
Such a same-model gate read recorded anyway, for example by an older window, makes the gate's
`check` and `close` refuse, naming the task (`cross_client_read_missing`): have the gate read
by the other model, or defer the task. Equality is per whole file, including mode and
deletion: a later change to the same file can keep the earlier task outstanding, and
only a deferral then lets the gate close.

Only the coordinator defers, with a reason, one outstanding task from one named open
gate (`decision --defer-read ... --gate ...`; otherwise `gate_defer_invalid`). The
deferral shows in status; the task stays outstanding and the next gate must cover or
defer it again. Nothing is ever deferred implicitly.

```sh
squadra task --actor coord --id D1 --title "Typo" --owner builder --repo /work/repo --route light \
  --read-at-gate --scope-file /evidence/d1.md --proof-file /evidence/d1-proof.json --requirements-file /evidence/d1-req.json
squadra prove --actor builder --task D1 --covers R1 -- python3 -B -m unittest tests.test_docs -v
squadra deliver --actor builder --task D1
squadra close --actor coord --task D1              # status: closed — read at the gate
squadra task --actor coord --id REL --title "Release batch" --owner integrator --repo /work/repo \
  --scope-file /evidence/rel.md --reviewer reader --release-gate
# ... prove and deliver REL; the reader ACKs its handoff ...
squadra review --actor reader --task REL --verdict accepted --report /evidence/rel-read.md --gate-covers D1
squadra check --actor coord --task REL
squadra close --actor coord --task REL             # D1 is now read
# Instead of covering D1, the coordinator may defer it from REL to the next gate:
squadra decision --actor coord --defer-read D1 --gate REL --reason "Docs only; next release"
```

Older windows ignore pool and read-at-gate metadata: every writer of a squad upgrades
together before using them.

## Defect fixes: show that the regression detects the original

Fixes covered by the final re-read block use normal proof and red-to-green evidence
in their task output, as described [above](#final-block), without registering an
agreement. This section applies to standalone declared defect fixes. Existing
declarations cannot be undone or superseded by ordinary final-read tasks.

The coordinator marks a task with `task --defect-fix`. It can register the oracle
at creation with `--challenge-file`, or later on an open task as below. Marking is
one-way and coordinator-only; a title containing “fix” is not a declaration.
Both forms of task replacement refuse to replace a defect fix unless the replacement
is itself marked as a defect fix, with its own base and oracle agreement.

For a correction of `VALUE = 1` to `VALUE = 2`, the regression's own method reads
the actual value and asserts 2. After writing the fix and its test, register:

```json
{"ids":["test_value.ValueTest.test_value"],"files":["tests/test_value.py"]}
```

```sh
squadra challenge --actor coord --task T1 --register \
  --contract-file /evidence/regression.json --reason "Value regression"
# Run the task's agreed prove command on the frozen candidate, then:
squadra challenge --actor builder --task T1 --output /evidence/challenge-T1 --timeout 30
squadra deliver --actor builder --task T1
```

The default runner is `python-unittest-v1`. `ids` contains complete unittest
method IDs, not discovery patterns or classes. `files` explicitly lists the test
and Python test helpers (`tests/**/*.py` or a root `test_*.py`); these exact bytes
are frozen as artifacts and placed on both private inputs. Include needed
`__init__.py` files. Each case class and underlying test method must be defined
in the listed files; an unregistered module with the same name cannot substitute
for them. Imports use the input root and its `tests` directory. Only
test files are overlaid: original product bytes come from the registered base,
never current HEAD. The remedy includes the exact tracked and non-ignored
untracked candidate, including uncommitted changes. Version 1 admits regular
files, not symlinks/submodules; unavailable bases and unsupported inputs refuse.

Every selected method must execute and fail by AssertionError in its test body
on the original and pass on the remedy. A passing original, import/setup/teardown
error, skipped/expected-failure test, unknown ID, timeout or uncertain cleanup
does not match. Results come from unittest objects, not a search for “FAIL” in
stdout. Keep ordinary positive tests in the delivery proof; there is no separate
control input for task fixes. Native results and both logs are retained on failure.
Write the oracle against behaviour the base already exposes: importing or calling
code that the fix adds errors on the base and cannot qualify as an assertion failure.

Custom oracles use `{"runner":"python-check-v1","oracle":"/evidence/oracle.py"}`,
the existing `check(repo)` interface. An assertion during module loading is an
instrument error. The agreement pins oracle/runner bytes, interpreter and the
resolved argv; arbitrary exit codes are not assertion evidence. Oracles are
trusted code, not sandboxed: the reader must check that they test the defect,
do not disguise setup failure as an assertion, and use declared dependencies.

The normal proof remains separate. `check` and Store close require a matching
task/base/contract/candidate receipt and its current independent read. A direct
Store close cannot waive it. Delivery includes the challenge; recording a new
receipt or exception after delivery needs redelivery and a fresh read within the
existing budget. Replacing the agreement needs a coordinator reason and new proof;
it preserves the base, prior attempts and spent rounds. Source drift invalidates
the old proof/read/challenge. Light fixes retain this obligation and their 80/4/P2
limits; promotion to full retains the fix but requires new bindings.

If an automated regression cannot reach the defect, the coordinator records:

```sh
squadra challenge --actor coord --task T1 --exception \
  --reason "Hardware-only failure; manual evidence and limits in the report"
```

The exception is candidate-bound, visible with its reason, and must be checked
by the independent reader. It waives only the automated red/green demonstration;
proof, review and publication authority are unchanged. It never says `matched`.
The dashboard places the result or exception next to the proof, with original
commit, remedy fingerprint and logs. A fingerprint is not an uncommitted commit.

Tasks without fix metadata and unbound historical challenges keep their behavior;
old challenge records do not qualify new fix tasks. Existing frozen squads need
an explicit coordinated upgrade of every writer. Old CLIs ignore these fields;
mixed-version enforcement is not promised. Unsupported future contracts can be
read but cannot qualify a close. Nothing installs or migrates a live squad.

<a id="errata-and-rasoio"></a>

## Lessons (errata) and the rasoio lens

The two built-in skills record their activations as registry events. The guides are
[errata](skills/errata.md) and [rasoio](skills/rasoio.md).

| Kind | Actor | Fields |
|---|---|---|
| `lesson` | the author | `id` (`L` + at least 4 digits, unique), `text` (1–1000 characters), `trigger` `{"paths": [glob…], "words": [word…]}` (each list at most 10, at least one entry overall), `source` `{"task", "event", "eligible"}` |
| `lesson.retire` | the coordinator or the lesson's author | `lesson`, `reason` (1–500 characters) |
| `lesson.recall` | the actor of `brief --task` or `start` | `task`, `at` (`brief` or `start`), `lessons` (1–5 distinct ids, none retired) |
| `lesson.ineffective` | any seat | `lesson`, `recall` (the seq of a `lesson.recall` that showed it), `reason` |
| `review` | unchanged | optional `lens` (`rasoio`) and `net_lines` (integer 0–1,000,000, only with `lens`) |

`squadra lesson --task T --source-event N --text … --path GLOB --word WORD` records a
lesson. The source must be one of the task's reds: a code review with `changes-required`, the
`run.end` of a failed proof, or the `close` of a task with `fix`. `--any-source` accepts
another event of the task and records `"eligible": false`. `--retire ID --reason …`,
`--ineffective ID --recall SEQ --reason …` and the read-only `--list` complete it.
`review --lens rasoio --net-lines N` declares the lens.

`brief --task` and `start` show at most five matching lessons: 3 points per trigger glob
matching a path named in the task's title, scope, requirements or brief, 1 per trigger word
found there, and 1 more if the lesson is already ineffective. Retired lessons are excluded;
ties go to the newest. When at least one matches, the `brief` or `start` event and a
`lesson.recall` are appended in one transaction; otherwise output and events are those of
3.8.0. A code read with `changes-required`, a failed `prove` and the `close` of a defect fix
add an `errata` object to their output: `record_lesson`, `task`, `source_event`,
`responsible` (the actor who ran the command), `reason` and a `command` template whose text
the agent writes. The registry never writes a lesson by itself.

Derived state, created on the first lesson event only: `state["lessons"][id]` with `retired`,
`recalls` (recall seqs), `recalled_tasks` (distinct tasks) and `ineffective`;
`state["lesson_recalls"]`. A lesson's status is `retired`, `ineffective`, `active` (shown) or
`new`. `lesson --list` returns the lessons with their status and the counts
`lesson_events`, `recall_events`, `recalled_pairs` (distinct task and lesson pairs),
`ineffective_marks`, `rasoio_reads` and `net_lines_declared`. Older Squadra windows ignore
these kinds, so mixed-version enforcement is not promised.

| Refusal | When |
|---|---|
| `lesson_text_required` | empty, blank, too long or control-character text |
| `lesson_trigger_required` | no glob and no word |
| `lesson_trigger_invalid` | a malformed glob or word, or more than 10 of either |
| `lesson_source_required` | missing or unknown task, an event outside the registry, no `eligible` |
| `lesson_source_not_eligible` | the cited event is not a red of the task (CLI; `--any-source` allows it) |
| `lesson_source_unrelated` | the cited event concerns another task (CLI) |
| `lesson_id_invalid`, `lesson_id_taken` | a malformed or reused id |
| `lesson_fields_invalid` | unknown fields |
| `lesson_unknown` | retire or ineffective on an unknown lesson |
| `lesson_already_retired`, `lesson_retire_not_allowed` | a second retirement, or by a seat that is neither the coordinator nor the author |
| `lesson_reason_required` | an empty reason |
| `lesson_recall_invalid` | a recall with an unknown stage, no lessons, more than five, repeats, retired or unknown lessons |
| `lesson_recall_unknown` | an ineffective mark citing a recall that did not show the lesson |
| `lesson_actor_required` | `lesson` without `--actor`, except `--list` (CLI) |
| `lens_unknown` | a lens other than `rasoio` |
| `net_lines_requires_lens`, `net_lines_invalid` | `net_lines` without the lens, or not an integer from 0 to 1,000,000 |

<a id="readme-protocol-details"></a>

## Protocol details from the README

*Moved from the README, word for word; the README keeps a summary of each part and links here.*

Readers may supply `review --findings-file findings.json` with bounded observations
(`severity`: `grave`, `minor` or `info`; a safe one-line `title`). See
[the review command](work.md). Missing observations say **Not recorded**;
`[]` explicitly means none. The view shows how many reads supplied this data.
**Accepted with conditions** is not recorded by the three-verdict protocol.
The optional metadata does not change approval, closing rules or frozen protocols.

A project brief starts with its goal; `## Steps` and `## Glossary` (or `## Passi`, `## Glossario`) are optional, and their steps stay the brief's own claim, shown apart from the closures the registry records. A task brief's first line becomes the task's title everywhere on the page, and the rest says why it exists, for whom and when it is finished; without one, the task keeps its recorded title and reads «descrizione non ancora scritta» (description not written yet). Only the coordinator records briefs: UTF-8, at most 8192 bytes, kept as artifacts. A new brief replaces the old one on the page, every version stays in the history, and a brief never changes a task's state.

`--id` names the decision (otherwise `dec-e<event number>`, a reserved form). `--task` links existing tasks; `--follows` names an earlier `ask:<id>` or `decision:<id>`, never a bare name, and is fixed when written. An owner instruction without a question may carry `--channel typed-here` or `relayed` with non-empty `--owner-words`; without one the page says «canale non registrato» (channel not recorded). `--choice` requires `--answers`, and an instruction, a mention or a `--follows` never closes a question.

**Replacing a task.** `task ... --supersedes T0` records that a new task replaces the open task T0; `supersede --actor coord --task T0 --by T1 --reason "..."` records it afterwards, when the work already moved to an existing, later T1 that does not wait for T0. T0 leaves the next steps and waits and keeps its whole history; tasks that depend on it stay blocked, and nothing is cancelled, released, completed or reassigned. Both are refused while T0 has a run still running or with an unverified stop, an owner holding a reservation, or an open question, and a task cannot depend on the task it replaces; `supersede` also needs a non-empty reason.

**Reading the graph.** Each box is a recorded fact, earlier on the left. **Solid lines** are recorded links; **dashed lines** only mean "later, in the same task", never a reason. Owner boxes always show the channel: «scritto qui» (typed here), «riferito da un'altra finestra» (relayed by another window) or «canale non registrato»; verdicts are written in words as well as colours. Select a box (click, Enter or Space) for who, when, the full words and the links both ways; filter by task and zoom. Refreshes keep selection, filter, zoom and focus, and say «elemento non più presente» when an item is gone. On a phone the graph becomes a list in time order, with reduced motion respected; circular links, possible only in an altered registry, are listed under «Collegamenti non ordinabili». The full rules are in [work, proof and review](work.md).

**The rules of the read.**
- **Block only on a grave finding**, including missing or invalid mandatory safety evidence; fix the rest now with a non-author check of the delta and the original case, or record medium and low findings as follow-ups. Each follow-up has its own task, or a decision naming the responsible one, with an observable trigger or deadline and an acceptance check, linked from the report until closed. A deferred requirement is not satisfied, and mandatory safety requirements are kept.
- **Readers** are available non-authors chosen with `task --reviewer`, working in parallel on frozen copies, with shared gates, ports and databases reserved. The handoff that `deliver` generates is the go for the assigned read once its recipient ACKs it; the CLI never invokes a client by itself. A corrective read uses the same budget, and no alias or new task resets spent rounds.
- **Browser and installation steps** need the capability proven first by a probe receipt in the right context (for a browser: a named DevTools connection, timeouts, private resources, owned cleanup). Without it, declare the step **not run**, cite the last valid run and inform the coordinator. **Never hand commands to the owner**, and no capability bypasses execution permissions.

- **Options and budgets:** `--depends T0` (prerequisites closed independently first), `--expected N` (recognized test count), `--full-gate` (a gate-labelled proof); none of them proves a suite complete, so agree the real command with the project. By default three plan reads, two code reads and three build attempts in the optional runner (`init --plan-rounds/--review-rounds`); acceptance binds to the latest plan bytes; exhausted budgets need an explicit disposition, never a reset. Small changes get proportionate checks.
- **`prove`** keeps the native status, duration, output and before/after fingerprints of tracked and non-ignored files; ignored dependencies need their own evidence. **`deliver`** needs a passing proof on the exact current source, freezes an immutable packet, returns a `review-…` ID and never invokes a model.
- **`review`** records `accepted`, `changes-required` or `blocked`. `probe` records independent tests and negative controls (`--expected-rc 1`; a matched negative is not a product pass); mutants go in isolated copies. `--covers` records coverage, not test adequacy, which the reader judges.
- **`check`** binds candidate, plan, proof, artifacts and coverage again before `close`. For new CLI tasks the delivered frozen source binds the proof and review; changing that copy invalidates both. Legacy tasks retain live-source binding. `advance` reconciles a delivered handoff without building, approving or waking clients; without `--reviewer` the manual review protocol applies.

`--reply-to` answers; `--to-new waiting-reader` creates an expected recipient, not an identity: register the real seat before its ACK. `transport --via manual` returns a pending handoff instruction and records `delivered: null` (unconfirmed), and success with `--via codex-queue` means queued, not read or awake. Every codex-queue attempt is recorded: one that did not deliver keeps `delivered: false` (or `null` after a timeout) with its `stage`, and the command exits 1. The coordinator relays any message a sandboxed window could not deliver (`transport --actor <coordinator> --message <ID> --via codex-queue`, or `--via manual`), recorded with `relayed_by`; only the recipient's ACK ends it. A Codex window keeps the registry folder among its writable roots (`codex --add-dir <registry>`). The protocol authorizes no external messaging. Save **one substantive output per completed step**: a result, decision, blocker or handoff that adds information (omit `--task` for area-wide coordination). Its declared next step is not evidence that anything happened. Routine progress comes from the registry, so no manual "ready" messages and no retelling of an existing proof; real ACKs, decisions, pauses and receipts stay required.

Attach an artifact named exactly `da-ricordare.json` (`form: "da-ricordare/1"`, task ID, `answer: "yes"` or `"no"`, `items`) to the closing agent's usual `squadra output --artifact ...`; see [the schema and examples](work.md#da-ricordare-at-close). Nothing is proposed or approved by itself. **If a memory service is in use, declare at close and do not propose the same item through another channel.** **Never** put secrets, personal data or private paths in it: registered artifacts are immutable, so an accidental secret cannot be removed. Plugin consumers may turn declarations into drafts; the action API only saves the user's selection and never sends it or runs a plugin ([action contract](plugins.md#action-requests)).

`--channel relayed` keeps words reported by another window. `decision --withdraws Q1` closes an obsolete question without inventing an answer; a `decision --reason` without links keeps a reason without closing a question. Answers and withdrawals are exclusive and never repeated. Chat text, mail prose and `output.next` are never silently read as decisions or authority.

Wait ages are computed when a view is read. Defaults, in seconds: **ACK 900**, **delivered 3600**, **reviewed 1800**, **heartbeat 1800**, **alive 300**; `cleanup-unknown` is always shown. `settings --actor coord --file /evidence/thresholds.json` sets chosen keys, such as `{"ack": 600, "alive": 300}` (zero warns at once); it replaces earlier custom values, and omitted keys use the defaults. A warning never retries, reassigns or releases anything.

```sh
squadra resource acquire --actor builder --name gate --reason "Frozen candidate"
# For a full-gate task, run its agreed proof with prove --gate.
# Release only after the terminal result and resource reconciliation.
squadra resource release --actor builder --name gate --reason "Run ended"
squadra ping --actor coord --to builder
# Forward each returned ID (codex-queue needs a Codex seat registered with --session); ping invokes no client.
squadra transport --actor coord --message PING-ID --via codex-queue
squadra standby --actor coord --agent builder --reason "Window unavailable"
squadra standby --actor coord --agent builder --resume --reason "Availability confirmed"
```

- **Resources:** reserve gates, ports and databases before use, and respect other windows' reservations. A stale heartbeat or a timeout does not prove a process stopped, nor does it free its resources.
- **Presence:** `ping --all` calls each other active identity once, skipping stand-by; a pending ping is reused and never extended. The sweep counts only a ping a transport reported `delivered: true`. `--to` can call a window in stand-by, whose real ACK lets it return at the next sweep; old ACKs, aliases and new registrations never do.
- **Only the coordinator changes availability**, with `ping --sweep` (re-checked inside the transaction) or `standby`. All aliases of a window share its stand-by, during which new assignments and review handoffs to it are refused.
- **Stand-by stops nothing:** it **does not cancel processes, release resources, transfer roles or reassign work**; return restarts nothing; no timer or page view changes the state; an unanswered ping proves no absence.

### Waiting for a resource and declaring proof coverage

`resource acquire --actor builder --name gate --reason "Frozen candidate" --wait 3600`
waits up to one hour, in arrival order among callers using `--wait`. The command
prints the holder and initial queue position once, polls every 0.2 seconds without
journal or projection writes, and reserves only after an actual release. A timeout
records one refusal; a dead waiter does not block the line. `--wait 0` tries the
resource once without waiting for a holder or passing earlier waiters. Its queue
bookkeeping tolerates up to 250 ms per mutex acquisition (two acquisitions), so
a brief concurrent `resource status` does not cause an immediate refusal. Positive
waits retain their single total deadline. A caller already holding the resource
(including an alias of its identity) refuses immediately. Without `--wait`, immediate
acquisition/refusal is unchanged; older
or immediate callers do not join the line and may win a reservation race.

Waiting requires POSIX locks and a private queue for one OS user: 0700 directories
and 0600 files under `resource-waits/` in the registry. Shared-user queues and
Windows waiting are refused explicitly; immediate resource commands remain
available. Each poll closes its SQLite transaction before sleeping and demands
only authenticated cache leaves. A missing, foreign or stale cache refuses
waiting rather than replaying/rebuilding history in a poll; prepare a moved or
copied registry with an ordinary write first, and use `doctor` for integrity
details. Queue tickets are runtime sidecars, outside checkpoints and the journal.
Non-ticket entries such as Finder's .DS_Store are ignored without opening or
removing them; ticket and mutex safety checks still apply. If the queue mutex
stays busy until the deadline, the refusal says "Resource queue mutex busy;
wait deadline expired", rather than claiming the resource is already reserved.

On a task with requirements, `prove` needs explicit `--covers ID` before any
precheck or proof starts. Partial coverage is allowed. A successful proof prints
`coverage_on_this_source`, with covered and uncovered IDs per task; it counts
only passing proofs with the current contract on the same exact source. This
summary is not delivery or review acceptance. Tasks without requirements keep
their previous behavior. A shared proof's coverage is the union of member IDs;
the shared-proof preflight also requires complete coverage for every member.

Every window writing to one registry should run the same Squadra version, or some rules may not be enforced; moving a working squad to another version is an explicit, authorized step.

For a task declared with `task --defect-fix`, closure requires proof that the
regression fails without the change. Register its own unittest method IDs and
frozen test files with `challenge --task T1 --register --contract-file ...
--reason ...` as coordinator, then run `challenge --actor builder --task T1
--output /evidence/challenge-T1`. For example, a value regression asserts 2:
it fails on the original value 1 and passes after the fix. The same test bytes
run on the registered base and exact candidate, even before commit. Errors,
skips, missing IDs and a test that passes both versions never qualify.

The result and logs appear next to the proof. A coordinator may declare an
exception with a reason; the independent reader checks it, and it never appears
as a passed challenge. Source or agreement changes need fresh bindings; light
fixes keep the requirement. The normal proof and independent review still apply.
Old tasks keep working; frozen squads require an explicit upgrade of every writer,
with no mixed-version enforcement. See the [complete example and limits](work.md#defect-fixes-show-that-the-regression-detects-the-original).

The coordinator may declare `task --route light` for a bounded, reversible change after the usual questions. Policy **light-v1 / P2** allows at most **80 added plus deleted lines in four files**, counted from the original clean Git base, including tests and documents. Replacing one line counts as two; commits during the task do not reset the count. Binary files, symlinks, type/mode changes, missing bases, unstable snapshots and empty changes refuse. No compression to qualify.

P2 admits descriptive Markdown under docs/references, README EN/IT, tests and `assets/dashboard.html`. All scripts, plugin bundles, client/CI/build/dependency files, instruction files and normative contracts stay protected; unknown paths are protected too. The exact exclusions are in [the workflow](work.md#light-route). A security impact or required whole gate still takes the full route: size and paths never grant permission.

The light route skips a plan read and uses the agreed targeted proof instead of a whole suite; it allows one code-read round. A non-author reader, assignment/handoff ACKs, exact source and contract bindings, native results, explicit requirement coverage, pauses, reservations, budgets and required evidence remain. Release final reads and the owner's typed publication permission do not change. Example: the steady refresh fix changed **23 added/deleted lines in two files** including its test; it could use the six loading-contract tests, while its required browser measurement remains.

The registry remeasures before a proof process, on delivery, check and close, including direct Store writes. An excess or protected path refuses with a `light_*` code and “Full route required”; a second light read is refused too. Only the coordinator can promote with `route --full --scope-file ... --proof-file ... --reason ...` (and `--full-gate` or `--expected` when required). Promotion keeps the base, authors, history and spent counters, invalidates old proof/read bindings, and has no reverse. It cannot reset an exhausted budget.

Existing tasks without a route keep their behavior. A frozen squad must explicitly coordinate an upgrade of **every writer** before registering light tasks; installing this release does not migrate it. Older CLIs ignore this new metadata: mixed-version enforcement is not promised, and a 3.6.x window in such a squad still shows a task promoted to the full route as light. Unknown future policies remain readable but cannot qualify work.

### Dashboard transfers

The page polls every two seconds while visible. Its compact state request includes
the token of the copy it holds; an unchanged reply retains that copy and advances
relative times locally. Registry changes, overdue-wait/presence transitions,
activity buckets and plugin discovery invalidate the token. Authentication and
registry verification still precede a cached answer. Older compact clients that
do not send the state-token header retain their previous response format.

The overview receives text previews and evidence summaries. Full message bodies,
outputs, history and artifact references load when their page, overview details
or task story is opened. Details are bound to the registry head, so an older reply
cannot overwrite newer state; a failed read offers retry. The full task/run JSON
remains available from the technical-data control. Usage and, after integration,
Balance poll independently because session data can change without registry events.


## Reader transfer, resource inspection and workflow improvements (3.11)

Upgrade every window before the first new task or transfer: these events use hash
format 3, which old clients refuse. Existing histories are not rewritten.

```text
squadra reassign --actor coord --task T1 --reviewer reader2 --reason "Reader occupied"
squadra resource status --name cpu-heavy
squadra resource acquire --actor builder --name port-8000 --task T1 --reason "T1 preview"
squadra task ... --proof python3 -B -m unittest tests.test_feature
squadra deliver --actor builder --task T1 --note "Check the deletion case too"
squadra close --actor coord --task T1 --note "Frozen candidate accepted"
```

Reassignment preserves the delivery, native proofs and spent budgets and replaces
only its reader/packet. The new reader ACKs its exact handoff. A live or uncertain
native reader must be reconciled first. Previous handoffs leave the pending queue;
old readers cannot record a verdict. A recorded verdict needs an explicit review
amendment, never a transfer that silently replaces it. New-policy verdict retries
return their receipt without another round; legacy CLI reviews retain their record
shape and stable event IDs prevent repeated submissions. For new-policy tasks, `review --amend RECEIPT` explicitly
amends the current verdict within the original budget.

Resource status shows reservations and locked FIFO tickets, never inferred process
presence. Stale tickets are omitted without cleanup. Task-scoped reservations stop
unrelated work from blocking supersession; legacy reservations remain conservative.
`send` returns the message receipt without the project table; use `status` for it.

At close, apply [migliora](skills/migliora.md) to evidenced friction. It adds no rule
without a trigger, evidence, real check and retirement condition. The helper can
append an instruction in `AGENTS.md`, check it and create a local pathspec commit,
with `--defer-commit` and a later explicit `migliora commit` when a required review
must happen between them. It preserves other staged work and native hooks. It never
publishes, pushes or approves project memory proposals.


## Frozen source storage and recovery

Each new delivery retains the working-tree bytes and Git objects for its delivered
commit (shallow depth 1), plus the light task base commit/tree/blobs when needed.
Both roots count toward the storage quota; other history and ignored dependencies
are excluded. A shared candidate can retain an additional light base without
changing its read-only checkout bytes. Default limits are 512 MiB per copy and
2 GiB total, including damaged copies and staging.
A conservative estimate is checked before fetching and actual bytes before sealing;
concurrent deliveries share a storage lock. A checkpoint can require up to one
additional total quota. Save packages also retain their separately documented repository bundles.

`squadra candidate status` reports retained bytes and per-delivery availability.
`doctor` reports journal integrity separately from `candidate_integrity`; a damaged
or missing source cache degrades the latter. Checkpoint/save/export preserve the
journal and artifacts even when a source cache is unavailable; the checkpoint report
and save manifest list omitted copies. Their historical proof is not a restored source.
Review and close still refuse unavailable or changed candidates, naming task, SHA and paths.

All mutations below require the coordinator and a recorded `--reason`:

```text
squadra candidate repair --actor COORD --task TASK --reason "Recover reader pollution"
squadra candidate retire --actor COORD --task CLOSED_TASK --reason "Retention after closure"
squadra candidate limits --actor COORD --max-copy-mib 1024 --max-total-mib 4096 --reason "Larger project"
```

Repair requires the original checkout to match the exact proved snapshot; it preserves
the damaged folder as `candidates/damaged-*` and rematerializes in read-only mode.
If those original bytes are gone, restore them or create a newly proved delivery.
Retirement requires every task referencing the source to be closed or superseded.
It records removal, preserves the journal/proofs, and makes doctor/checkpoint agree on
`retired`. No cache is silently collected. Quarantine remains evidence and counts toward
quota; archive the registry if that evidence fills storage. Quotas may be explicitly
raised (up to 100 GiB total) before delivering a larger project. A successful repair
receipt contains `phase: completed`; an interrupted attempt may retain only `phase: started`.

Delivery and close notes remain in the journal/state and delivery packet; the compact
`stato.md` table does not display the closure note. Read the task state for its full text.
Claude plugin paths are explicit (`./` and `./migliora`), following the
[official manifest contract](https://code.claude.com/docs/en/plugins-reference#fields).
Manifest guards verify both paths; client discovery is a separate integration check.
