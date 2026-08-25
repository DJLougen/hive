"""Tests for HIVE_BACKEND resolution and wiring."""

from __future__ import annotations

import os
from unittest.mock import patch

from hive.backend import resolve_backend
from hive.stack import HiveStack


def test_resolve_backend_defaults_to_python():
    with patch.dict(os.environ, {}, clear=True):
        with patch("hive.backend._native_available", return_value=False):
            assert resolve_backend() == "python"


def test_resolve_backend_env_native():
    with patch.dict(os.environ, {"HIVE_BACKEND": "native"}, clear=True):
        with patch("hive.backend._native_available", return_value=True):
            assert resolve_backend() == "native"


def test_hivestack_stats_includes_backend():
    stack = HiveStack(backend="python")
    stats = stack.stats()
    assert stats["backend"] == "python"


def test_native_compress_with_telemetry_does_not_crash():
    """Native compress must not reference the Python-only ``out`` variable."""
    from unittest.mock import patch

    from hive.rust_brain import RustBrain
    from hive.rule_fast import RuleFastHoneyComb
    from hive.telemetry import Telemetry

    telemetry = Telemetry()
    stack = HiveStack(
        honey_comb=RuleFastHoneyComb(),
        telemetry=telemetry,
        backend="native",
        rust_brain=RustBrain(),
    )
    native_out = {
        "role": "user",
        "content": "hi",
        "label": "CORE",
        "original_tokens": 10,
        "compressed_tokens": 5,
    }
    with patch("hive.backend.native_compress", return_value=native_out):
        result = stack.compress("user", "hello world")

    assert result.content == "hi"
    assert telemetry.compression[-1].original_tokens == 10
    assert telemetry.compression[-1].compressed_tokens == 5
