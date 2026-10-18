# Set up Squadra — instructions for a coding agent

This file is for the coding agent (Claude Code, Codex, Cursor or another) that a user asked to install Squadra, for
example with the sentence at the top of the [README](README.md). Follow it step by step. People can read it too; the
manual steps are in the README's [Installation](README.md#start) section.

## Rules for the whole setup

1. **Steps 1 and 2 change nothing.** They only read. Nothing is installed before the user answers yes to the plan.
2. **Stop at the first failure.** Show the command, its exit status and its output, and ask the user what to do. Do not
   retry with other commands, other folders or other flags.
3. **Only the commands in this file.** No `sudo`. Do not install Python, Git or anything else, and do not download or run
   anything that is not listed here. Adding a line to a shell profile (`~/.zshrc`, `~/.bashrc`) needs its own yes.
4. **Respect the client's permissions.** If the client's sandbox or approval prompt stops a command (network access, a
   folder outside the project), ask for the client's own approval for that command. Never work around it.
5. **One installation per client.** Never the plugin and the standalone installer together: the skill would be
   registered twice. An existing installation is reported to the user, not installed again.
6. **Report every exit status**, including the ones that are 0.

Shell variables do not always survive between two commands in an agent's shell; every block below is complete on its
own.

## Step 1 — Read-only checks

**Which client you are.** You know which client you run in: Claude Code, Codex, Cursor or another. If you are not sure,
ask the user. In Claude Code, `CLAUDECODE=1` is set in the shell your commands run in.

**Requirements**, in every client:

```sh
python3 --version
git --version
```

`python3` must be 3.12 or later, and Git must answer. If either fails, stop and tell the user what is missing; do not
install it yourself.

**Standalone installation**, in every client:

```sh
for p in "$HOME/.claude/skills/squadra" "$HOME/.codex/skills/squadra" "$HOME/.cursor/skills/squadra" "$HOME/.local/bin/squadra"; do
  if [ -e "$p" ] || [ -L "$p" ]; then echo "present: $p"; else echo "absent: $p"; fi
done
```

Any `present` line means Squadra (or a folder with its name) is already there. If `$HOME/.local/bin/squadra` is
present, also run `"$HOME/.local/bin/squadra" --version`. For the standalone route, also check the folder its plan
clones into:

```sh
if [ -e "$HOME/.local/src/squadra" ]; then echo "present: $HOME/.local/src/squadra"; else echo "absent: $HOME/.local/src/squadra"; fi
```

**Plugin installation in Claude Code** (when the `claude` command exists, in any client):

```sh
claude --version
claude plugin list --json
claude plugin marketplace list --json
```

Any plugin whose `id` starts with `squadra@` means Squadra is already installed in Claude Code: `squadra@squadra` from
this repository, `squadra@skills-dir` from the standalone installer, another name after `@` from another marketplace,
such as the Claude directory. In the marketplace list, note whether one is named `squadra` and which `repo` it has.

**Plugin installation in Codex** (when the `codex` command exists, in any client):

```sh
codex --version
codex plugin list --json
codex plugin marketplace list
```

`squadra@squadra` among the installed plugins means Squadra is already installed in Codex. In the marketplace list,
note whether one is named `squadra`.

A command that does not exist (`claude` or `codex` not installed) is an answer, not a failure: that client has nothing
to check.

### Choose the route

| You are | Route | Only when |
|---|---|---|
| Claude Code | Claude Code plugin | no `squadra@…` in `claude plugin list` and no `present` line |
| Codex | Codex plugin | no `squadra@squadra` in `codex plugin list` and no `present` line |
| Cursor, or a client without plugins | Standalone installer | no `present` line, `~/.local/src/squadra` absent, and no Squadra plugin in Claude Code or Codex |

