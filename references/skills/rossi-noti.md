# rossi-noti — known reds of main

**A test that is already red on clean `main` is named once, with its reason, and every
later proof separates it from new reds.** Without the list, each window that sees the red
re-runs the suite and digs through Git history to prove the failure is not its own. This
guide is the procedure for the
[known-reds rule](../../SKILL.md#known-reds-rule--a-red-already-on-main-is-named-once).

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

- The list covers test IDs only: a crash, a skip, a count mismatch or a discovery error
  is never accepted.
- A known red still blocks a release unless the owner records its deferral.
- The list says a test fails; it does not say the product is right.
