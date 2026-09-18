"""Smoke tests: the public API happy path. Not the grading criterion."""

from __future__ import annotations

from streamcsv import iter_chunks, iter_rows, parse_row, read_records

DATA = 'name,note\nada,"hello"\nbob,plain\n'


def test_plain_records_round_trip():
    assert list(iter_rows([DATA])) == ['name,note', 'ada,"hello"', "bob,plain"]


def test_quoted_field_containing_a_newline_is_one_record():
    assert list(iter_rows(['a,"one\ntwo",c\n'])) == ['a,"one\ntwo",c']


def test_fields_are_split_on_unquoted_delimiters():
    assert parse_row('ada,"hello, world",3') == ["ada", "hello, world", "3"]


def test_an_escaped_quote_inside_a_quoted_field_is_literal():
    assert parse_row('a,"say ""hi"""') == ["a", 'say "hi"']


def test_records_are_available_with_a_roomy_chunk_size():
    assert read_records(DATA, chunk_size=1024) == [
        {"name": "ada", "note": "hello"},
        {"name": "bob", "note": "plain"},
    ]


def test_chunks_cover_the_text_once():
    assert "".join(iter_chunks("abcdef", 2)) == "abcdef"


# --- Spec coverage: the issue's stated requirements, not just the happy path.
# These fail on the shipped repo; the held-out oracle checks the edge cases.


def test_records_do_not_depend_on_the_chunk_size():
    text = 'a,"x\ny",b\nc,d,e\n'
    wide = list(iter_rows(iter_chunks(text, 4096)))
    for size in (1, 2, 3, 5):
        assert list(iter_rows(iter_chunks(text, size))) == wide, size


def test_a_quoted_field_straddling_a_boundary_is_one_record():
    chunks = ['a,"one', '\ntwo",b\n']
    assert list(iter_rows(chunks)) == ['a,"one\ntwo",b']


def test_an_unterminated_quoted_field_is_malformed():
    import pytest

    with pytest.raises(ValueError):
        list(iter_rows(['a,"never closed\n']))
