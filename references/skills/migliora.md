# Migliora — remove observed development friction

Apply this built-in skill automatically after a task closes, after repeated failed
checks, or when the owner asks to improve the development process. `close` prints
the task's observed failed or repeated proofs. `squadra migliora inspect --task ID`
recomputes the observations without changing the project. Counts describe recorded
runs, not proven waste or measured savings.

Read the applicable project instructions and recalled lessons before acting. Keep
the current assignment, ownership and permissions. First fix the tool or code that
causes a recurring problem. Add an instruction only when there is a concrete future
trigger and a check that distinguishes improvement from regression. A single incident
may justify a rule when its cause is demonstrated; repetition alone proves no cause.
Errata holds the failure and lesson; migliora implements and measures the remedy.

For each useful intervention, identify the evidence, trigger, action, check and
retirement condition. Prefer a few short local instructions to a growing checklist.
Check existing rules for overlap and conflicts. Never weaken required tests, change
requirements, amend another agent's work or override a project mandate automatically.
No actionable evidence means no change, which is a successful outcome.

When used standalone in a project without a Squadra registry, use that project's
existing logs, tests and decisions as evidence and its ordinary write/check/commit
workflow. Do not initialize a team registry solely for this skill. The helper below
is optional for projects already using Squadra.

## Applying a project instruction

The deterministic helper appends a managed block to `AGENTS.md`, preserving existing
text. It refuses pre-existing changes on that path and symlinks. A rule JSON contains:

```json
{
  "id": "reuse-proved-delivery",
  "evidence": ["ACTUAL-TASK-EVENT-OR-RUN-ID"],
  "trigger": "When only the assigned reader changes on a proved delivery",
  "action": "Use squadra reassign and preserve the proof and consumed review budget.",
  "check": ["python3", "-B", "-m", "unittest", "tests.test_reader_transfer"],
  "retire_when": "The project no longer uses assigned delivery readers."
}
```

Replace the example with evidence and a meaningful command that actually exist in
this project. Evidence must belong to the named task. The check runs without a shell,
with a timeout and a synthetic home; its native status and logs are retained.

```text
squadra migliora apply --actor BUILDER --task TASK --rule-file rule.json
```

Within an already authorized task this applies the instruction, runs its check and
automatically creates a local Git commit containing only `AGENTS.md`. It preserves
unrelated staged changes and runs the native Git hooks: migliora commits as the project
would, so the project's hooks run. This commit is the one exception; no other Squadra git
command runs repository config programs (hooks, fsmonitor, filters). No force, hook bypass, push,
publication, installation or approval of project memories is implied. Git identity
and signing must already work. A rejected commit remains reviewable; do not retry
blindly or disable the rejecting hook.

When the project requires independent review before committing, prepare the change:

```text
squadra migliora apply --actor BUILDER --task TASK --rule-file rule.json --defer-commit
# Obtain the review already required by the project, then:
squadra migliora commit --actor BUILDER --task TASK --rule-file rule.json
```

The commit step binds the checked instruction bytes and original Git head. The agent
must obtain any required independent review; the helper does not certify that a review
happened. Amendments, retirement and code/tool remedies follow the normal project
workflow. At the next matching trigger measure whether the intervention helped;
rewrite or remove an ineffective rule with evidence. Report observed time separately
from an estimate, and include maintenance cost. Do not invent a saving.

Automatic activation is an instruction to the participating agent, like errata and
rasoio. It is not a hidden daemon or a promise that every coding client invokes the
skill. Project startup instructions can explicitly request it for other clients.

Git hooks run against an isolated index. Refused/failed commits leave owned rule bytes unstaged. A hook-created commit with unexpected files is recorded with its actual hash, files, mismatch and logs, then requires reconciliation; it is never silently retried. Unrelated staged entries are preserved.
