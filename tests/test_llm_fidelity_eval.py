"""Tests for the LLM fidelity eval's graders and prompts (no model needed)."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from fidelity_corpus import build_corpus  # noqa: E402
from llm_fidelity_eval import grade_fact, question_for  # noqa: E402


def test_graders_accept_verbatim_ground_truth():
    """An answer that quotes the original content must always grade correct."""
    samples = build_corpus(seed=7, per_category=4, include_real=True, scale=0.2)
    for s in samples:
        for f in s.facts:
            assert grade_fact(f.name, f.needle, s.content), (
                f"{s.id}: grader for {f.name!r} rejected the original content"
            )


def test_graders_reject_empty_answer():
    samples = build_corpus(seed=7, per_category=2, include_real=False, scale=0.2)
    for s in samples:
        for f in s.facts:
            assert not grade_fact(f.name, f.needle, "I don't know.")


def test_graders_tolerate_phrasing():
    assert grade_fact(
        "failure count summary", "3 failed, 5 passed", "3 tests failed and 5 passed."
    )
    assert grade_fact(
        "deepest stack frame",
        'File "src/auth/refresh.py", line 142, in refresh',
        "The error occurred in src/auth/refresh.py at line 142.",
    )
    assert grade_fact(
        "target function signature",
        "def parse_auth_007(payload, *, timeout=42):",
        "The function is `parse_auth_007` with timeout = 42.",
    )
    assert grade_fact("exit code", "exit 1", "The exit code was 1.")
    assert not grade_fact("exit code", "exit 1", "It printed 1000 lines of output.")
    assert grade_fact(
        "answer location",
        "src/db/handlers.py:733",
        "It is defined in src/db/handlers.py on line 733.",
    )


def test_every_sample_has_a_question():
    samples = build_corpus(seed=7, per_category=2, include_real=True, scale=0.2)
    for s in samples:
        q = question_for(s)
        assert q and "{" not in q
