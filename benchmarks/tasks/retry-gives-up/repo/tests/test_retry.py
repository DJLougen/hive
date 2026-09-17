import pytest

from retry import retry


def test_succeeds_first_try():
    calls = []

    @retry(times=3, on=(ValueError,))
    def fn():
        calls.append(1)
        return "ok"

    assert fn() == "ok"
    assert len(calls) == 1


def test_succeeds_on_third_attempt():
    calls = []

    @retry(times=3, on=(ValueError,))
    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise ValueError("flaky")
        return "ok"

    assert fn() == "ok"
    assert len(calls) == 3


def test_gives_up_after_times():
    calls = []

    @retry(times=2, on=(ValueError,))
    def fn():
        calls.append(1)
        raise ValueError("always")

    with pytest.raises(ValueError):
        fn()
    assert len(calls) == 2


def test_non_matching_exception_not_retried():
    calls = []

    @retry(times=5, on=(ValueError,))
    def fn():
        calls.append(1)
        raise KeyError("different")

    with pytest.raises(KeyError):
        fn()
    assert len(calls) == 1
