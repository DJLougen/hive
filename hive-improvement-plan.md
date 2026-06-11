# Hive Improvement Plan

Handoff plan for `DJLougen/hive`. Ordered by impact. Phase 1 is the only thing that changes what the repo *is*; everything after is hygiene and positioning. Each task has acceptance criteria so an agent can self-verify.

---

## Phase 1: Real-workload evaluation (highest priority)

**Goal:** Replace proxy metrics (compression ratio, routing accuracy on a sanity sample) with the one number that matters: task success on a real agent workload, with and without Hive in the loop.

### Task 1.1: SWE-bench-lite A/B eval

- Build or adapt a minimal SWE-agent harness (mini-swe-agent or a thin custom loop against vLLM/llama.cpp) that can run with Hive disabled (baseline) and enabled (route + compress + remember).
- Run 50 SWE-bench-lite instances per condition. Same model, same seed policy, same max-turn budget for both arms.
- Record per instance: resolved (bool), total input tokens, total output tokens, turns used, LLM calls avoided by busyBee, wall-clock.
- Output a single results table: resolve rate, mean tokens, mean turns, tokens-per-resolve, for baseline vs Hive.
- Script lives at `scripts/hive_swebench_eval.py`, results JSON at `docs/benchmarks/swebench-lite/`.

**Acceptance:** One reproducible command produces the table. Results committed as JSON with hardware manifest, same format as existing benchmark envelopes.

**Decision rule:** If resolve rate drops more than ~2 points with compression on, that is the finding. Do not bury it. Tune honey-comb aggressiveness (see Task 1.2) and rerun before publishing.

### Task 1.2: Compression sensitivity analysis

- The ratios in the README are deletion ratios. Measure what is lost, not just how much.
- Parameterize honey-comb aggressiveness (e.g., number of failures retained, head/tail word counts) and sweep 3-4 settings against the Task 1.1 harness on a 20-instance subset.
- Plot/tabulate resolve rate vs compression ratio. This is the speed-accuracy tradeoff curve for the system. A staircase procedure converging on the most aggressive setting that holds resolve rate within tolerance is a natural fit here and a good story.

**Acceptance:** A table or figure in `docs/benchmarks/` showing resolve rate as a function of compression aggressiveness, referenced from the README.

---

## Phase 2: README claim corrections

### Task 2.1: Reframe section 2 (routing)

- Do not lead with the 100/100 number. Lead with 98.2% on training distribution, state OOD performance is unknown, and keep the 100-row run as a reproducibility sanity check further down.
- Keep the $-per-100-turns framing; it is the honest version of the claim.

### Task 2.2: Fix the energy comparison units

- Section 4 currently compares ~5 J per second of wall-clock (power) against ~3 J per forward pass (energy per op). Convert both to joules per agent turn under a stated turn duration and forward-pass count.
- One sentence of methodology: NVML sampling interval, what is included in the CPU-path measurement.

### Task 2.3: Soften the "2x more turns per dollar" claim

- Gate it on Phase 1 results. Until then, phrase as "up to 2x context headroom at equal model, pending task-success validation (see eval)."

### Task 2.4: Trim the device matrix

- Cut TBD rows to the two with realistic near-term contributions: Jetson Thor and Raspberry Pi 5. Move the rest (Grace rack, iPhone) to a "wanted" list in CONTRIBUTING.md.

### Task 2.5: Promote the causal graph

- The supersedes/caused_by provenance chain is the differentiated feature; vector stores cannot do it. Give it a worked end-to-end example (tool result -> superseding write -> two-weeks-later chain walk) and move it above the compression tables, or at minimum expand it to equal weight.

---

## Phase 3: Packaging and distribution

### Task 3.1: Publish to PyPI

- Publish `hive` (consider name availability; `hive-stack` or `djl-hive` as fallback), `busybee-cpu`, and `honey-comb` with pinned inter-version dependencies.
- Quick start becomes `pip install hive-stack`. Keep the sibling-clone path documented as the dev workflow only.

### Task 3.2: Tag releases

- Tag v0.2.0 on all three repos, write GitHub release notes from CHANGELOG.md. CI should build wheels on tag.

### Task 3.3: Rename or annotate `rust_brain`

- It is a Python reference implementation. Either rename the package dir to `brain/` with `docs/component-rust_brain.md` explaining the planned Rust port, or add a loud README note. Pick one; the recurring "where is the Rust" issue is otherwise guaranteed.

---

## Phase 4: API hardening

### Task 4.1: Logical clocks for rust-brain

- Wall-clock monotonicity behind `TimestampRegression` breaks across processes, NTP corrections, and any multi-writer future.
- Add a hybrid logical clock (HLC) or Lamport counter as the ordering primitive, keep wall-clock as metadata. Preserve the hard replay-rejection semantics on the logical clock.
- This is a breaking change cheap at v0.2 and expensive at v1.0. Do it now.

**Acceptance:** Test demonstrating two writers with skewed wall clocks producing a consistent causal order; existing 37 tests still pass (updated where semantics changed).

### Task 4.2: Multi-writer test coverage

- Add concurrency tests for `remember()` under threads/processes. Document the consistency model explicitly in `docs/architecture.md`.

---

## Phase 5: Polish

- Add `citation.cff` validation to CI (file exists; make sure it parses).
- Add a 60-second GIF or asciinema of the five-line tour to the README.
- Issue templates: add an `eval-result` template mirroring the `performance` one once Phase 1 ships, so others can contribute SWE-bench runs the same way they contribute throughput numbers.

---

## Sequencing

1. Phase 1 first; it determines what Phase 2 claims can say.
2. Phase 2 and 3 in parallel after eval results land.
3. Phase 4 before any v0.3 tag.
4. Phase 5 whenever.

## Definition of done for v0.3

- README leads with a real-workload eval table (resolve rate + tokens, baseline vs Hive).
- All headline numbers either measured on real workloads or explicitly labeled synthetic.
- `pip install` one-liner works from PyPI.
- Ordering primitive is logical-clock based.
