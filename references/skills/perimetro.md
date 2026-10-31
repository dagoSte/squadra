# perimetro — the scope of a round

**A round closes what it started.** Every closed task tends to surface new problems; if
each one becomes a task at once, open tasks grow instead of closing. This guide is the
coordinator's procedure for the
[round-scope rule](../../SKILL.md#round-scope-rule--what-a-round-finds-waits-for-the-next-one).

## At the start of a round

Record the scope in one decision: the tasks the round will close.

```sh
squadra decision --actor COORD --id scope-ROUND --reason "Round scope: T1, T2, T3; nothing else until they close."
```

## During the round

For every new problem, ask one question: **does it block closing a task of this round?**

| Answer | What to do |
| --- | --- |
| Yes: a red proof, a read asking for changes, a missing capability | Handle it in that task's fix round (Rules 7 and 10) or as a recorded disposition |
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

- The rule bounds new tasks, not the fixes a task needs to close.
- It is not enforced by a command yet: `squadra checklist --moment assign` does not
  know about a scope decision. Until it does, the coordinator applies it at every `task`.
