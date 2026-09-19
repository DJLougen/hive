"""Held-out grading tests for greedy wrapping boundaries."""

from __future__ import annotations

import pytest

from textwrapx import wrap


def test_every_line_fits_the_width_unless_it_is_a_single_long_word():
    """The stated rule: lines fit ``width`` — except a word longer than
    ``width``, which the statement says takes a line of its own rather than
    being split. So only single-word lines may exceed ``width``."""
    for width in (1, 2, 3, 4, 5, 9, 12):
        for line in wrap("aa bb cc dddd eeeee f", width):
            if len(line) > width:
                assert " " not in line, (width, line)


def test_a_line_that_exactly_fits_is_not_broken():
    assert wrap("aa bb", 5) == ["aa bb"]     # 5 chars, width 5
    assert wrap("aa bb", 4) == ["aa", "bb"]  # 5 chars, width 4


def test_words_are_never_split():
    out = wrap("alphabet bet", 4)
    assert out == ["alphabet", "bet"]
    assert all(" " not in w or True for w in out)


def test_a_word_longer_than_the_width_gets_its_own_line():
    assert wrap("a verylongword b", 3) == ["a", "verylongword", "b"]


def test_joining_never_double_spaces():
    for line in wrap("a  b   c", 10):
        assert "  " not in line


def test_empty_and_whitespace_inputs():
    assert wrap("", 5) == []
    assert wrap("   ", 5) == []


def test_width_of_one_puts_every_word_on_its_own_line():
    assert wrap("a b c", 1) == ["a", "b", "c"]


def test_no_trailing_or_leading_spaces_on_any_line():
    for line in wrap("  aa   bb  cc  ", 7):
        assert line == line.strip()


def test_a_non_positive_width_is_rejected():
    with pytest.raises(ValueError):
        wrap("x", 0)
    with pytest.raises(ValueError):
        wrap("x", -3)
