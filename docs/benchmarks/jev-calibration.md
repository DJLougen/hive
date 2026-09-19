# Jev semantic-router calibration — first measurement

**Status:** initial calibration probe. Not a benchmark; a threshold-setting input.

## Why this exists

The semantic routing layer accepts a decision only when all four model signals
clear their thresholds. The integration plan lists defaults
(`safe_to_execute >= 0.95`, `requires_reasoning <= 0.10`, `needs_llm <= 0.10`)
but explicitly says:

> Do not hard-code final values. Use Jev shadow results to plot autonomous
> coverage vs wrong-route rate and choose an operating point.

This is the first measurement toward that. It shows the defaults as written are
**unusable against live Jev**, which is the kind of thing only a live probe can
tell you.

## Setup

- Model: **`jev-1.13.0`** (TypeSafe), via `hive.jev_backend.JevBackend`
- Schema: `hive-routing-v1` (`hive.semantic_schema`)
- State: `hive.semantic_state.compile_state` over five synthetic agent-loop states
- Date: 2026-09-19 · requests: 5 · tokens: ~660 in / ~135 out each

## Measured output

| state | tool (choice) | tool p | safe_to_execute | requires_reasoning | needs_llm | latency ms |
|---|---|---:|---:|---:|---:|---:|
| fresh (step 0) | list_files | 0.99 | **0.18** | 0.35 | 0.35 | 363 |
| just_listed | list_files | 0.36 | **0.33** | 0.34 | 0.32 | 326 |
| fail_seen | list_files | 0.97 | **0.16** | 0.52 | 0.45 | 398 |
| write_done | run_tests | 0.83 | **0.17** | 0.62 | 0.48 | 361 |
| verify_green | finish | 0.98 | **0.41** | 0.24 | 0.25 | 326 |

| signal | mean | max observed |
|---|---:|---:|
| tool confidence | 0.826 | 0.99 |
| safe_to_execute | **0.25** | **0.41** |
| requires_reasoning | 0.414 | 0.62 |
| needs_llm | 0.370 | 0.48 |

## Findings

1. **The plan's default thresholds give zero coverage.** `safe_to_execute`
   never exceeds **0.41** across these states, so a `>= 0.95` gate rejects
   **every** decision. `requires_reasoning` (mean 0.41) and `needs_llm`
   (mean 0.37) likewise never fall below the 0.10 defaults. As shipped, the
   semantic layer would escalate 100% of the time and behave exactly like
   `semantic_mode=off` while paying for the calls.
2. **Tool choice is confident and looks sensible.** `list_files` at step 0,
   `run_tests` after a write, `finish` on a green verify — the *tool* signal
   is the informative one; the three scalar gates are not, at least not at
   these thresholds.
3. **The scalars behave like a calibrated "caution" signal, not a permission.**
   Jev marks even a fresh `list_files` at 0.18 safe. That is defensible — a
   model can't know a repo is safe to enumerate — but it means the scalar must
   not gate routing on its own.
4. **`just_listed` is the one low-confidence tool case** (0.36 on `list_files`,
   the correct choice). That is the state shape where Jev is genuinely unsure,
   and therefore the interesting one for a coverage/fidelity curve.

## Consequence for the design

- The thresholds in `HiveConfig` are **defaults pending calibration**, as the
  plan intends; they must not be presented as validated.
- The cascade's `shadow` mode is the right first deployment: it accumulates
  coverage/fidelity data without letting an uncalibrated gate influence routing.
- A coverage/fidelity curve needs **ground-truth tool labels** (the actual
  action the agent took), which the `record_sink` comparison records already
  carry — so the corpus can be built from shadow runs.

## Reproduce

```bash
python - <<'PY'
import os
from hive.jev_backend import JevBackend
from hive.semantic_schema import routing_questions
from hive.semantic_state import compile_state
be = JevBackend(api_key=os.environ["HIVE_JEV_API_KEY"])
state = {"goal": "Fix the bug", "listed": True, "tests_run": 1, "tests_passed": False,
         "step": 2, "last_tool": "run_tests", "suggested_read": "pkg/a.py"}
print(be.decide(state=compile_state(state), questions=routing_questions()))
PY
```

## Do not

- do not treat these five synthetic states as a representative sample;
- do not read `safe_to_execute` as a permission — Hive's deterministic resolver,
  write-without-recalled-fix refusal, and loop guard remain authoritative;
- do not enable `cascade` on the strength of this document — it argues for
  `shadow` plus a real coverage/fidelity curve first.
