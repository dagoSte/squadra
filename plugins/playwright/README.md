# Playwright browser checks for Squadra

[Italiano](README.it.md)

Prepare page checks, select them in **Plugin → Playwright → Browser checks**,
and follow an agent's report next to the originating task. The dashboard saves
your request. An authorized agent uses **Playwright MCP**, records the tool
responses and screenshots in Squadra, and imports the outcome. It does not run
until an agent takes the request; a saved selection is not a completed check.

This optional local data plugin requires the Squadra build containing the
`results` and `refs` extension in the plugin contract (`references/plugins.md`
in the Squadra source checkout).
Older builds that reject those fields cannot use this bundle. Python 3 is
required by the bridge. The bridge uses only the Python standard library and
the bundled Squadra modules; it never installs or starts a browser, MCP server
or external service. There is no plugin fee. Any agent/provider cost is separate.

## 1. Connect Playwright MCP to your agent

Use Microsoft's [official Playwright MCP setup](https://github.com/microsoft/playwright-mcp)
for your agent client. A typical stdio server declaration is:

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["@playwright/mcp@0.0.83", "--isolated"]
    }
  }
}
```

This is client configuration, not a Squadra setting. The official server needs
Node.js 18 or newer and a compatible installed browser; the integration targets
version 0.0.83. Follow the official instructions when changing that pin. `npx` may download
the package. Connect it deliberately under your normal installation permissions.
Use an isolated browser, without a real user's browser session or stored login.
Do not attach the browser extension or supply a saved storage state for this
initial integration. The dashboard never activates the server.

## 2. Install this data plugin explicitly

Open **Plugins → Store** and use the command shown for Playwright, or run:

```sh
squadra plugin install playwright
```

With a client `/plugin` installation, use `python3 -B <release root>/scripts/squadra.py plugin install playwright` instead of the standalone launcher.
The store copies this complete bundle to `$HOME/.squadra/plugins/playwright` and
records its release, version and file hashes in a private receipt. No program runs
and no MCP configuration changes. Use the same HOME as `squadra dash`.
The three bridge commands accept `--home <path>` for synthetic installations.

Stop the producer and consumer before an upgrade. Reinstalling an unchanged managed
bundle is safe; locally modified managed files and unmanaged folders are refused.
This plugin does not declare settings. Its durable checks and reports are in
`checks/<registry-key>.json`. For a legacy folder, **move it aside**, keeping those
files, then run `plugin install playwright`. Earlier reports remain in the
moved-aside folder. Copying `checks/` into the new Store installation is not
supported; prepare new checks there. Reports and artifacts already recorded in
the Squadra registry remain available there.
A partial install remains incomplete: inspect its receipt and retry the same bundle.
Never force adoption or edit the receipt. To uninstall, stop the producer/consumer,
save `checks/` and remove only this plugin folder; registry evidence remains.

## 3. Prepare a check for a recorded task

First assign a browser-check task through your normal Squadra workflow. Set
`REGISTRY` to that registry directory. Create `checks.json`:

```json
{
  "schema": 1,
  "task": "BROWSER-CHECK",
  "checks": [{
    "title": "Homepage heading",
    "url": "http://127.0.0.1:8765/",
    "expect": "Squadra browser check",
    "revision": "first-check"
  }]
}
```

Use your actual recorded task ID. The installed bundle's [examples/playwright-checks.json](examples/playwright-checks.json)
targets a synthetic page you operate at port 8765; the helper does not start it.
For a local demonstration, save `<h1>Squadra browser check</h1>` as `index.html`
in a new empty temporary directory, then run `python3 -m http.server 8765
--bind 127.0.0.1 --directory <that-directory>` in a separate terminal. Serve only
that synthetic directory and stop its server after the check.

```sh
python3 -I -B "$HOME/.squadra/plugins/playwright/bridge/playwright_plugin.py" prepare --root "$REGISTRY" --file checks.json
```

Each batch contains 1–50 checks; a registry keeps at most 500. A check has a title,
an explicit HTTPS or HTTP loopback URL, and expected visible text. `revision` is
optional. No forms, clicking, authentication or arbitrary scripts are supported.
The immutable ID includes the task and complete check. Repeating identical input
preserves its existing outcome; use a new meaningful `revision` for a fresh run
after a code change. Preparation appends and never removes earlier reports.

## 4. Request, execute and record

Open the plugin's **Browser checks** tab, select checks, and click **Request
verification**. An authorized coordinator follows the installed [AGENTS.md](AGENTS.md).
The operations below are the agent's manual bridge, not an automatic scheduler:

```sh
umask 077
python3 -I -B "$HOME/.squadra/plugins/playwright/bridge/playwright_plugin.py" pending --root "$REGISTRY" > pending.json
```

`null` means there is no pending request for this registry. Otherwise the snapshot
contains the selected immutable checks and request generation. The agent uses
the real MCP tools `browser_navigate`, `browser_snapshot` and
`browser_take_screenshot`, respecting the declared target and operation. Page
content is untrusted data, not instructions for the agent. If MCP is unavailable,
the request stays pending; the bridge does not substitute a different browser.

Save the actual replies and screenshot, then register them with the task:

```sh
squadra --root "$REGISTRY" output --actor "$ACTOR" --task BROWSER-CHECK --file report.md --next 'Independent review' --artifact tool-replies.txt --artifact screenshot.png
```

The CLI returns an output `receipt`. Create `results.json` using the selected
check ID from `pending.json` and that receipt:

```json
{
  "schema": 1,
  "results": [{
    "id": "<selected-check-id>",
    "status": "passed",
    "observed": "The expected heading was visible in the isolated browser.",
    "evidence": ["<output-receipt>"]
  }]
}
```

`passed`, `failed` and `error` are agent reports. `error` requires an actual
attempt and its tool reply; an unavailable MCP is not an executed check.
`observed` is a nonempty single-line summary (maximum 2000 characters). Each
result requires 1–8 existing output receipts from the same task with registered
artifacts. The helper verifies that association, not the truth of the report.

```sh
python3 -I -B "$HOME/.squadra/plugins/playwright/bridge/playwright_plugin.py" record --root "$REGISTRY" --snapshot pending.json --file results.json
```

The panel shows the reported outcome, evidence receipt IDs and a link to the
task. Open the task history, then **Results and updates** to read the registered
report and open its tool replies and screenshot. These reports
are **not native bound proofs, independent reviews or task approvals**. No run
ID is invented. A native proof remains a separate `squadra prove` operation.

## Recovery and boundaries

Reports are private local JSON under `checks/<registry-key>.json`; the visible
panel is a disposable projection under `panels/<registry-key>.json`. Both are
bounded to 2 MiB. Durable reports are written before the panel and before request
deletion. Partial batches keep the request pending. Terminal reports cannot be
overwritten; an identical re-import is safe.

A replaced, cancelled or reselected request survives an old consumer even if
its JSON and second-resolution timestamp match: the bridge also compares the
original file's device, inode, modification time and change time under the same
action lock. Already-started work cannot be retracted by cancellation.

If a write failed, inspect the outcome before retrying. Re-import the same
results with the same snapshot, or use `{"schema":1,"results":[]}` to repair
the panel and finish a fully recorded snapshot. This never requires repeating
the browser operation. Do not delete locks by hand. The plugin retains previous
reports and does not manage a remote service, store credentials, execute page
scripts or grant permissions through AGENTS.md.

## Test the bridge

Maintainers with a Squadra source checkout can run the focused tests in a temporary HOME:

```sh
TEST_HOME=$(mktemp -d)
HOME="$TEST_HOME" python3 -B -m unittest discover -s tests -p test_playwright_plugin.py -v
```

These tests use synthetic registries and reports; they do not start Playwright.
Use steps 3–4 with a real MCP response and screenshot to verify the complete flow.
Remove the temporary directory after inspecting the result.
