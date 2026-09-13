"""Policy updater for busybee online learning.

Supports batch sklearn retraining (default) and LinUCB contextual bandit updates.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import numpy as np

from hive.feedback import OutcomeType

_log = logging.getLogger("hive.policy_updater")

_FEATURE_DIM = 32
_ALPHA = 1.0


def _state_features(state: dict[str, Any], dim: int = _FEATURE_DIM) -> np.ndarray:
    """Hash agent state into a fixed-length feature vector."""
    blob = json.dumps(state, sort_keys=True, default=str)
    vec = np.zeros(dim, dtype=np.float64)
    for i in range(dim):
        digest = hashlib.sha256(f"{blob}:{i}".encode()).digest()
        vec[i] = (int.from_bytes(digest[:4], "big") / 2**32) * 2.0 - 1.0
    return vec


class LinUCBPolicy:
    """Contextual bandit policy using LinUCB (pure numpy)."""

    def __init__(
        self,
        actions: list[str] | None = None,
        *,
        alpha: float = _ALPHA,
        feature_dim: int = _FEATURE_DIM,
    ) -> None:
        self.actions = actions or [
            "read_file",
            "run_tests",
            "apply_patch",
            "escalate",
        ]
        self.alpha = alpha
        self.feature_dim = feature_dim
        self._a_inv: dict[str, np.ndarray] = {}
        self._b: dict[str, np.ndarray] = {}
        for action in self.actions:
            self._init_action(action)

    def _init_action(self, action: str) -> None:
        self._a_inv[action] = np.eye(self.feature_dim, dtype=np.float64)
        self._b[action] = np.zeros(self.feature_dim, dtype=np.float64)

    def predict(self, state: dict[str, Any]) -> dict[str, Any]:
        x = _state_features(state, self.feature_dim)
        best_action = self.actions[0]
        best_score = float("-inf")
        for action in self.actions:
            a_inv = self._a_inv[action]
            theta = a_inv @ self._b[action]
            mean = float(x @ theta)
            conf = float(self.alpha * np.sqrt(max(0.0, x @ a_inv @ x)))
            score = mean + conf
            if score > best_score:
                best_score = score
                best_action = action
        confidence = 1.0 / (1.0 + np.exp(-best_score))
        return {
            "tool": best_action,
            "action": best_action,
            "args": {},
            "confidence": min(1.0, max(0.0, confidence)),
            "escalated": best_action == "escalate",
        }

    def train(self, examples: list[dict[str, Any]]) -> bool:
        """Apply LinUCB online updates from labelled examples."""
        for ex in examples:
            state = ex.get("state") or {}
            action = ex.get("action") or "escalate"
            reward = float(ex.get("reward", 0.0))
            if action not in self._a_inv:
                self._init_action(action)
            x = _state_features(state, self.feature_dim)
            a_inv = self._a_inv[action]
            x_col = x.reshape(-1, 1)
            denom = 1.0 + float(x @ a_inv @ x)
            a_inv = a_inv - (a_inv @ x_col @ x_col.T @ a_inv) / denom
            self._a_inv[action] = a_inv
            self._b[action] = self._b[action] + reward * x
        return True


class PolicyUpdater:
    """Update routing policy using collected feedback."""

    def __init__(self, *, use_linucb: bool = False) -> None:
        self.use_linucb = use_linucb

    def update(
        self,
        policy: Any,
        outcomes: list[Any],
    ) -> bool:
        if not outcomes:
            _log.warning("No outcomes to update policy with")
            return False

        training_samples = [self._convert_to_training_format(o) for o in outcomes]

        if isinstance(policy, LinUCBPolicy) or self.use_linucb:
            if not isinstance(policy, LinUCBPolicy):
                policy = LinUCBPolicy()
            try:
                return policy.train(training_samples)
            except Exception as e:
                _log.warning("LinUCB update failed: %s", e)
                return False

        try:
            policy.train(training_samples)
            return True
        except Exception as e:
            _log.warning("Policy update failed: %s", e)
            return False

    def _convert_to_training_format(
        self,
        outcome: Any,
    ) -> dict[str, Any]:
        reward = 1.0 if outcome.outcome_type == OutcomeType.CORRECT else 0.0
        if outcome.outcome_type == OutcomeType.ESCALATED_CORRECTLY:
            reward = 0.75
        elif outcome.outcome_type == OutcomeType.WRONG_ACTION:
            reward = 0.0
        action = outcome.actual_action or outcome.routed_action or "escalate"
        return {
            "state": outcome.state,
            "action": action,
            "reward": reward,
        }


__all__ = ["LinUCBPolicy", "PolicyUpdater"]
