"""Policy updater for busybee online learning.

This module updates the busybee routing policy using collected feedback.
It converts RoutingOutcome objects into training examples and retrains
the underlying scikit-learn classifiers.

Example:
    from hive.feedback import FeedbackBuffer, RoutingOutcome, OutcomeType
    from hive.policy_updater import PolicyUpdater

    fb = FeedbackBuffer(capacity=100)
    updater = PolicyUpdater()

    # ... collect outcomes ...
    if fb.is_full():
        batch = fb.get_batch()
        success = updater.update(stack.busybee, batch)
        if success:
            print(f"Policy updated with {len(batch)} samples")
"""

from __future__ import annotations

import logging
from typing import Any

from hive.feedback import OutcomeType

_log = logging.getLogger("hive.policy_updater")


class PolicyUpdater:
    """Update busybee routing policy using collected feedback.
    Converts :class:`RoutingOutcome` objects into training examples
    and retrains the underlying scikit-learn classifiers.

    Usage::

        updater = PolicyUpdater()
        success = updater.update(policy, outcomes)
    """

    def update(
        self,
        policy: Any,
        outcomes: list[Any],
    ) -> bool:
        """Retrain ``policy`` using ``outcomes``.

        Returns ``True`` if the update succeeded, ``False`` otherwise.
        """
        if not outcomes:
            _log.warning("No outcomes to update policy with")
            return False

        try:
            training_samples = [
                self._convert_to_training_format(o) for o in outcomes
            ]
            policy.train(training_samples)
            return True
        except Exception as e:
            _log.warning("Policy update failed: %s", e)
            return False

    def _convert_to_training_format(
        self,
        outcome: Any,
    ) -> dict[str, Any]:
        """Convert a RoutingOutcome to the format expected by busybee-cpu."""
        return {
            "state": outcome.state,
            "action": outcome.routed_action,
            "reward": 1.0 if outcome.outcome_type == OutcomeType.CORRECT else 0.0,
        }
