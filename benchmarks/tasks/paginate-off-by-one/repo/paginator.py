"""Tiny pagination helper used by the API layer."""


def paginate(items, page: int, per_page: int):
    """Return the slice of `items` for 1-indexed `page`.

    Pages are 1-indexed: page 1 is the first `per_page` items.
    Out-of-range pages return an empty list.
    """
    if page < 1 or per_page < 1:
        return []
    start = page * per_page
    end = start + per_page
    return list(items[start:end])
