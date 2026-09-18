"""Smoke tests: the disclosed spec. Fails on the shipped repo."""

from __future__ import annotations

from outbox import Consumer


def test_messages_apply_in_order():
    c = Consumer()
    c.apply("a", 1, 10)
    c.apply("a", 2, 5)
    assert c.balance("a") == 15


def test_a_redelivery_is_a_no_op():
    c = Consumer()
    c.apply("a", 1, 10)
    c.apply("a", 1, 10)  # same seq again — must not double-count
    assert c.balance("a") == 10


def test_an_out_of_order_older_message_is_dropped():
    c = Consumer()
    c.apply("a", 1, 10)
    c.apply("a", 2, 5)
    c.apply("a", 1, 100)  # stale redelivery — must not rewind the balance
    assert c.balance("a") == 15


def test_a_gap_is_held_not_skipped():
    c = Consumer()
    c.apply("a", 1, 10)
    # seq 3 arrives before seq 2: it is held, not applied yet.
    assert not c.apply("a", 3, 7)
    assert c.balance("a") == 10
    # When seq 2 lands, the held seq 3 flushes in order.
    c.apply("a", 2, 5)
    assert c.balance("a") == 22


def test_accounts_are_independent():
    c = Consumer()
    c.apply("a", 1, 10)
    c.apply("b", 1, 100)
    assert c.balance("a") == 10 and c.balance("b") == 100
