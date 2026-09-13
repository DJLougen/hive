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
