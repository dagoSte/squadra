<a id="sfida-a-un-difetto-e-banco-comparativo"></a>

# Defect challenges and comparison harness

Read this guide when you need to reproduce a defect against its remedy or test the
comparison between reviewers. These are separate from delivery proof and review:
expected negative results do not make a product green.

<a id="stessa-sonda-tre-sorgenti"></a>

## Same probe, three sources

This is the standalone command. For **declared defect-fix tasks**, use the
[task-bound regression workflow](work.md#defect-fixes-show-that-the-regression-detects-the-original):
the fix's unittest methods run on the registered base and exact candidate,
without a separate positive control. Its receipt (or a reasoned, independently
read exception) is required at close; standalone receipts do not satisfy it.

Prepare an immutable Python probe with `check(repo)` that uses real assertions:

```python
def check(repo):
    import runpy
    value = runpy.run_path(str(repo / "code.py"))["VALUE"]
    assert value == 2, "VALUE must be 2"
```

```sh
squadra challenge --actor reviewer --oracle /evidence/oracle.py --original /checkouts/defect --remedy /checkouts/remedy --positive /checkouts/control --output /evidence/challenge-001 --timeout 30
```

The three Git roots must be clean and committed. The tool extracts the tracked bytes
into three private copies, runs **the same** probe, and records the SHA of the original
code and the executed archive, argv, timings, stdout/stderr and native exit codes.
`matched` requires AssertionError/native1 on the original and pass/native0 on the remedy
and positive control, without instrument errors or uncertain stops. Import errors,
missing files and timeouts do not demonstrate a defect. The probe must not modify the
copies; the originals and probe remain intact. One challenge is not a general audit.
The oracle is trusted code chosen by the squad; the wrapper is not a sandbox for hostile code.

<a id="esempio-offline-ripetibile"></a>

## Repeatable offline example

In an already initialized registry, with a registered actor and no pause:

```sh
squadra bench-example --to /evidence/benchmark-example
squadra bench-run --actor coord --manifest /evidence/benchmark-example/manifest.json --output /evidence/benchmark-run-001
squadra bench-score --actor coord --from /evidence/benchmark-run-001 --oracle /evidence/benchmark-example/oracle.json
```

The example creates **fake** clients; it does not use installed Codex/Claude clients.
An arm with a `cursor` reviewer is refused before any output (`references/runtime.md`).
Offline mode requires that exact synthetic program, with an explicit path and its bytes
verified before any process; there is no fallback to an installed client. Custom clients
go through the `models` boundary with an explicit budget, even when they are fake clients
for a probe. There are four arms: Claude reads a fixture labelled Codex, Codex reads Claude,
Codex reads Codex in a new session, and Claude reads Claude in a new session. The author
labels explicitly refer to **hand-seeded fixtures**, not work produced by models.
K1 builds the fix; K2 contains three defects and two false targets; K3 contains a test
command that masks exit/collection; K4 is the clean control. Sixteen native turns, separate
processes/sessions and registries, one round per case and arm.

The manifest registers in advance the oracle SHA, fixtures/HEAD, arms, requested
client/model, author provenance, 1–10 repetitions and a timeout of 1–3600 seconds.
It does not contain the oracle: `bench-run` never receives it; `bench-score` requires it
after the responses. `RUNS.json` and each turn's files preserve the prompt, argv/permissions,
CLI version, actual session, exit, response, drift, timings, interventions and usage
**only if observed**. Native0 without a valid session/response/completion is insufficient.

`SCORE.json` and `REPORT.html` separate identified defects, false targets, ambiguous
findings, the clean control and a fix verified by a native assertion. Matching uses a
known ID, otherwise a path and a ±3-line window; multiple candidates remain ambiguous.
Scoring rechecks the bytes of native evidence and the response/session/usage. No overall
score proclaims a winner: the example demonstrates how the harness works.

<a id="pilota-con-modelli-veri--autorizzazione-distinta"></a>

## Pilot with real models — separate authorization

Do not start a pilot just because the owner requested a benchmark harness.
First agree on the question, maximum turns, models, quota and boundaries; a budget JSON
contains at least `owner_words` and `max_turns`. A manifest with `mode: "models"`,
verified real CLIs and `bench-run --allow-models --budget-file /evidence/budget.json`
are required. There are no automatic retries. Pause/timeout stops only owned processes;
unverified termination remains uncertain, never accepted.

Prepare candidates with actual author provenance, fresh turns, equal trace visibility
and equivalent capabilities. Default review profiles are **asymmetric**: Codex has a
read-only shell; Claude has only Read/Glob/Grep. Report separate cohorts until they are
comparable. The wrapper does not prove oracle isolation on the same filesystem or freedom
from trace contamination: for a pilot, keep the oracle outside the resources accessible
to reviewers and provide it only for scoring. Offline mode verifies the bytes of the
shipped synthetic program; the wrapper remains a mechanism between trusted windows,
without a network or filesystem sandbox for hostile code.

Quota, tokens, actual model and cost may remain unknown; do not invent them.
Repeated samples and positive/negative controls are necessary for quality inferences;
one turn or the self-test does not demonstrate Squadra's superiority.

<a id="readme-selftest-summary"></a>

## Self-test, challenge and comparison: the README summary

*Moved from the README, word for word.*

```sh
squadra selftest --output /durable/selftest-001
squadra challenge --actor reader --oracle /evidence/oracle.py \
  --original /checkouts/original --remedy /checkouts/remedy \
  --positive /checkouts/positive --output /evidence/challenge-001 --timeout 30
squadra bench-example --to /evidence/benchmark-example
squadra bench-run --actor coord \
  --manifest /evidence/benchmark-example/manifest.json --output /evidence/benchmark-run
squadra bench-score --actor coord --from /evidence/benchmark-run \
  --oracle /evidence/benchmark-example/oracle.json
```

- **`selftest`** checks Git, SQLite, local HTTP, JavaScript and fake clients, keeps native logs and `RESULT.json`, and makes no model calls: it tests the toolkit, not your product or a model.
- **Standalone `challenge`** runs one immutable `check(repo)` oracle on three clean, committed roots, in private copies that never modify the originals. `matched` needs a real assertion failure on the original and passes on remedy and positive control; instrument errors are not defects. It is bounded, not an audit or a sandbox for hostile code.
- **The offline comparison** runs exact bundled **fake clients**, not installed models, in four author/reader arms over four seeded cases, with a preregistered manifest and an oracle the run never sees; it never claims a winner. A pilot with real models needs the owner's separate authorization, an explicit budget and `bench-run --allow-models --budget-file`, with no automatic retries and no invented usage or cost; an offline run never shows model superiority.
