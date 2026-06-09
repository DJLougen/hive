# Compression Fidelity Benchmark

**Question:** after compression, can a downstream agent still see the
facts it needs to act? Throughput proves the compressor is fast; this
report measures whether it is *safe*.

- Compressor: `RuleFastHoneyComb` (the in-repo rule-based fast path)
- Corpus: 201 messages (seed=42, 40/category + real captured fixtures)
- Baseline: head-truncation of the raw message to the **same** token budget
- Machine: x86_64 / x86_64 / Linux
- Commit: `fababb1` — 2026-06-09T22:53:39+00:00

## Overall

| Metric | rule_fast | naive truncation (same budget) |
|---|---|---|
| Token reduction | 83.6% | 83.6% (matched) |
| Fact retention | **99.1%** | 28.7% |
| Messages with *all* facts intact | **98.5%** | 41.3% |

Throughput on this machine: 2,369 msg/s (single core).

## By category

| Category | Msgs | Token reduction | Fact retention | All-facts rate | Naive retention |
|---|---|---|---|---|---|
| pytest_log | 41 | 97.4% | 100.0% | 100.0% | 4.3% |
| traceback | 40 | 0.0% | 100.0% | 100.0% | 100.0% |
| file_read | 40 | 82.6% | 100.0% | 100.0% | 15.0% |
| search_results | 40 | 5.5% | 92.5% | 92.5% | 92.5% |
| command_output | 40 | 75.7% | 100.0% | 100.0% | 7.5% |

## Synthetic vs. real captured output

| Source | Msgs | Token reduction | Fact retention | All-facts rate |
|---|---|---|---|---|
| real | 1 | 77.0% | 100.0% | 100.0% |
| synthetic | 200 | 83.6% | 99.1% | 98.5% |

## How to read this

- **Fact retention** is the safety metric. 100% token reduction is
  worthless if the agent can no longer see which test failed.
- **All-facts rate** approximates per-step survival: a single lost
  fact can derail the step that consumes the message.
- Categories where rule_fast beats the naive baseline justify the
  content-aware rules; categories where it loses are concrete,
  measured targets for improvement.

## Not yet measured

- End-to-end task success with an LLM in the loop (e.g. SWE-bench
  resolve rate with Hive on vs. off). This benchmark bounds the
  information available to the model; it does not measure what the
  model does with it.
- Routing accuracy of `busybee` (separate repo, not installed here).

## Reproduce

```bash
pip install -e ".[dev]"
python3 scripts/fidelity_benchmark.py
```
