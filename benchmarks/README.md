# hive-bench

Real tool-execution benchmark for Hive. Nothing is simulated:

- each `tasks/<id>/repo/` is a real Python repo with a real failing pytest suite
- the agent acts through real tools (`list_files`, `read_file`, `grep`,
  `run_tests`, `write_file`, `finish`) executed by the harness
- `tests/` is read-only — a resolve can't be gamed by editing tests
- resolve = a real `pytest` run at the end of the episode
- LLM calls are real calls to an OpenAI-compatible endpoint (native function
  calling); token counts come from the API `usage` field

**Baseline** sends every action decision to the LLM. **Context** is the
escalate-only control (CPU policy handles nothing). **Hive** routes mechanical
transitions through `hive.harness.load_routing_policy()`, compresses tool
observations via `stack.compress()`, and recalls/records fixes via causal
memory (`stack.brain`).

---

## Results

Three tiers, easiest to hardest. The hard tier is the headline — it's the one
built to *separate* the arms.

### Hard tier — discriminating tasks, n=15 (the headline)

Six tasks ([`tasks/suite.hard.json`](tasks/suite.hard.json)) authored to
separate the arms — every oracle rule disclosed in the problem statement, spec
review equalized across all arms, hidden tests injected only at grading.
15 repeats × 6 tasks = 90 episodes per arm, `temperature=0.7`, memory fresh,
commit `79a24c7` (clean tree).

| Task | baseline | context | hive |
|---|---|---|---|
| sliding-window-limit | 15/15 | 15/15 | 15/15 |
| snapshot-event-fold | 15/15 | 15/15 | 15/15 |
| kway-merge-dedup | 15/15 | 13/15 | 10/15 |
| reservation-expiry | 15/15 | 15/15 | 15/15 |
| idempotent-outbox | 15/15 | 15/15 | 15/15 |
| lru-ttl-cache | 2/15 | 1/15 | 4/15 |
| **Total** | **77/90 (86%)** | **74/90 (82%)** | **74/90 (82%)** |

| Arm | mean LLM calls | USD total | USD/resolved |
|---|---|---|---|
| baseline | 7.31 | $0.368 | $0.0048 |
| context | 7.20 | $0.394 | $0.0053 |
| hive | **3.04** | **$0.214** | **$0.0029** |

Verdicts (exact McNemar, n=6 tasks): all pairs **not_separable** (p=1.0, zero
discordant tasks). Hive matches baseline/context task-for-task at **58% fewer
LLM calls** and ~45% lower cost. `lru-ttl-cache` is the only task still
discriminating (hive leads it 4/15 vs 2/15, 1/15).

**Provenance:** [`docs/benchmarks/hive-bench-hard.json`](../docs/benchmarks/hive-bench-hard.json)
(git_sha `79a24c7`, clean). Full run history, retractions, and closed levers:
[`docs/benchmarks/PROVENANCE.md`](../docs/benchmarks/PROVENANCE.md) +
[`PROVENANCE.json`](../docs/benchmarks/PROVENANCE.json). **Contamination note:**
`oracle/tests/` are git-tracked (`oracle_public_in_git=13`), so absolute
resolve rates are contaminated for a model that has seen this repo — the
routing/cost delta is not.

**Reproduce:** `python scripts/hive_bench.py --backend openai --endpoint <EP>
--api-key-env <KEY> --model <M> --suite benchmarks/tasks/suite.hard.json
--arm all --repeat 15 --temperature 0.7 --memory fresh`.

### Trained CPU policy — hard tier, held out of training

The line above uses the **rule-based** state machine. This is the same suite with
the **trained `CPURouterPolicy`**, which is the product's actual router.

The policy is a RandomForest fitted on `benchmarks/trajectories-rebuilt.jsonl`
(1,503 usable decisions from the **16 tasks outside the hard tier**), so the hard
tier is genuinely held out — it is not train-on-test. It reaches

| arm | resolve | mean LLM calls | usd |
|---|---|---|---|
| trained policy | 73/90 | 2.98 | $0.2116 |

against the rule-policy arm's 74/90 at 3.04 calls, and the LLM-everything
baseline's 77/90 at 7.31 — i.e. **the same task-for-task outcome at 59% fewer
LLM calls than baseline**. Exact McNemar over per-task majority: baseline vs
trained p=1.0 (0 discordant tasks), rule-hive vs trained p=1.0. That is the point:
a policy that has never seen these tasks routes them as well as the hand-written
one, so the routing win is not an artifact of hand-tuned rules.

