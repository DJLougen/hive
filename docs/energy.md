# Hive energy benchmark

This document records the energy methodology behind the README's measured 11.2% lower joules/token claim.

## Result

Measured on RTX 3090 with GPT-2, 117M parameters:

| Metric | Baseline | With Hive | Delta |
|---|---:|---:|---:|
| Joules per token | 1.64 J/tok | 1.46 J/tok | 11.2% lower |
| Baseline tokens | 445 | - | - |
| Hive tokens | - | 396 | - |
| Baseline LLM calls | 3 | - | - |
| Hive LLM calls | - | 1 | - |

Raw data:

```text
results/energy_real.json
```

Reproduce:

```bash
python scripts/energy_benchmark_real.py --prompts 10
```

## Hardware and protocol

- Hardware: RTX 3090
- CUDA: 13.0
- Model: GPT-2, 117M parameters
- Power sampling: NVML
- Sampling interval: 10 ms
- Energy integration: trapezoidal integration
- Prompt count: 10
- Repeat count: 3

The benchmark measures inference waste reduction. Hive is not changing the model weights or making the model intrinsically more energy efficient. It reduces work sent to the GPU.

## Mechanisms

The measured reduction comes from three system-level effects:

1. **Fewer LLM calls**: busybee-cpu routes predictable mechanical actions before inference.
2. **Fewer tokens per call**: honey-comb compresses prompt state before inference.
3. **Cleaner memory state**: rust-brain reduces redundant or stale recall.

## Scope

This is a small-model measurement, not a full 70B serving study.

The result is still useful because it measures the correct system-level mechanism: less unnecessary inference work. However, absolute joules/token will vary by:

- model size
- quantization
- batching
- KV-cache behavior
- prompt length
- output length
- inference backend
- GPU architecture
- CPU/GPU residency
- whether routing avoids a call entirely or only shortens context

## 70B-class extrapolation

The earlier README used a first-order FLOPs-linear extrapolation to estimate 70B-class savings:

| Scenario | Baseline energy | Hive savings |
|---|---:|---:|
| 10k sessions/month, 500 tokens/session | 16.4 MWh/year | 1.8 MWh/year |
| 100k sessions/month, 500 tokens/session | 164 MWh/year | 18.4 MWh/year |

That extrapolation holds the measured 11.2% reduction constant while scaling model compute linearly with parameter count.

Treat it as directional product math, not a substitute for a measured 70B run. A production 70B benchmark should measure:

- actual serving backend
- target quantization
- real context lengths
- batching policy
- power draw under load
- CPU routing overhead
- end-to-end joules/session, not only joules/token

## Recommended README wording

> Hive measured 11.2% lower joules/token on an RTX 3090 GPT-2 workload by reducing unnecessary inference work. This is a system-level energy reduction, not a model-level efficiency claim. Raw data and reproduction commands are published in `results/energy_real.json` and `scripts/energy_benchmark_real.py`.
