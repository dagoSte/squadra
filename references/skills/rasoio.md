# rasoio — the lean-code lens

**Ask for the laziest solution that really works: the shortest, the simplest, the most
minimal, never cutting security, validation or error handling.** Lazy means efficient,
not careless. The best code is the code never written.

## When it activates in a squad

- **Every code read whose candidate changes code** (not only prose or docs) applies the
  lens, without asking the owner: `squadra review ... --lens rasoio --net-lines N`.
- **A builder** follows the ladder below while writing the change.
- The lens **adds observations only**. It never changes a verdict, a check or a close
  rule: a review with the lens is accepted, changed or blocked exactly as without it.

## The ladder

Before writing code, stop at the **first** step that holds:

1. **Must it exist?** A speculative need is not built; say so in one line (YAGNI).
2. **Does it already exist in this codebase?** A helper, a utility, a pattern already
   there: reuse it. Look before writing; re-implementing what sits two files away is the
   most common mistake.
3. **Does the language's standard library do it?** Use it.
4. **Does a native feature of the platform cover it?** The browser (a native input
   instead of a library, CSS instead of JavaScript), the database (a constraint or index
   instead of application logic), the operating system or the shell.
5. **Does an installed dependency solve it?** Use it. Never add a new one for what a few
   lines cover.
6. **Can it be one line?** One line.
7. **Only then:** the least code that works.

The ladder starts **after** the problem is understood, never instead of it. Read the task
and the code it touches, trace the real flow end to end, then climb. A bug fix fixes the
root cause: find every caller of the function before touching it.

## Rules

- No abstraction nobody asked for: no interface with one implementation, no factory for
  one product, no setting for a value that never changes.
- No boilerplate and no scaffolding "for later".
- Deleting beats adding. Boring beats clever.
- Fewest files possible. The shortest working diff wins, but only after the problem is
  understood: the smallest change in the wrong place is a second bug.
- A complex request gets the minimum that meets every explicit requirement. Propose
  smaller alternatives; never replace a requested feature with an incomplete one without
  the owner's decision.
- A deliberate simplification with a known ceiling (a global lock, a quadratic scan, a
  naive heuristic) carries a `rasoio:` comment naming the ceiling and the upgrade path.
  `grep -rn "rasoio:"` then lists that debt.

## Comments

A comment says only **what this is, why it is here, what it does**. Briefly.

- **No narrative:** no story of the bug, no "first it did X, then we found Y", no
  changelog in the code. History lives in version control.
- If the code is obvious, there is no comment.
- **A comment that names the wrong defence is worse than none:** it decides which line a
  later cut keeps. Fix that comment first, then judge the cut again.
- Keep licences, linter and type-checker directives, runtime annotations, runnable
  examples and conditions callers need: being a comment does not make them removable.
- A written project convention that demands narrative wins, but it must be cited, file
  and line.

## The review

Scope: over-engineering, complexity and comment hygiene only. Correctness, security and
performance belong to the normal read. The lens **lists** cuts; it does not apply them.

- **Read the whole file, not only the hunk:** "it already exists in this codebase" is
  exactly what a diff cannot show.
- **No finding without a check, and the check is a command:** a `delete:` needs a search
  that shows no caller; a `stdlib:` or `native:` needs the replacement to exist in the
  installed version, not in memory.
- **Equivalence before the cut:** the replacement keeps types, errors, order, expiry,
  invalidation and concurrency where they are part of the contract.
- **A partial read says so:** name the files read and skipped; the net figure counts the
  files read.

One line per finding: `L<line>: <tag> <what>. <replacement>.` (or `<file>:L<line>: …`).

| Tag | Meaning | Replacement |
|---|---|---|
| `delete:` | dead code, unused flexibility, a speculative feature | nothing |
| `stdlib:` | something the standard library already ships | name the function |
| `native:` | a dependency or code doing what the platform already does | name the feature, and say if it is not installed |
| `yagni:` | an abstraction with one implementation, a setting nobody sets, a layer with one caller | inline it |
| `reduce:` | the same logic in fewer lines | show the shorter form |
| `comment:` | a narrative, historical or obvious comment; **gravest: a comment that states a false defence** | nothing, or the one what/why line; a false one is corrected, not deleted |

Close with the only figure that matters: `net: -<N> lines possible.` If there is nothing
to cut: `Already lean. Ship.` Record that same N with `--net-lines N`: it is a declared
estimate, not a measurement. When the cut is later made, measure the real line count
before and after and put it next to the estimate.

A single smoke test or `assert`-based self-check is the minimum, not bloat: never flag it
for deletion.

## When not to be lean

Never simplify away: validation at trust boundaries, prepared statements, output
escaping, CSRF protection, error handling that prevents data loss, security,
accessibility basics, or anything explicitly required. Never be lazy in understanding the
problem: the ladder shortens the solution, never the reading. Lean code without its check
is unfinished: non-trivial logic leaves one small runnable check that fails if the logic
breaks.
