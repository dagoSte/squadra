# Usage: local model tokens

Open **Usage** (**Consumi** in Italian), or run `squadra usage --root <registry>`.
`--json` returns the same numbers as structured data. These are recorded tokens,
not a bill, processor utilization or evidence that a window is currently working.
The page has charts and tables for days, windows, models, tasks and observed Codex
quota. It refreshes only while open, through the dashboard's existing serial cycle.

## Project scope

For a verified project registry, token totals start at each window's first recorded
registration of its current identity and session, no earlier than registry creation.
Earlier days and token observations are excluded from every project aggregate,
including models, tasks, charts and optional cost estimates. Registering another
role alias or updating the same registration does not reset that boundary. A new
session gets its own boundary. Unregistered Claude subagents inherit their parent's
boundary; independently registered subagents use their own.

Codex counters are still normalized over the complete available session, so a
pre-project counter remains a baseline. A nonzero delta spanning the join is
excluded, with `project_boundary_overlap` and partial coverage; it cannot be split
accurately. A Claude message first observed before the join and updated afterwards
is excluded for the same reason. A window with no matching registration event is
excluded with `project_registration_unknown`. If the input has no registry creation
event, the response explicitly says **Session history**, with
`scope.reason: project_boundary_unknown`; those totals are not project spending.

The response's additive `scope` field and the page/CLI explain the scope. Original
session files and the numeric calculation cache retain their history. Codex quota
and quota history remain account observations and can predate the project: they
are neither reset nor relabelled as project consumption. This changes no registry
event format and needs no migration.

## What is read

Only locally available sessions belonging to registered windows are selected.
Claude Code files are under `CLAUDE_CONFIG_DIR/projects` (default
`$HOME/.claude/projects`); Codex files are under `CODEX_HOME/sessions` (default
`$HOME/.codex/sessions`). Alias roles of one identity count once. A unique session
prefix can resolve a full ID; ambiguous IDs, missing sources, unsupported clients
and unknown formats show **Not measured**, with a reason. A registered Claude
subagent takes precedence over counting its file under its parent. Symlinks,
nonregular files and files or opened directories owned by another user are refused.
Remote sessions and Cursor usage are not measured by this collector.
A window is matched through the session it was registered with (`agent --session <id>`,
or an identity named `claude-window-<id>` or `codex-thread-<id>`); a window registered
without one is **Not measured** with `session_not_registered`. Only the roots of clients
that have such a window are walked.

Each bounded JSONL line is parsed once. Conversation text may pass through the
parser's memory; it is immediately discarded. Only numeric usage and structural
identity, model, deduplication and timestamp fields are retained. No conversation
text is cached, logged, exported or shown. Usage does not create a registry, append
events, rewrite projections, send telemetry or call a model/provider API. The
protected local `/api/usage` route retains the dashboard's Host and token checks.
It returns tokens only; `/api/state` does not scan transcripts.

## Counts and their limits

- Claude streamed/repeated observations of a message update one sample. Conflicting
  counters are not silently added. Parent and child duplicates count once.
- Codex usage is the difference between successive cumulative counters. Repeated
  totals add zero. The first total is a baseline, not spending attributed to the
  current task. A reset opens a new baseline and declares a gap.
- The four additive kinds are uncached input, cache reads, cache creation and
  output. Codex cached input is subtracted from input once; reasoning is an output
  subset and is never added to the total or charged again.
- A measured zero differs from missing data (`null`). Partial totals are observed
  subtotals with exclusions, not complete session totals. Source changes invalidate
  cached observations. Incomplete trailing lines remain pending.
- Day and hour charts use UTC observation timestamps. Hourly series contain 168
  bins through the current, explicitly partial hour. The last 24 bins are UTC
  hours, not an exact rolling 24-hour interval. Rolling five-hour and seven-day
  values are calculated from samples over their stated endpoints. A Codex delta
  spans an interval: attributing it to its observation time remains estimated,
  including at a model, task or date boundary. An unknown model stays unknown.
- Task accounting starts at the seat's recorded assignment acknowledgement/start
  and ends at delivery, or runs from current review-handoff acknowledgement to the
  code verdict. A new recorded start/acknowledgement opens a correction span.
  Gaps remain unassigned. Distinct overlapping tasks share each additive token kind
  equally; stable task-ID order receives integer remainders. Such numbers are
  marked estimated and conserved, not a semantic reading of the conversation.
- Codex quota is the latest primary/secondary observation per window, retaining
  its actual interval and reset. Percentages are never summed across windows:
  several windows may share an account, and no account ID is collected. Stale or
  expired observations are labelled. These are not per-model allowances. Claude
  transcripts do not supply measured quota.

The collector keeps bounded selected observations. First reads prioritize the
tail, then extend backwards; an unread prefix remains partial. Appends keep the
already counted records and continue both the historical scan and the new tail.
Valid, consistent appends do not reduce a previously measured total. A conflicting
duplicate, counter reset or proven source change remains an explicit limitation;
the collector never hides a correction by keeping the larger of two totals.

Change detection uses the file's device and inode, its size, and SHA-256 samples
of the first up-to-64 KiB and the up-to-64 KiB immediately before the previously
accepted end. Reuse checks the old boundary, not only the new tail. An unchanged
in-process file stat needs no payload read. An append reads bounded samples and
new bytes, without reparsing or recounting old records or hashing the whole
prefix. Replacement, truncation or a changed sample invalidates that source's
observations. **This is sampled change detection, not full-file integrity
verification:** a same-length rewrite outside the sampled regions is not detected
and retains its old counts until the file is replaced, truncated or changes in a
sampled region. A restored cache verifies the same samples before reusing counts.

