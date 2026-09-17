"""Username deduplication for the mailer."""


def dedup(usernames: list[str]) -> list[str]:
    """Drop duplicate usernames, preserving order and first-seen spelling."""
    seen = set()
    out = []
    for name in usernames:
        if name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out
