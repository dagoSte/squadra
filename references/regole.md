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
| R80 | **What reads verify.** Each registry read verifies only its stated scope (table below); `doctor` without `--quick` is the full audit; windows are not cryptographically authenticated. An older `registry.sqlite3`, or one that lost its `-wal`, still verifies while missing its tail: the next projection refresh refuses, `squadra export` does not check; continuity needs backups to another destination, and nothing promises «never lose». | Owner's decision recorded here (R25) | `squadra doctor` | [recovery.md](recovery.md) | U122 U123 U124 U125 U126 |
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
hex digits of the SHA-256 of its whitespace-normalised text, and the evidence its homes must hold:
the first 10 hex digits of the SHA-256 of each clause (sentence, or sentence of a table cell), or `→` the page
a pointer names. `tests/test_regole_indice.py` pins these bytes and rebuilds them from Git when the history
is present.

<!-- units:begin -->
```
U001 e9909f83ffcd ["7c3f036f8b", "dc40ecb0dd"]
U002 fd55cf071e81 ["1e323a44f2", "07a34fa246"]
U003 002951c042cf ["c127e07733", "80fa142c02", "929a5e472b", "b4ac284bcd", "9f5a3dcb6b", "59c81e11b4"]
U004 af27c791fd5e ["b06fbb11b3"]
U005 a8d593c27287 ["16d9b1773e", "7d5cd49b0a", "ef67881c55", "3862f3b750"]
U006 2047ae6e7347 ["3180727039", "c36b0ca8d8", "71209965ba", "c357f29a85", "716ac594ab", "49c248ef1d", "3919f77e1d", "c8ca341e7a"]
U007 d22181dcb293 ["76f996812c", "ec425672ac", "85760b45f2", "93e3575e44"]
U008 8e9f57b45ef4 ["632f0bc93f", "539dc4411a", "3032cc76d5", "3ce5608d16", "2af34dd04b", "7dbe0157d5", "5b33ccdc7b", "f0c73b55a2", "5e58e60423", "0cfa8a7043"]
U009 d51a084d953f ["dcc071a229", "094d800986", "a9107fe383", "eceeff9076", "4bce51f262", "c736ff0063", "7231c90b01", "30582275a9", "4d5b599d37", "2583f14d81"]
U010 39dcdde9259f ["79b4a734d1", "8d4d34e25c", "cde5373a1d", "2b85c6d248", "26d39d1ce3", "cb930334a9", "b0ba51b566", "b717c7023e"]
U011 2b781fb0f25c ["bf32ffe711", "e3a383e258", "d42407757e", "55cf012868", "d6d2122495"]
U012 dd749d749cb6 ["→references/start.md", "→references/work.md"]
U013 d640948c5c3c ["aae320c8ab", "9ed0038063", "2a8cacab08"]
U014 940f8f067172 ["e45b858634", "ecb07e64ea"]
U015 af8e597fc3b2 ["5af01895c6", "68b2dd1ef7", "8e811f726e"]
U016 5c288e997953 ["40ae241df6", "4f6c019957", "01a8f47067", "83d9e5f0f0"]
U017 1aaabab0fee3 ["fe7486c58c", "e489837ee7", "045dfdd7f6"]
U018 873fbd242b38 ["9bb9b05bc1", "cdd7a3f753"]
U019 b11a902b1787 ["→references/skills/sovrapposizioni.md"]
U020 e3fcb6add073 ["1d9ad9ff04", "362cb2494e", "064e9c152c"]
U021 c09f16f8500a ["2b206b1c20", "616a014cd3", "f5b620cb50"]
U022 b58505d3c45e ["d040150192", "c7a3a61b8f"]
U023 9ea3c4b479a0 ["45fc6c1a5b", "27e8815d6c", "7a383a357c"]
U024 19d899585e32 ["bf657ce148", "6473cb93df", "cb36479b27", "3ea0ca7e42"]
U025 ce251bafd15b ["→references/skills/ripresa.md"]
U026 098a2d0e4904 ["d8682ca862", "4fd2dc7a6d"]
U027 a0747b03d995 ["63027df779", "e3bcf9432d", "9262c48cad"]
U028 c1c341651c8b ["2beff29b92", "aa402fd483", "c3ea0acea2"]
U029 3ea9dda1f732 ["afccffa943", "51881f97ec"]
U030 cb7803984ace ["1e0dd88793", "c1ed11bde1", "e476838e87"]
U031 5cbf1d6eec6f ["b779ab023d", "255e3092ad"]
U032 cb450fcfc04b ["→references/skills/rossi-noti.md"]
U033 7c0ba20c02f3 ["7c0ba20c02"]
U034 85a0c21e2435 ["3ebc3dadde", "665d4b8280", "d2d3fa0237", "88cf2b8097", "1395e46aab"]
U035 25b7ffeefc96 ["5727b79a9c", "8d0a25f84a"]
U036 ceeafbfd0726 ["eb2b349684", "325b1d641a", "6006b816b0"]
U037 a955695b37e0 ["91dd66419e", "1c9c31fd57", "3051ca79d5"]
U038 f1a6e1a934aa ["→references/skills/finestre-ferme.md"]
U039 f22eb4456f09 ["4508ae5b2f", "b1a37c0ee0", "4daf0e60b7", "dcf00f0e7f", "bdc3f52bef", "b2920043a2", "3407754ebd", "6f2c41d74d", "e812f66b84", "c50fe3d3d0", "316cd08cbb", "0707503375"]
U040 56736e4aab2f ["ee8731fa9c", "46909418cd"]
U041 862bdaf22da1 ["5d447f56e8", "78ddb44900"]
U042 b450dfcc956a ["b92f75941a", "fc3bf47c5b"]
U043 1faf151044bd ["90cd913e60", "a1b42c40e7"]
U044 1caf624cab66 ["8afa6e39e8", "a274885645"]
U045 801615c4650e ["07705e89b7", "dd4e6f3b16"]
U046 a599d07f1a73 ["→references/skills/perimetro.md"]
U047 3c9754660711 ["a0743b737d", "eb9c0c74c5", "df3e72a9fc", "f4c96e5487", "307d128b9b", "e46f8777b1", "5a80e206c0", "8618974657", "24af8ae301", "d519d56660", "88623e5df3"]
U048 121f345e7452 ["09d81669e6", "73f183751c", "15f3b82ae9"]
U049 fac3721895ce ["fc7620ca71", "808b97c415"]
U050 032dbfb8d641 ["4dba9386e9", "b948c4b7a3", "0c7f820fe2", "a4d4073693"]
U051 a0f3b1da991c ["d91a21d1fe"]
U052 9294a074b49b ["a8b43e5c34", "3c662ddf47", "0720fc5f5e", "016031a39f", "8651c6a428", "33e14fdd5c", "ff4799f75a", "8480ad4c40"]
U053 fab92b197947 ["10426cd7d5", "84624d1f23", "09a542f0e2"]
U054 c734f2cc0ff6 ["d474a69a15", "cbd4a37197"]
U055 545c29373efc ["01900f21b3", "0fad83faeb"]
U056 697eadf59313 ["d5ed602e25", "2c2afb36d6", "385eff8f00", "ad8575f5e6", "c5e81e3f0d", "49ade1808a", "2d8f07c48d", "5704733925", "4c74421773", "2fe1c2b710", "3d5045e36b", "cf9ecd060b", "ebf7c8b845"]
U057 9854a88a3fc4 ["41abfa42d0", "43b1098d35"]
U058 d9e06012b471 ["b83f34c70e", "2cf803d951"]
U059 6c41b8e164dd ["4e7a394541", "efebfc07fe", "fe44316f71"]
U060 39f23194cdff ["7fc770a924", "9ff0496daa"]
U061 677f813ada37 ["49f1049eb4", "c4fad812e8", "86bb23792e", "322149395a", "54bade5a1a", "9169242bc1", "8dc36f0eaa", "e7650eecd5", "c732f884f0", "3b1d592288", "ca89f4b4cd", "215cfe65d2", "ac13be5b68", "5dbf62930d", "a9fe2f9434", "2a512715ea", "5efe7a44f4", "130c02e646"]
U062 1126ba247c17 ["ce5e26cdef", "e795b77a0a"]
U063 00921b8c57af ["4672a945cc"]
U064 58ea05aa45be ["819e3a1c28", "53b8c94a31", "c705bfe97e", "34ad028342", "b06232a049"]
U065 2ce7708d51a8 ["7f3895ea88", "d673757e95", "efb1344649", "b419e878c3", "e166f44c11", "9b658e682f"]
U066 1656c10a44d3 ["7526ff3266"]
U067 7011abebe2f1 ["915d33746a", "c47b572a03", "30d5246e2d"]
U068 76a6ba569d58 ["8ed62c7916"]
U069 0e517007cafc ["3c97d5758b"]
U070 f46ef6f18d1f ["f6e56029a4", "cbd4ae518b", "240d910518"]
U071 2cb44f6c56a1 ["086f38219a", "76190be240", "d564360f25"]
U072 191b9dac8381 ["1dacf38445", "5c386a9826", "029f6cd16c", "484ccdf826"]
U073 61738b2bbe07 ["bbd5991c74", "b5c73e337a", "8804752f93"]
U074 a98cb905f252 ["a0083a5f34", "7c689c1248", "c4e0d99ca9", "e7c4679240", "88d9b33a81", "9d475d9102"]
U075 7883ff70f389 ["640a7fdea0", "6c35f509bd", "ef84552bc0", "df91a6ea4e", "9391388bb1", "c88bb7ca48", "834614bc27", "41994b5040", "5a1b929860", "1e34808035", "8ae05e02ba"]
U076 8341e0eb0b13 ["e3f14b2ce5", "d94aa6b975", "502ff6a5fb", "85f98d9649"]
U077 e3af4a37fde2 ["293078546d", "9300feb5be", "94a159af49", "665e907ec7", "e0464f0d64", "0b5232b78a", "d43d2018d0", "0b82c99fda", "2482e92bab", "b1d06b354f", "4ed60dc4d1", "366e2ca934", "7c57bd5f71", "9bb6541daa", "faf0b844d6", "ac607a5cca", "b9f846645e", "d3ab293ea2", "c58679537f", "5157be75d2", "19bd950f0d"]
U078 ac8a61ce9a98 ["26c47e87ee", "c956697ec3"]
U079 f5b728e51b88 ["6c6e5a45ca", "81b43ba14f", "d999bd8c7d"]
U080 a4563a0a4e8d ["a43d58128e", "87826c6d7a", "b262c6c88c", "96bcb421e0", "774f44a25b"]
U081 21855851cf61 ["3c2ebdc262", "efb71c5653", "85574cf0dd", "59d146e0c0"]
U082 33ac76170e16 ["27597102c6", "3d6ecda036", "df399324af", "630ad63d87", "09f4d76bfe", "bfb1431be4", "eb3250c8b2", "af62333453", "271028767b", "1af3db7acc", "7bec50d6ef", "aa09204bbb", "5233acf995", "5b79e272f1", "b62dfcd35c"]
U083 f51f86b42570 ["fb7c2825df", "ddecc8fa97", "18fafa3b08", "d0c96ef565", "5880be0187", "1f91577307"]
U084 85ec76838f7f ["ca3209f4e6", "c1b85e45da", "4e5a6ab183", "fd4992f97c", "3ed35c3bd6", "552bb235d2", "6a77f305d9", "673f5b7375", "b8c4e6eaa7"]
U085 b51b13504500 ["→references/work.md", "→references/runtime.md"]
U086 02cdfa27a93b ["38009217c7", "8f03039394", "e241608981", "6b73bc3120", "355e2868f4", "3b2da777d5", "c2e0a030f3", "e88d3c1acf", "cef443a5a4"]
U087 653fc956136a ["bd6154ac15", "34b6906a35", "01c156d4f7", "b9f3ff439b", "8cbc354c6c", "c77607519d"]
U088 e346907c7dd2 ["3974eec907", "b65abf73b3", "34ffe2015f", "a2ec984d2d", "12f71818a9", "4648a3e3d8", "ea1f207cd4"]
U089 1db70dbe6095 ["e3d32446a1", "c9e192f25b", "64a8105dae", "875ac3ed4a", "37d3e8a672", "5c7a99cbc7", "69842bdd54"]
U090 b00f18d4a23d ["74010b7190", "db12ffdbfd"]
U091 5a781556e9bc ["5f18ef7d8f", "fa6a2dbbed", "c6cd1a2b7a", "0d6d4d4310", "0c31f25512", "9aab304143"]
U092 b72a806fe99b ["04dd27fc88", "78cd4a8a41", "c9606f1d0b", "4449437014", "53817820dc", "4c4904be4e", "390e0e6b44", "7979d240f9", "3de678a604", "81c6c7e160"]
U093 cf50d310c13f ["1e0dfa969e", "484e5c1b12", "886a8dae08", "d837e36c8d", "e38194c657", "f89252977a", "8a827fd933", "d1a918f684", "1bbf9fea2f", "0aabf45bf5", "a5f1406f02", "c7856c08b4", "35a5a7eb9e", "169ba4ef71", "48d82d0f84"]
U094 a87ad4be4420 ["93409a9755", "48ecf0e168", "b02d740167", "dc03b39bee", "bef45816fc", "19c5a2c85f", "ad10004622", "4a1671ff0c", "267e11bea9", "891bb2fe4a"]
U095 2339d0ea015b ["496ed95eda", "8fe87fa37c", "b801bf75fb", "1f838d1ce0", "6ed78ca3f5"]
U096 76f4d020c2ec ["df974efeaf", "7707977c43", "ac3f844a59"]
U097 9af4803ec8bd ["ebb4ca0e57", "39268543fa", "edb1174f3d"]
U098 75d3d7a204e8 ["edc2f10455", "6f47ac5a62", "6b7ee5d978"]
U099 6b8863fae578 ["2c221e5fa7", "d69ffba43e", "2d7fa089ff"]
U100 47b0ab1b2e10 ["365e462098", "ce22e6a2a4", "0d7a1a70ec", "10be21c145", "ca88bd7968", "55e439cc27"]
U101 98c994e592ab ["ccc66a8749", "37599a5ba1", "435487dbf2", "fb4f9f0837", "ea5ec86a87", "79253be1c2", "19be7c43af", "9ef93ad322", "32b12e89dd", "dea1f4946f", "8e35b9ec85", "999868f038", "48e6c83d8e"]
U102 a92cf58bd44a ["8e3663b886", "4bc2d8264f", "5dce8b34ea", "b2a7a847e3", "4e1e18ae16", "fd13f056ea", "365a35d6f9", "a8c3da64c1"]
U103 6b9838f329bb ["d8d21029c7", "fedec60efb", "06387c9c3e", "a2022bd346", "d4abcdab8f", "c68b15fa01", "4962861b8a", "7282e2d5fd", "2808ddb550", "4e3c5c3385"]
U104 ecc6a697b61b ["ea618d9228", "ae101e4749", "340290677d", "4c038bf5ac", "0d8a67684b", "54aa3796ab", "4a045dc587", "e357adc10a", "3e1eafa0c4", "cffd7d4968", "1d1c1a3e92", "4dca8aa2b4", "cd09684c78", "aec7b6a43b", "f9cedced04", "23ff9bedc2", "b398291f60", "585c1589bc", "c24dbef8c6", "27b074aa03", "62c4b08926", "0bafca1205", "6bb061d9dd", "a3ac8af985"]
U105 754768d6399c ["a702e56ace", "d50f8c08d1", "51ebef6039", "e23a968da8", "0ca1f77b19"]
U106 34b04a3a5586 ["34b04a3a55"]
U107 0f622f048e10 ["336bad690f", "b9747dc679", "c9e1b50431", "91083bd247"]
U108 8561ac6a6c6b ["fe8134e602", "ff4395df79", "f8c4c447fc", "7a5e5fe98d", "af7fc610cb", "29af908b52"]
U109 1156e1efd207 ["4e281c67ac", "bf792a0c02", "18b341a173", "a36faf9b38", "015ccbbf63"]
U110 7ca884977584 ["68235da6f1", "3ab66b97a6", "978e3e3ee4", "8a96aa4644", "5f9e8db30d", "093caaadc4", "9f00a5faae"]
U111 32e36f6c7d36 ["b13ecec8e7", "18a092980d", "a6134ebb26", "c16beaffd3", "ef808db259", "df4c195173", "53d9d67c31", "94f95fe8bd", "270224c5ab", "610d3da27d", "21f3d8e10a"]
U112 4cc61244e751 ["09cdf5e887", "6a28c2e8de", "d8e8516e6b", "ec65ae7501", "86a5019550", "19a0fc32fb", "6f695829a4", "e89b589884", "51b8203023", "b19037d58b", "3828d3863f", "94891c3d94", "ca2b6e4157", "db1c2a3224", "89beb98fcf"]
U113 817f8610ea62 ["f5f168e6d6", "01dd367752", "b9bbb2d5bd", "b60a9965e5", "91b637b1d7", "24a364e0ce", "8deee316d4", "57385157eb", "f08aeb17ad", "a6585784df"]
U114 65df74e8224a ["fb83b20f61", "bb15af2a0d", "82cf4c6916", "40f5b89139", "8a2ca11ae0", "f002fa3e90", "cbfe67725d"]
U115 5dc0cdd9deac ["46d436bdfa", "9ba7f5e966", "700a352f5b", "f1ea7c3ff8"]
U116 ded4b97fda2b ["9150a5a513", "84df16fd29", "a249d21677"]
U117 1737d2dfb5e9 ["273cf20459", "5b899dccbc", "dbc0ac17ef", "1bb9592a5b", "92c4dd0066"]
U118 ecc47543933b ["3a566fd8c6", "e4bf210f6a", "0ecab22b9d"]
U119 e7e14ddec86d ["7f7bef480a", "4ccdb2353b"]
U120 8a6ce982beda ["f68ff1ecf9", "a4666ecb05", "d10cb49901"]
U121 c8b07d3a5641 ["6684f9e306", "29cf73dc15", "0a9520145c", "75d9964117", "3e47f5af32", "f5eaf51ced", "258600543a", "7804452d16", "aad0e54cfb", "ef8bdd701a", "2e52ef0734", "e516f597ae", "1217a763d7", "c7186ffe17", "42dcfa8723", "c0b943f826", "5f52aa85d4", "3ef3ce322b", "189b89d734", "737fdcfd55", "06b8323a51"]
U122 6f1c828bb87f ["aa950e5d47", "593952dbd8", "c3b744fbdf", "cc20416297", "bfadb450dc", "f57c1483a0", "e167da14f3", "0b174873ec", "cba62c7f16", "239cb05c77", "11d7723833", "ed51e2c629", "7394e08915", "81e1df8de9", "fcfce9b067", "b2eaffc652", "27b832493a"]
U123 bc959bf8f7c2 ["515359afae", "649f402ffa", "fc0773944a", "d43ea31d36", "a454c83d92", "096f6604c0"]
U124 db7008feb2f7 ["473e749202", "e7a625385a", "12f3a9481a", "a5498a75fc", "80f1c37dde"]
U125 bdc0ef5512e6 ["a3b57e972f", "2d6cd3835e", "864c7375b1", "d6e8bce429", "706c2dd76f", "eabd62d0dc", "73110a6c97", "4359624e1b", "dfa9b62951", "1d6448b7f6", "7731f28d02", "2f4217e94b", "71bf4a216f", "30830e8809"]
U126 646cae7ad97c ["b50afadfb9", "882a034c2b", "13450eb1ab", "c5c4823488", "3d42a9fedf", "446544f2d4", "66efe06632", "9b315ac500", "b9bdb4a005"]
U127 0eab20a96399 ["181dcb8c3a", "7efc842019", "dae0aa1a28", "c9b7f7e6e6", "e5a8abf662", "7f91fec2cc", "424bcb8ef8", "bceaef375e", "44a0baad29"]
```
<!-- units:end -->
