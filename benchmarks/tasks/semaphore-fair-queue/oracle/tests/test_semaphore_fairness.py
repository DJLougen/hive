"""Held-out grading tests for semaphore fairness."""

from __future__ import annotations

import pytest

from semaphore import Semaphore


def test_fifo_order_is_preserved_across_several_releases():
    s = Semaphore(1)
    s.acquire("holder")
    for who in ("w1", "w2", "w3"):
        s.acquire(who)
    assert s.waiting == 3
    s.release()                       # -> w1 (longest waiting)
    assert s.cancel("w1") is False    # w1 was woken, so it is no longer queued
    assert s.cancel("w2") is True     # w2 still queued
    assert s.cancel("w3") is True     # w3 still queued
    # put w2 back in order and release again: w2, then w3, are served FIFO
    s.acquire("w2")
    s.acquire("w3")
    s.release()                       # -> w2
    assert s.cancel("w2") is False
    assert s.cancel("w3") is True
    s.release()                       # -> w3
    assert s.cancel("w3") is False
    assert s.waiting == 0


def test_a_full_round_trip_keeps_the_queue_order():
    s = Semaphore(2)
    s.acquire("a")
    s.acquire("b")
    s.acquire("x")   # queued first
    s.acquire("y")   # queued second
    s.release()      # x gets it
    s.release()      # y gets it
    assert s.waiting == 0
    assert s.available == 0


def test_release_with_no_waiters_leaves_a_permit_free():
    s = Semaphore(2)
    s.acquire("a")
    s.release()
    assert s.available == 2
    assert s.waiting == 0


def test_cancel_of_a_woken_waiter_reports_false():
    s = Semaphore(1)
    s.acquire("holder")
    s.acquire("waiter")
    s.release()
    assert s.cancel("waiter") is False   # already woken, not queued


def test_available_never_goes_negative():
    s = Semaphore(1)
    s.acquire("a")
    assert s.available == 0
    s.release()
    assert s.available == 1


def test_releasing_past_the_held_count_raises():
    s = Semaphore(2)
    s.acquire("a")
    s.release()
    with pytest.raises(ValueError):
        s.release()


def test_a_waiter_that_was_never_queued_cancels_false():
    s = Semaphore(2)
    assert s.cancel("ghost") is False
