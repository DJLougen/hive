"""Tests for backend resolution defaults and the python/native divergence.

``resolve_backend`` must never pick the native hive-cpp backend on its own:
``auto`` (the default) resolves to ``"python"`` even when the ``hive_cpp``
wheel is importable, because ``rust_compress`` is lossy in a way the Python
path is not — it keeps ``ceil(n/2)`` whitespace tokens and rejoins them with
single spaces, destroying newlines and code layout. A compressor whose output
depends on whether an unrelated wheel is installed is not reproducible, so
native is opt-in only (``HIVE_BACKEND=native`` / ``backend="native"``).
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import math
import os
import sys

import pytest

from hive.backend import resolve_backend


def _require_native() -> bool:
    return os.environ.get("HIVE_REQUIRE_NATIVE") == "1"


def test_auto_resolves_to_python_when_native_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default with no env var and no hive_cpp wheel → python."""
    monkeypatch.delenv("HIVE_BACKEND", raising=False)
    monkeypatch.delitem(sys.modules, "hive_cpp", raising=False)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert resolve_backend() == "python"


def test_auto_resolves_to_python_even_when_native_importable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: an installed hive_cpp wheel must NOT flip the default."""
    monkeypatch.delenv("HIVE_BACKEND", raising=False)
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name: importlib.machinery.ModuleSpec(name, loader=None),
    )
    assert resolve_backend() == "python"


def test_env_native_selects_native(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIVE_BACKEND", "native")
    assert resolve_backend() == "native"


def test_env_python_selects_python(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIVE_BACKEND", "python")
    assert resolve_backend() == "python"


def test_unknown_env_value_warns_once_and_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrecognized HIVE_BACKEND must not be silently ignored."""
    monkeypatch.setenv("HIVE_BACKEND", "rust")
    with pytest.warns(UserWarning, match="unknown HIVE_BACKEND") as record:
        assert resolve_backend() == "python"
    assert len(record) == 1


def test_explicit_argument_beats_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``backend="native"`` opts in even when the env says python."""
    monkeypatch.setenv("HIVE_BACKEND", "python")
    assert resolve_backend("native") == "native"
    assert resolve_backend("python") == "python"


# ---------------------------------------------------------------------------
# Differential test: the two compress paths must not silently disagree.
# ---------------------------------------------------------------------------

_CORPUS: list[tuple[str, str]] = [
    ("tool", "DEBUG: x\n" * 200),
    (
        "tool",
        "import os\n\n\ndef parse(path: str) -> dict:\n"
        "    with open(path) as fh:\n"
        "        return json.load(fh)\n",
    ),
    (
        "tool",
        "===== FAILURES =====\n"
        "____ test_login ____\n"
        "tests/test_auth.py:42: in test_login\n"
        "    assert resp.status_code == 200\n"
        "E   assert 500 == 200\n"
        "1 failed, 11 passed in 0.42s\n",
    ),
    (
        "assistant",
        "I found the bug in auth.py.\n"
        "The token check runs before expiry validation.\n"
        "Next I will swap the two checks and rerun the tests.\n",
    ),
]


def test_native_and_python_compress_diverge_as_documented() -> None:
    """Pin the documented difference instead of letting it drift silently.

    The Python path (``RuleFastHoneyComb``) preserves line structure; the
    native path keeps ``ceil(n/2)`` whitespace tokens rejoined with single
    spaces. If either path changes so they agree — or disagree differently —
    this test fails and names the diverging path.
    """
    if not _require_native():
        pytest.skip("hive_cpp not built; set HIVE_REQUIRE_NATIVE=1 to require it")
    pytest.importorskip("hive_cpp")

    from hive.rule_fast import RuleFastHoneyComb
    from hive.stack import HiveStack

    py_stack = HiveStack(backend="python", honey_comb=RuleFastHoneyComb())
    rs_stack = HiveStack(backend="native", honey_comb=RuleFastHoneyComb())

    for role, text in _CORPUS:
        py = py_stack.compress(role, text)
        rs = rs_stack.compress(role, text)

        # Documented native contract: ceil(n/2) input tokens, single-space
        # joined, original order — so no newlines survive.
        in_tokens = text.split()
        out_tokens = rs.content.split()
        assert rs.compressed_tokens == math.ceil(len(in_tokens) / 2), (
            f"native path token count changed for {role}: "
            f"{rs.compressed_tokens} != ceil({len(in_tokens)}/2)"
        )
        assert len(out_tokens) == rs.compressed_tokens
        assert "\n" not in rs.content, (
            "native path preserved newlines — no longer matches "
            "compressor.rs's space-joined output"
        )
        # Kept tokens are a subsequence of the input tokens.
        it = iter(in_tokens)
        assert all(tok in it for tok in out_tokens), (
            "native path emitted a token not present in the input"
        )

        # The paths must diverge exactly as documented: python keeps line
        # structure (verbatim or line-based distill), native flattens.
        assert rs.content != py.content, (
            f"python and native paths now agree on {role} input — "
            "the documented divergence is gone; update docs and this test"
        )
        if "\n" in text:
            assert "\n" in py.content or py.content == text, (
                f"python path lost all line structure on {role} input"
            )