The dashboard's
collection slice is 250 ms, checked between bounded record parses, with 64 KiB
read chunks and at most 16 MiB per record. The normal backward window is 8 MiB;
finishing one long record may extend it to 16 MiB + 64 KiB, plus the 64 KiB Codex
identity header. Raw fragments never persist between calls. A single parse can overrun the slice;
this is not a hard real-time guarantee. At most 1,000 matched files per identity
and 100,000 retained structural/numeric records per collector are allowed. Discovery
walks a bounded inventory (100,000 entries, beyond which it reports `discovery_limited`)
and resumes across slices: while it runs, the windows it serves and the coverage say
`collecting`. A completed inventory refreshes at most every 30 seconds, or sooner when a
walked folder changes. The explicit Usage CLI and `squadra balance` give each slice the
time left before their 30-second deadline and report partial coverage when it expires.
Within a slice each selected file gets an equal share of the time left, from a rotating
start, so one large file does not hold back another window. While collection runs, the
cache is saved at most every 5 seconds. Automatic Balance reads on the dashboard take one
slice and reuse the same cache.

## Disposable calculation cache

To avoid restarting collection when the dashboard restarts, Usage keeps a private
calculation cache under `~/.cache/squadra/usage-v1/`, using hashed cache and source
keys. It contains only allowed numeric and structural observations: token counts,
model names, times, session and hashed message identifiers, Codex quota figures,
file identity, sampled fingerprints and collection offsets. It never contains
conversation text or incomplete raw lines. Directory permissions are 0700 and
cache files are written atomically with mode 0600. A versioned schema and payload
digest are validated before reuse; missing, corrupt or incompatible entries are
ignored and rebuilt from the original session files. Source ownership and symlink
checks still apply.

The cache is limited to 64 MiB per registry and 256 MiB overall, including temporary
replacement space, with older entries evicted when necessary. Persistence failures
do not prevent collection in memory. The cache folder is opened without following
symlinks from the filesystem root, so when any folder above `HOME`, or `~/.cache`,
`~/.cache/squadra` or its `usage-v1` folder, is a symlink (for example a `HOME` under macOS
`/var` or `/tmp`, or a symlinked `/home`), the cache stays off and the Usage page says so
(`coverage.notices`): figures remain correct, but a dashboard restart reads the session files
again. The location is outside the registry and
project, so ordinary `checkpoint` and `save` do not include it; persistence is
disabled if the destination overlaps either. It is safe to delete the cache:
Usage will rebuild it, and no transcript or registry entry is removed. Deleting
the cache does not remove the original session files.

The protected `/api/usage` response has its own revision, independent of registry
events. The Usage page can request a compact view and send its last revision using
`X-Squadra-Usage-View: compact` and `X-Squadra-Usage-Revision`. A matching revision
returns a small unchanged response. A busy collector returns a small busy response.
In both cases the page keeps its last displayed numbers; while updating they are
historical observations, not a new measurement. First load still shows loading.
New session data and relevant time boundaries invalidate the revision even when
no registry event was added. No new timer, write route, price header or provider
request is involved; callers that do not request the compact view retain the full
schema-1 response, including hourly and rolling fields.

## Suggestions and optional estimates

Suggestions are advisory heuristics with visible measured evidence, never automatic
reassignment or model switching. With at least 1,000 measured tokens, a cache-read
share of 75% suggests retaining useful cached context; an output share of 50%
suggests smaller deliverables when appropriate. An observed Codex quota of at least
80%, no older than 15 minutes and not yet reset, suggests planning work around the
reset. Missing evidence gives no suggestion. No savings or quality improvement is
guaranteed.

Prices are disabled by default. Enter your own rates per million tokens on the
Usage page. They are kept in that browser's local storage and never sent to the
server. The labelled estimate includes only measured kinds/models with supplied
rates; missing rates remain explicit exclusions. A zero-token kind needs no rate.
Partial observations produce a partial estimate. No built-in price, exchange rate,
subscription entitlement or billing total is inferred.

For the CLI, `squadra usage --prices FILE --json --root <registry>` reads the same
`{currency, models}` prices object (or a schema-1 settings document containing
`prices`). `currency` is a three-letter uppercase label; `models` maps observed
model names to nonnegative numeric rates for `input`, `cache_read`,
`cache_creation` and `output`. Up to 100 model rows are allowed. The file is local,
regular, not a symlink, and at most 64 KiB. No rate is inferred for a missing kind.

## Interface for Balance

`scripts/usage.py:collect(events, state, *, roots=None, now=None,
budget_seconds=0.25)` returns schema 1. Pass verified registry data; HTTP callers
cannot select filesystem roots. One server/process cache serves both readers;
Balance never scans transcripts independently. Windows and model rows include
`hourly` and `rolling.h5`/`rolling.d7`, each with coverage, nullable totals and
estimation reasons. Window `recent` is a 900-second observed token total divided
by 15 for tokens/minute, only with fresh source observation and adequate coverage.
Unknown intervals are not padded with zeros.

Browser settings use `squadra.usage.settings.v1` with separate `prices` and `balance`
objects. `usageReadSettings()` and `usageWritePrices(prices)` preserve the Balance
sibling. Balance owns its own reference values; Usage neither interprets them nor
sends them to an endpoint.
