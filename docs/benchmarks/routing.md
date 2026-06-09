# CPU Routing Accuracy Eval

Does the CPU policy pick the right tool often enough to skip an LLM call?
Evaluated **through HiveStack.route()** so this measures the full Hive
integration path, not just the raw classifier.

- Train: `/workspace/tests/fixtures/routing/train_synthetic_200.jsonl` (200 rows, 16.444s, augment=True)
- Eval: `/workspace/tests/fixtures/routing/eval_synthetic_50.jsonl` (50 held-out rows)
- Machine: x86_64 / Linux / 4 cores
- Commit: `6fb595a` — 2026-06-09T23:09:45+00:00

## Overall

| System | Action accuracy | Args semantic | Escalation rate | P50 latency |
|---|---|---|---|---|
| HiveStack + busybee | **98.0%** | 48.0% | 22.0% | 10.40 ms |
| busybee direct | 98.0% | 48.0% | 22.0% | 10.39 ms |
| always escalate (baseline) | 22.0% | — | 100% | — |
| majority class (majority_escalate) | 22.0% | — | 100.0% | — |

Throughput: 90 routes/s via HiveStack.
HiveStack matches direct busybee: True.

## Per tool (HiveStack)

| Tool | Eval rows | Accuracy |
|---|---|---|
| apply_patch | 15 | 93.3% |
| escalate | 11 | 100.0% |
| read_file | 11 | 100.0% |
| run_tests | 13 | 100.0% |

## How to read this

- **Action accuracy** is the routing metric: did the CPU pick the same
  tool a human/agent would? Wrong picks waste one turn; the loop retries.
- **Args semantic** is harder — filenames and patch bodies need not be
  perfect at routing time; the resolver fills them from state on the
  next turn.
- This eval uses busyBee's *synthetic held-out* set (200 rows in the
  full corpus; bundled 50-row sample for CI). For SWE-bench held-out
  numbers see busyBee-cpu's `reports/honest_evaluation.md` (96.4% on
  11,881 unseen issues with the combined model).

## Reproduce

```bash
pip install -e ../busyBee-cpu   # or pip install busybee-cpu
python3 scripts/routing_eval.py
```
