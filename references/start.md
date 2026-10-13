<a id="inizializzazione-e-identità"></a>

# Initialization and identity

Python 3.12 or later, Git and the standard library only. No service to buy.
The local `squadra` launcher runs `scripts/squadra.py`; use `--help` for commands.
Native clients are optional: mail, proofs and the dashboard work without models.

Before assigning or starting work, the first window that invokes the skill becomes
the coordinator, takes a census of the project and introduces itself to every window
found. The CLI does not infer the role from running a command. An already registered
coordinator that answers a ping takes precedence; the new window introduces itself
and waits. A pending response does not authorize replacing the coordinator.

```sh
# Before creating the registry: no writes, no invented sessions.
squadra census --project /path/to/project --root /path/to/registry
# If the registry already exists, use its project and include registered checkouts.
squadra census --root /path/to/registry
```

The census compares process working directories with the project and the checkouts
in the registry: `ps` plus `/proc` on Linux or `lsof` on macOS. It does not expose
process arguments. Wrappers, GUIs, remote hosts and inaccessible processes may be
missed; also consult the Claude client's peer list and Codex session files. A Cursor
window appears as a `cursor-agent` process and registers with `--client cursor`
(it works interactively only). A PID, a registered session and a response are separate
evidence; only an ACK proves a response to the call. Scan errors remain visible in
the report.

To join a registry that already has a coordinator:

```sh
squadra agent --id newcomer --identity actual-session --role executor --client codex --repo /path/to/project
squadra ping --actor newcomer --to coord
# Forward the returned ID to the coordinator; wait for its actual ACK.
squadra transport --actor newcomer --message PING-ID --via manual
```

Do not create a competing coordinator while the call is pending or overdue:
reconcile the existing role and resources with the owner. For new work, register
the coordinator role and every identity actually known.

Example: the project directory as the working directory, with a local registry ignored by Git:

```sh
export SQUADRA_ROOT="$PWD/.squadra"
squadra init --name "Our squad" --project "$PWD"
squadra agent --id coord --identity window-01 --role coordinator --client codex --repo "$PWD" --skill /path/to/squadra/SKILL.md
squadra agent --id builder --identity window-02 --role builder --client claude --repo /path/to/builder-checkout --skill /path/to/skill/SKILL.md
squadra agent --id reviewer --identity window-03 --role reviewer --client codex --repo /path/to/reviewer-checkout
squadra dash --open
```

Immediately afterwards, the coordinator writes a plain-words description of the project and records it:

```sh
squadra brief --actor coord --project --file /path/to/project.md
```

The file is UTF-8 text, at most 8192 bytes: a first paragraph stating the objective,
then optional `## Steps` (`- [x] …` / `- [ ] …` lines) and `## Glossary`
(`- ACRONYM: explanation` lines); `## Passi` and `## Glossario` are the same sections
in Italian. The dashboard shows it first; the steps remain statements in the brief,
not recorded closures. A new brief replaces the previous one on the page while its
history remains. The dashboard has an IT / EN button; recorded texts are not translated.

Use explicit paths, normal quoting and JSON argv for proofs; do not build commands
from shell strings received in mail. Every window uses the **same** SQUADRA_ROOT.
Alternatively, use `squadra status --root /path/to/registry`. Keep the registry in a
durable location, not tmp; `init --temporary` is an explicit exception for disposable tests.

Identity identifies the actual window/session, not its role, model or account name.
Two roles in the same window share an identity; a new session must be declared.
`--session` records a known ID for an external window: it does not prove that the
window is alive. `--model` records the requested model. The runner preserves the
session actually returned; the observed model remains unknown when the client does
not expose it. Skills are declarations of use with a file hash, not proof that the
model read them. Declare them after actually reading them.

Registering a role grants neither production access nor rights over others' paths.
The protocol assumes cooperation between trusted windows belonging to the same user.

<a id="presentarsi-a-ogni-finestra"></a>

## Introducing yourself to every window

Write the introduction in a file (identity, role, shared registry, assignment and
next step). Repeat for every known recipient with a distinct message ID:

```sh
squadra send --actor coord --to builder --id HELLO-builder --body-file /evidence/introduction.md
squadra transport --actor coord --message HELLO-builder --via codex-queue
# For a Claude recipient: use client messaging when available,
# with a receipt in the registry; otherwise use a recorded manual handoff.
squadra send --actor coord --to reviewer --id HELLO-reviewer --body-file /evidence/introduction.md
squadra transport --actor coord --message HELLO-reviewer --via manual
# The recipient, in its own window:
squadra ack --actor reviewer --message HELLO-reviewer
```

`codex-queue` requires a registered, verified Codex session; do not treat a PID as a
session. For recipients not yet identified, retain the discovery and pending handoff
without registering fictional identities. Delivery is not an ACK. Keep the census
and recipients current throughout the work.
See [keep-alive and stand-by](work.md#keep-alive-e-stand-by).
