<a id="client-nativi-opzionali"></a>

# Optional native clients

The runner starts **new non-interactive turns**: it does not connect two already open
windows on its own or promise to wake them. Register existing windows and use mail/ACK.
Consult the local CLI with `codex exec --help` or `claude --help`; incompatible flags
are a recorded failure, not a reason to remove permissions or invent fallbacks.

**Cursor is never started automatically.** `cursor-agent`, even authenticated with a key
and using an empty HOME, downloads and runs team hooks at startup, as well as user and
enterprise hooks, without a sandbox; no documented option disables them together with MCPs.
For a `cursor` window, a `read` or `build` turn (and a benchmark arm) stops before any source
snapshot, directory or process is created, with “Squadra does not launch cursor-agent:
Cursor runs team, user and enterprise hooks unsandboxed. Cursor seats work interactively”.
Cursor windows work interactively with the squadra CLI under the same rules (a non-author
reviewer, bound proof); `prove` remains available. Squadra does not read, pass or store
Cursor credentials. This refusal will be reconsidered only when a Cursor version documents
a switch that disables all hooks and MCPs, confirmed by a non-author source review and an
actual capability test.

```sh
squadra run --actor builder --task T1 --mode build --prompt-file /evidence/build.txt --timeout 600
squadra run --actor reviewer --task T1 --mode read --stage code --prompt-file /evidence/read.txt --timeout 180
```

The task must be acknowledged; the builder must own it; the reviewer must have a different
identity from the authors. `--cli /path/to/client` allows an explicit binary.
Prompts are passed from files through stdin, never through shell concatenation.
No model choice is hardcoded: `agent --model` is optional, and the choice must be agreed
with the owner. The toolkit does not estimate unknown costs. These commands consume the
chosen client's quota; `selftest` uses fake clients only and consumes no quota.

Codex: JSONL, a read-only sandbox for reviews/workspace-write for builds,
approval_policy never; reviews ignore user config, require a schema and refuse checkouts
with `.codex/config.toml` to avoid unexamined project MCPs. Reviews run on a clean copy;
native authentication is preserved. Claude: JSON, safe-mode reviews, Read/Glob/Grep only,
empty and strict MCP, dontAsk, no Chrome; builds use acceptEdits without a global permission
bypass. These profiles are not a security boundary against a hostile user of the same
account; do not claim they protect every file on disk. The reviewer's shell probes run
separately in isolated synthetic copies.

A valid result requires a session UUID and an actually completed turn, the expected native
exit code, a non-empty response and, for reviews, well-formed verdict/summary/findings/
coverage/limitations. Exit 0 without a result, a contradictory response or code changed
during the review fails. The actual session, CLI --version, usage and observed model are
separate from the requested model; a model not exposed remains unknown. The full log,
prompt, schema and response are kept locally. Do not put secrets in prompts or in a
dashboard you intend to share.

**A completed turn is not an accepted review.** The coordinator reads the result,
evidence and limits; the reviewer records the review only against the exact candidate.
`changes-required` requires corrections and new proof, within the budget. Native failure,
quota exhaustion, timeouts and unavailable tools preserve the evidence; no endless retries.

`cancel` records a stop request; the runner checks pause/cancel and timeout, terminates
only the process group it started and saves the native result. A forcibly killed runner
process may leave a run `running`: `doctor` reports it without inventing an outcome.
Reconcile the process actually owned before releasing resources.

Synthetic tests cover both clients' formats and argv. Compatibility of a specific version
with a real turn and the value of its results require separate, authorized verification:
fake clients do not demonstrate them.

If the system denies a signal, the result is `cleanup-unknown`: the receipt, available
native exit code and logs remain saved, but the descendants' termination is unverified.
`doctor` and the dashboard keep it among the outcomes to reconcile. Physically check the
owned processes and retain a reconciliation receipt before releasing resources; do not
turn this state green. A POSIX process group does not include children that change sessions.
Windows/taskkill and real model turns require specific qualification: macOS tests do not
qualify them.
