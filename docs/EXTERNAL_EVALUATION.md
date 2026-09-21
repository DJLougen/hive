# External evaluation — `scripts/external_bench.py`

`external_bench.py` runs the same real-tool episodes as `hive_bench.py`
(`scripts.hive_bench.Task` / `run_episode`, unchanged) against a task suite
that lives **outside** this repository's published benchmark, under a
versioned manifest that pins every task's source tree by hash. It exists to
answer one question honestly: does Hive help on tasks it was not built or
tuned against?

> **Status: machinery only.** As of this writing, no genuine untouched
> external workload has been run through this runner. Every artifact it can
> produce so far comes from offline fake-backend tests, which validate the
> plumbing — manifest verification, overlap guard, budgets, receipts,
> fail-closed reporting — and say **nothing** about model capability. Do
> not cite this tooling as external evidence until a real run against a
> real endpoint on a disjoint suite exists.

## The ten guarantees

| # | Guarantee | Mechanism |
|---|-----------|-----------|
| 1 | Strict versioned manifest | `schema_version: 1` JSON; unknown keys, wrong version, empty/duplicate/unsafe task ids, and hash maps that do not cover exactly the task list are rejected. `task.json` must declare the same id, and `oracle_dir`/`solution_patch` must resolve inside the task directory. |
| 2 | Immutable sources | `source_sha256` pins the *entire* task directory (`task.json`, `repo/`, `oracle/`). Recomputed at startup, before **every** episode, and once at the end; any change aborts the run. Symlinks inside a task tree are rejected — they could escape the pinned directory. |
| 3 | Overlap guard | `--training-inventory` is a JSON object `{task_ids, source_hashes}` supplied by the caller. Missing file, malformed JSON, unknown keys, blank/unsafe ids, non-sha256 hashes, or an *empty* inventory all fail closed — an empty inventory is not evidence of disjointness. Any task-id or source-hash overlap refuses the run. |
| 4 | Three conditions | `model-only` (hive_bench `baseline` arm), `hive-rule` (deterministic `RuleBasedRoutingPolicy` via `load_routing_policy()`), `hive-trained` (`CPURouterPolicy.load`, signature-guarded). |
| 5 | Rotating order | `condition_order(task_index)` rotates the condition list per task to reduce fixed ordering bias, not guarantee its elimination. Every episode gets a fresh `HiveStack` (fresh causal memory) **and** a deep-copied routing policy — `CPURouterPolicy.predict` stores `_last_route`, so a shared policy object would leak routing state between episodes — plus a fresh workdir under `<output>/workdirs/`. |
| 6 | Metered budgets | `MeteredBackend` enforces `--max-requests`, `--max-tokens-total`, `--wall-seconds` *before* each call, so an exhausted budget never produces a billable request. Token headroom is reserved conservatively (one token per serialized input byte + the per-call `max_tokens` cap); a call that could overspend is refused. A backend `timeout` attribute is clamped to the remaining wall budget — that bounds the socket wait but cannot hard-kill a call already in flight. `BudgetExceeded`/`UsageUnknownError`/`BackendError` are `BaseException`s: they evade `chat_with_retry`'s `except Exception` retry loop, so retries cannot hide spend. A response with missing/non-positive usage aborts the run rather than recording a zero. |
| 7 | Atomic incremental artifacts | `results.jsonl` and `receipts.jsonl` are appended with flush+fsync per row; `report.json` is written via tmp+rename. Every receipt carries `task_id`, `pass_idx`, `condition`, and the model id the endpoint actually reported. |
| 8 | Paired, priced outcomes | `report["paired"]["grid"]` gives per-task × per-pass resolved booleans per condition. The pairing unit is the exact `(task_id, pass_idx)` cell: a cell enters `grid` only when every selected condition produced exactly one valid row for *that* pass, so a partial run can never pair baseline pass 0 against hive pass 1. Cells missing a condition's verdict — including planned passes that never ran — are listed in `incomplete_cells` (with the verdicts that did land); cells where a condition produced more than one row (even one valid + one crashed) are listed in `ambiguous_cells` and never collapsed into a pairing. `report["summaries"]` gives per-condition resolve counts and token-priced `usd_estimated` / `usd_per_resolved` (null at zero resolves — cost/0 is undefined, not free). **No significance or equivalence inference is computed.** |
| 9 | Fail-closed completeness | `report["status"]` is `complete` only when every manifest task × repeat × condition row exists, is unique, ran to a verdict, and recorded usage. Duplicates, unmatched ids, missing cells, crashes, malformed counters, and usage-less rows all leave the run `partial` (or `aborted`), `promotable: false`, with the problems listed in `audit_problems`. An empty plan is itself a failure. Partial runs are preserved on disk and never promoted. |
| 10 | Offline-verifiable | `tests/test_external_bench.py` runs the whole pipeline against an injected fake backend — no network, no model calls, no paid runs. `dry-run` validates everything below without touching a backend. |

