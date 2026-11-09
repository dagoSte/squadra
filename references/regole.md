# Rule inventory

Every rule of the Squadra core (SKILL.md) has one row here: its ID, the rule in one line, the condition
under which it leaves, the command that applies it when the CLI does, its home (the reference that holds the
full text) and the units of the 3.14.0 SKILL.md it comes from. A rule leaves the core only through this table:
its row changes to a recorded exit, with the decision that retired it.

No rule of 3.14.0 has been retired. «CLI» names the command that enforces or prints the rule; the agent
still follows the line in the core.

## Inventory

| ID | Rule | Exit | CLI | Home | 3.14.0 units |
|---|---|---|---|---|---|
| R01 | **Language.** This file is in English for agents; always answer the user in the user's language. | Owner's decision recorded here (R25) | — | [start.md](start.md) | U001 |
| R02 | **The coordinator is Claude.** The first Claude window that invokes the skill for a piece of work coordinates; before assigning it takes a census of every Codex, Claude and Cursor window and introduces itself to each one. | Owner's decision recorded here (R25) | — | [start.md](start.md) | U002 U012 |
| R03 | **Codex does not coordinate.** A Codex window (or any client that cannot see and wake every window) never coordinates: it says so and gives the owner the exact sentence to type in a Claude window; with no Claude window the owner decides. | Owner's decision recorded here (R25) | — | [start.md](start.md) | U003 U004 U005 |
| R04 | **Census.** Census with `squadra census` (before `init` add `--project`), Claude's peer list and the Codex sessions; a PID does not prove mail is read, and denied access is a limit, not an absent window. | Owner's decision recorded here (R25) | `squadra census` | [start.md](start.md) | U006 |
| R05 | **A coordinator exists.** A coordinator already registered: register as executor and `ping` it; a pending ping grants no coordination and a silent coordinator is reconciled, never doubled. | Owner's decision recorded here (R25) | — | [start.md](start.md) | U007 |
| R06 | **Introduce and deliver.** Introduce yourself with `send` (identity, role, registry, next step); deliver with `transport --via codex-queue` or `--via manual`; the coordinator relays for sandboxed windows; only the recipient's ACK ends the wait. | Owner's decision recorded here (R25) | `squadra transport` | [start.md](start.md) | U008 |
| R07 | **Composition.** At introduction, every resume and every join or leave, tell the owner the **Composition** advice that `status`, `census` and the dashboard print; it assigns nothing. | Owner's decision recorded here (R25) | `squadra status / census` | [start.md](start.md) | U009 |
| R08 | **Ping and sweep.** While coordinating use `ping --all`; `ping --sweep` records stand-by only for expired pings a transport delivered; no timer changes a state. | Owner's decision recorded here (R25) | `squadra ping` | [work.md](work.md) | U010 |
| R09 | **Stand-by.** Stand-by covers every role of an identity, blocks new tasks and review handoffs and stops no process; a return needs a new ping and its ACK, or `standby --resume`. | Owner's decision recorded here (R25) | `squadra standby` | [work.md](work.md) | U011 |
| R10 | **No overlap.** **No overlap, ever.** Before every assignment, delivery, merge and scope change: one writer per tree and branch, one owner per file or part of a shared file; an overlap is resolved (split, `--depends`, own checkout, reassign) and recorded with `decision --task` before work starts; an unseen tree means the check is not done. | Owner's decision recorded here (R25) | — | [skills/sovrapposizioni.md](skills/sovrapposizioni.md) | U013 U014 U015 U016 U017 U018 U019 |
| R11 | **Resume check.** At every resume and coordination handover run `squadra doctor --resume` before assigning: a blocker (exit 1) stops the resume, notices are reconciled and named; the report changes nothing. | Owner's decision recorded here (R25) | `squadra doctor --resume` | [skills/ripresa.md](skills/ripresa.md) | U020 U021 U022 U023 U025 |
| R12 | **Storage notice.** When the report carries `storage.suggest`, tell the owner the sentence it names; any `storage … --apply` runs only on the owner's word. | Owner's decision recorded here (R25) | `squadra doctor --resume` | [skills/ripresa.md](skills/ripresa.md) | U024 |
| R13 | **Known reds.** A test red on clean `main` is recorded once in `tests/known_reds.json` with reason, version and retiring condition; a task proof may use `--accept-known`; a new red always fails; the release proof stays strict. | Owner's decision recorded here (R25) | `tools/parallel_tests.py --accept-known` | [skills/rossi-noti.md](skills/rossi-noti.md) | U026 U027 U028 U029 U030 U031 U032 |
| R14 | **Stale windows.** Windows silent for two days or more are parked with `standby` only on the owner's word, after reconciling what they keep (coordinator role, tasks, reads, resources); never to hide open work. | Owner's decision recorded here (R25) | `squadra doctor --resume` | [skills/finestre-ferme.md](skills/finestre-ferme.md) | U033 U034 U035 U036 U037 U038 |
| R15 | **Checklist.** Run `squadra checklist --moment resume&#124;assign&#124;deliver&#124;push` at that moment; `fail` stops the dependent action, `unverifiable` is reported; `ready` grants no permission to publish. | Owner's decision recorded here (R25) | `squadra checklist` | [skills/checklist.md](skills/checklist.md) | U039 |
| R16 | **Round scope.** Once a round's scope is fixed by a decision, a finding becomes a task only if it blocks a close; the rest goes once into the deferral decision with owner, trigger and acceptance check. | Owner's decision recorded here (R25) | — | [skills/perimetro.md](skills/perimetro.md) | U040 U041 U042 U043 U044 U045 U046 |
| R17 | **Automatic lanes.** Automatic lanes: `task --paths GLOB` creates lanes for independent groups through balance; shared files go to `<PARENT>.L0`, owned by the coordinator; preview with `lanes`, never force a split. | Owner's decision recorded here (R25) | `squadra task --paths / lanes` | [work.md](work.md) | U047 |
| R18 | **Parallelism.** Parallelism is the value: assign independent work at once to every available window; separate trees, contract first and frozen, seam answers recorded as decisions; dependencies, reservations and proofs remain. | Owner's decision recorded here (R25) | — | [work.md](work.md) | U048 U049 U050 U055 |
| R19 | **Double final read.** The final read of a release is done together by a Claude reader and a Codex reader, neither an author of what it reads. | Owner's decision recorded here (R25) | — | [work.md](work.md) | U051 |
| R20 | **Read by the other model.** Codex writes and Claude reads; Claude writes and Codex reads: the read that counts comes from the other model, unless `--same-client-reason` records why. | Owner's decision recorded here (R25) | `task --reviewer, check, close` | [work.md](work.md) | U052 |
| R21 | **Coordinator integrates.** The coordinator builds only the integration and the release; lanes go to builders chosen with `squadra balance`, and says so when it must build a lane itself. | Owner's decision recorded here (R25) | — | [work.md](work.md) | U053 |
| R22 | **Coordinator mail.** The coordinator reads its own mail in the registry at every step. | Owner's decision recorded here (R25) | — | [work.md](work.md) | U054 |
| R23 | **Table always.** Every update to the owner ends with the `squadra board` table (In progress, To do, Done; Window &#124; Work &#124; State &#124; Time), one sentence before it and «You now: …» after it; estimates are marked, no invented percentages. | Owner's decision recorded here (R25) | `squadra board` | [work.md](work.md) | U056 |
| R24 | **Read before you cite.** Read before you cite: counts, ids and paths enter a report only from a command output read in this step; a wrong id is corrected in a new message. | Owner's decision recorded here (R25) | — | [work.md](work.md) | U057 |
| R25 | **Decision before a pinned test.** Record the decision (with the owner's words when they are the source) before changing a test that pins a requirement; never edit a test only to make it pass. | Owner's decision recorded here (R25) | — | [work.md](work.md) | U058 |
| R26 | **Lanes own files.** Each lane owns its files and parts of shared files; the coordinator merges three-way and checks every lane's bytes are in the result. | Owner's decision recorded here (R25) | — | [work.md](work.md) | U059 |
| R27 | **Cost before money.** Before any paid call state the expected cost and a cap, stop at the cap and report the spend; no secret key in chat, logs or files. | Owner's decision recorded here (R25) | — | [work.md](work.md) | U060 |
| R28 | **Final block.** Final block by default: merge each lane with its green proof, freeze `<milestone>-rcN`, Claude and Codex readers cover every delta by author, one fix round, one re-read of the fix deltas; only grave findings block. | Owner's decision recorded here (R25) | — | [work.md](work.md) | U061 |
| R29 | **Faster next time.** At every close write «Faster next time: …» from where the time went, and apply it to the next assignment. | Owner's decision recorded here (R25) | — | [work.md](work.md) | U062 |
| R30 | **Cut-off.** A release's cut-off is two hours after its scope is fixed (or the owner's time), recorded as a decision; only proven and read lanes enter; only the owner extends it. | Owner's decision recorded here (R25) | — | [work.md](work.md) | U063 U064 |
| R31 | **Scoped final reads.** Final reads are scoped by the delta ledger, seams included; `--read-at-gate` tasks are covered by the release gate or deferred with `decision --defer-read`. | Owner's decision recorded here (R25) | `task --release-gate, decision --defer-read` | [work.md](work.md) | U065 |
| R32 | **Release proof.** With `tools/parallel_tests.py` the release proof is the whole suite, collected equal to executed. | Owner's decision recorded here (R25) | — | [work.md](work.md) | U066 |
| R33 | **Stalled reads.** A delivery without ACK for 15 minutes moves to a free non-author reader; a reviewer pool does it by itself. | Owner's decision recorded here (R25) | `task --reviewer (pool)` | [work.md](work.md) | U067 |
| R34 | **Fix rounds.** A fix round fixes only the problem: bounded fix tasks from the frozen candidate, red-to-green evidence, whole-suite proof, one re-read of the fix deltas. | Owner's decision recorded here (R25) | — | [work.md](work.md) | U068 U069 U070 U071 U072 U073 |
| R35 | **What it is.** Squadra is a shared registry with roles and proofs bound to code; explain to a newcomer what is done, by whom, at which step and who waits, with identifiers under «Technical details». | Owner's decision recorded here (R25) | — | [start.md](start.md) | U074 U075 |
| R36 | **Announce.** Announce the skill, read the mandate and the owner's instructions, keep given authorizations; an accepted review never authorizes publishing. | Owner's decision recorded here (R25) | — | [start.md](start.md) | U076 |
| R37 | **Before assigning.** Before assigning read `status`, `doctor --resume`, tasks, the latest delivery and mail; every window without a native queue arms `inbox --follow` on all its names and re-arms it on expiry. | Owner's decision recorded here (R25) | `squadra inbox --follow` | [start.md](start.md) | U077 |
| R38 | **Durable registry.** A new squad uses a durable registry folder outside `/tmp`. | Owner's decision recorded here (R25) | — | [start.md](start.md) | U078 |
| R39 | **Identity and role.** Identity (the real window or session) is not a role; two roles share one identity; an alias creates no independence. | Owner's decision recorded here (R25) | — | [start.md](start.md) | U079 |
| R40 | **Squad size.** Size the squad to the work: coordinator, the builders needed, a non-author reviewer; do not invent work. | Owner's decision recorded here (R25) | — | [start.md](start.md) | U080 |
| R41 | **Task contents.** Every task has scope, Git base, dependencies and the agreed proof; queue delivery is not an ACK: the recipient confirms before starting. | Owner's decision recorded here (R25) | `squadra ack` | [start.md](start.md) | U081 |
| R42 | **Launcher and formats.** The command is `squadra`, else `python3 <skill-folder>/scripts/squadra.py`; one `--root` for every window; upgrade every window before a new journal format. | Owner's decision recorded here (R25) | — | [start.md](start.md) | U082 |
| R43 | **Forecast.** At task creation, briefing and start read the printed forecast; follow its route or record why not in a decision. | Owner's decision recorded here (R25) | `squadra forecast` | [skills/previsione.md](skills/previsione.md) | U083 |
| R44 | **Balance.** At task creation, briefing and start read the short balance printed after the forecast; follow it or record why not; an author is never a reader candidate. | Owner's decision recorded here (R25) | `squadra balance` | [skills/bilanciamento.md](skills/bilanciamento.md) | U084 |
| R45 | **Triage.** At creation, every finding and every scope change apply the five IT/EN triage questions: full path, fix now with proof, or one code read; no pattern or score decides. | Owner's decision recorded here (R25) | — | [work.md](work.md) | U085 U086 |
| R46 | **Only grave blocks.** Only grave findings block; others are fixed with proof and a non-author check, or deferred with assignee, trigger and acceptance check. | Owner's decision recorded here (R25) | — | [work.md](work.md) | U087 |
| R47 | **Reader chosen first.** Choose the reader at once with `task --reviewer`: available, not an author, of the other model; after the ACK of the handoff from `deliver` it reads without another go. | Owner's decision recorded here (R25) | `squadra task --reviewer, deliver` | [work.md](work.md) | U088 |
| R48 | **Proven capability.** Before assigning a browser or an installation ask for a receipt of a probe of the real capability; without it the step is not executed. | Owner's decision recorded here (R25) | — | [work.md](work.md) | U089 |
| R49 | **Skills per task.** Choose skills per task, recording path and hash; no council, audit or full suite only because they exist. | Owner's decision recorded here (R25) | — | [work.md](work.md) | U090 |
| R50 | **Cost follows the change.** The cost follows the change and is said first: state the minutes; reads and full suites only when asked or due; a small change gets at most one check. | Owner's decision recorded here (R25) | — | [work.md](work.md) | U091 |
| R51 | **Budgets.** Budgets are fixed before the cycle (3 plan reads, 1 build + 2 fixes, 2 code reads); at the limit keep candidate and findings; only the owner extends with `decision --extend-budget`. | Owner's decision recorded here (R25) | `squadra decision --extend-budget` | [work.md](work.md) | U092 |
| R52 | **Light route.** `task --route light` (light-v1: at most 80 lines in 4 files, 16 KiB) for small reversible changes; outside the limits or after a rejected read, `route --full`. | Owner's decision recorded here (R25) | `squadra task --route light, route --full` | [work.md](work.md) | U093 |
| R53 | **Bound proof and delivery.** The builder runs `prove` on frozen code and delivers with `deliver`; a source change invalidates the proof; review and close bind the delivered copy. | Owner's decision recorded here (R25) | `squadra prove, deliver, advance` | [work.md](work.md) | U094 |
| R54 | **Requirements.** Explicit requirements use `--requirements-file`; proof and read each declare `--covers ID`; `check` and `close` refuse missing coverage. | Owner's decision recorded here (R25) | `squadra check, close` | [work.md](work.md) | U095 |
| R55 | **Reviewer.** The reviewer reads diff and contracts, keeps probes in isolated copies, separates product failures from instrument errors and never fixes the candidate. | Owner's decision recorded here (R25) | — | [work.md](work.md) | U096 |
| R56 | **Completed turns.** A runner exit 0 without a completed turn, coverage or valid answer fails; `changes-required` is not acceptance. | Owner's decision recorded here (R25) | `squadra run` | [work.md](work.md) | U097 |
| R57 | **Check then close.** The coordinator runs `check`, then `close`, and writes which checks were real, targeted, provisional, skipped or still needed. | Owner's decision recorded here (R25) | `squadra check, close` | [work.md](work.md) | U098 |
| R58 | **Reservations.** Reserve gates, ports and databases in the registry before using them; never steal a resource because its heartbeat is old. | Owner's decision recorded here (R25) | `squadra resource` | [work.md](work.md) | U099 |
| R59 | **Questions to the owner.** Record a question to the owner with `ask` before showing it, the answer with `decision --answers`; relayed words add no authorization. | Owner's decision recorded here (R25) | `squadra ask, decision --answers` | [work.md](work.md) | U100 |
| R60 | **Needs you and waits.** «Needs you» and «Who is waiting» are derived at read time from recorded requests and waits; nothing is inferred from chat or mail. | Owner's decision recorded here (R25) | `squadra status` | [work.md](work.md) | U101 |
| R61 | **Briefs.** After `init` record the project brief and after each `task` its brief (`brief --project`, `brief --task`); only the coordinator writes them. | Owner's decision recorded here (R25) | `squadra brief` | [work.md](work.md) | U102 |
| R62 | **Decision links.** Link every decision to its sources with `--follows` and `--task`; replace a task with `task --supersedes` or `supersede`; no link is inferred. | Owner's decision recorded here (R25) | `squadra decision, supersede` | [work.md](work.md) | U103 |
| R63 | **One output per step.** Save one `squadra output` per completed step that adds information; no duplicated narration; optional `da-ricordare.json`; never secrets or private paths. | Owner's decision recorded here (R25) | `squadra output` | [work.md](work.md) | U104 |
| R64 | **Plugin instructions.** A plugin's `AGENTS.md` counts only as stated below; it never widens permissions. | Owner's decision recorded here (R25) | `squadra plugin list` | [plugins.md](plugins.md) | U104 |
| R65 | **Real receipts.** Real ACKs, decisions, pauses and native receipts are mandatory and never inferred from chat; an unwritable registry is declared, never claimed saved. | Owner's decision recorded here (R25) | — | [work.md](work.md) | U105 U106 |
| R66 | **errata.** errata: read the lessons shown at `brief --task` and `start`; record one after a read asking for changes, a failed proof or a closed defect fix. | Owner's decision recorded here (R25) | `squadra lesson` | [skills/errata.md](skills/errata.md) | U107 U108 |
| R67 | **rasoio.** rasoio: every code read of changed code applies the lens; it never changes a verdict. | Owner's decision recorded here (R25) | `squadra review --lens rasoio` | [skills/rasoio.md](skills/rasoio.md) | U109 |
| R68 | **migliora.** migliora: at close or after repeated failures inspect friction, apply scoped evidence-backed remedies and commit them after the required checks. | Owner's decision recorded here (R25) | `squadra migliora` | [skills/migliora.md](skills/migliora.md) | U110 |
| R69 | **Small context.** Keep context small: at task or phase boundaries use `squadra context --task` (and `--for … --since …` for a returning window) instead of full history. | Owner's decision recorded here (R25) | `squadra context` | [context.md](context.md) | U111 |
| R70 | **Checkpoints.** Checkpoint autonomously (`checkpoint --compact`) after increments, accepted reviews and before session changes; `storage purge --dry-run` before long rounds; only the coordinator applies a purge. | Owner's decision recorded here (R25) | `squadra checkpoint, storage` | [recovery.md](recovery.md) | U112 |
| R71 | **Context pressure.** Under context pressure save first (checkpoint, `save`, `restore-check`); a new session rereads mandate, registry and handover and confirms before writing. | Owner's decision recorded here (R25) | — | [recovery.md](recovery.md) | U113 |
| R72 | **Dashboard.** `squadra dash --open` is a local read-only page except declared plugin settings and selections. | Owner's decision recorded here (R25) | `squadra dash` | [recovery.md](recovery.md) | U114 |
| R73 | **Selftest.** `squadra selftest` checks the protocol without model calls; it is not a benchmark of agents. | Owner's decision recorded here (R25) | `squadra selftest` | [recovery.md](recovery.md) | U115 |
| R74 | **Stop.** When the owner stops: `pause`, interrupt only owned processes, `save` to a durable folder, `restore-check` on a new one; a process without receipt stays uncertain. | Owner's decision recorded here (R25) | `squadra pause, save, restore-check` | [recovery.md](recovery.md) | U116 |
| R75 | **Save.** `save` captures registry, repositories, Git history, patches and untracked files; it promises no absence of secrets. | Owner's decision recorded here (R25) | `squadra save` | [recovery.md](recovery.md) | U117 |
| R76 | **Leaked artifact.** A secret recorded into an artifact: delete the file and record `decision --scrub-artifact SHA256 --reason`. | Owner's decision recorded here (R25) | `squadra decision --scrub-artifact` | [recovery.md](recovery.md) | U118 |
| R77 | **Resume.** Resume only on the owner's request, after reconciling `doctor --quick`, reservations, sources and messages; record `resume`. | Owner's decision recorded here (R25) | `squadra resume` | [recovery.md](recovery.md) | U119 |
| R78 | **Old packets.** Old packets import as unverified historical copies: no tasks, ACKs or acceptances. | Owner's decision recorded here (R25) | `squadra legacy` | [recovery.md](recovery.md) | U120 |
| R79 | **Defect fixes.** A standalone defect fix records `task --defect-fix` and runs `challenge` red on the base, green on the candidate. | Owner's decision recorded here (R25) | `squadra challenge, task --defect-fix` | [benchmark.md](benchmark.md) | U121 |
| R80 | **What reads verify.** Each registry read verifies only its stated scope (table below); `doctor` without `--quick` is the full audit; windows are not cryptographically authenticated. | Owner's decision recorded here (R25) | `squadra doctor` | [recovery.md](recovery.md) | U122 U123 U124 U125 U126 |
| R81 | **Replacing windows.** Replace exhausted windows with `replace` (preview `--dry-run`), keeping authors and budgets; `wait --cause 429&#124;outage` records a service limit. | Owner's decision recorded here (R25) | `squadra replace, wait` | [replacement.md](replacement.md) | U127 |
| R82 | **Handover.** Passing coordination to another seat follows the handover protocol. | Owner's decision recorded here (R25) | `squadra handover` | [handover.md](handover.md) | — (new in 3.15) |

## Former names

Texts moved from 3.14.0 keep their former names (for example «Rule 7»). They map to IDs as follows.

| 3.14.0 name | IDs |
|---|---|
| Rule 0 | R02–R09 |
| Fundamental rule (no overlap) | R10 |
| Resume rule | R11–R12 |
| Known-reds rule | R13 |
| Stale-windows rule | R14 |
| Checklist rule | R15 |
| Round-scope rule | R16 |
| Rule 1 | R17–R22 |
| Rule 2 | R23 |
| Rule 3 | R24 |
| Rule 4 | R25 |
| Rule 5 | R26 |
| Rule 6 | R27 |
| Rule 7 | R28 |
| Rule 8 | R29 |
| Rule 9 | R30–R33 |
| Rule 10 | R34 |

## Index of the 3.14.0 units

One line per rule-bearing unit of SKILL.md at 0e56ba3 (paragraph, bullet or table): number, the first 12
hex digits of the SHA-256 of its whitespace-normalised text, and the evidence its home must hold (command
tokens, `¶` first sentence, or `→` the page a pointer names). `tests/test_regole_indice.py` pins these bytes
and rebuilds them from Git when the history is present.

<!-- units:begin -->
```
U001 e9909f83ffcd ["¶This file is written in English for agents;"]
U002 fd55cf071e81 ["¶**The coordinator is a Claude window.** The first Claude window that invokes this skill for a piece of work takes the coordinator role."]
U003 002951c042cf ["¶A Codex window, or any client that cannot see and wake every window, does not take the role when it is called first:"]
U004 af27c791fd5e ["¶The Claude window that then takes the role introduces itself to every window, including the Codex window that sent the owner there, before assigning work."]
U005 a8d593c27287 ["transport --via manual"]
U006 2047ae6e7347 ["--project <project>", "cursor-agent", "init", "squadra census"]
U007 d22181dcb293 ["ping"]
U008 8e9f57b45ef4 ["--via manual", "codex --add-dir <registry>", "relayed_by", "send", "squadra transport --actor <coordinator> --message <ID> --via codex-queue", "transport --via codex-queue", "transport --via manual"]
U009 d51a084d953f ["census", "status"]
U010 39dcdde9259f ["--via codex-queue", "alive", "delivered: true", "ping --all", "ping --sweep", "settings"]
U011 2b781fb0f25c ["ping --to <window>", "standby --resume --reason ..."]
U012 dd749d749cb6 ["→references/start.md", "→references/work.md"]
U013 d640948c5c3c ["¶**The coordinator always checks that the windows it coordinates do not overlap**, so that their work and their commits never collide (owner, 2026-10-30)."]
U014 940f8f067172 ["cpu-heavy", "reassign", "task"]
U015 af8e597fc3b2 ["¶**One writer per tree and per branch.** Two windows never build in the same checkout or commit to the same branch;"]
U016 5c288e997953 ["--depends", "decision --task"]
U017 1aaabab0fee3 ["¶**During the work** the coordinator rechecks at every delivery, merge and mail that changes a scope;"]
U018 873fbd242b38 ["¶When the coordinator cannot see a window's tree or branch, the overlap check is **not done**:"]
U019 b11a902b1787 ["→references/skills/sovrapposizioni.md"]
U020 e3fcb6add073 ["squadra doctor --resume"]
U021 c09f16f8500a ["¶**A blocker stops the resume** (exit 1):"]
U022 b58505d3c45e ["¶**Notices are reconciled, not ignored**:"]
U023 9ea3c4b479a0 ["¶The report changes nothing:"]
U024 19d899585e32 ["--apply", "storage adopt", "storage compact", "storage purge", "storage.suggest"]
U025 ce251bafd15b ["→references/skills/ripresa.md"]
U026 098a2d0e4904 ["main", "tests/known_reds.json"]
U027 a0747b03d995 ["KNOWN RED", "NEW RED", "STALE KNOWN RED", "known_red", "new_red", "only_known_reds", "passed", "stale_known"]
U028 c1c341651c8b ["tools/parallel_tests.py --accept-known"]
U029 3ea9dda1f732 ["--accept-known"]
U030 cb7803984ace ["--accept-known"]
U031 5cbf1d6eec6f ["main"]
U032 cb450fcfc04b ["→references/skills/rossi-noti.md"]
U033 7c0ba20c02f3 ["¶**Windows that stopped answering are parked in stand-by, so the board, the composition advice and the waits show only the squad that is really there** (owner, 2"]
U034 85a0c21e2435 ["archivable", "keeps", "parked_with", "squadra doctor --resume", "standby"]
U035 25b7ffeefc96 ["ask", "decision --owner-words", "standby --reason \"<owner's words>\""]
U036 ceeafbfd0726 ["reassign"]
U037 a955695b37e0 ["ping", "silent_windows"]
U038 f1a6e1a934aa ["→references/skills/finestre-ferme.md"]
U039 f22eb4456f09 ["--actor", "--repo", "--task", "fail", "ready", "squadra checklist --moment resume|assign|deliver|push", "unverifiable"]
U040 56736e4aab2f ["¶**Once a round's scope is fixed by a recorded decision, a problem found during the round becomes a new task only if it blocks closing one of the round's tasks**"]
U041 862bdaf22da1 ["decision"]
U042 b450dfcc956a ["¶A finding that blocks a close (a red proof, a read asking for changes) is handled in that task's fix round (Rules 7 and 10), including the bounded fix tasks Rul"]
U043 1faf151044bd ["--follows decision:<scope decision>", "task"]
U044 1caf624cab66 ["¶Only the owner's word brings a deferred item into the current round;"]
U045 801615c4650e ["¶An instrument error (a lost lock, an overloaded machine, a shallow copy) is resolved where it happens and recorded as a disposition;"]
U046 a599d07f1a73 ["→references/skills/perimetro.md"]
U047 3c9754660711 ["--apply --actor COORD", "<PARENT>.L0", "lanes --task ID --paths GLOB", "task --lanes none", "task --paths GLOB"]
U048 121f345e7452 ["¶The squad's value is parallelism."]
U049 fac3721895ce ["¶Builds on separate trees, one builder per tree;"]
U050 032dbfb8d641 ["¶A large job is split by area after the shared contract (data format, interfaces) is written:"]
U051 a0f3b1da991c ["¶The final read of a release is done together by a Claude reader and a Codex reader, neither of them an author of what it reads."]
U052 9294a074b49b ["--same-client-reason", "check", "close", "task --reviewer"]
U053 fab92b197947 ["squadra balance"]
U054 c734f2cc0ff6 ["¶The coordinator reads its own mail in the registry at every step:"]
U055 545c29373efc ["¶Dependencies, reservations, budgets and reader independence remain:"]
U056 697eadf59313 ["squadra board"]
U057 9854a88a3fc4 ["¶**Rule 3 — read before you cite.** A count, a commit, task, message or proof id, or a file path enters a report only after it was read from a command's output i"]
U058 d9e06012b471 ["squadra decision"]
U059 6c41b8e164dd ["¶**Rule 5 — parallel lanes own their files.** Each lane is told which files, and which parts of a shared file, it may change."]
U060 39f23194cdff ["¶**Rule 6 — state the cost before spending money.** Before any paid call (model APIs, voice, images, cloud runs) the window states the expected cost and a cap, s"]
U061 677f813ada37 ["<milestone>-rcN"]
U062 1126ba247c17 ["¶**Rule 8 — faster next time.** At every close the coordinator reads where the task's time went (the dashboard measures waiting for the acknowledgement, building"]
U063 00921b8c57af ["¶**Rule 9 — the release leaves on time.** These are rules, not coordinator choices."]
U064 58ea05aa45be ["¶**Cut-off.** When a release's scope is fixed, its cut-off is set two hours later, unless the owner names a time, and recorded as a decision."]
U065 2ce7708d51a8 ["--read-at-gate", "--release-gate", "decision --defer-read … --gate …"]
U066 1656c10a44d3 ["tools/parallel_tests.py"]
U067 7011abebe2f1 ["task --reviewer"]
U068 76a6ba569d58 ["¶**Rule 10 — a fix round fixes only the problem.** When a read asks for a change:"]
U069 0e517007cafc ["¶gather the findings into one round of bounded fix tasks on branches from the frozen candidate, each carrying only its assigned changes;"]
U070 f46ef6f18d1f ["¶merge their proven deliveries and read the fix deltas in one non-author re-read block (Rule 7)."]
U071 2cb44f6c56a1 ["¶a fix covered by that block carries its normal proof and red-to-green evidence in the task output."]
U072 191b9dac8381 ["¶the proof is the parallel whole-suite runner when the repository has it:"]
U073 61738b2bbe07 ["¶tell the assigned block readers at once."]
U074 a98cb905f252 ["¶A squad with a shared registry, recognizable roles and proofs bound to the delivered code."]
U075 7883ff70f389 ["¶Whoever opens the dashboard may know neither the project nor this skill."]
U076 8341e0eb0b13 ["¶Announce the skill when you apply it."]
U077 e3af4a37fde2 ["--all", "--message ID", "--since", "--until-first", "coord", "doctor --resume", "inbox", "inbox --follow", "posta/exchange-YYYY-MM-DD.md", "status"]
U078 ac8a61ce9a98 ["/tmp"]
U079 f5b728e51b88 ["¶Distinguish **identity** (the actual window/session) from **role**."]
U080 a4563a0a4e8d ["¶Choose a squad sized to the work:"]
U081 21855851cf61 ["¶Every task contains scope, Git base, dependencies and the agreed proof command."]
U082 33ac76170e16 ["--root", "/plugin", "SQUADRA_ROOT", "python3 <skill-folder>/scripts/squadra.py", "reassign --task ID --reviewer READER --reason ...", "resource acquire --task ID", "resource status --name cpu-heavy", "review --amend RECEIPT", "squadra", "squadra …"]
U083 f51f86b42570 ["squadra forecast --task ID"]
U084 85ec76838f7f ["squadra balance", "squadra balance --task"]
U085 b51b13504500 ["→references/work.md", "→references/runtime.md"]
U086 02cdfa27a93b ["¶At creation, at every finding or change of scope use the [five IT/EN questions](references/work.md#triage-in-una-pagina)."]
U087 653fc956136a ["¶**Only grave findings block**, including missing mandatory security proofs."]
U088 e346907c7dd2 ["deliver", "task --reviewer"]
U089 1db70dbe6095 ["¶Before assigning a browser or an installation, ask the window for a receipt of a probe of the real capability."]
U090 b00f18d4a23d ["¶Skills are chosen **for each task**, recording path and hash."]
U091 5a781556e9bc ["output", "task --route light"]
U092 b72a806fe99b ["decision --extend-budget TASK --stage plan|code --by N --owner-words ... --channel ..."]
U093 cf50d310c13f ["route --full", "task --route light"]
U094 a87ad4be4420 ["advance", "deliver", "prove", "task --reviewer"]
U095 2339d0ea015b ["--covers ID", "--requirements-file", "check/close"]
U096 76f4d020c2ec ["¶The reviewer reads the diff and the contracts, chooses its own probes when needed and keeps originals, positive controls and mutants in isolated copies."]
U097 9af4803ec8bd ["changes-required"]
U098 75d3d7a204e8 ["check", "close"]
U099 6b8863fae578 ["¶Reserve gates/ports/databases in the registry before using them and also consult the project's reservations."]
U100 47b0ab1b2e10 ["--withdraws", "ask", "decision --answers", "relayed", "typed-here"]
U101 98c994e592ab ["output.next", "squadra export", "stato.md", "status", "waits(state, now, settings)"]
U102 a92cf58bd44a ["## Glossario", "## Glossary", "## Passi", "## Steps", "brief --project --file <text>", "brief --task <ID> --file <text>", "init", "task"]
U103 6b9838f329bb ["--answers", "--channel typed-here", "--follows ask:<id>", "--follows decision:<id>", "--owner-words", "--task <ID>", "--withdraws", "decision --id <name>", "relayed", "supersede --task <OLD> --by <NEW> --reason \"…\"", "task --supersedes <ID>"]
U104 ecc6a697b61b ["$HOME/.squadra/plugins/<name>/AGENTS.md", "da-ricordare.json", "files: verified", "form: da-ricordare/1", "instructions: valid", "squadra output", "squadra plugin list"]
U105 754768d6399c ["squadra board"]
U106 34b04a3a5586 ["squadra board [--lang it|en]", "squadra export", "stato.md", "status"]
U107 0f622f048e10 ["¶These activate by themselves at their documented moments within the authorized project scope;"]
U108 8561ac6a6c6b ["brief --task", "lesson", "lesson --ineffective", "lesson.ineffective", "lesson.recall", "squadra lesson", "start"]
U109 1156e1efd207 ["review --lens rasoio --net-lines N"]
U110 7ca884977584 ["--defer-commit", "AGENTS.md", "migliora apply", "migliora commit"]
U111 32e36f6c7d36 ["context --task <ID> --for <WINDOW> --since <SEQ:HASH>", "squadra context --task <ID> --root <registry>"]
U112 4cc61244e751 ["--apply", "--reuse PREVIOUS", "checkpoint --actor WINDOW --compact --to <new-durable-folder>", "cp -R", "cp -c", "restore-check", "save", "storage adopt --scan DIR", "storage compact --dry-run", "storage keep", "storage purge --apply --actor COORD --reason TEXT", "storage purge --dry-run"]
U113 817f8610ea62 ["restore-check", "save"]
U114 65df74e8224a ["squadra dash --open"]
U115 5dc0cdd9deac ["squadra selftest"]
U116 ded4b97fda2b ["pause", "restore-check", "save"]
U117 1737d2dfb5e9 ["--repo", "checkpoint", "save"]
U118 ecc47543933b ["decision --scrub-artifact SHA256 --reason ..."]
U119 e7e14ddec86d ["doctor --quick", "resume"]
U120 8a6ce982beda ["¶Old packets can be imported as **unverified historical copies**;"]
U121 c8b07d3a5641 ["bench-example/run/score", "challenge", "challenge --register --task ... --reason ...", "challenge --task ... --output ...", "python-unittest-v1", "task --defect-fix"]
U122 6f1c828bb87f ["balance", "check", "checkpoint", "close", "doctor", "doctor --quick", "export", "restore-check", "save", "usage"]
U123 bc959bf8f7c2 ["--quick", "doctor", "doctor --repair"]
U124 db7008feb2f7 ["¶The other ordinary reads above do **not** reverify events before the anchor, untouched cache leaves, or whether the cached root equals a complete replay of the "]
U125 bdc0ef5512e6 ["checkpoint", "events.jsonl", "export", "format: 2", "save", "squadra export", "state.json"]
U126 646cae7ad97c ["-wal", "events.jsonl", "registry.sqlite3", "squadra export"]
U127 0eab20a96399 ["--dry-run", "--map", "replace --actor COORD --old OLD --new NEW --reason TEXT --session ID", "wait --actor AGENT --until TIME --cause 429|outage"]
```
<!-- units:end -->
