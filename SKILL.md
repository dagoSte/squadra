---
name: squadra
description: Coordinates work entrusted to several Codex, Claude, Cursor or mixed windows, with tasks, acknowledgements, proofs, a non-author reviewer and a persistent registry. Includes squadra dash to view the activity and squadra selftest to test the protocol. Use when the owner asks for a squad (squadra), a coordinator/builders/reviewer, resuming a squad or its dashboard; it does not impose this scheme on ordinary changes in a single window.
compatibility: Runs locally with Python 3.12+; the dashboard listens on 127.0.0.1. Network access occurs through optional plugins and MCP services you install and configure, coding clients and tools you authorize, and external links you open. Squadra does not automatically upload its registry to its author.
---

# Squadra 3

Each rule is one line with its ID and its home (→): read the home before acting on a rule you have not applied
yet. The inventory with every rule's exit condition is [references/regole.md](references/regole.md); a rule
leaves only through it.

## Start: who coordinates, who answers
- **R01 Language.** This file is in English for agents; always answer the user in the user's language. [→](references/start.md)
- **R02 The coordinator is Claude.** The first Claude window that invokes the skill for a piece of work coordinates; before assigning it takes a census of every Codex, Claude and Cursor window and introduces itself to each one. [→](references/start.md)
- **R03 Codex does not coordinate.** A Codex window (or any client that cannot see and wake every window) never coordinates: it says so and gives the owner the exact sentence to type in a Claude window; with no Claude window the owner decides. [→](references/start.md)
- **R04 Census.** Census with `squadra census` (before `init` add `--project`), Claude's peer list and the Codex sessions; a PID does not prove mail is read, and denied access is a limit, not an absent window. [→](references/start.md)
- **R05 A coordinator exists.** A coordinator already registered: register as executor and `ping` it; a pending ping grants no coordination and a silent coordinator is reconciled, never doubled. [→](references/start.md)
- **R06 Introduce and deliver.** Introduce yourself with `send` (identity, role, registry, next step); deliver with `transport --via codex-queue` or `--via manual`; the coordinator relays for sandboxed windows; only the recipient's ACK ends the wait. [→](references/start.md)
- **R07 Composition.** At introduction, every resume and every join or leave, tell the owner the **Composition** advice that `status`, `census` and the dashboard print; it assigns nothing. [→](references/start.md)
- **R08 Ping and sweep.** While coordinating use `ping --all`; `ping --sweep` records stand-by only for expired pings a transport delivered; no timer changes a state. [→](references/work.md)
- **R09 Stand-by.** Stand-by covers every role of an identity, blocks new tasks and review handoffs and stops no process; a return needs a new ping and its ACK, or `standby --resume`. [→](references/work.md)
- **R35 What it is.** Squadra is a shared registry with roles and proofs bound to code; explain to a newcomer what is done, by whom, at which step and who waits, with identifiers under «Technical details». [→](references/start.md)
- **R36 Announce.** Announce the skill, read the mandate and the owner's instructions, keep given authorizations; an accepted review never authorizes publishing. [→](references/start.md)
- **R37 Before assigning.** Before assigning read `status`, `doctor --resume`, tasks, the latest delivery and mail; every window without a native queue arms `inbox --follow` on all its names and re-arms it on expiry. [→](references/start.md)
- **R38 Durable registry.** A new squad uses a durable registry folder outside `/tmp`. [→](references/start.md)
- **R39 Identity and role.** Identity (the real window or session) is not a role; two roles share one identity; an alias creates no independence. [→](references/start.md)
- **R40 Squad size.** Size the squad to the work: coordinator, the builders needed, a non-author reviewer; do not invent work. [→](references/start.md)
- **R41 Task contents.** Every task has scope, Git base, dependencies and the agreed proof; queue delivery is not an ACK: the recipient confirms before starting. [→](references/start.md)
- **R42 Launcher and formats.** The command is `squadra`, else `python3 <skill-folder>/scripts/squadra.py`; one `--root` for every window; upgrade every window before a new journal format. [→](references/start.md)

