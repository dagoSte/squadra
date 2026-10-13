# memoranda for Squadra

[Italiano](README.it.md)

Keep useful decisions, constraints and lessons after a squad task closes. Each
window declares what is worth remembering; the coordinator collects the declarations,
proposes eligible drafts and reads the outcomes. This plugin never approves memories.
Approval follows the subscriber's scope policy, manual by default. If a scope
approves automatically, further sending stops until the owner accepts that policy.

Installing this MIT plugin is free. The memoranda.dev service starts with a free
trial, then is paid. See [memoranda.dev](https://memoranda.dev) for current terms;
no trial duration or fee is promised here. Publisher: memoranda.

## Connect once

1. Sign in to the memoranda.dev console with your second factor. Open **Guided
   setup**, then **Configure your client**. Copy **Your tenant's address**, shaped
   like `https://mcp.memoranda.dev/t/<space>/mcp`.
2. Open **Agents and keys**. Name the agent and its identity in your memory; create
   its key. The console shows the key only once. Never paste it into chat or a command.
3. Ask the coordinator to install the bundle explicitly: `squadra plugin install
   memoranda`. For a client plugin installation use `python3 -B <release>/scripts/squadra.py
   plugin install memoranda`. Installation copies files; it does not contact memoranda.
4. In Squadra open **Plugins → memoranda → Settings**. Save the address and the key.
   The key is stored locally, owner-only, and is never displayed again. The form
   only says whether it is set. Clear it and save to disconnect. Environment or
   an explicit owner-only token file takes precedence; an invalid supplied key
   refuses instead of falling through to another source.
5. Keep the coordinator window active. It follows [AGENTS.md](AGENTS.md), runs the
   routine at start/resume and after each task close, then reports the outcome.
   The dashboard does not run a scheduler or MCP consumer. Missing instructions,
   a stopped window or a refused operation do not constitute a completed send.

## Da ricordare / Worth remembering

Every assignment asks: what durable decision, constraint or paid-for lesson is
worth retaining, who decided it, how certain is it, and is its content private?
Each window answers at close with an output artifact using `da-ricordare/1`, even
when the answer is no. Do not invent another window's declaration or infer one
from a report. Register [the synthetic example](examples/da-ricordare.json) only
after replacing its task and evidence references with your actual recorded ones.
The collector verifies registered evidence; it withholds private/unanswered items,
and contested items need an independent coordinator disposition.

Only the declared non-private sentence, type, scope, confidence and evidence
references leave the computer. No source file, report, key or ledger is attached.
This is a declaration filter, not a universal secret detector: the author must
remove private data and paths from a sentence declared non-private. Local previews
and ledgers remain private data and must not be published.

## Commands and outcomes

Use Python 3 with the installed bundle and the same HOME as the dashboard.
`<registry>` is the open registry folder, not the project checkout; shell-quote it.

```sh
python3 -I -B "$HOME/.squadra/plugins/memoranda/bridge/memoranda_plugin.py" collect --registry <registry>
python3 -I -B "$HOME/.squadra/plugins/memoranda/bridge/memoranda_plugin.py" send --auto --registry <registry>
python3 -I -B "$HOME/.squadra/plugins/memoranda/bridge/memoranda_plugin.py" status --registry <registry>
```

Automatic send uses every eligible draft and leaves manual requests untouched.
The same locked ledger and durable intent protect both send modes. An uncertain
outcome is never resent automatically; a retry explicitly authorized by the owner
can duplicate a proposal whose reply was lost. The plugin is propose-only, not an
exactly-once delivery system. The registry itself is read without writes, using a
private DB/WAL copy and the bundle's hash-pinned reader.

Run `status` even when `send --auto` stops, including an uncertain outcome or a scope
that approves automatically. Capture and report its exit code and outcomes too.
A collect refusal stops the routine, except E-OUTPUT for the same already saved
preview: retain it and continue, without deleting the preview or ledger.

Manual selection remains available. You can cancel a pending choice and choose again.
Cancelling does not stop a send already running: its admitted batch finishes, about
2.4 seconds per item plus network time. It does not disable automatic sends.
Process a saved selection with the same command using `send` without `--auto`.
A request belonging to another registry is left alone. A newer or cancelled
selection is never erased by an older consumer. No request returns exit **5**;
a successful operation, including no eligible automatic items, returns **0**.
Refused/stopped is 3, internal error 4, interrupted 130. Report fixed error codes;
do not retry automatically. `resolve`, `--resend` and `--accept-scope-policy` require
the owner's explicit instruction. Never propose these same items through a second tool.

## Storage, upgrade and migration

Store-managed code and instructions are immutable during collect/send/status.
Runtime data lives under `$HOME/.squadra/plugins/memoranda/data/`: previews and
`ledger-<registry-first-event-hash>.json`. Panels are disposable projections in
`panels/`. Settings and the key remain in `settings.json` and `secrets/key`.
Directories are private and files owner-only; symlinks/unsafe files are refused.
Migration accepts root-owned system aliases only above the user-controlled tree;
links within that tree are refused and the linked component is named in the error.
An explicit `--out-root` may select another private data directory; keep it stable
for all commands and never reset a ledger to make a refusal disappear. The directory
must be owned by you with mode 0700; ledger and lock descriptors must be regular
files owned by you with no group/other access. Unsafe paths refuse before ledger use.

Stop all producer/consumer processes before upgrading or migrating. Reinstalling
a managed bundle preserves settings, secrets and data. Modified managed files,
unmanaged existing folders and conflicting files refuse; never edit the receipt
or force adoption. Keep a private backup and retry an interrupted installation
with the same bundle.

Migration from the earlier standalone plugin is an owner-controlled operation:

1. Stop its coordinator routine and all sends/status/resolve operations. Back up
   the old plugin folder **and** its separate output directory, including every
   ledger. Keep originals; an old ledger is not a safe rollback after new sends.
2. Move the unmanaged plugin folder aside and install the Store bundle. Do not
   resume the coordinator yet: starting with an empty migrated ledger can resend.
3. Use the local migration helper, with the stopped old folders:

```sh
python3 -I -B "$HOME/.squadra/plugins/memoranda/bridge/migrate.py" --from-plugin <saved-plugin-folder> --from-data <old-output-folder>
```

It copies validated settings, secrets and exact ledger bytes without network
access, rejects missing ledgers/conflicting destinations and never imports old
code or panels. A durable pending marker blocks the routine during partial
migration; retry only with the same inputs. All registry identities, bindings,
proposal IDs, decisions, cursors, policy dispositions and uncertain intents survive.
Do not delete the pending marker to force a send. After success, collect then status
and verify the history before enabling automatic sending. Never run both versions.
Rollback requires stopped writers and the **latest** ledger, never an earlier copy.
The owner's actual installation moves only on the owner's explicit instruction.

Only the bundled reader is supported. Its SHA-256 is checked against the reviewed
pin before a normal import; changed code refuses without being loaded. External
reader paths are not accepted. A new registry build needs a reviewed bundle/pin update.

If E-LEDGER occurs, delivery may be uncertain even if the local save failed. Preserve
the ledger, run status once the private storage is usable, and inspect the memoranda
review queue. A retained sending intent becomes uncertain and is never resent
automatically. Follow the recovery/resolve steps above only on the owner's instruction.
