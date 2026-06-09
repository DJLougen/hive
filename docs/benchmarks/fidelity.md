# Compression Fidelity Benchmark

**Question:** after compression, can a downstream agent still see the
facts it needs to act? Throughput proves the compressor is fast; this
report measures whether it is *safe*.

- Compressor: `RuleFastHoneyComb` (the in-repo rule-based fast path)
- Corpus: 201 messages (seed=42, 40/category + real captured fixtures)
- Baseline: head-truncation of the raw message to the **same** token budget
- Machine: x86_64 / x86_64 / Linux
- Commit: `da5663a` — 2026-06-09T22:30:17+00:00

## Overall

| Metric | rule_fast | naive truncation (same budget) |
|---|---|---|
| Token reduction | 95.4% | 95.4% (matched) |
| Fact retention | **40.7%** | 16.8% |
| Messages with *all* facts intact | **26.4%** | 26.4% |

Throughput on this machine: 5,448 msg/s (single core).

## By category

| Category | Msgs | Token reduction | Fact retention | All-facts rate | Naive retention |
|---|---|---|---|---|---|
| pytest_log | 41 | 98.6% | 48.2% | 0.0% | 0.9% |
| traceback | 40 | 0.0% | 100.0% | 100.0% | 100.0% |
| file_read | 40 | 99.5% | 0.0% | 0.0% | 0.0% |
| search_results | 40 | 75.5% | 32.5% | 32.5% | 32.5% |
| command_output | 40 | 91.9% | 0.0% | 0.0% | 0.0% |

## Synthetic vs. real captured output

| Source | Msgs | Token reduction | Fact retention | All-facts rate |
|---|---|---|---|---|
| real | 1 | 83.4% | 75.0% | 0.0% |
| synthetic | 200 | 95.4% | 40.5% | 26.5% |

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
