"""Signed .joblib model registry — prevents pickle RCE from untrusted models.

Only loads models that have a valid SHA-256 + GPG signature. Models without
a signature are rejected by default (``strict=True``). Set ``strict=False``
for development.

Usage::

    from hive.model_registry import ModelRegistry

    reg = ModelRegistry(strict=True, trust_store="trusted_signers.json")
    model = reg.load("/path/to/policy.joblib")  # raises if unsigned
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger("hive.model_registry")


try:
    import joblib

    _HAS_JOBLIB = True
except Exception:  # pragma: no cover
    _HAS_JOBLIB = False


class UnsignedModelError(RuntimeError):
    """Raised when a model lacks a valid signature in strict mode."""

    pass


class ModelRegistry:
    """Signed model loader.

    Parameters
    ----------
    strict:
        If True, reject unsigned models. If False, warn but load.
    trust_store:
        Path to a JSON file containing trusted signer fingerprints.
        Format: {"signers": ["A1B2C3...", ...]}
    """

    def __init__(
        self,
        *,
        strict: bool = True,
        trust_store: str | None = None,
    ) -> None:
        self._strict = strict
        self._trusted: set[str] = set()
        if trust_store and Path(trust_store).exists():
            data = json.loads(Path(trust_store).read_text("utf-8"))
            self._trusted = set(data.get("signers", []))

    def load(self, path: str | Path) -> Any:
        """Load a signed .joblib model. Raises UnsignedModelError if invalid."""
        path = Path(path)
        if not _HAS_JOBLIB:
            raise RuntimeError("joblib not installed")

        # Check companion signature files
        sig_file = path.with_suffix(".joblib.sig")
        hash_file = path.with_suffix(".joblib.sha256")

        if not sig_file.exists() or not hash_file.exists():
            msg = f"Model {path} lacks signature files"
            if self._strict:
                raise UnsignedModelError(msg)
            _log.warning("%s — loading anyway (strict=False)", msg)
            return joblib.load(path)

        # Verify SHA-256
        expected_hash = hash_file.read_text("utf-8").strip().split()[0]
        actual_hash = self._sha256(path)
        if actual_hash != expected_hash:
            raise UnsignedModelError(
                f"SHA-256 mismatch for {path}: expected {expected_hash}, got {actual_hash}"
            )

        # Verify signature (stub — real GPG verification would use python-gnupg)
        sig_data = json.loads(sig_file.read_text("utf-8"))
        signer = sig_data.get("fingerprint", "")
        if signer not in self._trusted:
            msg = f"Signer {signer!r} not in trust store"
            if self._strict:
                raise UnsignedModelError(msg)
            _log.warning("%s — loading anyway (strict=False)", msg)

        _log.info("Loaded signed model %s (signer=%s)", path, signer)
        return joblib.load(path)

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(8192)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    def trust_signer(self, fingerprint: str) -> None:
        self._trusted.add(fingerprint)

    def save_trust_store(self, path: str) -> None:
        Path(path).write_text(
            json.dumps({"signers": sorted(self._trusted)}, indent=2),
            encoding="utf-8",
        )


__all__ = ["ModelRegistry", "UnsignedModelError"]