Per-task: sliding-window-limit 15/15, snapshot-event-fold 15/15,
reservation-expiry 15/15, idempotent-outbox 15/15, kway-merge-dedup 11/15,
lru-ttl-cache 2/15.

Artifact: [`docs/benchmarks/hive-bench-hard-trained.json`](../docs/benchmarks/hive-bench-hard-trained.json).
**Reproduce:** `python scripts/rebuild_trajectories.py --out benchmarks/trajectories-rebuilt.jsonl`
then `python scripts/hive_bench.py --backend openai --endpoint <EP> --api-key-env <KEY>
--model <M> --suite benchmarks/tasks/suite.hard.json --arm hive --policy trained
--policy-path benchmarks/cpu_router.joblib --repeat 15 --temperature 0.7`.

### Two new tasks — A/B result (annotated: ceiling)

`semaphore-fair-queue` and `greedy-wrap-boundary` were added to widen the suite
and balance its families. Verified by `--verify-tasks` (smoke RED→GREEN, oracle
RED→GREEN) and run at n=15 × 3 arms:

| arm | resolve | mean LLM calls | usd |
|---|---|---|---|
| baseline | 30/30 (100%) | 6.93 | $0.1086 |
| context | 30/30 (100%) | 6.73 | $0.1082 |
| hive (trained) | 30/30 (100%) | **3.00** | **$0.0559** |

**Both tasks are at ceiling for every arm**, so this run does **not**
discriminate on resolve — it shows the tasks are solvable and that hive keeps
its ~48% cost advantage, but adds no resolve signal. It is reported separately
and never pooled silently with the 6-task tier. Artifact:
[`docs/benchmarks/hive-bench-hard-new2.json`](../docs/benchmarks/hive-bench-hard-new2.json).

### Capability tier — held-out tasks, three arms

Six harder tasks graded against **held-out** pytest suites the agent never
sees (`grade_patch` replays the agent's writes into a pristine repo and
injects the hidden tests only there). The visible `smoke/` suite covers the
*disclosed* spec — it fails on the buggy repo, so a green `run_tests` means
the stated requirements are met; the oracle keeps the edge cases. 5 repeats ×
6 tasks = 30 episodes per arm, `temperature=0.7`, memory fresh.

| Arm | Resolve rate | 95% CI | pass^5 | USD/resolved |
|---|---|---|---|---|
| baseline | **97%** (29/30) | [83%, 99%] | **83%** pass^5 | $0.0103/resolved |
| context | **87%** (26/30) | [70%, 95%] | **67%** pass^5 | $0.0129/resolved |
| hive | **93%** (28/30) | [79%, 98%] | **83%** pass^5 | $0.0084/resolved |

Verdicts (exact McNemar over per-task majority outcomes, n=6 tasks): baseline vs context: **not_separable** (p=1.0); baseline vs hive: **not_separable** (p=1.0, zero discordant tasks); context vs hive: **not_separable** (p=1.0). Hive matches baseline task-for-task while spending **37% fewer LLM calls** (7.33 vs 11.6 mean).

**Provenance:** all three arms fully measured in one run (90 episodes).
Raw artifact: [`docs/benchmarks/hive-bench-capability.json`](../docs/benchmarks/hive-bench-capability.json).

**Reproduce:** `python scripts/hive_bench.py --backend openai --endpoint <EP>
--api-key-env <KEY> --model <M> --suite benchmarks/tasks/suite.capability.json
--arm all --repeat 5 --temperature 0.7 --memory fresh`.

### A1 suite — 10 tasks, 3 repeats

10 real bug-fix tasks, 3 repeats each = 30 episodes per arm, `temperature=0`.
The Hive arm runs the rule-based state machine
(`hive.harness.RuleBasedRoutingPolicy`, the `--policy rule` default) — *not*
the trained `CPURouterPolicy`. Per-pass means: baseline LLM calls 5.9 / 6.1 / 6.0 (stderr 0.06), Hive 1.0 / 1.0 / 1.0 (stderr 0.00).

| Metric | Baseline | Hive | Delta |
|---|---|---|---|
| Resolve rate | 100% (30/30) | 100% (30/30) | **0 pp** |
| Mean LLM calls | 6.0 | 1.0 | **−83.3%** |
| Mean prompt tokens | 8,590 | 1,534 | **−82.1%** |
| Mean completion tokens | 441 | 227 | **−48.5%** |
| Mean turns | 6.0 | 7.0 | +16.7% |
| Mean wall clock (s) | 8.94 | 2.80 | **−68.7%** |
| Memory recall hits | — | 24/30 | — |

