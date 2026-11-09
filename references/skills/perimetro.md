# perimetro — the scope of a round

**A round closes what it started.** Every closed task tends to surface new problems; if
each one becomes a task at once, open tasks grow instead of closing. This guide is the
coordinator's procedure for the
[round-scope rule](../../SKILL.md#no-overlap-resume-and-round-scope) (R16).

## At the start of a round

Record the scope in one decision: the tasks the round will close.

```sh
squadra decision --actor COORD --id scope-ROUND --reason "Round scope: T1, T2, T3; nothing else until they close."
```

## During the round

For every new problem, ask one question: **does it block closing a task of this round?**

| Answer | What to do |
| --- | --- |
| Yes: a red proof, a read asking for changes, a missing capability | Handle it in that task's fix round (Rules 7 and 10), with the bounded fix tasks Rule 10 requires and only their assigned changes, or as a recorded disposition |
| It is an instrument error (lost lock, overloaded machine, shallow copy) | Fix it where it happens and record the disposition; no new task |
| No | Add it to the round's deferral decision, once |

The deferral decision names, for each item, the owner, the trigger and the acceptance
check, and follows the scope decision:

```sh
squadra decision --actor COORD --id defer-ROUND --follows decision:scope-ROUND \
  --reason "Deferred: (1) … owner …, trigger …, acceptance …"
```

Only the owner's word brings a deferred item into the current round. Record it as a
decision that follows the scope decision, with the owner's words.

## At the next kickoff

Open one task per deferred item, with its owner, trigger and acceptance check, before
anything new. A deferred item is never dropped silently: it is opened or explicitly
withdrawn by a decision.

## Limits

- The rule bounds new scope, not the fixes a task needs to close: Rule 10's bounded fix
  tasks are part of the round.
- It is advisory, not enforced: `squadra checklist --moment assign` reads the latest
  decision whose reason starts with exactly `Round scope:`. A task created after it whose
  ID is not named in that reason as a whole token gets `round_scope` = `unverifiable`,
  never `fail`, because the registry cannot tell the owner's word or a blocking fix from
  prose. The coordinator still reconciles it at every `task`.

## Rules moved from the core (SKILL.md 3.14.0)

The core keeps one line per rule; this is the full text, unchanged except for relative links.

### Round-scope rule — what a round finds waits for the next one

**Once a round's scope is fixed by a recorded decision, a problem found during the round
becomes a new task only if it blocks closing one of the round's tasks** (owner,
2026-10-31). Everything else goes into the round's deferral decision with an owner, a
trigger and an acceptance check, and is opened as a task at the next round's kickoff.
- A finding that blocks a close (a red proof, a read asking for changes) is handled in
  that task's fix round (Rules 7 and 10), including the bounded fix tasks Rule 10
  requires; those carry only the assigned changes and never unrelated scope.
- A finding that does not block is recorded once, in the deferral decision, linked with
  `--follows decision:<scope decision>`. No new `task` is created for it in this round.
- Only the owner's word brings a deferred item into the current round; record it as a
  decision that follows the scope decision.
- An instrument error (a lost lock, an overloaded machine, a shallow copy) is resolved
  where it happens and recorded as a disposition; it does not widen the round either.
- Fix the scope with `decision` when the round starts: the tasks it closes, nothing else.

