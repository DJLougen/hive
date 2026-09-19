"""The shipped trained policy must be usable, and a stale one must fail closed.

A `cpu_router.joblib` fitted against an older `featurize()` does not degrade
gracefully: sklearn raises on the width mismatch at the first `predict()`, so
every routed decision crashes the episode and the arm records 0 resolved. The
committed artifact was exactly that (19 features vs 47) until it was retrained.

These tests pin the invariant, not the policy's accuracy.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import pytest

# The artifact is intentionally unsigned (CI has no private key), so allow the
# documented dev path before importing anything that loads it.
os.environ.setdefault("HIVE_ALLOW_UNSIGNED_MODEL", "1")

from hive.cpu_policy import TOOLS, CPURouterPolicy, featurize

ROOT = Path(__file__).resolve().parent.parent
POLICY = ROOT / "benchmarks" / "cpu_router.joblib"

SAMPLE_STATE = {
    "goal": "fix the bug",
    "listed": True,
    "tests_run": 1,
    "tests_passed": False,
    "writes": 0,
    "files_read": ["tests/test_thing.py"],
    "suggested_read": "pkg/thing.py",
    "step": 3,
    "last_tool": "run_tests",
}


def _load():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # sklearn version-skew warning
        return CPURouterPolicy.load(POLICY)


def test_committed_policy_declares_the_live_feature_width() -> None:
    assert POLICY.is_file(), "benchmarks/cpu_router.joblib is missing"
    policy = _load()
    assert policy.clf.n_features_in_ == len(featurize(SAMPLE_STATE))


def test_committed_policy_predicts_without_raising() -> None:
    """The exact failure that silently zeroed an arm: predict() crashing."""
    policy = _load()
    decision = policy.predict(dict(SAMPLE_STATE))
    assert decision["tool"] in (*TOOLS, "escalate")


def test_stale_policy_is_refused_at_load(tmp_path) -> None:
    """A width-mismatched artifact must raise at load, not at first predict."""
    import joblib

    policy = _load()
    blob = {
        "threshold": policy.threshold,
        "tools": tuple(policy.tools),
        "algorithm": policy.algorithm,
        "clf": policy.clf,
        "classes": list(policy.classes_),
        "metrics": dict(policy.train_metrics),
    }
    # Truncate the feature width to simulate a policy trained on old featurize().
    import numpy as np

    blob["clf"] = _Truncated(policy.clf, n_features=policy.clf.n_features_in_ - 1)
    _ = np  # keep import meaningful
    bad = tmp_path / "stale.joblib"
    joblib.dump(blob, bad)

    with pytest.raises(ValueError, match="stale CPU policy"):
        _load_from(bad)


class _Truncated:
    """Stand-in exposing only ``n_features_in_`` and the classes."""

    def __init__(self, inner, *, n_features: int) -> None:
        self._inner = inner
        self.n_features_in_ = n_features

    def predict_proba(self, _x):  # pragma: no cover - load refuses first
        raise AssertionError("should never be reached")


def _load_from(path):
    import warnings as _w

    with _w.catch_warnings():
        _w.simplefilter("ignore")
        return CPURouterPolicy.load(path)
