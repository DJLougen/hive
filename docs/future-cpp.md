# Future: `hive-cpp` — the C++ / Rust native core

The Step 1 release ships Hive as a Python meta-package. The **planned**
Step 2 release is `hive-cpp`: a single Rust binary (with C++ shims for
NVIDIA-specific APIs) that replaces the Python hot path.

## Why C++/Rust?

* **Latency.** A native call costs ~10 ns; a Python call costs ~1 µs.
  On the hot loop (per-message classification) that is a 100× win.
* **Memory.** No GC, no interpreter footprint. A Rust binary uses
  <5 MB resident at idle; the Python meta-package sits at ~150 MB.
* **NVIDIA native.** CUDA driver API, NVRTC, NVML, MPS — all available
  from C++ without crossing an FFI boundary.
* **Edge deployment.** A static binary on a Jetson Thor boots in <100 ms
  and is hot in <1 s. No Python interpreter, no virtualenv, no
  `site-packages` to manage.

## Scope of the native port

* **busyBee-cpu** — re-implement the TF-IDF + voting ensemble in Rust
  with `tokenizers` and a small linear-algebra crate. Target: <1 ms per
  predict on a single Vera core.
* **honey-comb** — port the rule-based classifier and the deterministic
  compressor to Rust with no Python dependencies. Target: <100 µs per
  message.
* **rust-brain** — the *core* is already designed for native execution.
  The Python shim in this repo is the reference oracle. The Rust port
  will use `parking_lot` locks and a roaring-bitmap index for retrieval.

## Integration with the GPU

The native port talks to llama.cpp via FFI, so a single process can:

1. Run the busyBee router on a CPU core.
2. Run the honey-comb compressor on a second CPU core.
3. Schedule the LLM inference on the GPU (CUDA stream or MPS queue).
4. Persist results to rust-brain, all without crossing a language boundary.

This eliminates the ~50 µs Python ↔ C round-trip per turn, which is the
dominant cost on small models and a measurable cost even on 70B-class
models.

## 2026 hardware targets

* **NVIDIA Vera CPU** (Grace successor, 2026) — SVE2 vector ops for
  similarity search and graph walks in rust-brain.
* **NVIDIA Jetson Thor** (2026) — single-board deployment, ≤40 W TDP.
  A native Hive binary is a comfortable fit; the Python stack is too
  heavy for some thermal envelopes.
* **Grace + Hopper / Blackwell** — server-class, NVLink-C2C between
  Grace CPU and GPU. The native port exploits the coherent memory.

## Backwards compatibility

The Python API in `hive/__init__.py` is the source of truth. The native
binary ships a CPython extension module with the same signatures, so
existing agents keep working unchanged. Switching the backend is a
single environment variable:

```bash
HIVE_BACKEND=native python my_agent.py    # uses hive-cpp
HIVE_BACKEND=python python my_agent.py    # uses the current Python stack
```

## Schedule

| Step | What                                                      | Status     |
|------|-----------------------------------------------------------|------------|
| 1    | Python meta-package, validated on 3090 / Spark + ARM64    | **shipped** |
| 2    | Native port of busyBee + rust-brain                       | planned    |
| 3    | Native port of honey-comb + llama.cpp FFI                 | planned    |
| 4    | Hand-off to NVIDIA for Jetson Thor / Vera reference impl  | planned    |
