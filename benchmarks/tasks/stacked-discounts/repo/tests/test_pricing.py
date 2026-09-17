from pricing import apply_discounts


def test_single_discount():
    assert apply_discounts(100.0, [10]) == 90.0


def test_stacked_discounts_apply_sequentially():
    assert apply_discounts(100.0, [10, 20]) == 72.0


def test_no_discounts():
    assert apply_discounts(59.99, []) == 59.99


def test_full_discount_floors_at_zero():
    assert apply_discounts(50.0, [100]) == 0.0
    assert apply_discounts(50.0, [80, 80]) == 2.0


def test_fractional_price():
    assert apply_discounts(19.99, [25]) == 14.99
