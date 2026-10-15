# Bilanciamento — suggest windows and models by load

Use this system skill when creating, briefing or starting a task, and when asked
which window or model should take work. The CLI prints the advice automatically
at those three moments, after the forecast. It assigns nothing.

```sh
squadra balance --task TASK --root REGISTRY [--reference FILE]
squadra balance --root REGISTRY
```

The command reads an existing registry without appending events or exporting
projections. An unknown task fails with a native nonzero result. A missing
registry is not created. The dashboard page **Bilanciamento** / **Balance** reads
the same calculation from `/api/balance`, only while the page is open.

## What it reads

- **Load**, from the registry: open tasks a window owns, and next steps it
  holds (build and read), from the same next-step rule as the board. Seats that
  share one identity count as one window. Stand-by windows are listed as excluded.
- **Tokens and quota**, from the Usage collector's numbers only
  (`scripts/usage.py`, schema 1). Balance never opens a transcript.
  - Codex quota is measured: each provider slot keeps its latest observation,
    with percent, window and reset. A slot is current only if it is measured,
    observed within 900 seconds and its reset is still ahead.
  - Claude quota is not in Claude's files, so it is only an **estimate**. The
    viewer declares a reference (`{"claude":{"h5":N,"week":N}}`, tokens per 5 h
    and per week). Balance divides the exact rolling 5-hour and 7-day sums of all
    registered Claude windows by it. It assumes those windows share one account,
    and says so. There is no reset time for an estimate. Without a reference the
    page shows the two sums and «estimate unavailable».
  - The automatic print at task, brief and start does not collect tokens: they
    are marked «not collected» there. Run `squadra balance` for them.
- **History**, from one previsione cohort for the task: tasks each window built
  and code verdicts it gave. A median or a changes-required share appears only
  with at least three samples for that window.

## The order

Candidates for building have a builder or executor seat. Candidates for reading
have a reviewer or coordinator seat. A reader whose identity is any author of the
task is excluded as «author», including an author's alias on another seat.

The order is lexicographic, not a score:

1. not near quota (any current slot or complete estimate at 80 % or more);
2. fewer next steps held;
3. fewer open tasks owned;
4. more comparable history;
5. fewer tokens per hour over the last 15 minutes;
6. identity, for a stable result.

**Missing data.** When any eligible candidate lacks quota, history or the token
rate, that dimension is left out of the order for the whole list. The output names
the dimension and the windows that lack it. An unknown value is never treated as
zero, as free or as busy. A partial observation never proves capacity below the
limit.

## Suggestions per model

- **S1**: a window holds at least 2 pending reads while an eligible independent
  reader holds none. The suggestion names the task, and that task's authors are
  excluded.
- **S2**: a window is near its quota: Codex measured, Claude estimated.
- **S3**: a window owns at least 3 open tasks while an active window of the same
  client owns none.
- **S4**: a model's windows are idle while S1 or S3 fired for another model.

Each suggestion shows the numbers it rests on. A rule whose input is missing is
silent.

## What the coordinator does

Follow the advice, or record why not in an ordinary decision linked to the task.
The advice never changes the route, reviewer, budget or permissions. It never
overrides independence or stand-by. Never quote an estimate as a measurement.

## Release audit record

Required files: `SKILL.md`, `scripts/registry.py`, `scripts/squadra.py`,
`scripts/dashboard.py`, `scripts/system_skills.py`, `assets/dashboard.html`,
`references/skills/bilanciamento.md`, `scripts/balance.py`, `scripts/forecast.py`
and `scripts/usage.py`. The coordinator supplies the real scan record at
integration. Until then the page shows **Not audited for this version**.
