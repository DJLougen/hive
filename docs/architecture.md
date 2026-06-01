# Hive — Architecture

## 1. Goals

* **CPU-first.** The hot path (per-turn) lives entirely on the CPU. We
  only call the GPU when the LLM is actually needed.
* **Deterministic where possible.** busyBee routes, honey-comb compresses,
  rust-brain protects against stale writes. Only the LLM is non-deterministic.
* **Edge-friendly.** No mandatory CUDA, no mandatory Docker, no mandatory
  GPUs. The same code runs on a Raspberry Pi 5, a Jetson Thor, a DGX Spark,
  and a 3090.
* **Composable.** Each component is a separate package with its own
  release cadence. Hive just glues them.

## 2. Components

```
                  ┌──────────────────────────────────────────────┐
                  │                  Hive                        │
   user request ─►│  route ─► compress ─► llm ─► remember        │─► response
                  │  │           │              │                │
                  │  ▼           ▼              ▼                │
                  │ busyBee    honey-comb    rust-brain          │
                  │ (CPU)      (CPU+rules)   (CPU, graph)        │
                  └──────────────────────────────────────────────┘
                                            │
                                            ▼
                                       GPU LLM (vLLM / llama.cpp)
```

### 2.1 busyBee-cpu

A 4-action supervised classifier (read_file / run_tests / apply_patch /
escalate) that intercepts obvious mechanical decisions. Routes that don't
match the 4-class problem are escalated to the LLM.

### 2.2 honey-comb

A 6-label inline compression system (CORE / DISTILL / COMPACT / DROP /
STALE / ESCALATE) operating on a per-message hot loop. The cool loop runs
every N turns and drops stale or superseded entries.

### 2.3 rust-brain

A timestamp-protected, graph-structured memory store. Edges are typed
(RELATED_TO, CAUSED_BY, SUPERSEDES, ATTACHED_TO) and the store rejects
monotonic-time regressions. The Python shim is the reference for the
upcoming Rust core (`hive-cpp`).

## 3. Data flow

For each turn:

1. **route(state)** — busyBee returns the next action or `escalate`.
2. **compress(messages)** — honey-comb reduces the transcript to the
   honey; the LLM only sees the distilled view.
3. **build prompt** — we assemble the system + memory + transcript into
   an OpenAI-compatible message list.
4. **llm(messages)** — only when busyBee escalated. We do not call the
   LLM when the mechanical policy already decided what to do.
5. **remember(result)** — rust-brain records the outcome, tagged and
   causally linked to upstream decisions.

## 4. Performance budget

| Stage        | Target (Jetson Thor) | Target (RTX 3090) | Measured (RTX 3090) |
|--------------|----------------------|-------------------|---------------------|
| route        | <30 ms               | <5 ms             | 0.49 µs (2.06M/s)   |
| compress     | <5 ms / message      | <1 ms / message   | 50 µs (19.8K/s)     |
| remember     | <1 µs / write        | <1 µs / write     | 3.7 µs (270K/s)     |
| llm (when escalated) | model-dependent | model-dependent   |

## 5. Failure modes and recovery

* **GPU OOM** — escalate to a smaller model, or fall through to busyBee
  with no LLM call at all.
* **busyBee wrong** — confidence-based escalation already handles this;
  the LLM is called and the trace is logged.
* **Honey-Comb mislabels** — rule-based mode always falls back to
  DISTILL, never drops CORE content. Worst case is over-compression.
* **rust-brain stale** — monotonic timestamps surface this loudly; the
  store refuses to write and the agent retries with a fresher timestamp.

## 6. Future: hive-cpp

The Rust port targets the same Python surface, with three wins:

* **<1 µs** writes for the hot path on Vera CPU (Grace / Thor).
* **NEON / SVE2 vector ops** for similarity search and graph walks.
* **FFI bindings** to llama.cpp so a single Rust binary can do routing,
  compression, memory, and inference on the GPU without crossing a
  Python boundary.

See `docs/future-cpp.md` for the full plan.
