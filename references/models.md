# Window model and effort

`squadra usage` and the Windows, Usage and Balance dashboard views show the latest
verified model/effort observation, its source and timestamp. This is the setting
reported by the client, not a measurement of cognitive work or a quality score.
It is separate from the model that consumed the most tokens. A Codex `turn_context`
provides `model` and `effort`; a Claude assistant sample provides its model, with effort
unknown when absent. Missing/unsupported data stays unavailable. Parent windows do
not inherit a child's model. A changed pair is labelled as an observed client change;
when the client does not expose its reason, Squadra says so instead of guessing.

## Explicit window choice

Each new window asks the owner first; an answer already given for that window is kept.
These commands operate on the registered real identity, session and client. Role aliases
share the choice. A replacement session asks again. Reading a preference writes nothing.

```sh
squadra models --actor builder
squadra models --actor builder --auto on
squadra models --actor builder --auto off
squadra models --actor builder --auto on --profiles /path/to/profiles.json
squadra models --actor builder --task T1
squadra models --actor builder --task T1 --mode read --json
```

`agent --auto-models on|off` records an explicit answer during registration. Automatic
selection is off until answered on; a new CLI registration with an unanswered choice
cannot start a native model turn. Legacy registrations with no choice remain compatible.
The choice is retained in existing `agent` events, without a new event format. A later
ordinary re-registration preserves it. No personal Codex, Claude or Cursor defaults are
rewritten.
Turning the option off keeps its configured profiles for a later re-enable; disabling
it while a rejected turn is ending also prevents that turn's automatic fallback.

## Profiles and conservative selection

[model-profiles.json](model-profiles.json) contains the proposed profiles shown when
enabled: Codex uses GPT-6 Luna / low and GPT-6 Astra / max; Claude uses Sonnet / low
and Opus / max. These are editable choices, not promises that an account can invoke them
or universal rankings of all available models. Confirm models, efforts and required tools
in the client's current catalog. A catalog is not proof of entitlement or real-turn
compatibility. No model/provider call is made to read or save the preference.

Only an existing, valid light-route contract is eligible for economy: up to the original
light policy's file/line limits. Before changes exist this is a bounded plan, explicitly
labelled as such; when a diff exists it is measured. Large, protected, unmeasurable,
dependent, fixing, release or plan work uses quality. Independent reviews use quality.
Unknown complexity never authorizes a cheaper profile. Selecting a profile does not
change task route, proof, reader, capabilities, permissions or budgets. The native
runner keeps every existing sandbox, settings and review flag.

Every automatic run prints its requested model, effort and fixed reason to stderr,
preserving the CLI's bounded write receipt on stdout. Its `run.start` records
`model_selection`, `requested_model` and `requested_effort`. `models --actor WINDOW`
also shows the last automatic selection. Requested settings and actual observed models
remain distinct; a client's effort overrides or model remaps are not fabricated as
measurements.

At most one fallback is attempted after a structured model-unavailable error, or an
explicitly model-specific quota error, before operational output and without changed
source bytes. An economy turn may step up to quality. A quality turn has no default
cheaper fallback. A shared account quota, generic error, timeout, interrupted turn or
tool output never triggers replay. The second attempt is separately recorded with the
original reason, previous pair and run, remaining timeout and ordinary task budget.
An explicitly configured profile can have one `fallback` entry with model/effort;
the owner must choose it to preserve the task's required capabilities. No endless retry.

## Client boundaries and checked documentation

Checked on 2026-11-11 against Codex CLI 0.167.1 and Claude Code 2.1.322 help, local
Codex turn records and the following official documentation:

- [Codex models and reasoning](https://learn.chatgpt.com/docs/models?surface=cli),
  [CLI flags](https://learn.chatgpt.com/docs/cli/reference),
  [configuration keys](https://learn.chatgpt.com/docs/config-file/config-reference) and
  [App Server](https://learn.chatgpt.com/docs/app-server): CLI `-m` selects a model,
  `-c model_reasoning_effort=...` configures effort; a managed app-server can pass
  model/effort per turn and discover supported values with `model/list`.
- [OpenAI model selection](https://developers.openai.com/api/docs/guides/model-selection)
  and [reasoning](https://developers.openai.com/api/docs/guides/reasoning): the same
  effort names are not equivalent capabilities across models. Max remains a single-agent
  choice; Ultra introduces subagents and is not silently enabled by these profiles.
- [Claude Code model configuration](https://code.claude.com/docs/en/model-config) and
  [effort](https://platform.claude.com/docs/en/build-with-claude/effort): `--model` and
  `--effort` are client controls, with support and overrides that depend on the model
  and environment. Aliases can resolve to newer models.
- [Cursor Router](https://cursor.com/docs/cursor-router) and
  [CLI parameters](https://cursor.com/docs/cli/reference/parameters): Cursor has its own
  model catalog, Auto/Router modes and availability limits. The CLI documents `--model`
  and model discovery, without a general `--effort` counterpart. Do not translate Codex
  effort flags to Cursor. Squadra's existing refusal to launch Cursor automatically
  remains; interactive Cursor windows keep mail, proofs and reviews. No compatible
  Cursor binary or hook-disable capability was verified in this environment.

Squadra starts new native turns; it does not attach to or change an already running
interactive window. Use the owning client's controls for such a window. Synthetic
tests verify selection and argv contracts without spending quota; they do not prove
the native model will honour every configured setting or preserve output quality.
