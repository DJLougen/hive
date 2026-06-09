"""Corpus for the compression-fidelity benchmark.

Generates realistic agent-loop tool messages (pytest logs, tracebacks,
file reads, search results, command output) where every sample carries a
list of ground-truth *critical facts*: substrings a downstream agent must
still be able to see after compression in order to act correctly
(failing test names, exception lines, target function signatures,
matching file:line locations, error lines, exit codes).

All generators are seeded so the corpus is fully deterministic and the
benchmark is reproducible byte-for-byte.

Real-world fixtures (genuine pytest output captured from a live run, see
``scripts/capture_real_fixtures.py``) are loaded from
``tests/fixtures/fidelity/`` and mixed into the corpus.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

FIXTURE_DIR = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "fidelity"
)

CATEGORIES = (
    "pytest_log",
    "traceback",
    "file_read",
    "search_results",
    "command_output",
)


@dataclass(slots=True)
class Fact:
    """A single ground-truth fact that must survive compression."""

    name: str  # human-readable, e.g. "failing test name"
    needle: str  # exact substring checked against the compressed content


@dataclass(slots=True)
class Sample:
    id: str
    category: str
    role: str
    content: str
    facts: list[Fact]
    source: str = "synthetic"  # "synthetic" | "real"


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

def _rr(rng: random.Random, lo: int, hi: int, scale: float = 1.0) -> int:
    """``rng.randrange`` with optional range scaling.

    At ``scale=1.0`` this is byte-identical to ``rng.randrange(lo, hi)``
    (same rng consumption), so the default corpus is unchanged. Smaller
    scales shrink message sizes for compute-bound consumers such as the
    LLM-in-the-loop eval (CPU inference).
    """
    lo2 = max(1, round(lo * scale))
    return rng.randrange(lo2, max(lo2 + 1, round(hi * scale)))


_MODULES = ["auth", "billing", "search", "cache", "api", "worker", "db", "config"]
_VERBS = ["login", "refresh", "expiry", "parse", "retry", "timeout", "encode", "merge"]
_EXC_TYPES = [
    ("KeyError", "'refresh_token'"),
    ("ValueError", "invalid literal for int() with base 10: 'abc'"),
    ("TypeError", "unsupported operand type(s) for +: 'int' and 'NoneType'"),
    ("AttributeError", "'NoneType' object has no attribute 'commit'"),
    ("ConnectionResetError", "[Errno 104] Connection reset by peer"),
    ("AssertionError", "expected status 200, got 500"),
]


def _test_names(rng: random.Random, n: int) -> list[str]:
    names = []
    for i in range(n):
        names.append(f"test_{rng.choice(_MODULES)}_{rng.choice(_VERBS)}_{i:03d}")
    return names


# ---------------------------------------------------------------------------
# Generators (one per category)
# ---------------------------------------------------------------------------


def gen_pytest_log(rng: random.Random, idx: int, scale: float = 1.0) -> Sample:
    """Verbose pytest run: the agent must learn *which* tests failed."""
    n_tests = _rr(rng, 80, 600, scale)
    n_fail = _rr(rng, 1, 15, scale)
    names = _test_names(rng, n_tests)
    fail_set = set(rng.sample(range(n_tests), n_fail))

    lines = [
        "============================= test session starts ==============================",
        "platform linux -- Python 3.12.3, pytest-8.3.4, pluggy-1.5.0",
        f"collected {n_tests} items",
        "",
    ]
    for i, name in enumerate(names):
        mod = name.split("_")[1]
        status = "FAILED" if i in fail_set else "PASSED"
        lines.append(f"tests/test_{mod}.py::{name} {status}")

    lines += ["", "=================================== FAILURES ==================================="]
    facts: list[Fact] = []
    for i in sorted(fail_set):
        name = names[i]
        mod = name.split("_")[1]
        exc_type, exc_msg = rng.choice(_EXC_TYPES)
        lineno = rng.randrange(10, 400)
        lines += [
            f"_______________________________ {name} _______________________________",
            "",
            f"    def {name}():",
            f">       assert client.get('/api/{mod}').status_code == 200",
            f"E       {exc_type}: {exc_msg}",
            "",
            f"tests/test_{mod}.py:{lineno}: {exc_type}",
        ]
        facts.append(Fact(name="failing test name", needle=name))

    summary = f"{n_fail} failed, {n_tests - n_fail} passed"
    lines.append(f"========================= {summary} in {rng.uniform(1, 60):.2f}s =========================")
    facts.append(Fact(name="failure count summary", needle=summary))

    return Sample(
        id=f"pytest_log_{idx:03d}",
        category="pytest_log",
        role="tool",
        content="\n".join(lines),
        facts=facts,
    )


def gen_traceback(rng: random.Random, idx: int, scale: float = 1.0) -> Sample:
    """A crash traceback: the agent needs the exception and the deepest frame."""
    depth = rng.randrange(3, 8)
    lines = [
        f"$ python3 -m {rng.choice(_MODULES)}.main --verbose",
        "starting up ...",
        "loaded 14 plugins",
        "Traceback (most recent call last):",
    ]
    deepest = ""
    for d in range(depth):
        mod = rng.choice(_MODULES)
        fn = f"{rng.choice(_VERBS)}_{rng.choice(_VERBS)}"
        lineno = rng.randrange(10, 900)
        deepest = f'File "src/{mod}/{fn}.py", line {lineno}, in {fn}'
        lines.append(f"  {deepest}")
        lines.append(f"    result = self.{rng.choice(_VERBS)}(payload)")
    exc_type, exc_msg = rng.choice(_EXC_TYPES)
    exc_line = f"{exc_type}: {exc_msg}"
    lines.append(exc_line)

    return Sample(
        id=f"traceback_{idx:03d}",
        category="traceback",
        role="tool",
        content="\n".join(lines),
        facts=[
            Fact(name="exception line", needle=exc_line),
            Fact(name="deepest stack frame", needle=deepest),
        ],
    )


def gen_file_read(rng: random.Random, idx: int, scale: float = 1.0) -> Sample:
    """A read_file result: the agent went looking for one specific function."""
    n_funcs = _rr(rng, 20, 80, scale)
    target_idx = rng.randrange(2, n_funcs)  # never in the first 2 functions
    lines = ['"""Service module."""', "", "import os", "import json", ""]
    target_sig = ""
    target_const = ""
    for i in range(n_funcs):
        fn = f"{rng.choice(_VERBS)}_{rng.choice(_MODULES)}_{i:03d}"
        sig = f"def {fn}(payload, *, timeout={rng.randrange(1, 120)}):"
        if i == target_idx:
            target_sig = sig
            target_const = f"    limit = {rng.randrange(100, 9999)}"
            body = [target_const]
        else:
            body = [f"    x_{j} = payload.get('{rng.choice(_VERBS)}')" for j in range(rng.randrange(3, 10))]
        lines += [sig, '    """Handler."""'] + body + ["    return x_0" if i != target_idx else "    return limit", "", ""]

    return Sample(
        id=f"file_read_{idx:03d}",
        category="file_read",
        role="tool",
        content="\n".join(lines),
        facts=[
            Fact(name="target function signature", needle=target_sig),
            Fact(name="target constant", needle=target_const.strip()),
        ],
    )


def gen_search_results(rng: random.Random, idx: int, scale: float = 1.0) -> Sample:
    """grep-style output: one hit is the answer the agent searched for."""
    n_hits = _rr(rng, 10, 60, scale)
    answer_pos = rng.randrange(n_hits)
    lines = []
    answer_loc = ""
    answer_snippet = ""
    for i in range(n_hits):
        mod = rng.choice(_MODULES)
        lineno = rng.randrange(5, 900)
        loc = f"src/{mod}/handlers.py:{lineno}"
        if i == answer_pos:
            answer_loc = loc
            answer_snippet = f"SESSION_TTL_{idx:03d} = {rng.randrange(60, 86400)}"
            lines.append(f"{loc}: {answer_snippet}")
        else:
            lines.append(f"{loc}: session = get_session(request.{rng.choice(_VERBS)})")

    return Sample(
        id=f"search_results_{idx:03d}",
        category="search_results",
        role="tool",
        content="\n".join(lines),
        facts=[
            Fact(name="answer location", needle=answer_loc),
            Fact(name="answer snippet", needle=answer_snippet),
        ],
    )


def gen_command_output(rng: random.Random, idx: int, scale: float = 1.0) -> Sample:
    """A build/deploy log: the agent needs the error line and the exit code."""
    n_steps = _rr(rng, 20, 120, scale)
    # Past the head the compressor keeps (identical to randrange(8, n) at scale=1).
    err_pos = rng.randrange(min(8, n_steps - 1), n_steps)
    lines = [f"$ make deploy ENV={rng.choice(['staging', 'prod'])}"]
    error_line = ""
    for i in range(n_steps):
        if i == err_pos:
            error_line = (
                f"ERROR! failed to push image to registry.internal:{rng.randrange(5000, 6000)}"
                " (connection timed out)"
            )
            lines.append(error_line)
        else:
            lines.append(f"-> compiling {rng.choice(_MODULES)}_{rng.choice(_VERBS)} ... ok ({rng.uniform(0.1, 9):.1f}s)")
    exit_line = "exit 1"
    lines.append(exit_line)

    return Sample(
        id=f"command_output_{idx:03d}",
        category="command_output",
        role="tool",
        content="\n".join(lines),
        facts=[
            Fact(name="error line", needle=error_line),
            Fact(name="exit code", needle=exit_line),
        ],
    )


_GENERATORS = {
    "pytest_log": gen_pytest_log,
    "traceback": gen_traceback,
    "file_read": gen_file_read,
    "search_results": gen_search_results,
    "command_output": gen_command_output,
}


# ---------------------------------------------------------------------------
# Fixture loading (real captured tool output)
# ---------------------------------------------------------------------------


def load_real_fixtures(fixture_dir: Path = FIXTURE_DIR) -> list[Sample]:
    """Load real captured outputs (``X.txt`` + ``X.facts.json`` pairs)."""
    samples: list[Sample] = []
    if not fixture_dir.is_dir():
        return samples
    for txt in sorted(fixture_dir.glob("*.txt")):
        meta_path = txt.with_suffix("").with_suffix(".facts.json")
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text())
        samples.append(
            Sample(
                id=f"real_{txt.stem}",
                category=meta["category"],
                role=meta.get("role", "tool"),
                content=txt.read_text(),
                facts=[Fact(name=f["name"], needle=f["needle"]) for f in meta["facts"]],
                source="real",
            )
        )
    return samples


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_corpus(
    *,
    seed: int = 42,
    per_category: int = 40,
    include_real: bool = True,
    scale: float = 1.0,
) -> list[Sample]:
    rng = random.Random(seed)
    samples: list[Sample] = []
    for category in CATEGORIES:
        gen = _GENERATORS[category]
        for i in range(per_category):
            samples.append(gen(rng, i, scale))
    if include_real:
        samples.extend(load_real_fixtures())
    return samples
