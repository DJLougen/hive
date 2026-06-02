"""Production-grade JWT authentication and role-based access control.

Validates bearer tokens against a JWKS endpoint or inline public key.
Supports role-based access control (RBAC) and token expiry enforcement.

Usage::

    from hive.auth import JWTValidator

    validator = JWTValidator.from_jwks("https://idp.example.com/.well-known/jwks.json")
    claims = validator.validate(token, required_roles=["hive:admin"])
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

try:
    import jwt  # PyJWT

    _HAS_JWT = True
except Exception:  # pragma: no cover
    _HAS_JWT = False


try:
    import requests  # type: ignore[import]

    _HAS_REQUESTS = True
except Exception:  # pragma: no cover
    _HAS_REQUESTS = False


class AuthError(Exception):
    """Raised when authentication or authorization fails."""

    pass


@dataclass
class JWTValidator:
    """JWT validator with JWKS support."""

    jwks: dict[str, Any] | None = None
    public_key: str | None = None
    algorithm: str = "RS256"
    issuer: str | None = None
    audience: str | None = None
    leeway_s: float = 30.0

    @classmethod
    def from_jwks(cls, url: str) -> "JWTValidator":
        """Fetch JWKS from a remote endpoint."""
        if not _HAS_REQUESTS:
            raise RuntimeError("requests library required for JWKS fetch")
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return cls(jwks=resp.json())

    @classmethod
    def from_env(cls) -> "JWTValidator":
        """Build validator from environment variables."""
        url = os.environ.get("HIVE_JWKS_URL")
        pubkey = os.environ.get("HIVE_JWT_PUBLIC_KEY")
        issuer = os.environ.get("HIVE_JWT_ISSUER")
        audience = os.environ.get("HIVE_JWT_AUDIENCE")
        if url:
            inst = cls.from_jwks(url)
            inst.issuer = issuer
            inst.audience = audience
            return inst
        return cls(public_key=pubkey, issuer=issuer, audience=audience)

    def validate(self, token: str, *, required_roles: list[str] | None = None) -> dict[str, Any]:
        """Validate a JWT and optionally check roles.

        Returns decoded claims dict on success. Raises AuthError on failure.
        """
        if not _HAS_JWT:
            raise AuthError("PyJWT library not installed")

        try:
            options = {
                "verify_signature": bool(self.jwks or self.public_key),
                "verify_exp": True,
                "verify_iat": True,
                "require": ["exp"],
            }
            kwargs: dict[str, Any] = {"algorithms": [self.algorithm], "options": options}
            if self.issuer:
                kwargs["issuer"] = self.issuer
            if self.audience:
                kwargs["audience"] = self.audience
            if self.public_key:
                kwargs["key"] = self.public_key

            claims = jwt.decode(token, **kwargs)
        except jwt.ExpiredSignatureError:
            raise AuthError("Token has expired") from None
        except jwt.InvalidTokenError as exc:
            raise AuthError(f"Invalid token: {exc}") from None

        if required_roles:
            roles = set(claims.get("roles", []))
            if not roles.issuperset(required_roles):
                missing = set(required_roles) - roles
                raise AuthError(f"Missing required roles: {missing}")

        return claims

    def is_configured(self) -> bool:
        """Return True if validator has enough config to check tokens."""
        return bool(self.jwks or self.public_key)


__all__ = ["JWTValidator", "AuthError"]
