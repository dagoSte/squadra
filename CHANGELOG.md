# Changelog

Every Squadra release, newest first. The README keeps the newest release and the three before it.


**3.11.2 — rules that check themselves: no overlaps, one-step resume, known reds, stale windows, checklist and round scope**

- **No overlap between windows.** The coordinator checks checkout, branch, owned files, base, reserved resources and expected commits before every assignment ([guide](references/skills/sovrapposizioni.md)).
- **One-step resume.** `doctor --resume` reports the interpreter, every checkout of open tasks and active windows, release drift and silent windows; a missing checkout of an open task blocks with exit 1.
- **Known reds of main.** `tests/known_reds.json` names tests already red on clean main; the parallel runner separates `KNOWN RED` from `NEW RED`, and `--accept-known` passes a task proof only on known reds. The release proof stays strict, and the list is empty again.
- **Stale windows.** `doctor --resume` marks silent windows `archivable` or says what `keeps` them, across every seat of an identity; only the owner's word parks them with `standby`.
- **Checklist.** `squadra checklist --moment resume|assign|deliver|push` checks the critical moments read-only: at push, an author or committer email never seen before fails.
- **Round scope.** Once a round's scope is fixed, what it finds waits for the next round unless it blocks a close.
- **Fixes.** `test_rf4a_upgrade` runs on a verified 5099f0c fixture; `test_challenge` no longer races Git maintenance.
- **Skill audits not refreshed.** The recorded scans in `assets/skill-audits.json` predate this release's changes to `SKILL.md` and `scripts/squadra.py`; the System skills page shows every skill as not audited (files changed) until a fresh scan.

**Watch the two-minute film of what's new in 3.10.2 — turn the sound on in the player:**

https://github.com/user-attachments/assets/6ea747a5-a993-4aa7-a58d-1b2b7f908720

**3.11.1 — replacing windows, access checks, one mailbox per window and re-entry context**

- **Replace closed windows in one step.** `replace` (or `--map`) hands tasks, reads, mail and resources from old windows to new ones in one recorded event, keeping authors, budgets, plan gates and the reservations of live runs. `--dry-run` previews it and reruns are safe; `census --unregistered` lists new sessions.
- **Service limits are visible.** `wait --until TIME --cause 429|outage` shows in status, board and dashboard that a window is waiting for its model service.
- **Permission check at entry.** `access probe` records, from inside the window, whether it can write the registry and its clone; `access status` shows verified, denied or not verified.
- **One mailbox per window.** `inbox` and its follower read every role of the same window and keep the recipient of each ACK; `ready` shows a reader waiting for a delivery.
- **Re-entry context.** `context --for WINDOW --since SEQ:HASH` shows what changed for one window and always keeps the current constraints, pending ACKs, budgets and proofs.
- **Fixes.** `transport` no longer silently re-delivers an older message under a reused ID; `save --repo` reports missing unselected repositories as warnings.
- Plugins: memoranda 1.0.10, Playwright 1.0.11, Sentry 0.1.8.

**3.11.0 — frozen delivery, reader transfers and migliora**

Refresh: dashboard startup releases its WAL read; JSON and transport replies are concise, and recovery supports recorded per-path name exceptions with clearer refusals.

- **Builders can continue after delivery.** A copy of the proved candidate stays in the registry, including uncommitted changes.
- **Change readers without rerunning the proof.** `reassign` preserves candidate, proofs and budgets and requires the new ACK.
- **Clearer mail and resources.** The inbox cursor is saved; `resource status` shows the holder and queue. Reservations can name their task.
- **Shorter commands.** Identical verdict retries do not duplicate reads; delivery and close take notes, task proofs accept direct argv, and `send` returns a short receipt.
- **Migliora.** An automatic skill for demonstrated friction: scoped rules, a check and local commits of owned files after the project’s required reviews.
- Plugins: memoranda 1.0.9, Playwright 1.0.10, Sentry 0.1.7.

**3.10.4 — a short private temporary folder for proofs, and Claude and Codex read each other's work**