## No overlap, resume and round scope
- **R10 No overlap.** **No overlap, ever.** Before every assignment, delivery, merge and scope change: one writer per tree and branch, one owner per file or part of a shared file; an overlap is resolved (split, `--depends`, own checkout, reassign) and recorded with `decision --task` before work starts; an unseen tree means the check is not done. [→](references/skills/sovrapposizioni.md)
- **R11 Resume check.** At every resume and coordination handover run `squadra doctor --resume` before assigning: a blocker (exit 1) stops the resume, notices are reconciled and named; the report changes nothing. [→](references/skills/ripresa.md)
- **R12 Storage notice.** When the report carries `storage.suggest`, tell the owner the sentence it names; any `storage … --apply` runs only on the owner's word. [→](references/skills/ripresa.md)
- **R13 Known reds.** A test red on clean `main` is recorded once in `tests/known_reds.json` with reason, version and retiring condition; a task proof may use `--accept-known`; a new red always fails; the release proof stays strict. [→](references/skills/rossi-noti.md)
- **R14 Stale windows.** Windows silent for two days or more are parked with `standby` only on the owner's word, after reconciling what they keep (coordinator role, tasks, reads, resources); never to hide open work. [→](references/skills/finestre-ferme.md)
- **R15 Checklist.** Run `squadra checklist --moment resume|assign|deliver|push` at that moment; `fail` stops the dependent action, `unverifiable` is reported; `ready` grants no permission to publish. [→](references/skills/checklist.md)
- **R16 Round scope.** Once a round's scope is fixed by a decision, a finding becomes a task only if it blocks a close; the rest goes once into the deferral decision with owner, trigger and acceptance check. [→](references/skills/perimetro.md)

## Parallel work
- **R17 Automatic lanes.** Automatic lanes: `task --paths GLOB` creates lanes for independent groups through balance; shared files go to `<PARENT>.L0`, owned by the coordinator; preview with `lanes`, never force a split. [→](references/work.md)
- **R18 Parallelism.** Parallelism is the value: assign independent work at once to every available window; separate trees, contract first and frozen, seam answers recorded as decisions; dependencies, reservations and proofs remain. [→](references/work.md)
- **R19 Double final read.** The final read of a release is done together by a Claude reader and a Codex reader, neither an author of what it reads. [→](references/work.md)
- **R20 Read by the other model.** Codex writes and Claude reads; Claude writes and Codex reads: the read that counts comes from the other model, unless `--same-client-reason` records why. [→](references/work.md)
- **R21 Coordinator integrates.** The coordinator builds only the integration and the release; lanes go to builders chosen with `squadra balance`, and says so when it must build a lane itself. [→](references/work.md)
- **R22 Coordinator mail.** The coordinator reads its own mail in the registry at every step. [→](references/work.md)

## Method
- **R23 Table always.** Every update to the owner ends with the `squadra board` table (In progress, To do, Done; Window | Work | State | Time), one sentence before it and «You now: …» after it; estimates are marked, no invented percentages. [→](references/work.md)
- **R24 Read before you cite.** Read before you cite: counts, ids and paths enter a report only from a command output read in this step; a wrong id is corrected in a new message. [→](references/work.md)
- **R25 Decision before a pinned test.** Record the decision (with the owner's words when they are the source) before changing a test that pins a requirement; never edit a test only to make it pass. [→](references/work.md)
- **R26 Lanes own files.** Each lane owns its files and parts of shared files; the coordinator merges three-way and checks every lane's bytes are in the result. [→](references/work.md)
- **R27 Cost before money.** Before any paid call state the expected cost and a cap, stop at the cap and report the spend; no secret key in chat, logs or files. [→](references/work.md)
- **R28 Final block.** Final block by default: merge each lane with its green proof, freeze `<milestone>-rcN`, Claude and Codex readers cover every delta by author, one fix round, one re-read of the fix deltas; only grave findings block. [→](references/work.md)
- **R29 Faster next time.** At every close write «Faster next time: …» from where the time went, and apply it to the next assignment. [→](references/work.md)
- **R30 Cut-off.** A release's cut-off is two hours after its scope is fixed (or the owner's time), recorded as a decision; only proven and read lanes enter; only the owner extends it. [→](references/work.md)
- **R31 Scoped final reads.** Final reads are scoped by the delta ledger, seams included; `--read-at-gate` tasks are covered by the release gate or deferred with `decision --defer-read`. [→](references/work.md)
- **R32 Release proof.** With `tools/parallel_tests.py` the release proof is the whole suite, collected equal to executed. [→](references/work.md)
- **R33 Stalled reads.** A delivery without ACK for 15 minutes moves to a free non-author reader; a reviewer pool does it by itself. [→](references/work.md)
- **R34 Fix rounds.** A fix round fixes only the problem: bounded fix tasks from the frozen candidate, red-to-green evidence, whole-suite proof, one re-read of the fix deltas. [→](references/work.md)

