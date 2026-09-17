from tokens import is_expired


def test_before_expiry_valid():
    assert is_expired(exp=100.0, now=99.9) is False


def test_at_expiry_is_expired():
    assert is_expired(exp=100.0, now=100.0) is True


def test_after_expiry_is_expired():
    assert is_expired(exp=100.0, now=100.1) is True


def test_far_future_valid():
    assert is_expired(exp=9999.0, now=1.0) is False
