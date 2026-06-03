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
    jwks_url: str | None = None
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
        return cls(jwks=resp.json(), jwks_url=url)

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
            inst.jwks_url = url
            return inst
        return cls(public_key=pubkey, issuer=issuer, audience=audience)

    def validate(self, token: str, *, required_roles: list[str] | None = None) -> dict[str, Any]:
        """Validate a JWT and optionally check roles.

        Returns decoded claims dict on success. Raises AuthError on failure.
        """
        if not _HAS_JWT:
            raise AuthError("PyJWT library not installed")
        if not self.is_configured():
            raise AuthError(
                "JWT validator is not configured (set HIVE_JWKS_URL or HIVE_JWT_PUBLIC_KEY)"
            )

        try:
            options = {
                "verify_signature": True,
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
            elif self.jwks:
                kwargs["key"] = self._signing_key_from_jwks(token)

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

    def _signing_key_from_jwks(self, token: str) -> Any:
        """Resolve the signing key for ``token`` from JWKS (cached or remote)."""
        try:
            from jwt import PyJWKClient, PyJWKSet  # type: ignore[import-untyped]
            from jwt.exceptions import PyJWKClientError, PyJWKSetError  # type: ignore[import-untyped]
        except ImportError as exc:
            raise AuthError("PyJWT JWKS support required for JWKS validation") from exc

        try:
            if self.jwks_url:
                client = PyJWKClient(self.jwks_url)
                return client.get_signing_key_from_jwt(token).key
            if self.jwks:
                return self._signing_key_from_inline_jwks(token, PyJWKSet)
            raise AuthError("JWKS not loaded")
        except (PyJWKClientError, PyJWKSetError, ValueError) as exc:
            raise AuthError(f"Invalid token: {exc}") from None

    def _signing_key_from_inline_jwks(self, token: str, py_jwk_set: Any) -> Any:
        """Resolve signing key from an in-memory JWKS dict (no remote fetch)."""
        import jwt as jwt_module

        jwk_set = py_jwk_set.from_dict(self.jwks)
        header = jwt_module.get_unverified_header(token)
        kid = header.get("kid")
        if kid is not None:
            for jwk in jwk_set.keys:
                if jwk.key_id == kid:
                    return jwk.key
            raise AuthError(f"Invalid token: unable to find signing key for kid {kid!r}")
        if len(jwk_set.keys) == 1:
            return jwk_set.keys[0].key
        raise AuthError("Invalid token: missing kid in token header")


__all__ = ["JWTValidator", "AuthError"]
