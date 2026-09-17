"""Held-out grading tests: records must not depend on chunk boundaries."""

from __future__ import annotations

from streamcsv import iter_chunks, iter_rows, read_records

DATA = (
    'name,note,score\n'
    'ada,"first line\nsecond line",10\n'
    'bob,"he said ""hello""",20\n'
    'cid,plain,30\n'
    'dee,"trailing \n",40\n'
)
EXPECTED = [
    "name,note,score",
    'ada,"first line\nsecond line",10',
    'bob,"he said ""hello""",20',
    "cid,plain,30",
    'dee,"trailing \n",40',
]


def test_a_roomy_chunk_size_reads_the_records():
    assert list(iter_rows([DATA])) == EXPECTED


def test_every_chunk_size_gives_the_same_records():
    for size in range(1, len(DATA) + 1):
        assert list(iter_rows(iter_chunks(DATA, size))) == EXPECTED, size


def test_a_boundary_inside_a_quoted_newline_does_not_split_the_record():
    text = 'a,"x\ny",b\n'
    for size in range(1, len(text) + 1):
        assert list(iter_rows(iter_chunks(text, size))) == ['a,"x\ny",b'], size


def test_an_escaped_quote_split_across_a_boundary_is_one_quote():
    chunks = ['a,"say "', '"hi"""', ",b\n"]
    assert list(iter_rows(chunks)) == ['a,"say ""hi""",b']


def test_records_are_identical_for_any_chunk_size_through_the_reader():
    wide = read_records(DATA, chunk_size=4096)
    assert wide == [
        {"name": "ada", "note": "first line\nsecond line", "score": "10"},
        {"name": "bob", "note": 'he said "hello"', "score": "20"},
        {"name": "cid", "note": "plain", "score": "30"},
        {"name": "dee", "note": "trailing \n", "score": "40"},
    ]
    for size in (1, 2, 3, 5, 7, 13):
        assert read_records(DATA, chunk_size=size) == wide, size


def test_a_missing_final_newline_still_yields_the_last_record():
    assert list(iter_rows(iter_chunks('a,"x\ny"', 3))) == ['a,"x\ny"']
    assert list(iter_rows(["a,b\nc,d"])) == ["a,b", "c,d"]


def test_empty_and_blank_input_is_ignored():
    assert list(iter_rows(iter_chunks("", 4))) == []
    assert read_records("name,note\n") == []
    assert list(iter_rows(["\n\n"])) == ["", ""]


def test_a_chunk_that_ends_inside_a_quoted_field_keeps_the_quote_open():
    text = 'id,text\n1,"open\nand more"\n2,done\n'
    rows = read_records(text, chunk_size=7)
    assert [r["id"] for r in rows] == ["1", "2"]
    assert rows[0]["text"] == "open\nand more"
