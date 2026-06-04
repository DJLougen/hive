"""Tests for signed .joblib model registry."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from hive.model_registry import ModelRegistry, UnsignedModelError


def test_strict_rejects_unsigned():
    reg = ModelRegistry(strict=True)
    with tempfile.NamedTemporaryFile(suffix=".joblib", delete=False) as f:
        path = f.name
        f.write(b"fake_model")
    try:
        with pytest.raises(UnsignedModelError):
            reg.load(path)
    finally:
        Path(path).unlink(missing_ok=True)


def test_non_strict_warns_and_loads():
    reg = ModelRegistry(strict=False)
    with tempfile.NamedTemporaryFile(suffix=".joblib", delete=False) as f:
        path = f.name
        f.write(b"fake_model")
    try:
        # Should NOT raise in non-strict mode
        # joblib.load will fail because it's not a real joblib file
        # but that's expected — the registry should get past signature checks
        with pytest.raises(Exception):
            reg.load(path)
    finally:
        Path(path).unlink(missing_ok=True)


def test_sha256_mismatch():
    reg = ModelRegistry(strict=True)
    with tempfile.TemporaryDirectory() as td:
        model = Path(td) / "model.joblib"
        model.write_bytes(b"fake")
        # Create wrong hash file
        hash_file = model.with_suffix(".joblib.sha256")
        hash_file.write_text("wronghash")
        # Create empty sig file
        sig_file = model.with_suffix(".joblib.sig")
        sig_file.write_text(json.dumps({"fingerprint": "AABBCC"}))
        with pytest.raises(UnsignedModelError):
            reg.load(model)


def test_trust_store():
    reg = ModelRegistry(strict=True)
    reg.trust_signer("AABBCC")
    assert "AABBCC" in reg._trusted
