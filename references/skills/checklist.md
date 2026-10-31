# checklist — checks at the critical moments

Run the checklist for the step being taken. It reads the existing registry and local
Git; it writes no event, projection, ref or configuration and never fetches.

```sh
squadra checklist --moment resume --actor COORD --root REGISTRY
squadra checklist --moment assign --task TASK --root REGISTRY
squadra checklist --moment deliver --task TASK --root REGISTRY
squadra checklist --moment push --repo CHECKOUT --task TASK --root REGISTRY
```

The response is one JSON object: `moment`, `items` (each with `id`, `status`, `detail`)
and `ready`. A `fail` makes `ready` false and exits 1. Otherwise it exits 0.
`unverifiable` stays explicit, with its reason, and does not change the exit status:
**ready means no failed check, not that everything was verified or publication approved.**
Read and reconcile those limits before taking the dependent action.

## What each moment checks

| Moment | Recorded or local facts |
| --- | --- |
| `resume` | Required registered `--actor`, coordinator role, outside stand-by; the blockers from `readiness()`; listening for mail is always unverifiable |
| `assign` | Required open `--task`, recorded brief, registered independent reviewer or pool of another client (or recorded same-client reason), distinct checkout and branch from other open tasks, assignment with recorded transport or ACK; round scope (below) |
| `deliver` | Required `--task` not already closed, existing delivery, current native proof bound to its candidate using the registry's existing checks, ACK of a current review handoff |
| `push` | Required Git `--repo`, author and committer emails already present before the chosen interval, local upstream ancestor of HEAD; with `--task`, a closed task whose candidate commit is contained in HEAD |

The `deliver` moment checks an already recorded delivery **after its recipient ACK**;
it does not replace `deliver`, create a handoff, or start a read. A pool needs one actual
recipient ACK. A recorded assignment transport is evidence of tracking, not of reading;
its delivery observation is printed and the normal start still requires the actual ACK.

Checkout comparisons resolve paths, including symlink aliases. Branch comparisons use
shared Git storage or matching local origin configuration. Unreadable checkouts or Git
calls are reported as unverifiable; file ownership in prose still needs the coordinator's
[overlap check](sovrapposizioni.md). A JSON result never gives another window permission
to write an area it does not own.

## Round scope

The round scope is the latest decision whose reason starts with exactly `Round scope:`.
A task created after it whose ID does not appear in that reason as a whole token
(`R1` is not named by `R1-B` or `XR1`) makes `round_scope` unverifiable: the task
entered after the scope was fixed, so it needs the owner's word or must be a fix that
blocks a close. The item never fails, because the registry cannot tell a blocking fix
from prose; reconcile it before assigning. Without such a decision the item is ok.

## Before a push

`--range A..B` selects the commits to inspect; the default is `@{upstream}..HEAD`.
Both boundaries must name commits. Known emails are the exact author and committer
emails in the ancestry of `A`, fixed before checking any selected commit. An email first
introduced inside the interval is not learned from another commit in that interval.
With an explicit `--range`, `A` must be an ancestor of `B`; otherwise `emails` fails
with "range start is not an ancestor of its end". A valid empty interval such as
`HEAD..HEAD` stays ok and its detail reports 0 commits.

This detects a new address such as the observed contributor-attribution error; it
does not check whether an address belongs to a GitHub account. Legitimate new authors
need an explicit attribution review; the command does not change their commits.

Fast-forward uses the local upstream at the last fetch, even with an explicit range.
A missing upstream is unverifiable. Without it, the default interval is unverifiable
too; an explicit valid interval can still have its emails inspected. Nothing here
queries the remote or guarantees its current state. With `--task`, candidate ancestry
is checked against HEAD, independently of the selected email interval.

## Optional pre-push hook example

No hook is installed automatically. Review and adapt this example to an existing
`.git/hooks/pre-push`; preserve any hook already there. It supports only the currently
checked-out branch pushed to its established upstream. Other refs, new remote branches,
deletions and a remote tip absent locally refuse, so they need a separately reviewed
workflow. Replace the interpreter, skill, registry and task placeholders.

```sh
#!/bin/sh
set -eu
branch=$(git symbolic-ref --quiet --short HEAD)
head=$(git rev-parse HEAD)
test "$(git config --get "branch.$branch.remote")" = "$1"
merge=$(git config --get "branch.$branch.merge")
while read -r local_ref local_oid remote_ref remote_oid; do
    test "$local_ref" = "refs/heads/$branch"
    test "$local_oid" = "$head"
    test "$remote_ref" = "$merge"
    git merge-base --is-ancestor "$remote_oid" "$local_oid"
    python3 /path/to/skill/scripts/squadra.py --root /path/to/registry \
        checklist --moment push --repo . --task TASK \
        --range "$remote_oid..$local_oid"
done
```

The hook applies the same exit-1 rule for failed checklist items and prints unverifiable
limits for reconciliation. The normal task, proof, independent-read and owner-authorization
requirements remain in force.
