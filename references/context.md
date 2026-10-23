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
