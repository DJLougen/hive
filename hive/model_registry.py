"""Signed .joblib model registry — prevents pickle RCE from untrusted models.

Only loads models whose ``.joblib.sig`` sidecar carries a valid Ed25519
signature over the model's SHA-256 digest, produced by a signer whose
public key fingerprint is in the trust store. Models without a signature
are rejected by default (``strict=True``). Set ``strict=False`` for
development.

Signature format (``model.joblib.sig``)::

    {
      "fingerprint": "<sha256 hex of the DER public key>",
      "public_key":  "<base64 DER SubjectPublicKeyInfo>",
      "signature":   "<base64 Ed25519 signature over the sha256 hexdigest>"
    }

Sign models with :meth:`ModelRegistry.sign_model`.

Usage::

    from hive.model_registry import ModelRegistry

    reg = ModelRegistry(strict=True, trust_store="trusted_signers.json")
    model = reg.load("/path/to/policy.joblib")  # raises if unsigned
"""

from __future__ import annotations

import hashlib
import json
import logging
from base64 import b64decode, b64encode
from pathlib import Path
from typing import Any

_log = logging.getLogger("hive.model_registry")


try:
    import joblib

    _HAS_JOBLIB = True
except Exception:  # pragma: no cover
    _HAS_JOBLIB = False

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )

    _HAS_CRYPTO = True
except Exception:  # pragma: no cover
    _HAS_CRYPTO = False


class UnsignedModelError(RuntimeError):
    """Raised when a model lacks a valid signature in strict mode."""

    pass


def _public_key_fingerprint(public_key: Ed25519PublicKey) -> tuple[str, bytes]:
    """Return (hex fingerprint, DER bytes) for an Ed25519 public key."""
    der = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(der).hexdigest(), der


class ModelRegistry:
    """Signed model loader.

    Parameters
    ----------
    strict:
        If True, reject unsigned/unverifiable models. If False, warn but load.
    trust_store:
        Path to a JSON file of trusted signer fingerprints.
        Format: ``{"signers": ["<sha256-of-der-pubkey hex>", ...]}``
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

        # Verify SHA-256 integrity first (cheap, catches corruption)
        expected_hash = hash_file.read_text("utf-8").strip().split()[0]
        actual_hash = self._sha256(path)
        if actual_hash != expected_hash:
            raise UnsignedModelError(
                f"SHA-256 mismatch for {path}: expected {expected_hash}, got {actual_hash}"
            )

        # Verify the Ed25519 signature cryptographically.
        try:
            signer = self._verify_signature(sig_file, actual_hash)
        except UnsignedModelError:
            if self._strict:
                raise
            _log.warning(
                "%s signature unverifiable — loading anyway (strict=False)", path
            )
            signer = None

        _log.info("Loaded signed model %s (signer=%s)", path, signer or "unverified")
        return joblib.load(path)

    def _verify_signature(self, sig_file: Path, digest_hex: str) -> str:
        """Verify ``sig_file`` cryptographically. Returns the signer fingerprint."""
        try:
            sig_data = json.loads(sig_file.read_text("utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise UnsignedModelError(f"Cannot parse {sig_file}: {exc}") from exc

        pubkey_b64 = sig_data.get("public_key")
        signature_b64 = sig_data.get("signature")
        if not pubkey_b64 or not signature_b64:
            # Legacy sig files carried only a fingerprint — no cryptographic
            # content to verify, so they cannot be trusted in strict mode.
            raise UnsignedModelError(
                f"{sig_file} has no Ed25519 signature/public_key — "
                "re-sign the model with ModelRegistry.sign_model()"
            )
        if not _HAS_CRYPTO:
            raise UnsignedModelError(
                "cryptography library required to verify model signatures"
            )

        try:
            public_key_der = b64decode(pubkey_b64, validate=True)
            signature = b64decode(signature_b64, validate=True)
            public_key = serialization.load_der_public_key(public_key_der)
            if not isinstance(public_key, Ed25519PublicKey):
                raise UnsignedModelError(
                    f"{sig_file}: signing key must be Ed25519"
                )
        except UnsignedModelError:
            raise
        except Exception as exc:
            raise UnsignedModelError(f"{sig_file}: malformed signature data: {exc}") from exc

        fingerprint, _ = _public_key_fingerprint(public_key)
        declared = sig_data.get("fingerprint", "")
        if declared and declared != fingerprint:
            raise UnsignedModelError(
                f"{sig_file}: declared fingerprint {declared!r} does not match the public key"
            )
        if fingerprint not in self._trusted:
            raise UnsignedModelError(f"Signer {fingerprint!r} not in trust store")

        try:
            public_key.verify(signature, digest_hex.encode("utf-8"))
        except Exception as exc:
            raise UnsignedModelError(
                f"{sig_file}: Ed25519 signature does not verify: {exc}"
            ) from exc
        return fingerprint

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

    @staticmethod
    def sign_model(path: str | Path, private_key: Any) -> dict[str, str]:
        """Sign a model file with an Ed25519 private key.

        Writes ``<model>.sha256`` and ``<model>.sig`` sidecars and returns
        the signer's fingerprint so it can be added to the trust store.
        ``private_key`` is an ``Ed25519PrivateKey`` or PEM/DER key bytes.
        """
        if not _HAS_CRYPTO:
            raise RuntimeError("cryptography library required for signing")
        path = Path(path)
        if isinstance(private_key, (bytes, bytearray)):
            data = bytes(private_key)
            try:
                private_key = serialization.load_pem_private_key(data, password=None)
            except ValueError:
                private_key = serialization.load_der_private_key(data, password=None)
        if not isinstance(private_key, Ed25519PrivateKey):
            raise TypeError("sign_model requires an Ed25519 private key")

        digest_hex = ModelRegistry._sha256(path)
        path.with_suffix(".joblib.sha256").write_text(
            f"{digest_hex}  {path.name}\n", encoding="utf-8"
        )
        public_key = private_key.public_key()
        fingerprint, public_key_der = _public_key_fingerprint(public_key)
        signature = private_key.sign(digest_hex.encode("utf-8"))
        sig_doc = {
            "algorithm": "ed25519",
            "fingerprint": fingerprint,
            "public_key": b64encode(public_key_der).decode("ascii"),
            "signature": b64encode(signature).decode("ascii"),
        }
        path.with_suffix(".joblib.sig").write_text(
            json.dumps(sig_doc, indent=2), encoding="utf-8"
        )
        return sig_doc

    def trust_signer(self, fingerprint: str) -> None:
        self._trusted.add(fingerprint)

    def save_trust_store(self, path: str) -> None:
        Path(path).write_text(
            json.dumps({"signers": sorted(self._trusted)}, indent=2),
            encoding="utf-8",
        )


__all__ = ["ModelRegistry", "UnsignedModelError"]
