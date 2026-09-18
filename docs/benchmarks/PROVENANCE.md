# hive-bench hard tier — provenance record

## Frame

This is a measurement record, not a submission. The loop searched for whether
Hive's CPU routing holds up on a *hard* held-out task tier — tasks authored to
discriminate the arms, not to be easy. The keep rule: a result only counts if
the suite is fair (every oracle rule disclosed in the problem statement) and
the arms are comparable (identical spec-review opportunity). Three earlier runs
were discarded for failing one or both.

## Task

Can a deterministic CPU policy route mechanical agent-loop decisions at no
resolve cost, on tasks hard enough to separate the arms? The scored artifact is
`docs/benchmarks/hive-bench-hard.json` — 6 held-out tasks × 15 repeats × 3 arms.

## Box / Harness

- **Runner:** `scripts/hive_bench.py` — real tool-execution loop (list_files /
  read_file / grep / run_tests / write_file / finish), real pytest resolve.
- **Model:** `accounts/fireworks/models/deepseek-v4p1-flash` via Fireworks AI,
  temperature 0.7, 15 repeats, memory fresh.
- **Grading:** `grade_patch` replays the agent's `write_file` steps into a
  pristine repo and injects the hidden `oracle/tests/` only there — the agent
  never sees them.
- **Deviation flag:** `oracle/tests/*` are git-tracked (CI + `--verify-tasks`
  need them); `oracle/solution.patch` was untracked this session. A model
  trained on this repo may have seen the hidden tests — **absolute resolve
  rates are contaminated; the routing/cost delta is not.**

## Scoring surface

| Metric | Authority | Direction |
|---|---|---|
| resolve_rate | local — hidden oracle pytest on the replayed patch | higher |
| mean_llm_calls | local — LLM invocations per episode | lower |
| usd_total | endpoint-reported | lower |

There is no sealed external score; the artifact is the record.

## Results (hard-tier-final, commit 79a24c7, clean tree)

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

**Verdicts (exact McNemar, n=6 tasks):** all pairs `not_separable` (p=1.0, zero
discordant tasks). Hive matches baseline/context task-for-task at **58% fewer
LLM calls** and ~45% lower cost — the CPU routing is free capability, not a
capability tax. `lru-ttl-cache` is the only genuinely hard task left (hive
leads it 4/15 vs 2/15, 1/15).

## Retracted results

- **capability4** (n=5, 58e1b3d): baseline 67% / context 53% / hive 53%.
  Retracted — two tasks were undisclosed-rule traps. The all-arms-common-fail
  sweep replayed every patch and found one oracle test failing for *all* arms
  (lru-ttl sweep rule, snapshot negative-coverage) that the problem_statement
  never disclosed. The 0/5s measured mind-reading, not capability.
- **capability5** (n=5, 48d1570): baseline 60% / context 80% / hive 63%.
  Retracted — the spec-carrying escalation fired only for hive's policy
  (`reason "verify green"`); baseline/context never got the post-green spec
  re-check, so the routing delta was confounded by a prompt advantage.
- **discriminating** (n=15, 8404719): baseline 64% / context 56% / hive 49%.
  Retracted — predates the equalized spec review, the conftest gate, the
  vacuous-smoke fix, and the smoke_note prompt-truth fix.

**Adopted rule:** a per-task claim needs n≥15 and a suite where every oracle
rule is disclosed; a routing claim needs the spec-review opportunity equalized
across all arms.

## Closed levers

- **undisclosed-oracle-rules** — oracle tests asserting undisclosed behavior
  produce 0/5 for every arm and zero separation. Disclose in the statement +
  smoke, or drop the test.
- **vacuous-smoke-tests** — a smoke test green on the buggy repo teaches
  nothing. Assert the real invariant.
- **hive-only-spec-note** — a prompt advantage for one arm confounds the
  routing contrast. Equalize via a finish-intercept for all arms.
- **pytest-control-write-gaming** — conftest/ini/sitecustomize can deselect
  oracle tests. Blocklist in write_file + grade_patch.
- **n5-noise-band** — at 5 repeats, temp 0.7, per-cell rates swing ±2-3/5 with
  no intervention. Use n=15.

## Protocol

- One coherent change per run; fail-closed gates (`--verify-tasks`: smoke RED
  on pristine, smoke GREEN after patch, oracle GREEN after patch, oracle
  absent from repo, pytest-control blocked).
- Noise floor: at n=15, temp 0.7, a per-cell difference under ~3/15 is within
  the Wilson interval of the other arm — only larger gaps earn a claim.
- Same-binary rule: the routing delta is read off one artifact's
  `mean_llm_calls`, not across runs.

## Files / Sources

- `docs/benchmarks/hive-bench-hard.json` — the kept artifact (commit 79a24c7).
- `benchmarks/tasks/suite.hard.json` — the 6-task tier manifest.
- `scripts/hive_bench.py` — the harness (`--verify-tasks`, `grade_patch`,
  `_is_pytest_control`, `_SPEC_REVIEW_NOTE`).
- `scripts/check_claims.py` — the README-vs-artifact claim gate.
- `docs/benchmarks/PROVENANCE.json` — the machine-readable form of this record.