- **A short private temporary folder for proofs.** Proofs, quick checks, `selftest` and offline benchmarks keep their temporary files in a short private folder outside the registry, so a project kept under a shared folder such as MAMP's `htdocs` no longer fails on temporary paths that are too long or that others can write.
- **Refusals that name the files.** A refused read names the files that changed since delivery.
- **Two models that check each other.** Codex writes and Claude reads, Claude writes and Codex reads: a reader of the same model is refused unless you record why. In the final block, the read that counts for each change is the one by the other model.
- **Advice on splitting your windows.** The coordinator tells you how to split your windows between the two models, for example that with 2 Claude windows and 1 Codex window the Codex window would read everything.
- Plugins: memoranda 1.0.8, Playwright 1.0.9, Sentry 0.1.6.

**3.10.3**

- **Final review block by default.** Builders deliver checked work; independent Claude and Codex readers review the combined candidate before release.
- **Continuity for long tasks.** `squadra context --task ID` prepares a bounded resume packet from the task and its evidence.
- **Usage scoped to the project.** Token totals start at each window's recorded registration in that project; account quota remains separate.

<details>
<summary><strong>3.10.3: workflow, context and usage details</strong></summary>

**3.10.3 — the final block is the default.** Agree the shared contract and seams
before building. Merge lanes on delivery with their own green proof, keep a delta
ledger, then freeze a candidate for Claude and Codex non-author reads of every
delta and seam. One fix round is followed by one re-read block of its deltas.
Only grave findings block; one decision records non-grave carry-overs for tasks
created at the next milestone's kickoff. Required runtime proofs bind the release
head. Per-lane reads remain an owner-selected option. This changes the skill's
workflow, not CLI behaviour or the registry format; active squads keep their
recorded mode until explicitly switched. [Workflow](references/work.md#final-block).

**Smaller context for long tasks.** `squadra context --task ID` prepares a bounded,
read-only resume packet so an agent can continue from task, scope and proof
references rather than full conversation/log dumps. The workflow uses native
Codex/Claude compaction and preserves existing gates. It neither clears provider
cache nor guarantees savings. [Context workflow](references/context.md).

**Usage correction — 20 October 2026.** Project totals now count tokens only after
each window's recorded registration in that project. Reusing an older session no
longer brings previous days into the project's charts or cost estimates. Intervals
crossing registration are excluded and marked partial. Session history is preserved;
account quota stays separate. [Scope and limits](references/usage.md#project-scope).

</details>

**3.10.2 — messages that always arrive, and a dashboard that keeps up with a working squad**

The coordinator can deliver every message to its window, the dashboard updates only what changed, and the search box finds past work by the words people wrote. The registry format does not change.

<details>
<summary><strong>3.10.2: improvements and dashboard measurements</strong></summary>

- **Install with one sentence.** Paste the sentence in [Installation](README.md#one-sentence) into your coding agent: it shows the plan, waits for your yes, installs and checks ([SETUP.md](SETUP.md)).
- **Small changes take minutes.** Before starting, the agent tells you how many minutes it needs. A small change is simply made, with at most one check. A read by another window or the whole test suite runs only when you or the coordinator ask for it; for an important change it is suggested, with its time.
- **The squad starts from Claude.** A Codex window called first no longer coordinates windows it cannot see or wake: it tells you which sentence to type in a Claude window, and that window introduces itself to every window, the Codex one included.
- **Messages always find their way.** The coordinator can deliver any message, including one written by a sandboxed Codex window, and every delivery attempt is recorded. A Claude window receives its own mail as it arrives with `squadra inbox --follow`. A message to a Codex window that nobody has delivered yet is listed first, as «to forward», with how long it has been waiting.
- **Waits that tell the truth.** The waits list only what is still open: a closed task's confirmations leave it, and a plan waiting for its reader shows up there.
- **Search.** The search box finds past work by the words people wrote, in any order and with or without accents, including decisions, questions and reviews. The dropdown shows the newest results first and ends with a ready-made question for your agent.
- **Newest first, everywhere.** Every list and task picker shows the latest items on top.
- **A dashboard that keeps up.** After a new event the page updates only what changed; an idle page costs almost nothing, and a search sends only its results.
- **Shorter answers.** `task`, `brief` and `start` print a short summary. Plain `inbox` shows the messages waiting for you plus the latest 10; `--all` shows everything.
- **A safer registry.** A new registry folder is ignored by Git, so it cannot be committed by accident. A slow write holds the other windows only briefly. `doctor` is read-only and reports a damaged cache; `doctor --repair` fixes it and `--quick` is a fast check. Commands explain errors in plain words. A file leaked into the registry can be removed with a recorded decision.
- **Save and restore you can rely on.** `save` works on large projects and on repositories that track `.env.example`; the way back to an older version works with either version; a save package restores after you open it, and stashed work comes back.
- **Usage and Balance measure every window registered as the guide shows** (`--session`), also on machines with many saved conversations.
- **Install anywhere.** The installer works under a shared-group umask and with a linked home folder. On Python older than 3.12 it refuses with one clear line.
- **English stays English.** An English workspace gets English in every command.
- **Joining and presence.** A new window can answer its first presence call, and the coordinator's own sweep keeps the coordinator active.
- **Waiting in line.** `resource acquire --wait SECONDS` keeps your place in line for a busy resource, such as the test gate, instead of failing until it is free: windows that wait with `--wait` get it in the order they asked, and nothing is recorded while they wait. `prove` checks before it starts that the run covers the task's requirements, so a long proof is never thrown away for a missing `--covers`.
- **One proof for both final reads.** `prove --also <task>` lets one run of the suite serve several tasks on the same frozen code, such as a release's Claude and Codex final reads; each task still checks its own requirements. Use it when every reader's Squadra lists `--also` in `squadra prove --help`; otherwise prove each task on its own.
- **Windows that are gone stop filling the waits.** Mail to a window you put in stand-by shows as one line per window, for example «12 messages to core-A on stand-by (window closed)», and comes back as soon as the window returns.
- **A registry that stays small.** The registry's journal file goes back to a small size after writes, even with a dashboard open; `doctor` reports its size and `doctor --repair` compacts it. Nothing is deleted.
- **The coordinator coordinates, and releases leave sooner.** The coordinator builds only the integration and the release, and gives the lanes to the builders. In 3.10.2 each lane had its own full read, with fixes read together at release; 3.10.3 makes the final block the default instead.
- Plugins: memoranda 1.0.7, Playwright 1.0.8, Sentry 0.1.5.

**Measured on our own squad.** Our coordinator ran the same script against the dashboard of published 3.10.1 and then of 3.10.2, on the same copy of this squad's registry (11,488 events), on an 8-core machine under moderate load (load average 5 to 11 during the runs): five updates, each after one new message, and three searches. Time is the dashboard's own processing time for one request. Here the dashboard was already open on the copy before its first write, so the first update paid one more full read (8.5 s): a copied or moved registry has its cache re-bound by its first write in the new place, and a dashboard already open there pays one full read on the next update. The table shows the updates after it.

| What we measured | 3.10.1 | 3.10.2 |
|---|---|---|
| Page update after a new message | 4.7–5.3 s, 8.2 MB | 0.17 s, 0.56 MB |
| A search | 3.1–6.6 s, 8.3 MB | 0.4 s, at most 0.13 MB |
| Page update when nothing changed | 0.02 s | about 0.001 s |

</details>

**3.10.1 — see what every AI window really spends, and where the next task should go**

A squad mixes Claude Code and Codex windows, and each provider shows you only its own usage. Squadra 3.10.1 puts all of them in one view, for every task: which window spent how many tokens, with which model, against which quota, and which window or model should take the next task. You can see why each piece of advice is given. It is computed on your computer from the session files the clients already keep; no text of the conversations is stored, shown or sent anywhere.

- **Balance (new system skill).** Advice on windows and models by load, quota and history, with the numbers each piece of advice rests on. It assigns nothing. The page says in plain words what it checks, today's values, and why there is no advice when there is none.
- **Usage totals stay stable while session files grow.** Saved calculations let a dashboard restart reuse earlier counts; a file that was rewritten is counted again, and a missing or damaged cache is rebuilt. If a folder on the cache's path is a symbolic link, the cache stays off and the Usage page says so: the figures stay correct, but every dashboard restart reads the session files again.
- **Commands that cost the windows fewer tokens.** Every command used to print the whole list of open waits into the window that ran it; now it prints a short answer, and `squadra status --all` prints the full list. `task`, `brief` and `start` add the forecast and a short balance, the first window for building and for reading with its reason; `squadra balance --task` prints the whole ranking (the figures are in the table below).
- **A registry that stays fast as the history grows.** A command now reads and writes what changed, not the whole history; on a copy of our 8,500-event registry, sending a message to another window went from 27 s to 3.2 s. Integrity checks still cover the whole chain at the moments listed in the guide.
- **A lighter dashboard** that downloads only what changed, and **animated charts** on the Usage and Balance pages.
- **Information messages** (`send --info`) that expect no reply no longer stay open as waits.
- Plugins: memoranda 1.0.6, Playwright 1.0.7, Sentry 0.1.4.

**Measured on our own squad.** Our coordinator took these figures on this squad's registry (about 9,980 events) after installing 3.10.1. The two «before» times come from the same commands timed on an earlier copy of the same registry, before the change. A missing or moved cache needs verified reconstruction on the first write; its cost depends on the registry and machine. Read-only commands use verified replay until a cache is available. The times below describe subsequent warm commands, not a guaranteed cold start.

| What we measured | Before | With 3.10.1 |
|---|---|---|
| «Where are we» (`squadra status`) | 8.6 s | 0.85 s |
| Saving an output (`squadra output`) | 10.7 s | 0.86 s |
| Answer printed by a command | 75–95 KB | about 2.2 KB; `task`, `brief` and `start` about 2.0 KB with 10 windows (synthetic squad), forecast and balance included |
| Dashboard refresh when nothing changed | 8 MB | 0.2 KB |
| Usage totals while windows work | went up and down | only grow (50 samples, none lower) |

**3.10.0**
- **Usage page: tokens spent by the models.** Squadra reads the session files Claude Code and Codex already keep on your computer and keeps only the token counts, with the model, time and session and message identifiers needed to count each message once; it never stores, shows or sends the text of the conversations. It shows them per window, day, model and task, with charts and suggestions per model. Codex quota is shown as measured, for each provider window; an estimated cost appears only if you enter your own prices. `squadra usage` prints the same data.
- Plugins: memoranda 1.0.5, Playwright 1.0.6, Sentry 0.1.3.

**3.9.1**
- **Interface fixes.** More room on the System skills page and between its blocks; bold text in skill descriptions shows as bold; the project goal and the reading help appear on the overview only; more badges at the top of this page.
- Plugins: memoranda 1.0.4, Playwright 1.0.5, Sentry 0.1.2.

**3.9.0**
- **System skills that activate on their own.** *errata* brings back the lessons the squad recorded after a failed proof, a read that asked for changes or a closed defect fix, when a new task touches the same files or words. *rasoio* is a lean-code lens for code reads that declares how many lines could go. *previsione* compares a new task with similar finished ones and suggests a route. errata and rasoio record each activation in the registry; previsione is computed on demand and records nothing.
- **A System skills page in the dashboard** lists the three skills with what they do, their recorded activity and the security scan result for their files.
- **Faster whole-suite proofs.** `tools/parallel_tests.py` runs the suite across processes and fails if any collected test did not run: about three times faster on our test machine.
- **Reviewer pool.** A task can name up to four readers; the first to confirm takes the read. Small light changes can be read once at the release gate, and they show as closed but not yet read until then.
- **Quick checks before proof and delivery.** A project can declare fast checks, such as mirrored files being identical, that run on the exact files before `prove` and `deliver`.
- **A lighter README.** Same sections, about half the reading; the details moved to the guides.
- **The release leaves on time.** A rule, not a choice: the cut-off is two hours after the scope is fixed; final reads look only at how the lanes were joined when each lane was already read; a read nobody picks up moves to a free reader.
- Plugins: memoranda 1.0.3, Playwright 1.0.4, Sentry 0.1.1.

**3.8.0**
- **Defect fixes show that their test fails without them.** Declare a fix with `task --defect-fix`; before it closes, `challenge` runs the agreed tests on the original and on the delivered code.
- **Sentry plugin (preview).** It brings sanitised error summaries into Squadra. FREE · Sponsored by memoranda. Tested with a synthetic connection only.
- **A friendlier Reads view.** It keeps the table's scroll position, shows cards on phones and loses the blank band; numbered plugin steps show one marker.
- Plugins: memoranda 1.0.2, Playwright 1.0.3, Sentry 0.1.0.