**Why it works:** each episode runs ~6 mechanical turns (`list_files`,
reproduce `run_tests`, read the file the traceback names, verify `run_tests`).
Without Hive every one is a paid LLM call with the full transcript attached.
With Hive the CPU policy executes them locally; the model is called once —
with the failing test output and the unit under test already in context — and
writes the patch. `finish` is deliberately *not* routed: done-ness is a
judgment about the spec, so a green verify escalates to the model.

**Raw run:** [`docs/benchmarks/hive-bench-flash-r3.json`](../docs/benchmarks/hive-bench-flash-r3.json)
(30 episodes per arm). The earlier single-pass run
([`hive-bench-flash.json`](../docs/benchmarks/hive-bench-flash.json)) is superseded.

*Note: an earlier revision cited a "20-instance SWE-bench-lite" table
(85% vs 0%). That harness simulated the agent loop and drew resolve outcomes
from an RNG — the numbers were not real, and the script has been replaced
with a deprecation shim forwarding to `hive_bench.py`.*

---

## Run

```bash
python scripts/hive_bench.py \
    --backend openai --endpoint <openai-compatible-base-url> \
    --api-key-env <ENV_VAR_WITH_KEY> --model <model> \
    --output docs/benchmarks/hive-bench-<tag>.json
```

Useful flags: `--tasks <ids...>`, `--arm baseline|context|hive|all`,
`--max-turns`, `--keep-workdirs`, `--driver scripted` (no-LLM plumbing smoke;
resolve rate is meaningless in that mode), `--log <file.jsonl>` (log every
turn's `state -> action` for training), `--repeat N` (run each task N times
against the same stack — exercises memory replay), `--policy rule|trained`,
`--policy-path <file.joblib>`, `--suite <manifest>` (pin a task list),
`--memory {fresh,shared}`, `--verify-tasks` (no-cost integrity gate).

## Trained CPU policy

`hive.cpu_policy.CPURouterPolicy` is a small RandomForest trained by
*imitation*: log a run, fit the classifier on the observed `state -> action`
pairs, and it predicts the mechanical tool calls on the CPU. Below a
confidence floor, when args can't be resolved from state, and always for
`write_file` without a recalled fix, it escalates — it never guesses a patch.

```bash
# 1. log trajectories (any arm — LLM and policy decisions both count)
python scripts/hive_bench.py ... --log benchmarks/trajectories.jsonl

# 2. train
python scripts/train_cpu_policy.py \
    --trajectories benchmarks/trajectories.jsonl \
    --out benchmarks/cpu_router.joblib

# 3. run with the trained policy; --repeat 2 shows memory replay
# benchmarks/cpu_router.joblib has no .joblib.sig sidecar, and CPURouterPolicy.load
# refuses unsigned models by default (joblib unpickles arbitrary objects):
export HIVE_ALLOW_UNSIGNED_MODEL=1        # or sign it: scripts/train_cpu_policy.py --sign
python scripts/hive_bench.py ... \
    --policy trained --policy-path benchmarks/cpu_router.joblib --repeat 2
```

Measured on this suite (`deepseek-v4p1-flash`, `docs/benchmarks/hive-bench-cpu-policy.json`):
pass 0 = 10/10 resolved at 1.8 mean LLM calls; pass 1 = 10/10 resolved at
**0 LLM calls** — the policy replays the recalled patch
(`write_file -> run_tests -> finish`) entirely on the CPU.

## Trace coverage benchmark (`traces/`)

`benchmarks/traces/` holds 100 deidentified decision traces extracted from
real agent session logs (omp + prime harnesses) by
`scripts/extract_traces.py`, stratified across 5 workflow families
(edit-only, explore, edit-verify, mixed, web-research; 20 each). Each trace
keeps only: canonical tool name, ok/error flag, step index, per-tool call
histogram, argument *key names* and coarse classes. No user text, paths,
commands, code, or argument values — session ids are salted hashes.

`scripts/trace_bench.py` reports, per held-out step:

- **coverage** — fraction routed below the confidence floor vs escalated
- **fidelity** — of routed steps, fraction matching the real agent's tool
- **executable coverage** — the floor: only no-arg tools (`list_files`,
  `run_tests`, `finish`) count as routed, matching what `predict()` could
  actually execute when args aren't in state
- Wilson 95% CIs, per-family breakdown, and majority / repeat-last baselines