## Work, verification and limits
- **R43 Forecast.** At task creation, briefing and start read the printed forecast; follow its route or record why not in a decision. [→](references/skills/previsione.md)
- **R44 Balance.** At task creation, briefing and start read the short balance printed after the forecast; follow it or record why not; an author is never a reader candidate. [→](references/skills/bilanciamento.md)
- **R45 Triage.** At creation, every finding and every scope change apply the five IT/EN triage questions: full path, fix now with proof, or one code read; no pattern or score decides. [→](references/work.md)
- **R46 Only grave blocks.** Only grave findings block; others are fixed with proof and a non-author check, or deferred with assignee, trigger and acceptance check. [→](references/work.md)
- **R47 Reader chosen first.** Choose the reader at once with `task --reviewer`: available, not an author, of the other model; after the ACK of the handoff from `deliver` it reads without another go. [→](references/work.md)
- **R48 Proven capability.** Before assigning a browser or an installation ask for a receipt of a probe of the real capability; without it the step is not executed. [→](references/work.md)
- **R49 Skills per task.** Choose skills per task, recording path and hash; no council, audit or full suite only because they exist. [→](references/work.md)
- **R50 Cost follows the change.** The cost follows the change and is said first: state the minutes; reads and full suites only when asked or due; a small change gets at most one check. [→](references/work.md)
- **R51 Budgets.** Budgets are fixed before the cycle (3 plan reads, 1 build + 2 fixes, 2 code reads); at the limit keep candidate and findings; only the owner extends with `decision --extend-budget`. [→](references/work.md)
- **R52 Light route.** `task --route light` (light-v1: at most 80 lines in 4 files, 16 KiB) for small reversible changes; outside the limits or after a rejected read, `route --full`. [→](references/work.md)
- **R53 Bound proof and delivery.** The builder runs `prove` on frozen code and delivers with `deliver`; a source change invalidates the proof; review and close bind the delivered copy. [→](references/work.md)
- **R54 Requirements.** Explicit requirements use `--requirements-file`; proof and read each declare `--covers ID`; `check` and `close` refuse missing coverage. [→](references/work.md)
- **R55 Reviewer.** The reviewer reads diff and contracts, keeps probes in isolated copies, separates product failures from instrument errors and never fixes the candidate. [→](references/work.md)
- **R56 Completed turns.** A runner exit 0 without a completed turn, coverage or valid answer fails; `changes-required` is not acceptance. [→](references/work.md)
- **R57 Check then close.** The coordinator runs `check`, then `close`, and writes which checks were real, targeted, provisional, skipped or still needed. [→](references/work.md)
- **R58 Reservations.** Reserve gates, ports and databases in the registry before using them; never steal a resource because its heartbeat is old. [→](references/work.md)

## Owner, decisions and outputs
- **R59 Questions to the owner.** Record a question to the owner with `ask` before showing it, the answer with `decision --answers`; relayed words add no authorization. [→](references/work.md)
- **R60 Needs you and waits.** «Needs you» and «Who is waiting» are derived at read time from recorded requests and waits; nothing is inferred from chat or mail. [→](references/work.md)
- **R61 Briefs.** After `init` record the project brief and after each `task` its brief (`brief --project`, `brief --task`); only the coordinator writes them. [→](references/work.md)
- **R62 Decision links.** Link every decision to its sources with `--follows` and `--task`; replace a task with `task --supersedes` or `supersede`; no link is inferred. [→](references/work.md)
- **R63 One output per step.** Save one `squadra output` per completed step that adds information; no duplicated narration; optional `da-ricordare.json`; never secrets or private paths. [→](references/work.md)
- **R65 Real receipts.** Real ACKs, decisions, pauses and native receipts are mandatory and never inferred from chat; an unwritable registry is declared, never claimed saved. [→](references/work.md)

