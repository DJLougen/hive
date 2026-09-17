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
