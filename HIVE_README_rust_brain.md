# Component: rust-brain

> **Status:** Reference Python shim, shipping inside the
> [Hive](https://github.com/DJLougen/hive) meta-package (Step 1).
>
> **Role in Hive:** Timestamped, graph-structured agent memory with
> Hermes integration. The Python shim is the reference oracle; the
> production runtime is the planned `hive-cpp` Rust port.

---

## Hive positioning (2026)

`rust-brain` is the **memory layer** of Hive. It stores structured
agent memory — endpoints, decisions, test outcomes, causal links —
with three properties that matter at 2026 scale:

1. **Monotonic timestamps.** Replays of older writes fail loudly
   (`TimestampRegression`). Stale data cannot silently win.
2. **Graph relations.** Nodes carry typed edges
   (`related_to`, `caused_by`, `supersedes`, `attached_to`), so an
   agent can walk cause/effect chains.
3. **Hermes-native schema.** The on-wire format
   (`MemoryNode.to_dict()`) is exactly what HermesAgent-20 expects,
   so the same backend talks to a real RPC once the Rust core lands.

In 2026 this matters because agent memory is *not* a vector store. The
useful invariants are causal ("this decision was caused by that
observation") and temporal ("this is the freshest write we have").
A pure embedding store loses both.

## Snippet for your own README

```markdown
> Part of [Hive](https://github.com/DJLougen/hive) — the unified agent
> memory & context compression stack. The Python shim lives in
> [`hive/rust_brain/`](hive/rust_brain/) and is the reference for the
> native `hive-cpp` port.
```

## Local development

The Python shim is pure stdlib. No `pip install` is required to use it
— just `from hive.rust_brain import RustBrain`. To develop:

```bash
git clone https://github.com/DJLougen/hive
cd hive
python -m venv .venv && . .venv/bin/activate
pip install -e .
python scripts/smoke_rust_brain.py
```

## Validation in Hive

* `hive_benchmark.py` reports rust-brain throughput (≥150k writes/s on
  x86_64 with 5000 synthetic writes).
* Memory ordering is enforced — the smoke test covers
  `TimestampRegression` on a back-dated write.
* The on-wire schema matches the Hermes memory event shape, so the
  same backend can talk to a real Hermes harness today.

## See also

* Hive root: <https://github.com/DJLougen/hive>
* [`docs/future-cpp.md`](docs/future-cpp.md) for the Rust port plan.
* [`docs/architecture.md`](docs/architecture.md) for how rust-brain fits
  into the wider stack.
