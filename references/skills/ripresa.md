# ripresa — the resume check

**Before assigning anything after a resume, the coordinator runs one check and reads it
whole:** `squadra doctor --resume --root <registry>`. It costs seconds and replaces the
manual search for the interpreter, the source tree and the live windows. This guide is
the procedure for the
[resume rule](../../SKILL.md#no-overlap-resume-and-round-scope) (R11).

## When

- at every resume of a squad, after a pause or a new session;
- when coordination passes to another window;
- before the first assignment of a new milestone.

## What it reports

`doctor --resume` runs the `--quick` registry checks and adds a `resume` object:

| Field | Meaning |
| --- | --- |
| `ready` | `false` when at least one blocker exists; the command then exits 1 |
| `blockers` | open tasks whose checkout is missing or not a Git checkout |
| `notices` | checkouts of active windows that are missing, commits after the last tag, commits behind upstream, silent windows |
| `python` | interpreter path and version actually running Squadra |
| `checkouts` | per checkout: tasks, windows, branch, short head, uncommitted tracked changes, last tag, commits since it, upstream distance |
| `idle_tasks` | tasks whose owner ACKed the assignment 30 minutes ago or more without a recorded start, plan, run, proof, candidate or delivery; a parent of open automatic lanes is excluded; read-only advice (also printed by `status`) |
| `silent_windows` | active seats whose identity has no answer or (re)registration of any seat for two days or more: `archivable`, what `keeps` them, and for archivable ones the `standby` `command` ([stale windows](finestre-ferme.md)) |

If the command refuses with «needs Python 3.12 or newer», run it with a newer interpreter:
`python3.13 <skill-folder>/scripts/squadra.py …` (look for `python3.12`, `python3.13` or
`python3.14` on PATH or in `/opt/homebrew/bin`).

## Acting on it

1. **Blockers first.** Restore the checkout from a `save` or re-clone it at the task's base;
   if the work is gone, supersede the task with a decision. Rerun until `ready` is `true`.
2. **Release drift.** The distance from upstream is from local refs: `git fetch` before
   trusting it. Commits after the last tag mean `main` already holds unreleased work: say
   so before choosing the next version.
3. **Silent windows.** `ping --to <window>` and wait for the ACK, or record their stand-by.
   Never reassign their work only because they are silent.
   **Idle tasks:** ping the owner first; if the inactivity is confirmed, apply the owner's
   handoff rule (for a stalled Codex builder, the build passes to Claude) by superseding
   the task; nothing is moved automatically.
4. **Overlaps.** Feed the checkouts and branches to the
   [overlap check](sovrapposizioni.md) before the first assignment.
5. Record what remains open in the resume output.

## Limits

- No fetch, no network: upstream distance is as of the last fetch.
- When Git does not answer (timeout, missing binary) the checkout shows `git: null` and a
  notice: it is not inspected, and it is never reported as missing or as not Git.
- It reads checkouts registered in the registry; work outside it is not seen.
- A listed window is only an observation: the check never changes availability,
  stops a process, archives a window or moves work.

## Rules moved from the core (SKILL.md 3.14.0)

The core keeps one line per rule; this is the full text, unchanged except for relative links.

### Resume rule — one readiness check before any assignment

- **A blocker stops the resume** (exit 1): an open task whose checkout is missing or not a
  Git checkout. Restore or re-clone it, or supersede the task, before assigning anything.
- **Notices are reconciled, not ignored**: ping silent windows or record their stand-by,
  fetch before trusting the release distance, and say which notices remain open.
- The report changes nothing: it never fetches, stops, reassigns or archives. Its
  checkouts feed the [overlap check](sovrapposizioni.md).
- **Storage notice.** When the report carries `storage.suggest` (checkpoints made before 3.13
  that are not registered, or more than 1 GB of frozen candidates), tell the owner, in the
  owner's language, the sentence it names ("Use squadra to compact the storage of the registry
  at <path>") and what it does: `storage adopt`, `storage compact` and `storage purge`, each
  previewed first. Run any `--apply` only on the owner's word; nothing is compacted at opening.

