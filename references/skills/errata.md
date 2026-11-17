# errata — the squad learns from its own reds

**A long project makes the same mistakes more than once, because nobody left a mark where
the next pass will happen.** errata leaves that mark in the registry: a lesson recorded
when a red happens, shown again when a task touches the same ground, and marked when it
did not prevent a repeat.

## When it activates in a squad

Each moment is automatic, and none needs the owner:

- **A task is briefed or started:** `brief --task` and `start` show the lessons whose
  trigger matches the task, at most five, best first. Showing them records a
  `lesson.recall` event. Read them and apply them in this task.
- **A red happens:** a code read that asks for changes, a failed proof, or the close of a
  defect fix. The command's output asks the agent who ran it to record a lesson, naming
  the task and the event. Record it at once with `squadra lesson` if you can name its
  trigger; if you cannot, say why in your output.
- **The same mistake comes back** after a lesson was shown: mark the lesson ineffective
  with that recall, then record a better lesson.

Every activation leaves a registry event: `lesson.recall`, `lesson`,
`lesson.ineffective` or `lesson.promote`. The System skills page counts them.

## What a lesson is

| Part | What it holds | Why it is needed |
|---|---|---|
| **text** | the rule, in the imperative: what to do differently next time | it is the only actionable part |
| **trigger** | file globs (`--path`) and/or words (`--word`) that say when it applies | a lesson without a trigger never comes back: it is a diary note |
| **source** | the task and the event that prove it: the read, the proof or the close | a lesson without evidence becomes an opinion read as a fact |
| **author** | the seat that recorded it | the registry records it |

```sh
squadra lesson --actor builder --task T7 --source-event 812 \
  --text "Bump the version in every mirror, then search for the old one" \
  --path "plugins/*/bridge/registry.py" --word version
```

The source must be one of this task's reds. `--any-source` cites another event of the
task instead, and the lesson records `"eligible": false`, so readers can tell a lesson
grounded in a red from one that is not.

## Writing a lesson that comes back

- **Record when you can say where it will bite next time.** If you cannot say where, you
  cannot write the trigger, and the lesson will never resurface.
- **Concrete, not generic.** "Be careful with versions" has never saved anyone. "Bump the
  version in every mirror, then search for the old one" can be done under pressure.
- **Triggers on observable signals:** the paths a task's scope names, and the words its
  title, scope, requirements or brief use. Paths weigh more than words.
- **One lesson per mistake.** If the same mistake has a lesson already, the recurrence
  belongs to that lesson: mark it ineffective rather than writing a twin.
- **Never secrets, personal data or private paths.** Registry events cannot be edited.

## States

The state is derived from events, never written by hand.

| State | Meaning |
|---|---|
| `new` | recorded, never shown |
| `active` | shown at least once, never marked ineffective |
| `ineffective` | the mistake came back after the lesson was shown |
| `retired` | no longer applies; out of the recall, kept in the history |

**`ineffective` accuses the lesson, not the project.** Its trigger did not fire when it
should have, or its text was too vague to follow in a hurry. Write a better lesson and
retire the old one with that reason. Ineffective lessons rank higher in the recall.

```sh
squadra lesson --actor reader --ineffective L0003 --recall 905 --reason "Same miss in T9"
squadra lesson --actor coord --retire L0003 --reason "Replaced by L0011"
squadra lesson --list
```

Only the coordinator or the lesson's author retires a lesson. Nothing is deleted: the
registry is append-only.

## A lesson that already exists

Recording a lesson runs the recall score of `brief` and `start` on the two triggers, both
ways: an active lesson (new, active or ineffective; never retired) whose glob or word meets
the new trigger is an overlap. Nothing is blocked: the lesson is recorded, and the command
names every overlap on stderr, where a short receipt cannot hide it, with the ways forward:

- the mistake came back: mark the old lesson `--ineffective LID --recall SEQ --reason`;
- the old lesson no longer applies: `--retire LID --reason`;
- the new lesson is a twin by mistake: retire the new one.

When you already know the overlap and want both, record with `--despite LID` for **every**
overlapping id and a `--reason`: no notice, and the `lesson` event keeps
`despite: {lessons, reason}`, so a reader sees a twin was chosen, not missed. A `--despite`
that does not name exactly the overlaps is refused (`lesson_despite_mismatch`) and writes
nothing. The result lists `overlaps` either way.

## Promoted lessons: the repository keeps them

A lesson that proved itself across tasks can leave the registry and live with the code,
where every future squad on that repository finds it.

```sh
squadra lesson --actor coord --promote L0004 --repo /path/to/repo \
  --reason "Recalled in T7 and T12, never ineffective"
```

- **Earned, not declared.** Allowed only when `lesson.recall` events showed the lesson in at
  least two distinct tasks, it was never marked ineffective and it is not retired.
- **One file, stable bytes.** `<repo>/lessons/promoted.json` holds `{"version": 1,
  "lessons": [...]}`, one entry per lesson sorted by id: `id`, `text`, `trigger` and
  `provenance` (`registry` name, source `task`, `lesson_event`, `promote_event`,
  `promote_hash`, `utc`). The hash ties the entry to the `lesson.promote` event, so the
  claim can be checked in the registry; no local path is written. Commit the file with the
  code.
- **Replace, do not pile up.** `--absorbs LID` (repeatable) removes lessons the new one
  replaces. Each must be retired first (`lesson_absorbed_not_retired`). Promoting the same
  lesson again with nothing to absorb answers `unchanged` and writes nothing.
- **Nothing foreign is overwritten.** A damaged file is refused
  (`lesson_promoted_file_invalid`) until a person repairs it; an id another registry
  promoted into the same file is refused (`lesson_id_taken`).

At `brief --task` and `start`, the promoted lessons of the task's repository that match by
the same score follow the registry's, marked `"promoted": true`, at most five. A lesson the
registry already showed (same id and text) is not repeated. Promoted lessons are not
registry recalls: they add no `lesson.recall` and cannot be marked ineffective; retire or
improve the registry lesson and promote again. A missing file changes nothing; an unreadable
or malformed one shows no promoted lesson and a warning, and `brief`/`start` go on.

## Limits

- The recall matches text and globs; it does not understand code. A lesson about a file
  the scope never names will not come back: write its trigger with the words the next task
  will use.
- At most five lessons are shown, and a longer match list is cut, never padded.
- Lessons belong to one registry. Only promoted lessons travel, with the repository that
  keeps them.

## Rules moved from the core (SKILL.md 3.14.0)

The core keeps one line per rule; this is the full text, unchanged except for relative links.

### Built-in skills: errata, rasoio and migliora

These activate by themselves at their documented moments within the authorized
project scope; follow the guides without asking again for work already authorized.
Errata and rasoio retain their activation events; migliora records applied remedies
and commits in task outputs, while its inspections are computed on demand.
- **errata** ([guide](errata.md)). At `brief --task` and `start` the
  CLI shows the matching lessons and records a `lesson.recall`: read them and apply them.
  After a code read that asks for changes, a failed proof or a closed defect fix, the
  output asks the agent who ran the command for a lesson: record it at once with
  `squadra lesson` (`lesson` event), or say in the output why no trigger can be named.
  When a mistake comes back after its lesson was shown, mark it with
  `lesson --ineffective` (`lesson.ineffective` event) and record a better one.

