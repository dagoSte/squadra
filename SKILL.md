---
name: squadra
description: Coordinates work entrusted to several Codex, Claude, Cursor or mixed windows, with tasks, acknowledgements, proofs, a non-author reviewer and a persistent registry. Includes squadra dash to view the activity and squadra selftest to test the protocol. Use when the owner asks for a squad (squadra), a coordinator/builders/reviewer, resuming a squad or its dashboard; it does not impose this scheme on ordinary changes in a single window.
---

# Squadra 3

This file is written in English for agents; always answer the user in the user's language.

## Rule 0 — who coordinates, who answers

The first window that invokes this skill for a piece of work takes the coordinator
role. **Before assigning or starting work, it takes a census of every Codex, Claude
and Cursor window of the project and introduces itself to each one.** This is an
operating rule for the agent, not an automatic election run by any CLI command.

- Look for the shared registry; use `squadra census` (before `init`, add
  `--project <project>`). Complete the census with Claude's peer list and the
  available Codex sessions; Cursor appears as a `cursor-agent` process. A PID does not prove that mail is read;
  denied access or unobservable processes are limits, not absent windows.
- If you find a coordinator already registered in the same registry, register your
  identity with the executor role and send it a `ping`. If it answers with an ACK,
  introduce yourself and wait for the role you are assigned. A ping still pending does
  not grant coordination, and a stand-by does not transfer roles or resources: explicitly
  reconcile a coordinator that does not answer, without creating a second one.
- Introduce yourself to every window you found with `send` in the registry, stating
  identity, role, registry and next step. Use `transport --via codex-queue` for Codex
  sessions actually identified; for Claude use peer messaging if available, keeping
  its receipt, otherwise `transport --via manual` (also for Cursor).
  A process without a verified session remains a recipient to reconcile: do not
  invent its identity and do not declare the introduction complete.
- While coordinating, use `ping --all` and forward the messages; the recipient's ACK
  is the answer. `settings` configures `alive` (default 300 seconds). Time is computed
  at read time. `ping --sweep`, run by the coordinator, records stand-by for expired
  answers; no timer changes the state.
- Stand-by applies to every role of the same identity and prevents new tasks and
  review handoffs. A new `ping --to <window>` and its ACK let the coordinator's next
  sweep record the return; `standby --resume --reason ...` records it explicitly. An old
  ACK is not enough. Do not terminate processes, release resources or reassign work merely
  because a window is on stand-by.

