from csvlite import parse_line


def test_simple_fields():
    assert parse_line("a,b,c") == ["a", "b", "c"]


def test_quoted_comma():
    assert parse_line('"Smith, John",30,NYC') == ["Smith, John", "30", "NYC"]


def test_escaped_quote():
    assert parse_line('"say ""hi""",x') == ['say "hi"', "x"]


def test_empty_fields():
    assert parse_line("a,,c") == ["a", "", "c"]


def test_trailing_newline():
    assert parse_line("a,b\n") == ["a", "b"]
