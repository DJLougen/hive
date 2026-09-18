"""Smoke tests: the disclosed spec. Fails on the shipped repo."""

from __future__ import annotations

import pytest

from reserve import HoldExpired, Reservations, SlotTaken


def test_reserve_returns_the_expiry():
    r = Reservations(ttl=60.0)
    assert r.reserve("s1", 100.0) == 160.0


def test_confirm_within_the_hold_succeeds():
    r = Reservations(ttl=60.0)
    r.reserve("s1", 100.0)
    r.confirm("s1", 150.0)


def test_confirm_at_the_deadline_still_succeeds():
    # The deadline is inclusive: reserve+ttl is the last valid instant.
    r = Reservations(ttl=60.0)
    r.reserve("s1", 100.0)
    r.confirm("s1", 160.0)


def test_confirm_after_the_deadline_raises():
    r = Reservations(ttl=60.0)
    r.reserve("s1", 100.0)
    with pytest.raises(HoldExpired):
        r.confirm("s1", 160.001)


def test_an_expired_hold_frees_the_slot():
    r = Reservations(ttl=60.0)
    r.reserve("s1", 100.0)
    # At t=200 the hold is long dead: a new reserve must succeed.
    assert r.reserve("s1", 200.0) == 260.0


def test_a_confirmed_slot_stays_taken():
    r = Reservations(ttl=60.0)
    r.reserve("s1", 100.0)
    r.confirm("s1", 110.0)
    with pytest.raises(SlotTaken):
        r.reserve("s1", 500.0)
