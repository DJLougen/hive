from paginator import paginate

ITEMS = list(range(25))


def test_first_page():
    assert paginate(ITEMS, 1, 10) == list(range(10))


def test_second_page():
    assert paginate(ITEMS, 2, 10) == list(range(10, 20))


def test_last_partial_page():
    assert paginate(ITEMS, 3, 10) == list(range(20, 25))


def test_out_of_range_page_is_empty():
    assert paginate(ITEMS, 4, 10) == []


def test_single_item_pages():
    assert paginate(ITEMS, 1, 1) == [0]
    assert paginate(ITEMS, 25, 1) == [24]
    assert paginate(ITEMS, 26, 1) == []


def test_invalid_inputs():
    assert paginate(ITEMS, 0, 10) == []
    assert paginate(ITEMS, 1, 0) == []
