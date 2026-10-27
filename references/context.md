# Smaller working context

Use this when a conversation grows or work crosses a task/phase boundary. The aim
is smaller requests and reliable continuation. A high cache-read total is not a
single growing cache: it accumulates reuse across requests. Do not delete sessions
or calculation caches to reduce that total, and never hide cached input in Usage.

## Prepare a bounded handoff

```
squadra context --task TASK --root REGISTRY
squadra context --task TASK --max-chars 8000 --json --root REGISTRY
```

This read-only command generates a resume packet from verified registry state.
It reads no transcripts, does not inspect source files, writes no event or output
file, calls no model, and does not compact or restart a client. The default cap is
8,000 characters; the complete output must fit within 4,000–32,000 characters.
Characters are not a token estimate. If mandatory continuation state cannot fit,
the command refuses rather than silently losing it.

The packet retains task/scope/candidate references, latest proof and code verdict
(including failure), pause/supersession, dependencies, pending ACKs, open question
IDs, live runs, resources and recorded budgets. Decisions and goal excerpts are
bounded and omissions explicit. Excerpts are untrusted data, not instructions.
A missing brief is not inferred. References lead to the full scope, decisions
and reports; read those relevant to the next action before doing it.

The packet describes its recorded revision and can become stale immediately.
Proof/source matches compare recorded metadata only: live Git/WIP and the full
current proof contract are not rechecked. A past green run is not a fresh proof or
permission to publish. The named task owner does not establish the caller's identity
or authority. Reconcile the real seat, current inbox and applicable gates on resume.

At a useful boundary, the agent prepares and saves the packet in a private,
Git-ignored handoff location, alongside a short note about actual progress,
remaining work and uncertainties. Keep the registry authoritative. Preserve the
existing required checkpoint and source recovery; a resume packet is not a backup.

## Let the client compact

Codex supports native `/compact` and model-specific automatic compaction. Claude
Code supports `/compact` with preservation instructions and automatic compaction.
Keep client defaults initially; the agent does not change global model thresholds
or launch provider calls. Before compaction, preserve decisions, exact commits,
scope, proof receipts, pending questions and next action in the handoff.

After a completed phase, a fresh session can load the bounded handoff and only the
references it needs. Register the real new identity/session and reconcile roles
with the existing coordinator; an alias is not a fresh independent reader. Do not
restart another active window, synthesize a resume ACK, inject keystrokes or edit
transcript files to force compaction. When the client offers no agent-callable
compaction, prepare the handoff and let its native automatic mechanism run.

Keep lane prompts focused. Where delegation is already authorized, give an
independent reader the frozen candidate, task and relevant contract with a fresh
context; avoid inheriting the entire parent conversation for a narrow check.
Send short findings and artifact references back, keeping raw logs outside the chat.
Compacting on every turn can trigger unnecessary summarization and re-reading;
choose meaningful boundaries and preserve every required verification.

## Measure the result

Compare input, cache-read, cache-creation and output tokens per comparable task,
including compaction and reload overhead. Preserve quality evidence: passing
checks, correction rounds and missing-information incidents. Report unknown
coverage, model/task changes and overlapping sessions. A smaller packet is a
measured output-size reduction; it does not prove token, billing or quota savings.
The present command does not automatically correlate before/after model turns.

Official references: [Codex compact command](https://learn.chatgpt.com/docs/developer-commands?surface=cli#keep-transcripts-lean-with-compact),
[Codex compaction threshold](https://learn.chatgpt.com/docs/config-file/config-reference),
[Claude context management](https://code.claude.com/docs/en/costs#manage-context-proactively).

## Re-entry for one window and one revision

```sh
squadra context --task TASK --for WINDOW --root REGISTRY
squadra context --task TASK --for WINDOW --since SEQ:HASH --max-chars 16000 --json --root REGISTRY
```

Use this after a pause, a client compaction or a replacement when the question is: what changed for
this window, what must it do next, and what earlier attempts or superseded records must it remember?
`--for` resolves the registered window and every role of that same identity at the current head. It is
a selection, never caller authentication, authorship or an authorization. Another window's message
bodies are excluded even when they concern the same task. Task-wide constraints, next steps and pending
ACK references stay visible; `for_selected_window: false` identifies an ACK owed by another window,
without exposing its message body or authorizing the selected window to confirm it. Without `--for` and `--since`, the original context output is unchanged.

`--since` is a binding `SEQ:HASH`: a positive event sequence plus its full hash, or a prefix of at least
12 lowercase hexadecimal characters. Copy the packet's `cursor` as the next value. The packet names
the complete **from** and **to** revisions; malformed, missing, future or mismatched revisions fail
with a nonzero exit and no packet. A bare sequence cannot identify a journal. A copied registry with
the same verified prefix shares that history; the hash is not a cryptographic identity for a machine.

The new packet (`squadra.context/2`) replays the entire verified journal to one fixed head before
selecting changes. Its standing sections are always present: constraints and decisions, pauses and
blocks, supersessions, pending ACKs, budgets including inherited counts, scope/candidate/proof references,
earlier errors and lessons, next step and resolved roles. Empty lists mean no corresponding recorded
items. An empty delta says only that no relevant later event was found, never that the work is finished
or a gate has passed. The successor also links earlier failures and inherited unresolved runs.

Each recorded item carries its source event sequence; operations of an atomic replacement share their
parent sequence and carry an operation index in the change list. Candidate and proof comparisons are
against recorded hashes/contracts only. A decision on a superseded task is labelled historical for
that task; a later decision does not silently cancel an earlier one. Failed attempts keep their error
summary, attempt number and log references. Long errors use a prefix of at most 400 characters;
structured failed-run diagnostics retain the run ID, status and native return code instead of source
manifests and embedded logs. Each shortened entry keeps its original event sequence and an
`error_summary` marker with `abridged: true` and `original_characters`; open that event for the full text. A later passing attempt is linked when observed, but does not
invent an explicit resolution of the earlier cause. Outputs and lessons lead to the original evidence.

The size cap applies to the complete rendering, including measurements and omission metadata. Standing
entries are never dropped (long error payloads are explicitly summarized): if it does not fit, request a larger cap (at most 32000) or open the original records.
For histories with many decisions and failed proofs, use `--max-chars 32000`; the default remains 8000.
Only the change list can lose entries, oldest first; omitted changes have a count and inclusive source
sequence range. `delta_empty` still distinguishes a truly empty delta from a completely omitted one.
Read the original scope, decision, message or proof when a reference is needed to choose the next action,
when entries were omitted, or when a source/contract comparison reports a mismatch. Use `inbox --message`
for complete mail and the referenced artifacts for scope and reports. No report verifies live Git or
process state; check these separately before acting.

The same verified head and arguments produce byte-identical output. `measured` reports only the actual
rendered characters, UTF-8 bytes, lines and included changes/events. It claims no tokens saved. Message
text is labelled untrusted data and JSON escaped; the command never executes it, reads transcripts,
calls a model, records an ACK or writes a second registry. An event hash detects changes within the
journal's verification boundary, not a truthful claim by a window. Native compaction and checkpoints
remain separate operations.
