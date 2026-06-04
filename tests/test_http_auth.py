"""Tests for hive.http_auth."""

from __future__ import annotations

import os

import pytest

from hive.auth import AuthError
from hive.http_auth import require_auth_enabled, verify_bearer_token


def test_auth_disabled_by_default():
    os.environ.pop("HIVE_REQUIRE_AUTH", None)
    assert not require_auth_enabled()
    assert verify_bearer_token(None) == {}


def test_auth_required_rejects_missing_header():
    os.environ["HIVE_REQUIRE_AUTH"] = "true"
    try:
        with pytest.raises(AuthError, match="Missing"):
            verify_bearer_token(None)
    finally:
        os.environ.pop("HIVE_REQUIRE_AUTH", None)
