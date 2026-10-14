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

Every activation leaves a registry event: `lesson.recall`, `lesson` or
`lesson.ineffective`. The System skills page counts them.

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

## Limits

- The recall matches text and globs; it does not understand code. A lesson about a file
  the scope never names will not come back: write its trigger with the words the next task
  will use.
- At most five lessons are shown, and a longer match list is cut, never padded.
- Lessons belong to one registry. A squad's lessons do not travel to another squad.
