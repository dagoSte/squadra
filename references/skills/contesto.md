# contesto — the context lens

Correct code can still be the wrong change. Check the context that tests and a diff
alone cannot establish.

## When it activates

Every code read whose candidate changes code applies **contesto and rasoio in the
same review**, without asking the owner. Prose-only changes do not require it.
The lens adds observations; it never changes a verdict, check, close rule or budget.
A suspect value is a question to verify, not a confirmed defect or an automatic block.

## Three checks

1. **Orders of magnitude.** For every changed constant, limit, timeout or unit, write:
   `name: old → new (unit) — plausible? why`.
   Check seconds versus milliseconds, minutes versus hours, money units, retry
   counts, batch sizes, token lifetimes and cache TTLs. Read the callers and compare
   against documented usage or an observed boundary; name missing evidence rather
   than inventing production traffic. With no changed constants, declare zero.
2. **History and neighbours.** Read recent changes to the same files, earlier edits
   of the same value, reverts and its callers. Start with these commands, replacing
   the example paths and search terms with the actual changed files and symbols:

   ```sh
   git log -p --since='30 days ago' -- path/to/changed.py
   git log -p -S 'CONSTANT_OR_SYMBOL' -- path/to/changed.py
   git log --grep=revert -i -- path/to/changed.py
   rg -n -F 'CONSTANT_OR_SYMBOL' .
   ```

   Check whether a recent change conflicts, why an earlier attempt was reverted,
   and which upstream assumptions or downstream clients depend on the old value.
   Missing history in a shallow copy is unverified history, not an empty history.
3. **Requirement match.** Compare the diff with the task's recorded scope and each
   explicit requirement. List matches, extra behaviour and missing behaviour.
   Do not silently replace the requested result with a smaller one. Use Squadra's
   existing finding and triage rules to decide what the observations mean.

## Report and signature

Keep one compact section in the normal code-read report:

```text
Context
- Magnitudes: TIMEOUT 30 → 30000 (seconds) — suspect; caller expects seconds.
- History / neighbours: commands, relevant changes or reverts, callers checked.
- Requirement match: C1 …; extra: …; missing: …
- Declared constants: checked 1; suspect 1 (a subset of checked).
Verified: named files, requirements, commands and actual results.
Not verified: named missing history, usage evidence or checks, with the reason.
Signed: reviewer identity, candidate and recommendation under the existing rules.
```

Count each changed constant once; a repeated reference is not another constant.
Zero means the reader checked and found none. If constants or units could not be
checked, name them under **Not verified**; do not present that gap as verified zero.
The signature states the scope of the read, not a blanket approval.

Record both lenses on that same review:

```sh
squadra review --actor reader --task TASK --verdict accepted --report review.md \
  --lens rasoio --net-lines 0 --lens contesto \
  --constants-checked 1 --constants-suspect 1
```

Both constant counts are required with `--lens contesto`: integers from 0 through
1,000,000, with suspect no greater than checked. They are **declared counts**, not
measurements of correctness or proof that the checks ran. The report supplies the
evidence and limitations. The System skills page counts these declarations and
reviews; it does not turn them into confirmed defects or measured improvements.

## Source and scope

Adapted from the context checks in Step 2 and the explicit verification scope in
Step 3 of [ai-code-review-triage](https://github.com/laulauraluo/ai-code-review-triage),
commit `91a1c77ceab95c1c3f45680debcc29de2d684206`, by laulauraluo, MIT licence.
See [the attribution and full licence](../../THIRD_PARTY_NOTICES.md).
Independent readers, risk triage and verdicts keep Squadra's existing protocol;
this lens adds none of those mechanisms again.
