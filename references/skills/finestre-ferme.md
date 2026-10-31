# finestre-ferme — parking stale windows

**A window that stopped answering is parked in stand-by on the owner's word, so the
squad on the board is the squad that is really there.** Stale windows inflate the
composition advice, keep waits open and make the summary hard to read. This guide is the
procedure for the
[stale-windows rule](../../SKILL.md#stale-windows-rule--silent-windows-are-parked-on-the-owners-word).

## Finding them

`squadra doctor --resume` lists in `resume.silent_windows` every active seat whose identity
has no sign of life for two days or more: no recorded answer and no (re)registration of
any seat of that identity. Registering one seat refreshes the whole identity:

| Field | Meaning |
| --- | --- |
| `agent`, `identity` | the window and the identity that `standby` parks as a whole |
| `last_answer`, `days` | the identity's last recorded answer (`null` if it never answered) and the days since its latest sign of life: that answer or, if later, the most recent (re)registration across all its seats |
| `archivable` | `true` when nothing keeps it |
| `keeps` | why it cannot be parked yet: coordinator role, open tasks it owns or reads (reviewer or pool), resources it holds — for every seat of the same identity, prefixed `same identity as <seat>:` |
| `command` | for archivable windows only, one per identity: the `standby` command to run after the owner's word |
| `parked_with` | the other archivable seats of that identity: the seat whose command parks them too |

## Parking

1. Show the owner the list: archivable windows and, for the others, what keeps them.
2. Record the owner's answer: `ask` before showing a structured question, then
   `decision --answers`; or `decision --owner-words "…" --channel typed-here`.
3. For each window the owner named, run the `command` with the owner's words as
   `--reason`. Never park a window the owner did not name.
4. Run `doctor --resume` again: the parked windows leave `silent_windows`, and the
   composition advice of `status` no longer counts them.

## Windows that keep something

`standby` parks the whole identity, not one seat: a free alias of a coordinator or of a
window with open work is kept by that other seat.

- **Coordinator:** reconcile it with Rule 0 (ping, ACK, explicit handover); never park
  it from here.
- **Owns or reads open tasks:** ping it first; then `reassign` the read or supersede the
  task with a decision.
- **Holds resources:** ask it to release them; do not steal a resource because the
  window is silent.

## Limits

- Signs of life are recorded ACKs and (re)registrations across the whole identity;
  processes are not measured. A silent window may still be working. Stand-by stops no
  process and moves no work.
- A parked window returns when it answers a new `ping` (Rule 0) or with
  `standby --resume`.
