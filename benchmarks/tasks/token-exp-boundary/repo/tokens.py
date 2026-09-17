"""Token expiry checks."""


def is_expired(exp: float, now: float) -> bool:
    """True when the token is no longer valid.

    A token is valid strictly before `exp`; at `exp` it is expired.
    """
    return now > exp
