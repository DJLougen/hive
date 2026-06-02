# Hive ROI model

This document keeps the product math separate from the measured benchmark evidence.

The root README should lead with what Hive does and what has been measured. This file explains the deployment-scale cost model behind the headline claim: up to 65% lower LLM spend when routing, compression, and memory hygiene compound in an agent loop.

## Summary

Hive targets three cost sources:

1. **Call waste**: mechanical actions that can be routed before invoking the LLM.
2. **Token waste**: logs, files, traces, and search results that can be compressed before entering context.
3. **Memory waste**: stale or causally irrelevant memories that can be excluded from future prompts.

The 65% number is a deployment scenario, not an audited customer bill. The benchmark artifacts remain separate:

- `docs/benchmarks/latest-macro.json`
- `docs/benchmarks/latest-micro.json`
- `results/energy_real.json`

## Baseline scenario

Assumptions:

- Model pricing: GPT-5.4 style frontier API pricing
- Input: $2.50 per 1M tokens
- Output: $15.00 per 1M tokens
- Prompt-cache discount: not assumed
- Month length: 30 days
- Agent load: 1,000 turns per agent per day
- Average blended cost per agent turn: $0.03

At 65% cost reduction:

| Deployment | Monthly cost without Hive | Monthly cost with Hive | Monthly savings | Annual savings |
|---|---:|---:|---:|---:|
| Small team, 10 agents x 1k turns/day | $9,000/mo | $3,150/mo | $5,850/mo | $70,200/yr |
| Product team, 100 agents x 1k turns/day | $90,000/mo | $31,500/mo | $58,500/mo | $702,000/yr |
| Enterprise, 1000 agents x 1k turns/day | $900,000/mo | $315,000/mo | $585,000/mo | $7.02M/yr |

## Why the effects compound

Hive does not depend on one optimization. It stacks three reductions:

```text
Baseline:
100% of turns x 100% of tokens x 100% of repeated state

After CPU routing:
35% of turns still require LLM inference

After context compression:
35% of turns x 61% token load

After memory hygiene:
less stale recall, fewer repeated failure loops, cleaner prompts
```

The 65% headline is intentionally lower than the theoretical maximum implied by idealized routing and compression. Real deployments vary by:

- proportion of mechanical turns
- length and type of context
- model pricing
- prompt-cache behavior
- batching
- tool latency
- memory quality
- whether the agent workload is code, browsing, support, research, or operations

## Alternate session model

For a 10,000 sessions/month deployment at 50 turns/session:

| Effect | Without Hive | With Hive | Savings mechanism |
|---|---:|---:|---|
| LLM calls | 500,000 | 175,000 | mechanical turns routed on CPU |
| Tokens/call | full context | compressed context | honey-comb reduces context waste |
| Repeated state | unmanaged | timestamped causal memory | rust-brain limits stale recall |

This model is useful for estimating spend, but it should not replace measured workload results.

## How to use this honestly

Use the measured numbers for engineering credibility:

- busybee-cpu routing throughput
- honey-comb macro and micro throughput
- rust-brain write throughput
- measured RTX 3090 energy benchmark

Use the ROI table for product framing:

- what a 65% reduction would mean at different deployment sizes
- why routing and compression matter economically
- why agent control layers can be worth more than model-only optimization

Recommended wording:

> Hive can reduce deployment-scale LLM spend by moving mechanical routing, context compression, and causal memory management into a CPU-side control layer. In the published ROI scenario, these effects compound into up to 65% lower LLM spend. Measured benchmark artifacts are published separately under `docs/benchmarks/` and `results/`.
