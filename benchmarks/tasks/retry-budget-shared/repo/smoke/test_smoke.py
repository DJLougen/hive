"""Smoke tests: the public API happy path. Not the grading criterion."""

from __future__ import annotations

import pytest

from resilience import Client, RetryBudget, budget_for, delay_for, reset_budgets


@pytest.fixture(autouse=True)
def _clean_budgets():
    reset_budgets()
    yield
    reset_budgets()


def test_client_returns_the_value_when_nothing_fails():
    assert Client("smoke-value").call(lambda: 42) == 42


def test_client_retries_a_retryable_failure():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError("slow")
        return "ok"

    assert Client("smoke-retry", limit=5, sleeper=lambda _: None).call(flaky) == "ok"
    assert len(calls) == 3


def test_budget_reports_the_limit_it_was_asked_for():
    assert budget_for("smoke-limit", 4).limit == 4


def test_budget_refuses_past_its_limit():
    b = RetryBudget(2)
    assert b.spend() and b.spend() and not b.spend()
    assert b.remaining == 0


def test_delay_grows_then_caps():
    assert delay_for(1) < delay_for(2) <= 1.0


# --- Spec coverage: the issue's stated requirements, not just the happy path.
# These fail on the shipped repo; the held-out oracle checks the edge cases.


def test_same_name_returns_the_same_budget_object():
    assert budget_for("smoke-shared", 3) is budget_for("smoke-shared", 3)


def test_first_limit_wins_for_a_name():
    first = budget_for("smoke-capped", 2)
    again = budget_for("smoke-capped", 9)
    assert again is first and again.limit == 2


def test_two_clients_share_one_cap():
    from resilience import RetryBudgetExhausted

    def down():
        raise TimeoutError("down")

    first = Client("smoke-svc", limit=2, sleeper=lambda _: None)
    second = Client("smoke-svc", limit=2, sleeper=lambda _: None)
    with pytest.raises(RetryBudgetExhausted):
        first.call(down)
    calls = []

    def still_down():
        calls.append(1)
        raise TimeoutError("down")

    with pytest.raises(RetryBudgetExhausted):
        second.call(still_down)
    assert calls == []  # the shared cap was already spent — no new attempts