## Manifest format

```json
{
  "schema_version": 1,
  "name": "my-external-suite",
  "tasks": ["task-a", "task-b"],
  "source_sha256": {
    "task-a": "<64 lowercase hex>",
    "task-b": "<64 lowercase hex>"
  }
}
```

Each task id names a directory **next to the manifest** in the hive-bench
layout: `<id>/task.json`, `<id>/repo/`, and optionally `<id>/oracle/` (with
`oracle/tests/` for held-out grading). `task.json` is the same schema
`hive_bench.load_tasks` reads (`id`, `family`, `problem_statement`,
`test_cmd`, `test_timeout_s`, `oracle_dir`, `oracle_cmd`, …).

To mint the hashes for a suite you have authored:

```bash
python scripts/external_bench.py hash-tasks --tasks-dir /path/to/tasks
# → {"task-a": "<sha256>", ...} — paste into source_sha256
```

## Training inventory

```json
{"task_ids": ["task-c"], "source_hashes": ["<64 hex>", "..."]}
```

This file is supplied by **you** — the runner does not (and cannot) know
what the model or the trained policy saw. List every task id and every
task-directory hash that appears in any training corpus, trajectory log,
or tuning set. If you cannot produce this list, you cannot claim the eval
is external — the runner refuses to proceed without it.

## Usage

```bash
# 1. Validate everything without spending a token:
python scripts/external_bench.py dry-run \
    --manifest /path/to/manifest.json \
    --training-inventory inventory.json \
    --policy-path policy.joblib

# 2. Run (endpoint and key come from the environment, never the CLI):
export OPENAI_BASE_URL=https://api.example.com   # or --endpoint
export OPENAI_API_KEY=sk-...
python scripts/external_bench.py run \
    --manifest /path/to/manifest.json \
    --training-inventory inventory.json \
    --output results/ext-run-1 \
    --model my-model \
    --policy-path policy.joblib \
    --max-requests 500 --max-tokens-total 2000000 --wall-seconds 7200
```

`--output` must be a **new** directory — a run never overwrites a previous
one. Exit codes: `0` complete, `3` aborted (budget/usage/backend), `4`
partial (audit problems), `2` validation failure.

The `hive-trained` condition requires `--policy-path`; the artifact is
loaded through `CPURouterPolicy.load`, which cryptographically verifies
the `.joblib.sig` sidecar and refuses unsigned models. There is no flag to
skip this; `HIVE_ALLOW_UNSIGNED_MODEL=1` is the only override and is
recorded in `report["provenance"]["unsigned_model_override"]`.

## Reading a report

- `status` / `promotable` — only `complete` + `true` means every planned
  episode ran to a verdict with recorded usage.
- `usage_totals` — request attempts and token usage across the run, including
  recorded calls inside aborted episodes. If any request has unknown usage,
  `usage_recorded` is false and the full token totals are null.
  `measured_subtotal` preserves known-response usage; it is **not** full spend
  or an invoice. A budget stop before another call does not erase measured usage.
- `summaries.<condition>.usd_estimated` — token-priced estimate at the
  `--price-in`/`--price-out` rates; null whenever any outcome row lacks
  recorded usage, because a partial total must never read as a full one.
- `paired.grid` — the raw per-task × per-pass resolved booleans, one entry
  per `(task_id, pass_idx)` cell that every condition completed exactly
  once. Compare conditions from this, not from headline rates alone.
  `paired.incomplete_cells` lists observed cells where some condition has
  no valid verdict (with the verdicts that did land), and
  `paired.ambiguous_cells` lists cells where a condition produced
  duplicate rows — neither is ever silently merged into a pairing.
- `audit_problems` — exactly why a non-complete run is not promotable.

## What this does not do

- No statistical inference: no p-values, no equivalence claims, no
  "separated" verdicts. n is whatever your manifest says it is.
- No retries hidden from accounting: every backend attempt is receipted.
- No silent source drift: the hash pin is checked before every episode and at completion.
- No fabricated evidence: a `complete` report from the fake-backend test
  path is a plumbing check, not a benchmark result.
