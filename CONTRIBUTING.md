# Contributing

Thank you for your interest in Hive. The project is in **Step 1 (Python
meta-package)**; the native Rust port is queued for **Step 2**. Most
contributions at this stage land in the Python meta-package, the
benchmark suite, the documentation, and the CI matrix.

## Development setup

**End users** install from PyPI:

```bash
pip install hive-agent-memory
pip install "hive-agent-memory[full]"   # when busybee-cpu + honey-comb are on PyPI
```

**Contributors** clone the meta-package and its siblings side-by-side:

```bash
# 1. Clone all three repos side-by-side. The hive meta-package depends
#    on busyBee-cpu and honey-comb as sibling packages for full-stack dev.
git clone https://github.com/DJLougen/hive
git clone https://github.com/DJLougen/busyBee-cpu
git clone https://github.com/DJLougen/honey-comb
cd hive

# 2. Create a virtualenv and install the meta-package with the dev extras.
python -m venv .venv && . .venv/bin/activate
pip install -e ../busyBee-cpu ../honey-comb -e ".[dev,monitor]"

# 3. Run the test suite.
pytest -q

# 4. Run the macro benchmark (echo backend — no model server required).
python scripts/hive_benchmark.py --quiet

# 5. Run the per-component micro-benchmark.
python scripts/hive_benchmark_micro.py --runs 5
```

If you do not have a GPU, set `HIVE_NO_NVML=1` and the hardware monitor
falls back to no-op gracefully.

## Coding conventions

* **Python 3.10+**, fully type-annotated.
* **One responsibility per module.** `hive/rust_brain/` is a graph store;
  `hive/rule_fast/` is a regex compressor; `hive/hardware.py` is a
  sampler. No module imports the others unless it has to.
* **Public surface is dataclasses + functions.** No magic methods beyond
  what the stdlib gives us. No metaclass magic.
* **Comments only where the code is non-obvious.** The "why" is welcome;
  the "what" is usually a re-statement of the code.
* **Tests cover invariants, not defaults.** A test that breaks when the
  default value changes is a brittle test. Assert logical behaviour.

## Pull-request workflow

1. **Open an issue first** for anything beyond a small bug fix. The
   project is small enough that an RFC-style discussion in an issue is
   faster than back-and-forth on a PR.
2. **Branch off `main`.** Use a descriptive name (`feat/...`,
   `fix/...`, `docs/...`).
3. **Keep the diff focused.** A PR that touches 12 files for one feature
   is a PR that needs to be split.
4. **Add tests.** If you change a public function, add or update a test
   in `tests/`. The PR will not be merged without it.
5. **Run the test suite locally before pushing.** `pytest -q` should
   print `37 passed in < 10 s`.
6. **One approval from a maintainer.** The project is maintained by
   @DJLougen.

## Commit message convention

We use [Conventional Commits](https://www.conventionalcommits.org/).

```
<type>(<scope>): <description>

[body]

[footer]
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`.

## Out of scope

* Submitting AI-generated PRs wholesale. AI is welcome as a tool, but the
  author must be able to defend the change in review. The same policy
  that the upstream `llama.cpp` project follows.
* Reorganising the meta-package layout. The single-source-of-truth split
  (`hive/hive/` for the meta-package, `hive/scripts/` for benchmarks,
  `hive/tests/` for tests) is intentional and not under negotiation.

## Reporting bugs

Use the [GitHub issue tracker](https://github.com/DJLougen/hive/issues).
Include the version, the OS, the Python version, and a minimal
reproducer. For security-sensitive issues, see `SECURITY.md`.
