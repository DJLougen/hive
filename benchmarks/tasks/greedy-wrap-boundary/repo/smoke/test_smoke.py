"""Smoke tests: the public API happy path. Not the grading criterion."""

from __future__ import annotations

import pytest

from textwrapx import wrap


def test_short_text_stays_on_one_line():
    assert wrap("a b c", 20) == ["a b c"]


def test_text_breaks_between_words():
    assert wrap("alpha beta gamma", 11) == ["alpha beta", "gamma"]


def test_a_long_word_takes_its_own_line():
    assert wrap("ok supercalifragilistic ok", 5) == ["ok", "supercalifragilistic", "ok"]


def test_blank_input_yields_no_lines():
    assert wrap("   ", 10) == []


def test_width_must_be_positive():
    with pytest.raises(ValueError):
        wrap("a", 0)


# --- Spec coverage: the issue's stated rule, not just the happy path.
# These fail on the shipped repo; the held-out oracle checks the edge cases.


def test_a_line_never_exceeds_the_width():
    for line in wrap("aa bb cc dd ee", 5):
        assert len(line) <= 5, line


def test_a_line_exactly_filling_the_width_is_kept_together():
    # "aaaa bbbb" is exactly 9 characters, so it fits on one line.
    assert wrap("aaaa bbbb", 9) == ["aaaa bbbb"]


def test_a_line_never_reaches_width_plus_one():
    # "aa bb" is 5 characters: at width 4 it must break, since one more
    # character would not fit. (This is the off-by-one the issue describes.)
    assert wrap("aa bb cc", 4) == ["aa", "bb", "cc"]
