# Multi-Step Agent Loop Eval

Can a model navigate a fixed debugging trajectory (test → read →
patch → re-test) when tool outputs are **raw** vs **Hive-compressed**?

- Model: `local:Qwen/Qwen2.5-0.5B-Instruct (cpu)`
- Episodes: 6 (24 decision steps)
- Compressor: rule_fast via `HiveStack.compress`
- Machine: x86_64 / Linux / 4 cores
- Commit: `6fb595a` — 2026-06-09T23:11:03+00:00

## Overall

| Metric | Raw transcript | Compressed transcript |
|---|---|---|
| Step accuracy (tool picked correctly) | 25.0% | **4.2%** |
| Episodes fully resolved | 0.0% | 0.0% |
| Avg prompt tokens / episode | 631 | 534 (**-15.4%**) |

## Per episode (step accuracy)

| Episode | Raw | Compressed |
|---|---|---|
| auth_token_expiry | 25% (1/4) | 25% (1/4) |
| billing_rounding | 25% (1/4) | 0% (0/4) |
| db_connection_pool | 25% (1/4) | 0% (0/4) |
| search_unicode | 25% (1/4) | 0% (0/4) |
| cache_eviction | 25% (1/4) | 0% (0/4) |
| api_rate_limit | 25% (1/4) | 0% (0/4) |

## How to read this

- **Step accuracy** = fraction of turns where the model picked the
  right next tool. One wrong turn derails the episode.
- **Resolve rate** = episodes where every step was correct (the
  SWE-bench-style metric at this scale).
- Compressed ≥ raw means Hive compression helps or is neutral on
  multi-step navigation; compressed < raw is the measured cost.

## Caveats

- Fixed episodes with obvious next tools; not open-ended bug fixing.
- Small CPU model; absolute numbers are a lower bound. The
  raw-vs-compressed comparison is the signal.

## Reproduce

```bash
pip install torch transformers
python3 scripts/agent_loop_eval.py
```