**Already installed.** If the condition fails because Squadra is already there, do not install anything. Tell the user
what you found: which client, which route, which version, which path. If it is an older version, point to
[Updating](README.md#updating) in the README. Then go to Step 5.

**Plugin and standalone would meet.** The standalone installer links the skill into Claude Code, Codex and Cursor at
once. If Claude Code or Codex already has the Squadra plugin, the standalone installer would register it twice there.
Tell the user, and stop: removing a plugin first is their decision, with its own plan.

**Another client** (neither Claude Code, Codex nor Cursor): the standalone installer gives the `squadra` command and
links the skill into those three clients only. Tell the user that their client must be pointed at the skill's
`SKILL.md` by its own means, and ask whether to continue.

**A marketplace named `squadra` already exists.** If it points at `dagoSte/squadra`, leave out the marketplace command
from the plan. If it points somewhere else, stop and tell the user.

**`$HOME/.local/src/squadra` is present** on the standalone route but nothing is installed: it is a checkout, not an
installation. Tell the user and stop; do not overwrite it.

## Step 2 — Show the plan and wait

Show the user the table for the chosen route, in their language, with every command exactly as you will run it, then
ask whether to go ahead. Run nothing until the user answers with an explicit yes. A yes covers these commands only.

If the user wants Squadra in more than one client, put each client's rows in the same plan, and check that no client
gets both the plugin and the standalone installer.

**Claude Code plugin** (paths under `CLAUDE_CONFIG_DIR` when it is set):

| # | Command | What it changes | How to undo it |
|---|---|---|---|
| 1 | `claude plugin marketplace add dagoSte/squadra` | Adds the marketplace `squadra` to Claude Code's user settings (`~/.claude/settings.json`) and copies the repository into `~/.claude/plugins/marketplaces/squadra` | `claude plugin marketplace remove squadra` |
| 2 | `claude plugin install squadra@squadra` | Installs the plugin for your user in `~/.claude/plugins/cache/squadra/squadra/<version>` and enables it | `claude plugin uninstall squadra@squadra` |

**Codex plugin** (paths under `CODEX_HOME` when it is set):

| # | Command | What it changes | How to undo it |
|---|---|---|---|
| 1 | `codex plugin marketplace add dagoSte/squadra` | Adds the marketplace `squadra` to `~/.codex/config.toml` and copies the repository into `~/.codex/.tmp/marketplaces/squadra` | `codex plugin marketplace remove squadra` |
| 2 | `codex plugin add squadra@squadra` | Installs the plugin in `~/.codex/plugins/cache/squadra/squadra/<version>` and enables it in `~/.codex/config.toml` | `codex plugin remove squadra@squadra` |

**Standalone installer.** Write the backup folder's name with the current UTC time in place of `<UTC time>`, for example
`install-backup-20261018T090000Z`; the installer refuses a folder that already exists.

| # | Command | What it changes | How to undo it |
|---|---|---|---|
| 1 | `git clone https://github.com/dagoSte/squadra.git "$HOME/.local/src/squadra"` | A new folder with the repository, kept for later updates | Delete `~/.local/src/squadra` |
| 2 | `python3 -B "$HOME/.local/src/squadra/scripts/install.py" --backup "$HOME/.local/state/squadra/install-backup-<UTC time>"` | Copies this version to `~/.local/share/squadra/versions/<commit>`; links `~/.claude/skills/squadra`, `~/.codex/skills/squadra` and `~/.cursor/skills/squadra` to it; writes the `squadra` command at `~/.local/bin/squadra`; records every path in the backup folder's `INSTALL.json` | Remove the three links and `~/.local/bin/squadra`, then delete `~/.local/share/squadra/versions/<commit>` and the backup folder |

## Step 3 — Install

Run the approved commands in order, one at a time, and report each exit status. If one fails, stop (Rule 2): show its
output, and offer the undo of the commands that already ran, which also needs a yes.

## Step 4 — Verify

Find the installed copy:

- **Claude Code plugin:** in `claude plugin list --json`, the `squadra@squadra` entry gives `version` and `installPath`.
  The command is `python3 -B "<installPath>/scripts/squadra.py"`.
- **Codex plugin:** in `codex plugin list --json`, the `squadra@squadra` entry gives `version`. The command is
  `python3 -B "<CODEX_HOME or ~/.codex>/plugins/cache/squadra/squadra/<version>/scripts/squadra.py"`; the output of
  `codex plugin add` printed the same folder as «Installed plugin root».
- **Standalone installer:** the command is `"$HOME/.local/bin/squadra"`.

Then run this block once, with that command inside `sq()` in place of the example. It prints the version and checks a
throwaway registry in a temporary folder, which it removes at the end:

```sh
sh -s <<'EOF'
sq() { python3 -B "/path/to/installPath/scripts/squadra.py" "$@"; }
run() { printf '$ squadra %s\n' "$*"; sq "$@"; rc=$?; echo "exit $rc"; return $rc; }
T=$(mktemp -d) || exit 1
mkdir "$T/proj" &&
run --version &&
run init --temporary --lang en --root "$T/reg" --name setup-check --project "$T/proj" &&
run doctor --root "$T/reg" &&
run status --root "$T/reg"
rc=$?
rm -rf "$T"
exit $rc
EOF
```

All four must end with `exit 0`, and the version must be the one the client lists. If one does not, stop (Rule 2). The
check takes about a second; do not run `selftest` here, it runs the whole test suite.

## Step 5 — Tell the user

Say, in the user's language:

1. what is installed, which version and where, and each verification's exit status;
2. **the one act left to them:**
   - **Claude Code:** type `/reload-plugins` (or start a new session);
   - **Codex:** start a new session;
   - **Cursor:** start a new chat;
3. for the standalone installer, whether `~/.local/bin` is on the `PATH` (`command -v squadra`). If it is not, offer to
   add `export PATH="$HOME/.local/bin:$PATH"` to their shell profile, and do it only after its own yes. Agents can
   always use the full path `~/.local/bin/squadra`;
4. the first sentence to start a squad, ready to copy. It goes to a Claude window, which becomes the coordinator; a
   Codex window called first does not coordinate and points the user to a Claude window:

```text
Use the squadra skill for this project. Shared registry: ~/.local/state/squadra/my-project.
Read the project mandate first. Find the other Codex, Claude and Cursor windows working on it,
introduce yourself to each one and wait for their confirmations.
Then write a plain-words brief of the project and show me the progress table.
```

More prompts are in the README's [Example prompts](README.md#prompts).
