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

_log = logging.getLogger("hive.policy_updater")

from hive.feedback import FeedbackBuffer, RoutingOutcome, OutcomeType


class PolicyUpdater:
    """Update busybee routing policy using collected feedback.
    
    Converts RoutingOutcome objects into training format expected by
    busybee_cpu.CpuActionPolicy.train(), then retrains the policy.
    
    Only uses CORRECT and WRONG_ACTION outcomes for training (filters out
    escalations and unknown outcomes).
    """
    
    def __init__(self):
        """Initialize policy updater."""
        pass
    
    def update(self, policy: Any, batch: list[RoutingOutcome]) -> bool:
        """Update policy using collected feedback.
        
        Args:
            policy: The busybee_cpu.CpuActionPolicy instance to update
            batch: List of RoutingOutcome objects
        
        Returns:
            True if update succeeded, False otherwise
        
        Note:
            Only CORRECT and WRONG_ACTION outcomes are used for training.
            Escalations (ESCALATED_CORRECTLY, ESCALATED_INCORRECTLY) are
            filtered out since they represent cases where busybee correctly
            deferred to the LLM.
        """
        # Convert outcomes to training format
        training_samples = self._convert_to_training_format(batch)
        
        if not training_samples:
            return False
        
        try:
            # Retrain the policy
            policy.train(training_samples)
            return True
        except Exception as e:
            _log.warning("Policy update failed: %s", e)
            return False
    
    def _convert_to_training_format(
        self, batch: list[RoutingOutcome]
    ) -> list[dict[str, Any]]:
        """Convert RoutingOutcome objects to busybee training format.
        
        Training format:
            {
                "state": {"goal": "...", "current_step": ...},
                "routed_action": "run_tests",
                "actual_action": "run_tests",  # ground truth
                "outcome": "correct"  # or "wrong_action"
            }
        
        Only CORRECT and WRONG_ACTION outcomes are included.
        """
        samples = []
        
        for outcome in batch:
            # Skip unknown outcomes
            if outcome.outcome_type == OutcomeType.UNKNOWN:
                continue
            
            # Skip escalations - they're not routing decisions
            if outcome.outcome_type in (
                OutcomeType.ESCALATED_CORRECTLY,
                OutcomeType.ESCALATED_INCORRECTLY,
            ):
                continue
            
            # Build training sample
            state = outcome.state.copy()
            
            # Ensure state has required fields
            if "goal" not in state:
                state["goal"] = "default_goal"
            if "current_step" not in state:
                state["current_step"] = 0
            
            sample = {
                "state": state,
                "routed_action": outcome.routed_action,
                "actual_action": outcome.actual_action,
                "outcome": outcome.outcome_type.value,
            }
            
            samples.append(sample)
        
        return samples
    
    def stats(self, batch: list[RoutingOutcome]) -> dict[str, Any]:
        """Get statistics about the feedback batch.
        
        Args:
            batch: List of RoutingOutcome objects
        
        Returns:
            Dict with counts and rates for each outcome type
        """
        # Count by outcome type
        outcome_counts: dict[str, int] = {}
        for outcome in batch:
            key = outcome.outcome_type.value
            outcome_counts[key] = outcome_counts.get(key, 0) + 1
        
        total = len(batch)
        
        # Calculate action accuracy (only for CORRECT + WRONG_ACTION)
        action_outcomes = [
            o for o in batch
            if o.outcome_type in (OutcomeType.CORRECT, OutcomeType.WRONG_ACTION)
        ]
        
        if action_outcomes:
            correct_count = sum(
                1 for o in action_outcomes if o.outcome_type == OutcomeType.CORRECT
            )
            action_accuracy = correct_count / len(action_outcomes)
        else:
            action_accuracy = 0.0
        
        # Calculate escalation rate
        escalation_count = sum(
            1 for o in batch
            if o.outcome_type in (
                OutcomeType.ESCALATED_CORRECTLY,
                OutcomeType.ESCALATED_INCORRECTLY,
            )
        )
        escalation_rate = escalation_count / total if total > 0 else 0.0
        
        return {
            "total_outcomes": total,
            "outcome_distribution": outcome_counts,
            "action_accuracy": action_accuracy,
            "escalation_rate": escalation_rate,
            "training_samples": len(
                self._convert_to_training_format(batch)
            ),
        }