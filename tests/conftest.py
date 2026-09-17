"""Shared test fixtures.

Two things every network-ish test in this suite needs:

* ``free_port`` — an unused localhost port, so no test depends on a
  machine-wide port number (18080/9876 collided with whatever else was running).
* ``wait_until`` — poll a predicate instead of ``time.sleep(n)``, and fail with
  a clear AssertionError on timeout rather than racing the server.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Callable
from typing import Any

import pytest


def _wait_until(
    predicate: Callable[[], Any],
    timeout: float = 5.0,
    interval: float = 0.05,
) -> None:
    """Poll ``predicate`` until it returns truthy. Raise on timeout."""
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception as exc:  # last error is reported below
            last_error = exc
        time.sleep(interval)
    raise AssertionError(
        f"condition not met within {timeout}s (last error: {last_error!r})"
    )


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def free_port() -> int:
    """An unused localhost TCP port."""
    return _free_port()


@pytest.fixture
def wait_until() -> Callable[..., None]:
    """The polling helper, so tests can ``wait_until(lambda: ...)``."""
    return _wait_until
