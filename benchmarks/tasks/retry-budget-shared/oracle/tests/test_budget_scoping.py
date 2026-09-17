"""Held-out grading tests for the shared-retry-budget requirement."""

from __future__ import annotations

import pytest

from resilience import Client, RetryBudgetExhausted, budget_for, reset_budgets


@pytest.fixture(autouse=True)
def _clean_budgets():
    reset_budgets()
    yield
    reset_budgets()


def _always_times_out():
    raise TimeoutError("downstream is down")


class _Counter:
    def __init__(self, exc):
        self.calls = 0
        self.exc = exc

    def __call__(self):
        self.calls += 1
        raise self.exc


class _Sleeper:
    """Records every wait so the delay policy can be asserted on."""

    def __init__(self):
        self.waits: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


def test_same_name_returns_the_same_budget_object():
    assert budget_for("shared", 3) is budget_for("shared", 3)


def test_first_limit_wins_for_a_name():
    first = budget_for("capped", 2)
    again = budget_for("capped", 9)
    assert again is first
    assert again.limit == 2


def test_different_names_are_different_budgets():
    assert budget_for("a", 1) is not budget_for("b", 1)


def test_one_client_charges_every_attempt_to_the_budget():
    client = Client("solo", limit=3, sleeper=lambda _: None)
    with pytest.raises(RetryBudgetExhausted) as err:
        client.call(_always_times_out)
    assert budget_for("solo").spent == 3
    assert budget_for("solo").remaining == 0
    assert isinstance(err.value.__cause__, TimeoutError)


def test_two_clients_share_one_cap():
    first = Client("svc", limit=2, sleeper=lambda _: None)
    second = Client("svc", limit=2, sleeper=lambda _: None)
    with pytest.raises(RetryBudgetExhausted):
        first.call(_always_times_out)
    unmade = _Counter(TimeoutError("never reached"))
    with pytest.raises(RetryBudgetExhausted):
        second.call(unmade)
    # The cap is a property of the name: 2 attempts between them, not 2 each.
    assert unmade.calls == 0
    assert budget_for("svc").spent == 2


def test_the_wait_sits_between_attempts_never_after_the_last_one():
    sleeper = _Sleeper()
    client = Client("paced", limit=3, sleeper=sleeper)
    with pytest.raises(RetryBudgetExhausted):
        client.call(_always_times_out)
    # 3 attempts, 2 waits: nothing is slept after the attempt that exhausted
    # the budget, and never before the first attempt either.
    assert sleeper.waits == [0.05, 0.1]


def test_a_failed_call_is_never_slept_into_a_success():
    # A non-retryable failure must not consume a wait either.
    sleeper = _Sleeper()
    client = Client("unretryable", limit=3, sleeper=sleeper)

    def broken():
        raise ValueError("bad input")

    with pytest.raises(ValueError):
        client.call(broken)
    assert sleeper.waits == []
    assert budget_for("unretryable").spent == 1


def test_remaining_reflects_what_another_caller_spent():
    heavy = Client("metered", limit=3, sleeper=lambda _: None)
    with pytest.raises(RetryBudgetExhausted):
        heavy.call(_always_times_out)
    assert budget_for("metered").spent == 3
    assert budget_for("metered").remaining == 0
    # A second name is untouched by the first, and vice versa.
    assert budget_for("other").remaining == 3
