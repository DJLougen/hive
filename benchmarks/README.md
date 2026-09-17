# hive-bench

Real tool-execution benchmark for Hive. Nothing is simulated:

- each `tasks/<id>/repo/` is a real Python repo with a real failing pytest suite
- the agent acts through real tools (`list_files`, `read_file`, `grep`,
  `run_tests`, `write_file`, `finish`) executed by the harness
- `tests/` is read-only — a resolve can't be gamed by editing tests
- resolve = a real `pytest` run at the end of the episode
- LLM calls are real calls to an OpenAI-compatible endpoint (native function
  calling); token counts come from the API `usage` field

**Baseline** sends every action decision to the LLM. **Hive** routes mechanical
transitions through `hive.harness.load_routing_policy()`, compresses tool
observations via `stack.compress()`, and recalls/records fixes via causal
memory (`stack.brain`) — tasks sharing a `family` exercise recall.

## Run

```bash
python scripts/hive_bench.py \
    --backend openai --endpoint <openai-compatible-base-url> \
    --api-key-env <ENV_VAR_WITH_KEY> --model <model> \
    --output docs/benchmarks/hive-bench-<tag>.json
```

Useful flags: `--tasks <ids...>`, `--arm baseline|hive|both`, `--max-turns`,
`--keep-workdirs`, `--driver scripted` (no-LLM plumbing smoke; resolve rate
is meaningless in that mode), `--log <file.jsonl>` (log every turn's
`state -> action` for training), `--repeat N` (run each task N times against
the same stack — exercises memory replay), `--policy rule|trained` and
`--policy-path <file.joblib>`.

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

Headline finding, updated: the best state-only model (mlp, 48.0%) beats
repeat-last (43.1%) by ~5 points but remains far from safe-routing
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
