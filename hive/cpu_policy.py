"""hive/cpu_policy.py — trainable CPU routing policy.

The CPU policy's job: eat every tool-call decision that does not require
reasoning. It is trained by *imitation*: run an agent loop with a real LLM
(or the rule-based bootstrap policy), log every ``(state -> action)`` turn,
fit a small classifier on the observable-state features, and the resulting
model predicts the same tool choices on the CPU.

Two pieces do the work:

* :func:`featurize` — turns the raw state dict an agent loop exposes
  (``listed``, ``tests_run``, ``tests_passed``, ``writes``, ``files_read``,
  ``suggested_read``, ``verify_pending``, ``recalled_fix``, ``last_tool`` …)
  into a fixed-width numeric vector.
* :class:`CPURouterPolicy` — a ``RandomForestClassifier`` over those
  features plus per-tool *arg resolvers* that fill arguments from state
  (read the file the traceback names, replay a recalled fix, grep for the
  failing symbol). Below a confidence floor, or when args cannot be
  resolved, the policy escalates — it never guesses.

``write_file`` is special: a CPU policy cannot *generate* patch content, so
it only routes a write when ``state["recalled_fix"]`` supplies content from
causal memory (a task already resolved once). Everything else it must
escalate — that is the honest boundary of "eat all the tool calls".

The class exposes ``predict(state) -> dict`` — the same protocol
``HiveStack(busybee_policy=...)`` expects — so it drops straight in.
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from hive.arg_resolvers import resolve_args

_log = logging.getLogger("hive.cpu_policy")

TOOLS = ("list_files", "read_file", "grep", "run_tests", "write_file", "finish")

# Canonical vocabulary for real-agent traces (scripts/extract_traces.py maps
# harness tool names into this set).
TRACE_TOOLS = ("list_files", "read_file", "grep", "run_tests", "write_file",
               "run_command", "web", "finish", "other")

# Last-tool one-hot ordering (index 0 reserved for "no prior tool").
_LAST_TOOLS = ("none", "list_files", "read_file", "grep", "run_tests",
               "write_file", "run_command", "web", "finish", "other",
               "escalate", "invalid")


def featurize(state: dict[str, Any]) -> list[float]:
    """Observable state -> fixed-width feature vector.

    Only fields an agent loop can legitimately expose are used — no hints,
    no labels, nothing the LLM had to produce. Two state vocabularies share
    the vector: bug-suite workflow fields (``listed`` …) and deidentified
    trace fields (``hist``, ``n_err``, ``last_ok`` …); whichever the caller
    doesn't set reads as zero.

    Trace records also store ``n_args``/``has_path``/``has_pattern``/
    ``has_cmd`` — but those describe the *current* call's arguments, which
    only exist after the tool is chosen. Feeding them to the classifier is
    label leakage, so they are deliberately excluded here.
    """
    tests_passed = state.get("tests_passed")
    tp = -1.0 if tests_passed is None else (1.0 if tests_passed else 0.0)
    last_tool = str(state.get("last_tool") or "none")
    prev2 = str(state.get("prev2_tool") or "none")
    one_hot = [1.0 if last_tool == t else 0.0 for t in _LAST_TOOLS]
    prev2_hot = [1.0 if prev2 == t else 0.0 for t in _LAST_TOOLS]
    hist = state.get("hist") or {}
    return [
        float(bool(state.get("listed"))),
        float(state.get("tests_run") or 0),
        tp,
        float(state.get("writes") or 0),
        float(len(state.get("files_read") or [])),
        float(bool(state.get("suggested_read"))),
        float(bool(state.get("verify_pending"))),
        float(bool(state.get("recalled_fix"))),
        float(bool(state.get("memory_hit"))),
        float(bool(state.get("fail_signature"))),
        float(state.get("step") or state.get("t") or state.get("turn") or 0),
        # trace-derived fields (0 on bug-suite states)
        float(bool(state.get("last_ok", True))),
        float(state.get("n_err") or 0),
        float(len(hist)),
        *(float(hist.get(t, 0)) for t in TRACE_TOOLS),
        *one_hot,
        *prev2_hot,
    ]


def _make_classifier(name: str) -> Any:
    """Instantiate a sklearn classifier by short name."""
    if name == "rf":
        from sklearn.ensemble import RandomForestClassifier
        return RandomForestClassifier(n_estimators=60, max_depth=6,
                                      random_state=42, class_weight="balanced")
    if name == "rf-deep":
        from sklearn.ensemble import RandomForestClassifier
        return RandomForestClassifier(n_estimators=300, max_depth=None,
                                      random_state=42, class_weight="balanced",
                                      n_jobs=-1)
    if name == "extratrees":
        from sklearn.ensemble import ExtraTreesClassifier
        return ExtraTreesClassifier(n_estimators=300, random_state=42,
                                    class_weight="balanced", n_jobs=-1)
    if name == "hgb":
        from sklearn.ensemble import HistGradientBoostingClassifier
        return HistGradientBoostingClassifier(random_state=42)
    if name == "logreg":
        from sklearn.linear_model import LogisticRegression
        return LogisticRegression(max_iter=2000, class_weight="balanced")
    if name == "mlp":
        from sklearn.neural_network import MLPClassifier
        return MLPClassifier(hidden_layer_sizes=(128, 64), max_iter=400,
                             early_stopping=True, n_iter_no_change=10,
                             random_state=42)
    raise ValueError(f"unknown algorithm {name!r}")


class _MarkovModel:
    """k-th order Markov chain over (tool_{t-2}, tool_{t-1}) -> tool_t.

    Probabilities come from empirical transition counts with hierarchical
    backoff: trigram, then bigram on last_tool, then unigram. Pure CPU,
    zero featurization — a strong baseline when decisions are mostly
    sequence-driven.
    """

    def __init__(self, order: int = 2, alpha: float = 0.5) -> None:
        self.order = order
        self.alpha = alpha
        self.counts: dict[int, dict[tuple[str, ...], Counter]] = {
            k: {} for k in range(1, order + 1)}
        self.unigram: Counter = Counter()
        self.classes_: list[str] = []

    @staticmethod
    def _hist(state: dict[str, Any]) -> tuple[str, str]:
        return (str(state.get("prev2_tool") or "none"),
                str(state.get("last_tool") or "none"))

    def fit_rows(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            hist = self._hist(row.get("state") or {})
            for k in range(1, self.order + 1):
                key = hist[-k:]
                self.counts[k].setdefault(key, Counter())[row["tool"]] += 1
            self.unigram[row["tool"]] += 1
        self.classes_ = sorted(self.unigram)

    def predict_proba_state(self, state: dict[str, Any]) -> list[float]:
        hist = self._hist(state)
        # Back off: longest context, then shorter suffixes, then unigram.
        dist = self.unigram
        for k in range(self.order, 0, -1):
            c = self.counts[k].get(hist[-k:])
            if c:
                dist = c
                break
        total = sum(dist.values()) + self.alpha * len(self.classes_)
        return [(dist.get(t, 0) + self.alpha) / total for t in self.classes_]

    # sklearn-ish surface so save()/predict paths stay uniform
    def predict_proba(self, xs: list) -> Any:  # pragma: no cover - unused
        raise NotImplementedError("markov models use predict_proba_state")


class CPURouterPolicy:
    """Trained CPU router. ``predict()`` mirrors the busyBee protocol.

    Parameters
    ----------
    threshold:
        Min predicted-class probability before the policy escalates to the
        LLM instead of guessing.
    tools:
        Tool names the policy may route. Defaults to the bug-suite set;
        pass ``TRACE_TOOLS`` for real-agent trace workloads.
    algorithm:
        ``rf`` (default), ``rf-deep``, ``extratrees``, ``hgb``, ``logreg``,
        ``mlp``, or ``markov1``/``markov2`` — an n-gram transition model that
        ignores ``featurize`` and keys purely on the preceding tool(s).
    """

    ALGORITHMS = ("rf", "rf-deep", "extratrees", "hgb", "logreg", "mlp",
                  "markov1", "markov2")

    def __init__(self, *, threshold: float = 0.55,
                 tools: tuple[str, ...] = TOOLS,
                 algorithm: str = "rf") -> None:
        if algorithm not in self.ALGORITHMS:
            raise ValueError(f"algorithm must be one of {self.ALGORITHMS}")
        self.threshold = threshold
        self.tools = tools
        self.algorithm = algorithm
        self.clf: Any | None = None
        self.classes_: list[str] = []
        self.stats = {"routed": 0, "escalated": 0}
        self.train_metrics: dict[str, Any] = {}
        self._last_route: tuple[str, tuple[tuple[str, str], ...]] | None = None

    # -- inference ---------------------------------------------------------

    def predict(self, state: dict[str, Any]) -> dict[str, Any]:
        # Memory replay: a recalled fix is written mechanically — this is
        # what lets a repeated task run with zero LLM calls.
        if state.get("recalled_fix") and not state.get("writes"):
            self.stats["routed"] += 1
            fix = state["recalled_fix"]
            return {"tool": "write_file", "confidence": 0.99, "escalated": False,
                    "args": {"path": fix["path"], "content": fix["content"]}}

        if self.clf is None:
            self.stats["escalated"] += 1
            return {"tool": "escalate", "confidence": 0.0, "escalated": True,
                    "args": {"reason": "no model fitted"}}

        tool, conf = self.classify(state)
        if tool is None:
            self.stats["escalated"] += 1
            return {"tool": "escalate", "confidence": conf, "escalated": True,
                    "args": {"reason": "below confidence floor"}}

        # Patch synthesis is not CPU-decidable without a recalled fix.
        if tool == "write_file" and not state.get("recalled_fix"):
            self.stats["escalated"] += 1
            return {"tool": "escalate", "confidence": conf, "escalated": True,
                    "args": {"reason": "patch synthesis needs the LLM"}}

        args = resolve_args(tool, state)
        if args is None:
            self.stats["escalated"] += 1
            return {"tool": "escalate", "confidence": conf, "escalated": True,
                    "args": {"reason": f"cannot resolve args for {tool}"}}

        # Loop guard: emitting the identical route twice in a row means the
        # state is not advancing — hand the turn to the LLM instead.
        route_key = (tool, tuple(sorted((str(k), str(v)) for k, v in args.items())))
        if route_key == self._last_route:
            self.stats["escalated"] += 1
            return {"tool": "escalate", "confidence": conf, "escalated": True,
                    "args": {"reason": f"loop guard: {tool} repeated identically"}}
        self._last_route = route_key

        self.stats["routed"] += 1
        return {"tool": tool, "args": args, "confidence": conf, "escalated": False}

    def classify(self, state: dict[str, Any]) -> tuple[str | None, float]:
        """Raw tool prediction: ``(tool, confidence)`` or ``(None, conf)``
        when confidence is below the floor. Arg resolution and the
        write-without-recall refusal live in :meth:`predict` — this is the
        decision-quality probe used by the trace-coverage eval, where
        deidentified data carries no argument values to resolve."""
        if self.clf is None:
            return None, 0.0
        if isinstance(self.clf, _MarkovModel):
            probs = np.asarray(self.clf.predict_proba_state(state))
        else:
            probs = self.clf.predict_proba([featurize(state)])[0]
        idx = int(np.argmax(probs))
        tool, conf = str(self.classes_[idx]), float(probs[idx])
        if conf < self.threshold:
            return None, conf
        return tool, conf

    # -- training ----------------------------------------------------------

    def fit(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Fit on logged ``{state, tool}`` trajectory rows.

        Returns training metrics (class counts, feature width). Rows whose
        action failed or was ``invalid`` are skipped — we only imitate
        decisions that executed.
        """
        rows = [r for r in rows
                if r.get("tool") in self.tools and r.get("ok", True)]
        if not rows:
            raise ValueError("no usable trajectory rows")

        if self.algorithm.startswith("markov"):
            clf = _MarkovModel(order=int(self.algorithm[-1]))
            clf.fit_rows(rows)
            self.clf = clf
            self.classes_ = list(clf.classes_)
        else:
            xs = [featurize(r["state"]) for r in rows]
            ys = [r["tool"] for r in rows]
            self.clf = _make_classifier(self.algorithm)
            self.clf.fit(xs, ys)
            self.classes_ = [str(c) for c in self.clf.classes_]
        self.train_metrics = {
            "algorithm": self.algorithm,
            "samples": len(rows),
            "class_counts": dict(Counter(r["tool"] for r in rows)),
        }
        _log.info("CPURouterPolicy fitted: %s", self.train_metrics)
        return self.train_metrics

    def fit_file(self, path: str | Path) -> dict[str, Any]:
        rows = [json.loads(ln) for ln in Path(path).read_text().splitlines() if ln.strip()]
        return self.fit(rows)

    # -- persistence ---------------------------------------------------------

    def save(self, path: str | Path) -> None:
        import joblib

        joblib.dump({"threshold": self.threshold, "tools": self.tools,
                     "algorithm": self.algorithm,
                     "clf": self.clf, "classes": self.classes_,
                     "metrics": self.train_metrics}, path)

    @classmethod
    def load(cls, path: str | Path, *, trust_store: str | Path | None = None) -> CPURouterPolicy:
        """Load a saved policy, enforcing signature verification.

        A ``<name>.joblib.sig`` sidecar (written by
        :meth:`hive.model_registry.ModelRegistry.sign_model`) is verified
        cryptographically — an invalid signature always refuses the load.
        When no sidecar exists the model is refused unless
        ``HIVE_ALLOW_UNSIGNED_MODEL=1`` is set, because ``joblib.load``
        unpickles arbitrary objects and an unsigned model is a code-
        execution vector.

        ``trust_store`` (or the ``HIVE_MODEL_TRUST_STORE`` env var) names a
        JSON file of trusted signer fingerprints; without one the embedded
        signer is trusted on first use (integrity, not authenticity).
        """
        import joblib

        from hive.model_registry import ModelRegistry, UnsignedModelError

        path = Path(path)
        sig_file = path.with_suffix(".joblib.sig")
        store = trust_store or os.environ.get("HIVE_MODEL_TRUST_STORE")
        registry = ModelRegistry(strict=True, trust_store=str(store) if store else None)
        if sig_file.exists():
            if not store:
                # No trust store configured: pin the declared signer so the
                # cryptographic check still runs (TOFU — integrity only).
                try:
                    declared = json.loads(sig_file.read_text("utf-8")).get("fingerprint")
                except (json.JSONDecodeError, OSError):
                    declared = None
                if declared:
                    registry.trust_signer(declared)
            blob = registry.load(path)
        elif os.environ.get("HIVE_ALLOW_UNSIGNED_MODEL") == "1":
            _log.warning("Loading unsigned model %s (HIVE_ALLOW_UNSIGNED_MODEL=1)", path)
            blob = joblib.load(path)
        else:
            raise UnsignedModelError(
                f"Refusing to load unsigned model {path}: joblib unpickles "
                "arbitrary objects, so an unsigned model is a code-execution "
                "vector. Sign it with ModelRegistry.sign_model() to produce a "
                ".joblib.sig sidecar, or set HIVE_ALLOW_UNSIGNED_MODEL=1 to "
                "load unsigned models you trust."
            )
        policy = cls(threshold=blob.get("threshold", 0.55),
                     tools=tuple(blob.get("tools") or TOOLS),
                     algorithm=blob.get("algorithm", "rf"))
        policy.clf = blob["clf"]
        policy.classes_ = list(blob.get("classes", []))
        policy.train_metrics = blob.get("metrics", {})

        # Fail closed on a stale artifact. A policy fitted against an older
        # featurize() is not merely inaccurate — sklearn raises on the width
        # mismatch at the first predict(), so every routed decision crashes
        # the episode and the arm silently records 0 resolved. Catch it here
        # instead: a saved policy must declare the feature width it was
        # trained on and it must match the live featurize().
        expected = policy.clf.n_features_in_ if hasattr(policy.clf, "n_features_in_") else None
        if expected is not None:
            live = len(featurize({}))
            if expected != live:
                raise ValueError(
                    f"stale CPU policy {path}: artifact was fitted on {expected} "
                    f"features but featurize() now produces {live}. Retrain with "
                    "scripts/train_cpu_policy.py (see scripts/rebuild_trajectories.py "
                    "to rebuild a training set from published artifacts)."
                )
        return policy


def trajectory_row(state: dict[str, Any], tool: str, ok: bool) -> dict[str, Any]:
    """One training row for --log output. ``ok=False`` rows are dropped at fit."""
    keep = (
        "listed", "tests_run", "tests_passed", "writes", "files_read",
        "suggested_read", "verify_pending", "recalled_fix", "memory_hit",
        "fail_signature", "step", "last_tool",
    )
    snap = {}
    for k in keep:
        v = state.get(k)
        snap[k] = list(v) if isinstance(v, list) else (dict(v) if isinstance(v, dict) else v)
    return {"state": snap, "tool": tool, "ok": ok}