Read [startup and presentation](references/start.md) for the complete commands and
[keep-alive and return](references/work.md#keep-alive-e-stand-by) during the work.

## Rule 1 — parallel work

The squad's value is parallelism. The coordinator immediately assigns independent work
to every available window, Codex, Claude or interactive Cursor: no window stays idle
while there is independent work to give it.

- Builds on separate trees, one builder per tree; reads of different tasks run
  together on frozen copies.
- A large job is split by area after the shared contract (data format, interfaces)
  is written: the parts proceed together and are joined afterwards.
- The final read of a release is done together by a Claude reader and a Codex
  reader, neither of them an author of what it reads.
- The coordinator reads its own mail in the registry at every step: a window may
  receive work directly from the owner and report it only there.
- Dependencies, reservations, budgets and reader independence remain: parallelism does
  not skip proofs.

## Rule 2 — the table always visible

Every update to the owner, from any window, shows the state of the work by actor and
state, taken from the registry with `squadra board`: three groups **In progress**,
**To do**, **Done**, columns **Window | Work | State | Time (approx.)**, and «Done X of Y» at the end.
The live board measures elapsed time: assignment to close for Done, time since the first
recorded start (or assignment) for In progress, and «—» for To do, rounded to minutes,
hours or days. The saved projection keeps Done durations but shows «—» for In progress,
so it retains no running age. The coordinator adds an approximate estimate, explicitly marked as
an estimate: remaining time for In progress, total task time for To do.
Before the table one sentence says where things stand and what the owner needs; after
it, «You now: …» («Tu adesso: …» in Italian). No invented percentages: a state
declared but not recorded is written as such.

## Rules 3–8 — method

- **Rule 3 — read before you cite.** A count, a commit, task, message or proof id, or a
  file path enters a report only after it was read from a command's output in this step,
  never from memory. A wrong id already given is corrected in a new message.
- **Rule 4 — record the decision before changing a pinned test.** When a requirement that
  a test pins is withdrawn or changed, first record the decision (`squadra decision`, with
  the owner's words when they are the source), then change the test and the code together.
  Never edit a test only to make it pass.
- **Rule 5 — parallel lanes own their files.** Each lane is told which files, and which
  parts of a shared file, it may change. The coordinator merges the lanes with three-way
  merges, resolves conflicts and checks that every lane's accepted bytes are in the result.
  A lane that needs another lane's area asks the coordinator.
- **Rule 6 — state the cost before spending money.** Before any paid call (model APIs,
  voice, images, cloud runs) the window states the expected cost and a cap, stops at the
  cap, and reports what was spent and on what. No secret key enters chat, logs or files.
- **Rule 7 — the owner chooses the review mode.** Default: a non-author read of every lane
  plus the final dual read. On the owner's word a release uses one final review only:
  lanes are merged on delivery and the final dual read (Claude and Codex) covers every
  lane, with one fix round. The choice is recorded as a decision.
- **Rule 8 — faster next time.** At every close the coordinator reads where the task's
  time went (the dashboard measures waiting for the acknowledgement, building, waiting
  for the read and fix rounds), writes one line «Faster next time: …» in its closing
  output, and applies it to the next assignment.

## What it is

A squad with a shared registry, recognizable roles and proofs bound to the delivered
code. The coordinator keeps the thread; the reviewer checks a frozen candidate without
becoming its author. Windows can use Codex, Claude or Cursor.
Cursor works only as an interactive window: squadra never starts it by itself, because
Cursor runs team, user and enterprise hooks without a sandbox (`references/runtime.md`).

Whoever opens the dashboard may know neither the project nor this skill. Explain first
what is being done, who is doing it, which step it has reached and whose answer it is
waiting for. A candidate is a frozen version to check; a review is the check by someone
who did not write it; an ACK is the recipient's explicit confirmation.
Use the task's title or summary to describe the windows. Identifiers, code
fingerprints, paths and event numbers go in «Technical details».
The landing page must make the project, the current work, progress, waits and the path
from decisions to results readable; keep the detail available on request.
In explanations of presence, a ping is a call that asks for confirmation; stand-by
suspends new tasks and does not stop processes already started.

## Start and resume

Announce the skill when you apply it. Read the project's mandate and the owner's
instructions first. Keep the authorizations already given and the release boundaries:
an accepted review does not grant a new authorization to publish.

1. Look for an existing registry and read `status`, `doctor`, tasks, the latest delivery
   and mail before assigning work. Do not replay old queues blindly.
   All windows of the same squad use a single shared registry; separate clones do not
   create separate mailboxes. `posta/exchange-YYYY-MM-DD.md` is the daily projection
   of the events, not a second source to edit.
2. For a new squad choose a durable folder outside `/tmp`. Use
   `references/start.md` to initialize it and register windows/skills/versions.
3. Distinguish **identity** (the actual window/session) from **role**. If a window
   has two roles, register the same identity. An alias does not create independence.
4. Choose a squad sized to the work: coordinator, the builders needed, a non-author
   reviewer. Two builders are useful for independent scopes; do not invent work to
   keep them busy. Use separate checkouts and assign the owned paths.
5. Every task contains scope, Git base, dependencies and the agreed proof command.
   Queue delivery is not an ACK: the recipient must confirm in the registry before
   starting. An unreachable client stays pending.

The installed command is `squadra`. Without the launcher use
`python3 <skill-folder>/scripts/squadra.py`. Do not guess user paths.
`--root` and `SQUADRA_ROOT` select the same registry for every window.

## Work, verification and limits

Read `references/work.md` for tasks, plan, proofs and review. Read
`references/runtime.md` only if you must invoke a client automatically.
For the dashboard plugins' data and declared settings read [folders, schemas and limits](references/plugins.md); their labels do not authorize actions on the registry.

- At creation, at every finding or change of scope use the [five IT/EN
  questions](references/work.md#triage-in-una-pagina). The author proposes, the
  non-author reader confirms or dissents, the coordinator resolves with a recorded reason.
  Q1 yes or unknown, Q2 unresolved, hidden material harm under Q5, or a mandate
  require the **full path**: plan accepted before building, proof, non-author read
  and the checks due. In the other cases choose **fix now with proof** (reversible
  remedy, no choices, short non-author check) or **one code read** (scope, bound
  proof, independent read).
  No regex, path pattern or score decides; no required check is dropped.
- **Only grave findings block**, including missing mandatory security proofs.
  The others are fixed right away with proof and non-author verification, or recorded
  for later: their own task, or a decision that names the task, with an assignee,
  trigger and acceptance check, linked from the report and visible until closed.
  A deferred requirement needs an authorized disposition; do not declare it satisfied.
- Choose from the start, with `task --reviewer`, an available identity different from
  the authors. Readers of different tasks work in parallel on frozen copies; one
  builder per tree. Dependencies and reservations of gates, ports and databases remain.
  After the ACK of the handoff generated by `deliver`, the reader starts the read
  already assigned without another «go» from the coordinator; the command does not
  invoke the client.
- Before assigning a browser or an installation, ask the window for a receipt of a
  probe of the real capability. If it is missing, declare the step not executed and
  cite the last valid proof (or its absence); do not hand commands to the owner.
  Browser: timeouts for the connection, for each call and for the whole step; cleanup
  of your own processes only. Capability and delegation do not exceed the environment's permissions.
- Skills are chosen **for each task**, recording path and hash. Do not launch a
  council, an adversarial audit or the full suite just because they are available.
- Fix the budget before the cycle: by default 3 plan reads, 1 build + 2 fixes, 2 code
  reads. An explicit timeout per process. At the limit keep the candidate, the findings
  and the concrete question; do not restart with an alias or an identical task to reset
  the counter. A budget decision must be explicit and recorded; a narrow corrective
  reread also consumes the same budget, and no new cycle starts.
- The builder runs `prove` on frozen code and delivers with `deliver`. The command
  keeps argv, stdout/stderr, the native result and the fingerprint of tracked and
  untracked sources. A later change invalidates the proof and the review. Choose the
  reviewer with `task --reviewer`: `deliver` atomically saves an immutable packet
  and a message bound to the candidate. The real ACK of that message is required; `advance`
  reconciles the handoff without building, approving or invoking a model.
- For requirements to make explicit use `--requirements-file`; proof and read each
  declare `--covers ID` separately. `check/close` refuse missing coverage, or coverage
  that refers to old sources. The reviewer judges whether the assertions are sufficient:
  the registry checks completeness and links, not the meaning of the test.
- The reviewer reads the diff and the contracts, chooses its own probes when needed and
  keeps originals, positive controls and mutants in isolated copies. It distinguishes
  product failures from instrument errors. It does not fix the candidate.
- The client runner distinguishes a completed turn, a structured verdict and a
  recorded review. An exit 0 without completion, coverage or a valid answer fails.
  A successful turn with `changes-required` is **not** acceptance.
- The coordinator verifies `check`, then `close`. Recording a declared result does not
  mean having reproduced it. Write exactly which checks are real, targeted,
  provisional, skipped or still needed.
- Reserve gates/ports/databases in the registry before using them and also consult the
  project's reservations. The registry does not control windows that do not adopt it.
  Do not steal a resource because its heartbeat is old.

## Needs you — requests and waits

The coordinator records a question to the owner with `ask` **before showing it**:
options with ID, label and the sentence to say, the task and the relevant proofs. After
the answer it records `decision --answers` with the choice and the channel `typed-here` or `relayed`.
Relayed words are receipts: they add no executive authorization.
A question is closed once, with an answer or `--withdraws`.

`status`, `stato.md` and the dashboard use the same derived view: «Needs you» contains
open requests, exhausted budgets and pause; «Who is waiting» contains the waits between windows.
No question is inferred from the chat, from mail bodies or from the `output.next` text.
An empty panel only means that there are no recorded requests.
The dashboard shows the requests and the overview of the work first; the details are below.
Waits are computed at read time with `waits(state, now, settings)`; explicit thresholds
in seconds, no frozen age in the projections. A warning does not authorize retries,
reassignment or releasing resources; actual reconciliation is needed.
Read `references/work.md` for the commands.

## Explaining the work and following decisions

Someone arriving now must understand the project without knowing acronyms or identifiers.
Right after `init` the coordinator records the project's **brief** with
`brief --project --file <text>`: UTF-8 text of at most 8192 bytes, with the goal
in the first paragraph and, if needed, `## Steps` and `## Glossary` (or `## Passi` and `## Glossario`). After each `task`
it records the task's brief with `brief --task <ID> --file <text>`: the first line
becomes the task's name throughout the dashboard, the rest says why it exists, who it
is for and when it will be finished. Only the coordinator writes briefs; a new one takes
the place of the previous one on the page and the history remains. A brief does not change the state.

Link every decision to what it comes from: `decision --id <name>`, `--task <ID>` for the
tasks it concerns and `--follows ask:<id>` or `--follows decision:<id>` for earlier
entries, resolved once when it is recorded. An owner instruction without a
recorded question carries `--owner-words` and `--channel typed-here` or `relayed`.
No link is inferred from text, mail or closeness in time, and a link
does not close a question: that takes `--answers` or `--withdraws`. `task --supersedes <ID>`
replaces an open task without erasing its history; if the replacement already exists,
`supersede --task <OLD> --by <NEW> --reason "…"` records the same replacement
after the fact, with the same refusals. The **Decisions** view
shows the graph: each box is a recorded fact, solid lines are
recorded links, dashed ones only say «happened later, in the same
task». Read `references/work.md` and `references/start.md` for the commands.

## One substantial output per completed step

Save with `squadra output` a result, a decision, a block or a handoff
that adds information: task, outcome, proofs, next step and receipt.
Normal progress is read in the registry; do not duplicate assignments or
generated handoffs with manual «ready» messages, nor every exchange in the chat with an
output. A proof already recorded must not be rewritten as narration with nothing new.
At close you may attach `da-ricordare.json` (`form: da-ricordare/1`) to the output:
the IT/EN questions and the format are in `references/work.md`. If a memory service is
in use, declare at close and do not propose the same item through another
channel. Never secrets, personal data or private paths: artifacts stay
in the registry and cannot be removed after an accidental publication.
An installed plugin may ship `$HOME/.squadra/plugins/<name>/AGENTS.md`: instructions for agents.
Only a regular file owned by the user, not group- or other-writable and not a symlink counts.
The coordinator window reads it at start and resume and runs only the commands it specifies, within existing permissions: the producer after a close,
the consumer when the dashboard shows a pending request, then reports the outcome. Without a valid file, the request stays pending and the coordinator says so.
This file never widens permissions and never authorizes installation, push, publication or decisions reserved for the user; for any request beyond running that plugin’s producer or consumer, stop and report.
The dashboard never reads, writes or runs this file. See [the plugin protocol](references/plugins.md#agent-instructions).

Real ACKs, decisions, pauses and native receipts remain mandatory; do not infer them
from the chat. Show the Rule 2 table (`squadra board`), without invented percentages.
If the registry is not writable, keep the durable finding, declare the limit and
reconcile before proceeding: do not claim a save that did not succeed.

`squadra board [--lang it|en]` shows the tables by window, work and state, also at the top of `status` and `stato.md`.

## Dashboard, stop and continuity

- **Autonomous checkpoints:** do not wait for the owner to ask for them. After a
  completed increment, an accepted review and before a session change or
  publication, run `checkpoint --to <new-durable-folder>` and record the
  receipt in the output. The checkpoint keeps the registry; to also protect
  sources and WIP use `save` and a real `restore-check`. Do not rerun tests that are
  already valid just to create a resume point. Read the handover protocol in
  `references/recovery.md`.
- **Context under pressure:** if the client signals compaction/a limit, or you
  can no longer reliably reconstruct task, constraints and proofs, save
  before continuing. Do not invent a percentage of remaining tokens. Prepare a
  durable summary and consider a session with a clean context; if the client
  allows it, start it within the task and budget already authorized. The new session
  must reread the mandate, the registry and the handover, confirm receipt and
  reconcile sources and resources before writing. A prepared prompt does not prove
  that a window was opened. Preserve authors, identities, budgets and blocks;
  do not use the context change to reset them or to create a second writer.
- `squadra dash --open`: a local page, read-only except declared settings and selections of installed plugins (it saves requests, it does not run actions); refreshed every 2 seconds.
  The **Summary** explains work, steps, waits and the history of the tasks; **Decisions**
  and **Communications** keep the recorded links and ACKs. Technical
  details on request, IT/EN for the interface, never translations of the recorded texts.
  Read `references/recovery.md` for how it works and its limits.
- `squadra selftest`: synthetic toolkit checks without model calls, with
  result and logs kept. It verifies the protocol, not the general quality of the
  generated code or the security of a product. Do not present it as a
  comparative benchmark of two agents.
- When the owner stops the work, record `pause`, interrupt only owned processes,
  wait for the terminal receipts and save with `save` into a durable folder, then use
  `restore-check` on a new folder to really prove the recovery.
  A process without a receipt stays in an uncertain state: do not declare it stopped.
- `save` captures the registry and all registered repositories, Git history, separate
  index/worktree patches and untracked files; `--repo` is a partial choice. `checkpoint`
  remains the copy of the registry only. Read the filters and limits in
  `references/recovery.md`; do not promise that secrets are absent from the whole history.
- To resume, wait for the owner's request, reread and reconcile
  `doctor`, reservations, sources and messages, record `resume` and assign the current step.
  An ACK, a resume or a review does not authorize an implicit publication.
- Old packets can be imported as **unverified historical copies**;
  they generate no tasks, ACKs or acceptances. A squad already active keeps its
  frozen protocol until it receives an explicit version switch.

For original/remedy/positive challenges or benchmarks read `references/benchmark.md`.
`challenge` distinguishes an assertion from an instrument error; it does not replace a
delivery proof. `bench-example/run/score` test the comparison with fake clients. A pilot
with real models requires the owner's budget; no superiority is inferred
from the selftest, from a single turn or from different permission profiles.

The local registry protects against concurrent overwrites and detects accidental
alterations with a hash chain. It does not cryptographically authenticate the windows:
anyone with the same account can impersonate a name or rewrite the database.
Continuity requires backups to another destination; no disk guarantees
"never lose" against failure or total deletion.
