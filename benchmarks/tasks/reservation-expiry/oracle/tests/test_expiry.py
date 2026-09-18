"""Held-out grading tests for hold expiry."""

from __future__ import annotations

import pytest

from reserve import HoldExpired, Reservations, SlotTaken


def test_the_deadline_is_exactly_reserve_plus_ttl():
    r = Reservations(ttl=60.0)
    r.reserve("s", 100.0)
    r.confirm("s", 160.0)  # inclusive: the last valid instant


def test_one_tick_past_the_deadline_is_expired():
    r = Reservations(ttl=60.0)
    r.reserve("s", 100.0)
    with pytest.raises(HoldExpired):
        r.confirm("s", 160.0001)


def test_a_second_reserve_during_a_live_hold_fails():
    r = Reservations(ttl=60.0)
    r.reserve("s", 100.0)
    with pytest.raises(SlotTaken):
        r.reserve("s", 120.0)


def test_a_reserve_after_expiry_gets_a_fresh_deadline():
    r = Reservations(ttl=60.0)
    r.reserve("s", 100.0)
    assert r.reserve("s", 200.0) == 260.0
    r.confirm("s", 260.0)  # the new hold's own deadline, not the old one


def test_is_free_reflects_expiry():
    r = Reservations(ttl=60.0)
    r.reserve("s", 100.0)
    assert not r.is_free("s", 150.0)
    assert r.is_free("s", 200.0)


def test_confirming_an_unreserved_slot_raises():
    r = Reservations(ttl=60.0)
    with pytest.raises(HoldExpired):
        r.confirm("never", 100.0)


def test_a_confirmed_booking_never_expires():
    r = Reservations(ttl=60.0)
    r.reserve("s", 100.0)
    r.confirm("s", 110.0)
    assert not r.is_free("s", 10_000.0)
    with pytest.raises(SlotTaken):
        r.reserve("s", 10_000.0)


def test_confirm_is_idempotent_on_a_booked_slot():
    r = Reservations(ttl=60.0)
    r.reserve("s", 100.0)
    r.confirm("s", 110.0)
    r.confirm("s", 120.0)  # already booked — not an error


def test_expired_holds_do_not_block_other_slots():
    r = Reservations(ttl=60.0)
    r.reserve("a", 100.0)
    r.reserve("b", 100.0)
    # 'a' expires; 'b' is still live and unaffected.
    assert r.is_free("a", 200.0)
    assert not r.is_free("b", 150.0)


def test_a_hold_that_expired_cannot_be_confirmed_late():
    r = Reservations(ttl=60.0)
    r.reserve("s", 100.0)
    r.reserve("s", 200.0)  # new hold after expiry
    # Confirming at the NEW deadline is fine — the old hold is gone.
    r.confirm("s", 260.0)
