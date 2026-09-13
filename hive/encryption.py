"""Encryption at rest for Hive sensitive data.

Provides AES-256-GCM transparent encryption for RustBrain values.
Keys are derived from ``HIVE_ENCRYPTION_KEY`` env var via PBKDF2.
When encryption is disabled (no key configured) values pass through
unchanged — fully backward compatible.

Usage::

    from hive.encryption import Encryptor

    e = Encryptor.from_env()
    ciphertext = e.encrypt("sensitive-api-key")
    plaintext = e.decrypt(ciphertext)
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from base64 import b64decode, b64encode
from typing import Any

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore[import]

    _HAS_CRYPTO = True
except Exception:  # pragma: no cover
    _HAS_CRYPTO = False


class Encryptor:
    """AES-256-GCM encryptor with PBKDF2 key derivation."""

    def __init__(self, key: bytes) -> None:
        if len(key) not in (16, 24, 32):
            raise ValueError("AES key must be 16, 24, or 32 bytes")
        self._key = key
        self._enabled = True

    @classmethod
    def from_env(cls, env_var: str = "HIVE_ENCRYPTION_KEY") -> Encryptor:
        """Create an encryptor from an environment variable.

        If the variable is absent or empty, encryption is disabled
        (pass-through mode).
        """
        raw = os.environ.get(env_var, "")
        if not raw:
            return cls._disabled()
        # Derive 32-byte key from passphrase via PBKDF2-HMAC-SHA256
        salt = b"hive-agent-memory-v0"
        key = hashlib.pbkdf2_hmac("sha256", raw.encode(), salt, iterations=100_000, dklen=32)
        return cls(key)

    @classmethod
    def _disabled(cls) -> Encryptor:
        """No-op encryptor for backward compatibility."""
        inst = cls.__new__(cls)
        inst._key = b""
        inst._enabled = False
        return inst

    def encrypt(self, plaintext: Any) -> str:
        """Encrypt a JSON-serialisable value. Returns base64(ciphertext)."""
        if not self._enabled:
            return json.dumps({"__unencrypted__": True, "v": plaintext})
        if not _HAS_CRYPTO:
            raise RuntimeError("cryptography library required for encryption")
        nonce = secrets.token_bytes(12)
        payload = json.dumps(plaintext).encode("utf-8")
        ct = AESGCM(self._key).encrypt(nonce, payload, None)
        return b64encode(nonce + ct).decode("ascii")

    def decrypt(self, ciphertext: str) -> Any:
        """Decrypt a value. Returns the original Python object."""
        if not self._enabled:
            # Pass-through: try legacy unencrypted JSON or raw string
            try:
                parsed = json.loads(ciphertext)
                if isinstance(parsed, dict) and parsed.get("__unencrypted__"):
                    return parsed["v"]
                return parsed
            except json.JSONDecodeError:
                return ciphertext
        if not _HAS_CRYPTO:
            raise RuntimeError("cryptography library required for decryption")
        raw = b64decode(ciphertext)
        nonce, ct = raw[:12], raw[12:]
        pt = AESGCM(self._key).decrypt(nonce, ct, None)
        return json.loads(pt.decode("utf-8"))

    def is_enabled(self) -> bool:
        return self._enabled


__all__ = ["Encryptor"]
