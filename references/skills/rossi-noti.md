# rossi-noti — known reds of main

**A test that is already red on clean `main` is named once, with its reason, and every
later proof separates it from new reds.** Without the list, each window that sees the red
re-runs the suite and digs through Git history to prove the failure is not its own. This
guide is the procedure for the
[known-reds rule](../../SKILL.md#no-overlap-resume-and-round-scope) (R13).

## The list

`tests/known_reds.json`, versioned with the code:

```json
{
  "version": 1,
  "known_reds": [
    {
      "id": "test_module.Class.test_method",
      "reason": "Why it fails on clean main, in one sentence.",
      "since": "v3.11.1",
      "retire_when": "The observable condition that makes it pass again."
    }
  ]
}
```

`id`, `reason`, `since` and `retire_when` are required and non-empty; a malformed list
or a duplicate refuses the run. The runner accepts `--known-reds PATH` for another file.

## Adding a known red

1. Run the strict suite on the clean `main` head, with no local changes, and read the
   failure: `python3 -B tools/parallel_tests.py`.
2. Confirm it is not an instrument error (a lost lock, a leftover `__pycache__`, a
   missing interpreter). Those are fixed in the environment, never listed.
3. Name the task or decision that owns the fix, then add the entry in a coordinator
   commit of its own.

## Using it

| Proof | Command | Exit 0 when |
| --- | --- | --- |
| Task proof | `tools/parallel_tests.py --accept-known` | every failure is a listed known red, no list entry is stale |
| Release proof (Rule 9) | `tools/parallel_tests.py` | everything passes |

The summary always reports `known_red`, `new_red`, `stale_known` and
`only_known_reds`; `passed` stays the strict verdict.

## Retiring

When a known red passes again, or its test is no longer collected, the runner prints
`STALE KNOWN RED` and `--accept-known` refuses until the entry is removed. Remove it in
the commit that fixes the test.

## Limits

- The list covers test IDs only: a crash, a skip, an unexpected success, a count or ID
  mismatch or a discovery error is never accepted. A worker that only had failing tests
  exits with code 86 after writing its receipt; any other nonzero exit, 1 included, is an
  instrument error.
- A listed `@expectedFailure` test that passes again is an unexpected success: the
  entry is stale and must be removed.
- A known red still blocks a release unless the owner records its deferral.
- The list says a test fails; it does not say the product is right.

## Rules moved from the core (SKILL.md 3.14.0)

The core keeps one line per rule; this is the full text, unchanged except for relative links.

### Known-reds rule — a red already on main is named once

- The parallel runner reads the list and prints `KNOWN RED`, `NEW RED` and
  `STALE KNOWN RED` lines; its summary adds `known_red`, `new_red`, `stale_known` and
  `only_known_reds`. The strict `passed` verdict never changes.
**A test that fails on clean `main` is recorded once in `tests/known_reds.json`**, with
its reason, the version it appeared in and the condition that retires it (owner,
2026-10-31). Nobody re-investigates it at every proof.
- A task proof may be agreed as `tools/parallel_tests.py --accept-known`: it exits 0
  only when every failure is a listed known red. **A new red always fails**, and so do
  crashes, skips, missing tests and count mismatches.
- A known red that passes again, or is no longer collected, makes `--accept-known` fail
  until it is removed from the list: the list never outlives the defect.
- **The release proof stays strict** (Rule 9): never `--accept-known`. A known red blocks
  a release until it is fixed or the owner records its deferral in a decision.
- A test enters the list only after it was shown red on the clean `main` head, with a
  task or decision that owns its fix; the coordinator adds it in its own commit.

