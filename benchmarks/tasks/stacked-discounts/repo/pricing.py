"""Pricing helpers."""


def apply_discounts(price: float, percents: list[float]) -> float:
    """Apply each percent discount sequentially to the running total."""
    total = price
    for pct in percents:
        total -= price * (pct / 100.0)
        if total < 0:
            total = 0.0
    return round(total, 2)
