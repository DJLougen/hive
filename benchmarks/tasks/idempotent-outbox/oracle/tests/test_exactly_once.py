"""Held-out grading tests for exactly-once, in-order application."""

from __future__ import annotations

from outbox import Consumer


def test_a_redelivery_of_the_newest_seq_is_dropped():
    c = Consumer()
    c.apply("a", 1, 10)
    c.apply("a", 2, 5)
    c.apply("a", 2, 5)
    assert c.balance("a") == 15


def test_a_stale_message_after_a_gap_flush_is_dropped():
    c = Consumer()
    c.apply("a", 1, 10)
    c.apply("a", 3, 7)   # held
    c.apply("a", 2, 5)   # flushes 2 and 3
    c.apply("a", 2, 999) # stale — must not re-apply or rewind
    assert c.balance("a") == 22


def test_a_long_gap_flushes_in_order():
    c = Consumer()
    c.apply("a", 1, 1)
    c.apply("a", 4, 4)   # held
    c.apply("a", 3, 3)   # held
    c.apply("a", 2, 2)   # flushes 2,3,4
    assert c.balance("a") == 10


def test_out_of_order_across_accounts_is_independent():
    c = Consumer()
    c.apply("a", 2, 20)  # held (a is at 0)
    c.apply("b", 1, 100) # b applies fine
    c.apply("a", 1, 10)  # flushes a's 1 and 2
    assert c.balance("a") == 30 and c.balance("b") == 100


def test_the_first_message_must_be_seq_1():
    c = Consumer()
    assert not c.apply("a", 5, 50)  # held — seq 1..4 never arrived
    assert c.balance("a") == 0


def test_a_duplicate_of_a_held_message_is_held_once():
    c = Consumer()
    c.apply("a", 2, 20)
    c.apply("a", 2, 20)  # duplicate of a held message — still one pending
    c.apply("a", 1, 10)
    assert c.balance("a") == 30


def test_apply_reports_whether_it_or_a_held_message_moved():
    c = Consumer()
    assert c.apply("a", 1, 10)      # applied now
    assert not c.apply("a", 3, 30)  # held
    assert c.apply("a", 2, 20)      # applied, and flushed 3


def test_interleaved_redelivery_and_gap():
    c = Consumer()
    c.apply("a", 1, 10)
    c.apply("a", 1, 10)  # dup
    c.apply("a", 3, 30)  # held
    c.apply("a", 1, 10)  # dup again
    c.apply("a", 2, 20)  # flush
    c.apply("a", 3, 30)  # dup of flushed
    assert c.balance("a") == 60


def test_negative_deltas_apply_once_too():
    c = Consumer()
    c.apply("a", 1, 100)
    c.apply("a", 2, -30)
    c.apply("a", 2, -30)
    assert c.balance("a") == 70


def test_a_new_account_starts_at_seq_1():
    c = Consumer()
    c.apply("a", 1, 5)
    c.apply("b", 3, 99)  # held — b has no seq 1,2
    assert c.balance("b") == 0
    c.apply("b", 1, 1)
    c.apply("b", 2, 2)
    assert c.balance("b") == 102
