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
the report. Run the census from a Claude window: inside a Codex sandbox the process
list hides the other windows (`ps` is refused or lists only the sandbox's own
processes), so the report is partial and its errors say to run census from a Claude
window.

To join a registry that already has a coordinator:

```sh
squadra agent --id newcomer --identity actual-session --session CODEX-THREAD-ID --role executor --client codex --repo /path/to/project
squadra ping --actor newcomer --to coord
# Forward the returned ID to the coordinator; wait for its actual ACK.
squadra transport --actor newcomer --message PING-ID --via manual
```

Do not create a competing coordinator while the call is pending or overdue:
reconcile the existing role and resources with the owner. For new work, register
the coordinator role and every identity actually known.

Example: the project directory as the working directory, with a local registry ignored by Git.
`init` creates a private `.gitignore` containing `*` inside the registry folder before
opening its database. It refuses the Git worktree root or a registry folder with
tracked files: an ignore rule cannot remove files already in Git. Do not force-add
the registry. Existing registries receive the same ignore file on their next
writable open if it is absent; `doctor` warns if the database is already tracked
and prints the command to remove the folder from the index, without running it.

```sh
export SQUADRA_ROOT="$PWD/.squadra"
squadra init --name "Our squad" --project "$PWD" --lang en
squadra agent --id coord --identity window-01 --session CLAUDE-SESSION-ID --role coordinator --client claude --repo "$PWD" --skill /path/to/squadra/SKILL.md
squadra agent --id builder --identity window-02 --session CLAUDE-SESSION-ID --role builder --client claude --repo /path/to/builder-checkout --skill /path/to/skill/SKILL.md
squadra agent --id reviewer --identity window-03 --session CODEX-THREAD-ID --role reviewer --client codex --repo /path/to/reviewer-checkout
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
window is alive. Usage and Balance measure a window only through it (the transcript's
file name under `~/.claude/projects/<project>/`, or the UUID at the end of a Codex
`~/.codex/sessions/…/rollout-…` file); a window registered without one is reported as
`session_not_registered`. `--model` records the requested model. The runner preserves the
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

`codex-queue` requires a Codex seat registered with `agent --session`. Squadra checks
only that a session ID is recorded and that the local `codex queue` accepts `--thread`
and `--message`; it does not check that the ID names a live thread, so record only an
ID you have seen. Do not treat a PID as a session. For recipients not yet identified, retain the discovery and pending handoff
without registering fictional identities. Delivery is not an ACK. Keep the census
and recipients current throughout the work.
See [keep-alive and stand-by](work.md#keep-alive-e-stand-by).

<a id="install-and-join-details"></a>

## Installing and joining: details from the README

*Moved from the README, word for word.*

Install from a clean, committed checkout with a **new backup directory** each time. The installer freezes a copy by commit, links the skill for Codex, Claude and Cursor (`~/.cursor/skills/squadra`) and prepares `~/.local/bin/squadra` (keep it in PATH), preserving the previous skills and launcher. To roll back, replace only the four targets listed in `INSTALL.json` with their recorded predecessors. Without installing, run `python3 -B scripts/squadra.py <command>`. Installing never migrates a working squad: each keeps its version until you switch it explicitly.

1. **Install** and set `export SQUADRA_ROOT=...` in every window.
2. **Create the registry and register each window** with `init` and `agent`, as above.
3. **Find the windows and introduce yourself (rule 0):** `census` lists the project's Codex, Claude and Cursor (`cursor-agent`) processes without writing; `send` and `transport` deliver an introduction, and each window answers with `ack`.
4. **Say what the project is** in plain words with `brief --project`; the dashboard shows it first.
5. **Assign and explain the first task:** `task` assigns it; `brief --task` gives it a readable title and says why it exists, for whom, and when it is finished.
6. **The builder confirms and starts:** `ack` («I have received it»), then `start` («I am building»).
7. **The builder proves and delivers:** `prove` runs the agreed check on the exact files; `deliver` hands them over.
8. **Someone else reads:** a reader who did not write the work confirms the handoff and records a verdict with `review`.
9. **The coordinator closes:** `check` verifies that proof and read concern the same version; `close` records the end.
10. **Check who still answers:** the coordinator's `ping --all` calls every other window, and each answers with `ack`; `ping --sweep` puts in stand-by a window whose call went unanswered past the `alive` wait (300 seconds unless `settings` changes it). It gets no new assignments, and nothing it runs is stopped.
11. **Open the dashboard** with `dash --open`; «How to read this page» explains every term.
12. **Record what the owner says, with its links:** `decision` with `--owner-words`, `--channel` and `--task`; then select its box in the Decisions view.

Replace `PING-ID` with the returned ID and wait for the real ACK. Census matches executable names and working directories, can miss wrappers, graphical or remote clients and exited processes, and keeps scan errors visible; add Claude's peer list and the Codex session files. Processes, registrations and queue receipts do not prove that a window answers, and identity is never inferred from a PID or a shared directory. Each introduction (identity, role, registry, next step) goes out with `send` and `transport`: `--via codex-queue` for a Codex seat registered with `agent --session`, Claude's native peer messaging with a kept receipt, otherwise `--via manual`. Unanswered introductions stay pending. The coordinator should be an unsandboxed Claude window: it relays any message a sandboxed window could not deliver with `squadra transport --actor <coordinator> --message <ID> --via codex-queue` (a Codex recipient) or `--via manual`, recorded with `relayed_by`. A Codex window needs the registry folder among its writable roots (`codex --add-dir <registry>`), or it cannot write its ACKs. Details in [startup and presentation](start.md).

```sh
# Census writes nothing, even before init. Use the same shared --root throughout.
squadra census --root /path/to/registry --project /path/to/project
# Joining an existing squad: register the real window as an executor, then call the coordinator.
squadra agent --id arrival --identity actual-session --session CODEX-THREAD-ID --role executor --client codex --repo /path/to/project
squadra ping --actor arrival --to coord
squadra transport --actor arrival --message PING-ID --via manual
```

Every window uses the same `SQUADRA_ROOT` or `--root`; separate checkouts never create separate mailboxes. `agent --skill` records the path and hash of each skill actually read, `--session` a known session, `--model` a requested model; what a client does not expose stays unknown. **An identity is not a role:** two aliases of one window are not an independent reader, and a seat's identity and role cannot be rewritten, so a different window needs a new seat. The `executor` role also exists. Add builders only for independent scopes.
