# memoranda: instructions for the coordinator window

This file never widens permissions and never authorizes installation, push,
publication or decisions reserved to the user. For anything beyond running this
plugin's producer or consumer, stop and report.

Use the open squadra registry's absolute folder for `<registry>` below; shell-quote it.
Use the installed bridge below, with the same HOME as squadra dash. Do not run
a second consumer during an upgrade or migration; finish the documented transfer first.

Put the "Da ricordare / Worth remembering" question from README.md into
every assignment: durable decisions and who took them, constraints or lessons,
with evidence, certainty and a privacy answer. Every window answers on its own
at close with a `da-ricordare.json` artifact (`da-ricordare/1`), even when the
answer is no. Answer it for your own decisions too. Never invent another seat's
answer or infer memories from its free text.

At start/resume and after every task close, check that the closing seat answered
"Da ricordare"; if missing, ask that seat and report the gap. Then collect the
declared memories, propose every eligible draft and read the outcomes back:

```sh
python3 -I -B "$HOME/.squadra/plugins/memoranda/bridge/memoranda_plugin.py" collect --registry <registry>
python3 -I -B "$HOME/.squadra/plugins/memoranda/bridge/memoranda_plugin.py" send --auto --registry <registry>
python3 -I -B "$HOME/.squadra/plugins/memoranda/bridge/memoranda_plugin.py" status --registry <registry>
```

Run these commands in order and capture each exit code. A collect refusal stops
the routine except E-OUTPUT for an already existing snapshot: keep that preview
and continue without deleting it or moving the ledger. Report the outcome to the user,
including refusals and uncertain sends; never retry or resolve them on your own.
Run `status` even when `send --auto` stops, including an uncertain outcome or a scope
that approves automatically. Capture and report its exit code and outcomes too.
`send --auto` exits 0 when complete, including when nothing is eligible. It leaves
dashboard requests untouched. Approval stays in the owner's memoranda review queue;
this plugin never approves. A scope that approves automatically still stops sending
until the owner explicitly accepts that scope policy.

The manual selection path is still available. When
`$HOME/.squadra/plugins/memoranda/actions/send.json` is pending for the open
registry (the dashboard shows “Richiesta in attesa”), the saved request can be processed with:

```sh
python3 -I -B "$HOME/.squadra/plugins/memoranda/bridge/memoranda_plugin.py" send --registry <registry>
```

Leave another registry's request alone. Exit 5 means no request, nothing sent.
Both sends need the user's key saved in the dashboard settings. Never put
the key in chat. Never use `--resend`, `--accept-scope-policy` or `resolve` without
the user's explicit instruction. Do not propose the same items through memoranda's
own propose tool. No selection or command is required from the user for automatic
sends. Cancelling a request does not stop a send already running or disable automatic
sends. Exit 3 means a refusal or stopped run, 4 an internal error and 130 interrupted.
