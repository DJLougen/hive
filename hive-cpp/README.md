# hive-cpp: Native Rust Backend for Hive

Optional Rust implementation of Hive's hot paths, exposed to Python through PyO3.

## Overview

hive-cpp implements three modules behind the `hive_cpp` Python module:

- **Router** (`src/router.rs`) — decision-tree action routing (port of busybee-cpu)
- **Compressor** (`src/compressor.rs`) — rule-based context compression (port of honey-comb)
- **Memory** (`src/memory.rs`) — causal memory graph with keyed store/retrieve

Select it from Python with `HIVE_BACKEND=native` (or `backend="native"`); `auto` picks it up when the
`hive_cpp` extension is importable. See `hive/backend.py` for the adapter.

**Known difference from the Python path:** `rust_compress` keeps only `ceil(n/2)` whitespace tokens,
scored by a fixed importance table, so it is *lossy* even for short messages (`"hello world"` →
`"hello"`) and has no notion of the CORE/DISTILL/COMPACT taxonomy. The adapter labels the Rust output
with `hive.rule_fast.classify_label` so the label a caller sees stays in the project's taxonomy; the
Python `rule_fast` path keeps short/CORE messages verbatim.

**No performance numbers are published here.** The crate has no committed benchmark artifact, and the
`latest-micro.json` / `latest-macro.json` artifacts in the parent repo measure the **Python** stack, not
this crate. Run `cargo bench` and commit the output (`target/criterion/`) to publish a figure.

## Installation

```bash
# Build the Python extension from source
pip install maturin
cd hive-cpp
maturin develop --release
```

`maturin` reads the `pyo3` feature from `pyproject.toml`; there is no need to pass `--features` by hand.

## API Reference

The four PyO3 functions take/return JSON strings to keep the boundary simple.

### Router

```python
from hive_cpp import rust_router_decide

model_json = """
{
  "root": {
    "feature": "step",
    "threshold": 5.0,
    "left": {"feature": null, "threshold": null, "left": null, "right": null, "action": "read_file"},
    "right": {"feature": null, "threshold": null, "left": null, "right": null, "action": "apply_patch"},
    "action": null
  },
  "feature_names": ["step"],
  "tool_names": ["read_file", "apply_patch"]
}
"""

# AgentState requires all six fields (router.rs):
state_json = """
{
  "goal": "Fix authentication bug",
  "step": 10,
  "last_tool": "read_file",
  "recent_observations": [],
  "open_files": ["auth.py"],
  "available_tools": ["read_file", "apply_patch"]
}
"""

decision = rust_router_decide(model_json, state_json)
# → '{"action": "apply_patch", "confidence": 0.8, "reasoning": "...", "latency_ms": 0.01}'
```

### Compressor

```python
from hive_cpp import rust_compress

result = rust_compress("DEBUG: cache miss\n" * 200)
# → '{"compressed": "...", "original_tokens": 1200, "compressed_tokens": 12,
#      "ratio": 100.0, "latency_ms": 0.02}'
```

`rust_compress` takes the **message text**, not a token list.

### Memory

```python
from hive_cpp import rust_memory_store, rust_memory_retrieve

rust_memory_store(42, "Fixed auth bug on line 123", 0.9)   # key: int, value: str, importance: float
memory = rust_memory_retrieve(42)
# → '{"key": 42, "content": "Fixed auth bug on line 123", "importance": 0.9, "age_seconds": 0.5}'
# missing key raises KeyError
```

## Development

```bash
cd hive-cpp
cargo test            # crate unit tests (no pyo3 feature)
maturin develop       # debug build of the extension
maturin build --release
cargo bench           # criterion benchmarks
```

## Project Structure

```
hive-cpp/
├── src/
│   ├── lib.rs              # PyO3 bindings (4 functions)
│   ├── router.rs           # Decision tree implementation
│   ├── compressor.rs       # Rule-based compression
│   └── memory.rs           # Keyed causal memory graph
├── benches/
│   └── bench.rs            # Criterion benchmarks
├── Cargo.toml
├── pyproject.toml          # PyO3/maturin config
└── README.md
```

## License

MIT License - see LICENSE file for details

## Related Projects

- [hive](https://github.com/DJLougen/hive) - Python orchestration framework
- [busybee-cpu](https://github.com/DJLougen/busybee-cpu) - CPU decision routing (Python)
- [honey-comb](https://github.com/DJLougen/honey-comb) - Context compression (Python)
- [rust-brain](https://github.com/DJLougen/rust-brain) - Causal memory system (Python)
