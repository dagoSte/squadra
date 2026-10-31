# sovrapposizioni — the overlap check

**The coordinator always checks that the windows it coordinates do not overlap.** Two
windows writing the same tree, branch or file lose work, produce conflicting commits and
invalidate each other's proofs. This guide is the coordinator's procedure for the
[fundamental rule](../../SKILL.md#fundamental-rule--no-overlap-between-coordinated-windows).
It adds no command and no event: it uses the registry and Git as they are.

## When

- before every `task`, `reassign`, fix round and resume;
- at every `deliver`, merge and mail that changes a scope or a branch;
- whenever a window joins, leaves or returns from stand-by.

## The check

For the new work and for **every open task** (`squadra board`, `squadra status`) compare:

| Axis | Overlap when |
| --- | --- |
| Checkout | two writing windows use the same working tree (`--repo`) |
| Branch | two windows commit to the same branch |
| Files | two scopes (`--scope-file`) own the same file, or the same part of a shared file |
| Base | a lane builds on a base another lane is rewriting |
| Resources | gates, ports, databases or `cpu-heavy` reserved by another task (`resource status`) |
| Commits | a window is expected to commit what another window also changes (version, pins, mirrors, release docs) |

Read the real state, not the plan: for each writing checkout, `git -C <repo> status
--short`, `git -C <repo> branch --show-current` and `git -C <repo> diff --name-only
<base>`. A path changed outside a window's owned scope is an overlap already in progress.

## Resolving

Resolve **before** the work starts, choosing the least disruptive remedy:

1. split the scope so each file or part has one owner;
2. sequence it with `--depends` when the second lane needs the first one's result;
3. give the lane its own checkout and branch from the agreed base;
4. reassign the work to the window that already owns the area.

Record the choice with `squadra decision --task <ID>` and tell both windows. Version
bumps, pins, mirrors and release notes belong to the integration, built by the coordinator
(Rule 1), never to two lanes.

## Limits

- The check covers only what the coordinator can read. A window whose tree or branch is
  not visible is **not checked**: say so and ask it for checkout, branch and changed paths
  before assigning.
- Windows that do not use the registry are outside it; their reservations are not seen.
- The check never stops, cleans or resets another window's work: it reports and
  reassigns. Destructive Git actions on someone else's tree need the owner's word.
