# LLM-in-the-Loop Fidelity Eval

Given a real model and an agent-realistic question per message, does
the model answer as well from **compressed** context as from **raw**?
Grading is automatic against corpus ground truth.

- Model: `local:Qwen/Qwen2.5-0.5B-Instruct (cpu)` (greedy decoding)
- Corpus: 31 messages (seed=7, 6/category + real fixture, scale=0.2)
- Compressor: rule_fast via `HiveStack.compress`
- Machine: x86_64 / Linux / 4 cores
- Commit: `1010e8e` — 2026-06-09T22:51:26+00:00

## Overall

| Metric | Raw context | Compressed context |
|---|---|---|
| QA accuracy (graded facts) | 67.7% | **60.0%** |
| Messages fully answered | 32.3% | 38.7% |
| Avg prompt tokens | 657 | 220 (**-66.5%**) |

## By category (QA accuracy)

| Category | Raw | Compressed | Raw tokens | Compressed tokens |
|---|---|---|---|---|
| command_output | 50.0% | 58.3% | 302 | 209 |
| file_read | 50.0% | 0.0% | 1128 | 298 |
| pytest_log | 82.4% | 82.4% | 1286 | 132 |
| search_results | 91.7% | 91.7% | 206 | 218 |
| traceback | 58.3% | 58.3% | 256 | 256 |

## How to read this

- The raw-context column is the model's ceiling on this corpus; the
  compressed column shows what compression costs (or saves) on top.
- Categories where compressed ≥ raw mean compression removed
  distraction, not signal. Categories where compressed < raw are the
  measured price of the token savings.

## Caveats

- A small CPU model is a *lower bound* on answer quality; the
  raw-vs-compressed comparison is the meaningful signal, not the
  absolute accuracy. Rerun with `OPENAI_API_KEY` set for a frontier
  model (`HIVE_EVAL_MODEL` to choose).
- Single question per message; does not measure multi-step task
  success (SWE-bench-style runs remain the gold standard).

## Reproduce

```bash
pip install -e ".[dev]" torch transformers
python3 scripts/llm_fidelity_eval.py
```
