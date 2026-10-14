# Previsione — plan from observed work

Use this system skill when creating, briefing or starting a task, and when asked
to compare an open task with completed work. The CLI automatically prints the
forecast at those three moments. Read it before choosing the next step.

```sh
squadra forecast --task TASK --root REGISTRY
```

The command verifies an existing registry without appending events, exporting
projections or changing the task. SQLite may create an empty WAL and a shared-memory
sidecar on a read-only connection; these are not new registry events. A missing
registry is not created. Unknown tasks fail with a native nonzero result.

## Read the comparison

At least three complete, non-superseded closed tasks in this registry must have
the same route, changed-file band (1–5, 6–15 or 16+) and a shared file or non-root
parent directory. Missing historical repositories, bases, snapshots or timing
evidence exclude a sample with a stated reason.

Historical file scopes use the delivered snapshot against the original Git base.
An open task uses its recorded candidate, or a labelled working-tree measurement.
A clean new task has unknown planned changes, not a zero-size comparable scope.
The working-tree reader has a 15-second limit; an unavailable or timed-out scope
produces no estimate. Reading base objects has a 10-second limit per Git command.

- Inspect selected IDs, overlapping paths, source fingerprints and exclusions.
- Read median elapsed building and code-reading minutes. Plan-review waits and
  correction intervals remain separate. These are not active working hours.
- The changes-required numerator and denominator count code verdicts, not tasks.
- Insufficient history or scope means no duration or percentage to quote.

The coordinator follows the suggested route unless it records a reason to deviate
with an ordinary decision linked to the task. The advice retains the registered
route when supported; otherwise it calls for full-route/coordinator judgment.
It never overrides triage, protected paths, light eligibility, independent review
or existing permissions. Actual changes use the existing authorized commands.

The forecast writes no activation event. The page says **Computed on demand**,
with no historical counter, first/last activation or sparkline for this skill.
An empty history is not evidence of zero executions.

## Release audit record

The dashboard reads `assets/skill-audits.json`, never a scanner or network service.
The coordinator supplies the real record at integration; fixtures are not scans.
The JSON object has `schema: 1` and `records`, one record per audited skill.
Each record contains `skill`, `scanner: "cisco-ai-skill-scanner"`, `version: "2.2.2"`,
`scanned_at` (timezone-aware ISO date), `result`, `findings` (nonnegative integer
`critical`, `high`, `medium`, `low`) and `files` mapping relative paths to SHA-256.
The exact policy is
`{"name":"balanced","behavioral":true,"osv":true,"fail_on_severity":"high"}`,
representing `--policy balanced --use-behavioral --use-osv --fail-on-severity high`.

Required files: `SKILL.md`, `scripts/registry.py`, `scripts/squadra.py`,
`scripts/dashboard.py`, `scripts/system_skills.py`, `assets/dashboard.html`, and
`references/skills/<skill>.md`. Errata additionally requires `scripts/errata.py`;
previsione additionally requires `scripts/forecast.py`. Rasoio's recording path
is the common registry/CLI code. The integration scan covers this exact set,
including the validator, UI and guide explaining the audited behavior.

Any critical/high finding means Fail; otherwise medium/low findings mean Warn;
zero findings means Pass. Contradictory results, missing/extra paths, changed
dependencies, missing/malformed records, unsafe paths, symlinks, unknown
providers/versions or unavailable files yield **Not audited for this version**.
Guide reads are regular-file-only and bounded to 64 KiB. The page renders text
and validated HTTPS links, never guide HTML, remote scripts or remote images.
