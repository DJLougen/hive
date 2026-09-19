"""Smoke tests: the public API happy path. Not the grading criterion."""

from __future__ import annotations

import pytest

from semaphore import Semaphore


def test_a_free_permit_is_granted_immediately():
    s = Semaphore(2)
    assert s.acquire("a") is True
    assert s.available == 1


def test_an_acquirer_waits_when_no_permit_is_free():
    s = Semaphore(1)
    assert s.acquire("a") is True
    assert s.acquire("b") is False
    assert s.waiting == 1


def test_release_wakes_a_waiter():
    s = Semaphore(1)
    s.acquire("a")
    s.acquire("b")
    s.release()
    assert s.waiting == 0
    assert s.available == 0  # the permit went straight to 'b'


def test_release_without_acquire_raises():
    s = Semaphore(1)
    with pytest.raises(ValueError):
        s.release()


def test_cancel_withdraws_a_waiter():
    s = Semaphore(1)
    s.acquire("a")
    s.acquire("b")
    assert s.cancel("b") is True
    assert s.waiting == 0


# --- Spec coverage: the issue's stated rule, not just the happy path.
# These fail on the shipped repo; the held-out oracle checks the edge cases.


def test_the_longest_waiting_acquirer_is_woken_first():
    s = Semaphore(1)
    s.acquire("holder")
    s.acquire("first")
    s.acquire("second")
    s.release()
    assert s.waiting == 1
    # The freed permit goes to 'first' (waiting longest), so 'first' is no
    # longer queued while 'second' still is.
    assert s.cancel("first") is False   # already woken
    assert s.cancel("second") is True   # still queued


def test_a_cancelled_waiter_never_consumes_a_permit():
    s = Semaphore(1)
    s.acquire("holder")
    s.acquire("quitter")
    s.cancel("quitter")
    assert s.waiting == 0
    s.release()
    assert s.available == 1  # nobody took the freed permit
