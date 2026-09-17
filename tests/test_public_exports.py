"""Public (README-documented) names must be importable from the package root."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "name", ["HiveConfig", "HiveStack", "HiveUnavailable", "RouteDecision"]
)
def test_public_export(name: str) -> None:
    module = __import__("hive", fromlist=[name])
    assert getattr(module, name) is not None


def test_from_hive_import_config_snippet() -> None:
    from hive import HiveConfig, HiveStack

    stack = HiveStack(config=HiveConfig(rate_limit=0))
    assert stack.rate_limiter is None


def test_unknown_attribute_still_raises() -> None:
    import hive

    with pytest.raises(AttributeError):
        hive.NotAThing  # noqa: B018