Two training regimes:

- default: stratified 5-fold CV over the eval suite (every trace scored
  held-out)
- `--pool benchmarks/traces-all`: train on the full 1,695-trace corpus with
  the eval ids excluded — the research-grade setup

```bash
python scripts/extract_traces.py --n 100          # rebuild the suite
python scripts/extract_traces.py --out benchmarks/traces-all --n 100000
python scripts/trace_bench.py --output docs/benchmarks/trace-coverage.json
python scripts/trace_bench.py --pool benchmarks/traces-all --curve \
    --output docs/benchmarks/trace-bakeoff.json
```

## Algorithm bake-off (`docs/benchmarks/trace-bakeoff.json`)

Eight algorithms trained on the full pool (~148k decisions) and scored on
the held-out 100-trace suite (10,960 steps). During this experiment a
**label-leakage bug was caught and fixed**: trace steps stored
`has_path`/`has_pattern`/`has_cmd`/`n_args` describing the *current* call's
arguments — unknowable before the tool is chosen. Those features produced
a spurious ~72% argmax; with them removed from `featurize`, the honest
numbers are:

| algorithm | argmax agreement | coverage@0.45 | fidelity@0.45 | agreement@0.45 |
|---|---|---|---|---|
| mlp | 48.0% | 56.1% | 57.6% | 32.3% |
| markov2 | 47.5% | 61.0% | 56.4% | 34.4% |
| hgb | 46.8% | 62.7% | 53.7% | 33.6% |
| markov1 | 45.5% | 52.1% | 50.5% | 26.3% |
| extratrees | 39.6% | 39.1% | 48.8% | 19.1% |
| rf-deep | 37.4% | 28.7% | 49.7% | 14.3% |
| logreg | 27.1% | 35.5% | 18.7% | 6.7% |
| rf (60×d6) | 20.3% | 7.8% | 27.6% | 2.1% |
| *repeat-last* | *43.1%* | *100%* | *43.1%* | *43.1%* |
| *majority* | *37.9%* | *100%* | *37.9%* | *37.9%* |

Learning curve (argmax vs train fraction): hgb climbs 30%→47% with data;
markov saturates almost immediately (~45% at 2% of data) — tool choice is
largely *sequential*: the previous one or two calls carry most of the
predictable signal, and observable state adds only a few points.

Headline finding, updated: the best state-only model (mlp) reaches **48.0%** raw next-tool accuracy vs 43.1% repeat-last and 37.9% majority — ~5 points better than repeat-last but far from safe-routing
territory — next-tool choice in real sessions is driven by tool-output
*content*, which deidentified state does not carry. Contrast with the
structured bug-fix suite above, where the workflow shape makes ~85% of
calls mechanical: **CPU routing is a strong compressor for known workflow
shapes; open-ended planning still needs the model's context — which is
what motivates reading the decision from model hidden states (latent
probes) rather than from observable state alone.**

## Adding a task

1. `tasks/<id>/repo/` — a small package plus `tests/` that fail on a real bug.
2. `tasks/<id>/task.json` — `{id, family, problem_statement, test_cmd}`.
3. Add the id to `suite.json` `tasks` (order matters — later same-family tasks
   can benefit from memory recall).

Every task must fail its suite on a fresh checkout — verified by the harness
before an episode starts.

## Component throughput

Micro-benchmarks on one host (synthetic load; raw data in
[`docs/benchmarks/latest-micro.json`](../docs/benchmarks/latest-micro.json)):

| Component | Items/s (measured host) |
|---|---|
| rust_brain | 176,796 |
| compress[fast] | 19,914 |
| compress[honeycomb] | 1,185 |
| busybee_cpu | 112 |

`busybee_cpu` measures 111.7 items/s in `latest-micro.json`. `latest-macro.json`
reports 1,734,892/s for the same component **with `policy_loaded: false`** — a
constant-escalate fallback, not routing — so that figure is not published until
it is re-measured with a policy loaded.

```bash
python scripts/hive_benchmark.py          # macro (full stack)
python scripts/hive_benchmark_micro.py    # micro (per component)
```

Energy methodology and raw NVML samples live in
[`docs/energy.md`](../docs/energy.md).

## Long-context compression

`python scripts/hive_long_context_eval.py --smoke` — up to **153.8×** compression on 50k+ char synthetic logs, measured in
[`docs/benchmarks/long-context-smoke.json`](../docs/benchmarks/long-context-smoke.json)
(short agent turns stay near 1×; routing is the win there).
