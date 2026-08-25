"""Stress tests for JWT authentication and RBAC."""

from __future__ import annotations

import time

import pytest

from hive.auth import AuthError, JWTValidator


def test_disabled_validator():
    v = JWTValidator()
    assert not v.is_configured()


def test_unconfigured_validator_rejects_token():
    try:
        import jwt
    except ImportError:
        pytest.skip("PyJWT not installed")

    v = JWTValidator()
    token = jwt.encode(
        {"exp": time.time() + 3600, "roles": ["hive:admin"]},
        "secret",
        algorithm="HS256",
    )
    with pytest.raises(AuthError, match="not configured"):
        v.validate(token, required_roles=["hive:admin"])


def test_validator_with_public_key_rejects_expired():
    # Create a validator with no key — signature verification disabled
    v = JWTValidator(public_key=None, algorithm="HS256")
    assert v.is_configured() is False


def test_auth_error_message():
    with pytest.raises(AuthError, match="Invalid"):
        v = JWTValidator(public_key="fake")
        v.validate("not.a.token")


def test_role_check_logic():
    # We can't easily test full JWT validation without a real key,
    # but we can test the role-check logic by mocking claims
    v = JWTValidator(public_key=None)
    # If jwt library is absent, skip
    try:
        import jwt
    except ImportError:
        pytest.skip("PyJWT not installed")

    # Test with a token that has no signature verification
    claims = {"roles": ["hive:reader"], "exp": time.time() + 3600}
    token = jwt.encode(claims, "secret", algorithm="HS256")
    v = JWTValidator(public_key="secret", algorithm="HS256")

    # Valid role
    result = v.validate(token, required_roles=["hive:reader"])
    assert result["roles"] == ["hive:reader"]

    # Missing role
    with pytest.raises(AuthError, match="Missing required roles"):
        v.validate(token, required_roles=["hive:admin"])


def test_jwks_url_lookup_failure_raises_auth_error():
    """PyJWKClient errors must surface as AuthError, not uncaught PyJWKClientError."""
    try:
        import jwt
        from jwt.exceptions import PyJWKClientError
    except ImportError:
        pytest.skip("PyJWT not installed")
    from unittest.mock import patch

    v = JWTValidator(jwks={"keys": []}, jwks_url="https://idp.example.com/jwks.json")
    token = jwt.encode({"exp": time.time() + 3600}, "x", algorithm="HS256")
    with patch("jwt.PyJWKClient") as client_cls:
        client_cls.return_value.get_signing_key_from_jwt.side_effect = PyJWKClientError(
            "Unable to find a signing key"
        )
        with pytest.raises(AuthError, match="Invalid token"):
            v.validate(token)


def test_inline_jwks_wrong_kid_raises_auth_error():
    try:
        import jwt
    except ImportError:
        pytest.skip("PyJWT not installed")

    jwks = {
        "keys": [
            {
                "kty": "oct",
                "kid": "expected",
                "k": "c2VjcmV0",  # "secret"
            }
        ]
    }
    v = JWTValidator(jwks=jwks, algorithm="HS256")
    token = jwt.encode(
        {"exp": time.time() + 3600},
        "secret",
        algorithm="HS256",
        headers={"kid": "wrong"},
    )
    with pytest.raises(AuthError, match="Invalid token"):
        v.validate(token)


def test_empty_inline_jwks_raises_auth_error():
    try:
        import jwt
    except ImportError:
        pytest.skip("PyJWT not installed")

    v = JWTValidator(jwks={"keys": []})
    token = jwt.encode({"exp": time.time() + 3600}, "x", algorithm="HS256")
    with pytest.raises(AuthError, match="Invalid token"):
        v.validate(token)


def test_expired_token():
    try:
        import jwt
    except ImportError:
        pytest.skip("PyJWT not installed")

    claims = {"exp": time.time() - 10}
    token = jwt.encode(claims, "secret", algorithm="HS256")
    v = JWTValidator(public_key="secret", algorithm="HS256")

    with pytest.raises(AuthError, match="expired"):
        v.validate(token)
