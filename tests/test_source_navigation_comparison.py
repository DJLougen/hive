"""The development comparison must not promote empty or incomplete evidence."""
from __future__ import annotations

import pytest

from hive.llm import ModelResponse
from scripts.compare_source_navigation import BudgetStop, MeteredBackend, summarize


def _row(condition, *, calls=3, resolved=True, task="sample", pass_idx=0):
    return {"condition": condition, "task_id": task, "pass_idx": pass_idx,
            "resolved": resolved, "llm_calls": calls, "prompt_tokens": 100,
            "completion_tokens": 20, "wall_clock_s": 1.0,
            "usage_recorded": True, "crashed": False}


def test_empty_evidence_cannot_pass():
    assert not summarize([], 0)["observed_keep_gate"]
    assert not summarize([], 1)["complete"]


def test_complete_matched_lower_calls_can_pass_observed_gate():
    rows = [_row("legacy"), _row("reexports", calls=2)]
    assert summarize(rows, 1)["observed_keep_gate"]


@pytest.mark.parametrize("defect", ["missing", "duplicate", "quality", "usage", "crash", "tasks", "passes"])
def test_comparison_rejects_invalid_or_regressed_evidence(defect):
    rows = [_row("legacy"), _row("reexports", calls=2)]
    if defect == "missing":
        rows.pop()
    elif defect == "duplicate":
        rows *= 2
    elif defect == "quality":
        rows[1]["resolved"] = False
    elif defect == "usage":
        rows[1]["usage_recorded"] = False
    elif defect == "crash":
        rows[1]["crashed"] = True
    elif defect == "passes":
        rows[1]["pass_idx"] = 1
    else:
        rows[1]["task_id"] = "different"
    assert not summarize(rows, 1)["observed_keep_gate"]


class _Backend:
    calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        return ModelResponse(text="finish", prompt_tokens=10, completion_tokens=2,
                             duration_s=0.1, model="fake")


def test_request_limit_stops_before_another_backend_call(tmp_path):
    backend = _Backend()
    plan = {"limits": {"max_llm_requests": 1, "estimated_usd_limit": 1,
                       "wall_seconds": 60}}
    meter = MeteredBackend(backend, plan, tmp_path / "ledger.jsonl")
    meter.chat([])
    with pytest.raises(BudgetStop):
        meter.chat([])
    assert backend.calls == 1
    assert meter.prompt_tokens == 10


def test_missing_usage_aborts_instead_of_zero_cost(tmp_path):
    class MissingUsage:
        def chat(self, messages, **kwargs):
            return ModelResponse(text="finish", prompt_tokens=0, completion_tokens=0,
                                 duration_s=0.1, model="fake")

    plan = {"limits": {"max_llm_requests": 1, "estimated_usd_limit": 1,
                       "wall_seconds": 60}}
    meter = MeteredBackend(MissingUsage(), plan, tmp_path / "ledger.jsonl")
    with pytest.raises(BudgetStop, match="usage"):
        meter.chat([])