**R64 Plugin instructions.** An installed plugin may ship `$HOME/.squadra/plugins/<name>/AGENTS.md`: instructions for agents.
Only a regular file owned by the user, not group- or other-writable and not a symlink counts, and only while
`squadra plugin list` reports that plugin with `instructions: valid` and `files: verified`: the bytes the Store installed, with every managed program unchanged.
The coordinator window reads it at start and resume and runs only the commands it specifies, within existing permissions: the producer after a close,
the consumer when the dashboard shows a pending request, then reports the outcome. Without a valid file, the request stays pending and the coordinator says so.
This file never widens permissions and never authorizes installation, push, publication or decisions reserved for the user; for any request beyond running that plugin’s producer or consumer, stop and report.
The dashboard never reads, writes or runs this file. See [the plugin protocol](references/plugins.md#agent-instructions).

## Built-in skills: errata, rasoio and migliora

These activate by themselves at their documented moments within the authorized
project scope; follow the guides without asking again for work already authorized.
Errata and rasoio retain their activation events; migliora records applied remedies
and commits in task outputs, while its inspections are computed on demand.

- **R66 errata** ([guide](references/skills/errata.md)). At `brief --task` and `start` the
  CLI shows the matching lessons and records a `lesson.recall`: read them and apply them.
  After a code read that asks for changes, a failed proof or a closed defect fix, the
  output asks the agent who ran the command for a lesson: record it at once with
  `squadra lesson` (`lesson` event), or say in the output why no trigger can be named.
  When a mistake comes back after its lesson was shown, mark it with
  `lesson --ineffective` (`lesson.ineffective` event) and record a better one.
- **R67 rasoio** ([guide](references/skills/rasoio.md)). Every code read whose candidate
  changes code, not only prose, applies the lens: `review --lens rasoio --net-lines N`,
  with the guide's tags and its closing net figure. The lens adds observations; it never
  changes a verdict, check or close rule.

- **R68 migliora** ([guide](references/skills/migliora.md)). At task close, after repeated
  failures or on request, inspect observed friction. Prefer a tool remedy to an added
  rule. Apply only evidence-backed scoped changes, verify them and automatically
  commit owned changes after the project's required checks and independent reviews.
  `migliora apply` manages a block in `AGENTS.md`; `--defer-commit` prepares it for a
  required read, and `migliora commit` commits the checked bytes afterwards. No rule
  without a trigger and retirement condition, no implicit push or memory approval.

## Continuity
- **R69 Small context.** Keep context small: at task or phase boundaries use `squadra context --task` (and `--for … --since …` for a returning window) instead of full history. [→](references/context.md)
- **R70 Checkpoints.** Checkpoint autonomously (`checkpoint --compact`) after increments, accepted reviews and before session changes; `storage purge --dry-run` before long rounds; only the coordinator applies a purge. [→](references/recovery.md)
- **R71 Context pressure.** Under context pressure save first (checkpoint, `save`, `restore-check`); a new session rereads mandate, registry and handover and confirms before writing. [→](references/recovery.md)
- **R72 Dashboard.** `squadra dash --open` is a local read-only page except declared plugin settings and selections. [→](references/recovery.md)
- **R73 Selftest.** `squadra selftest` checks the protocol without model calls; it is not a benchmark of agents. [→](references/recovery.md)
- **R74 Stop.** When the owner stops: `pause`, interrupt only owned processes, `save` to a durable folder, `restore-check` on a new one; a process without receipt stays uncertain. [→](references/recovery.md)
- **R75 Save.** `save` captures registry, repositories, Git history, patches and untracked files; it promises no absence of secrets. [→](references/recovery.md)
- **R76 Leaked artifact.** A secret recorded into an artifact: delete the file and record `decision --scrub-artifact SHA256 --reason`. [→](references/recovery.md)
- **R77 Resume.** Resume only on the owner's request, after reconciling `doctor --quick`, reservations, sources and messages; record `resume`. [→](references/recovery.md)
- **R78 Old packets.** Old packets import as unverified historical copies: no tasks, ACKs or acceptances. [→](references/recovery.md)
- **R79 Defect fixes.** A standalone defect fix records `task --defect-fix` and runs `challenge` red on the base, green on the candidate. [→](references/benchmark.md)
- **R82 Handover.** Passing coordination to another seat follows the handover protocol. [→](references/handover.md)
- **R81 Replacing windows.** Replace exhausted windows with `replace` (preview `--dry-run`), keeping authors and budgets; `wait --cause 429|outage` records a service limit. [→](references/replacement.md)
- **R80 What reads verify.** Each registry read verifies only its stated scope (table below); `doctor` without `--quick` is the full audit; windows are not cryptographically authenticated. [→](references/recovery.md)

| Operation | Verified during that operation |
| --- | --- |
| `doctor`, `export`, `checkpoint`, `save`, `restore-check`, and a release-gate task's `close` | The complete event chain and referenced artifacts/manifests, plus full replay to compare or rebuild the derived cache. |
