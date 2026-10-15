# Squadra privacy notice

Updated 15 October 2026. [Italiano](PRIVACY.it.md).

Squadra is software you run on your computer. Its author, Stefano D'Agostino,
does not operate a service that collects data from Squadra. The coordination
registry and dashboard do not require an account with the author.

## What is stored on your computer

The registry you select with `--root` stores project and task descriptions,
participant identities, messages, decisions, timestamps, reviews, command
records, source fingerprints, and references to evidence. Registered artifacts,
prompts and command outputs can contain project files, names, paths, personal
information or confidential material supplied by you or your agents. These
records support coordination, recovery and tracing work to its proof and review.

The registry includes `registry.sqlite3`, derived documents and an `artifacts/`
folder. Squadra creates its registry directory with mode `0700` and its database
with `0600`; existing parent directories and copies you make have their own
permissions. Modes restrict local access; they do not encrypt the contents.
Do not include passwords, API keys or unnecessary personal data in messages,
prompts, artifacts or command output. Recorded artifacts cannot be selectively
edited through the dashboard.

Dashboard plugins live under `~/.squadra/plugins/<name>/` in the HOME of the
user running Squadra. Their folders can hold manifests, panel data and saved
action requests. The Store creates managed plugin folders with `0700`.
Declared settings are saved in plaintext `settings.json` with `0600`;
declared secrets are separate plaintext `secrets/<id>` files with `0600`
inside a `0700` directory. This is not an encrypted vault. The settings API
does not return saved secret values or copy those values into the registry.
Saved requests are handled when a separate plugin producer or consumer runs;
the dashboard does not execute those programs itself.

The Usage page and `squadra usage` read the session files that Claude Code and
Codex already keep in your HOME, for the windows registered in the squad. They
parse each line and keep only token counts, model names, times, Codex quota
figures and the session and hashed message identifiers needed to count each
message once, in memory; the text of the conversations is never stored, shown or
sent. Prices you enter on the page stay in your browser's
local storage and are not written to the registry.

## The dashboard and external services

The dashboard listens on `127.0.0.1` and checks its access token and Host.
It serves its bundled fonts, scripts and icons locally and displays registry
records and installed-plugin data. Opening a link you choose can reach an
external website. Your browser and password manager have their own settings
and data handling.

Squadra does not automatically upload its registry to the author. This does
not make every coordinated command offline. Coding clients you authorize may
send prompts, source code and task context to their providers under your
account. Optional plugins and MCP services may send selected data to the
services you configure. Authorized Git operations, browser actions, message
transports and proof commands have their own effects. Review those tools'
permissions, destinations and privacy notices before using them.

The memoranda plugin offered in the Store is one of these optional plugins. It
sends nothing until you install it and set your own memoranda.dev key. From then
on it sends the items your agents declared worth remembering to the memoranda.dev
address you configure, as proposals for your review: each item's sentence, kind,
scope, confidence, tags and references to its evidence in the registry (event
numbers, artifact names and hashes), not the evidence files themselves.
memoranda.dev handles them under its own privacy notice.

## Retention, removal and shared copies

Local records remain until you remove them; there is no automatic retention
deadline. Stop the relevant dashboard, agents and plugin producer or consumer
before removing data. Remove the selected registry directory to remove its
database, projections and registered artifacts. Remove a plugin's folder to
remove its local settings, secrets, panel data and requests. Do not remove a
shared HOME or other plugins. Uninstalling the Squadra skill alone does not
remove these data folders.

Exports, checkpoints, Git bundles, WIP archives, client histories and backups
are separate copies. Delete or retain them under your own policy. Local
deletion does not delete copies already sent to another service; use that
service's controls. Filesystem deletion is not a guarantee of secure erasure.

## Questions and information you choose to share

Use [Squadra's GitHub issues](https://github.com/dagoSte/squadra/issues) for
questions about this notice or reports. Issues are public: redact logs,
screenshots and archives before sharing them, and never post credentials or
private customer information. Information you submit there is stored by GitHub
and can be read by the author and others; GitHub's
[privacy statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement)
and deletion controls apply. Squadra does not impose a separate automatic
deletion deadline on GitHub issues or on copies other people make.
