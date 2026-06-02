"""Tests for online learning functionality."""

from __future__ import annotations


from hive.telemetry import Telemetry
from hive.feedback import FeedbackBuffer, RoutingOutcome, OutcomeType
from hive.policy_updater import PolicyUpdater


class MockPolicy:
    """Mock busybee policy for testing."""

    def __init__(self):
        """Initialize mock policy."""
        self.trained = False
        self.examples = []

    def predict(self, state: dict) -> dict:
        """Return mock prediction."""
        return {"action": "read_file", "confidence": 0.9}

    def train(self, examples):
        """Mock train method."""
        self.trained = True
        self.examples = list(examples)
        return True


def test_feedback_buffer_creation():
    """Test creating a feedback buffer."""
    fb = FeedbackBuffer(capacity=5)
    assert fb.capacity == 5
    assert len(fb) == 0
    assert not fb.is_full()


def test_feedback_buffer_record_outcome():
    """Test recording an outcome."""
    fb = FeedbackBuffer(capacity=3)

    outcome = RoutingOutcome(
        state={"goal": "read file"},
        routed_action="read_file",
        actual_action="read_file",
        outcome_type=OutcomeType.CORRECT,
    )

    fb.record(outcome)
    assert len(fb) == 1
    assert not fb.is_full()

    fb.record(outcome)
    fb.record(outcome)
    assert len(fb) == 3
    assert fb.is_full()


def test_feedback_buffer_drops_oldest_when_over_capacity():
    fb = FeedbackBuffer(capacity=2)
    for i in range(5):
        fb.add(
            RoutingOutcome(
                state={"goal": f"goal-{i}"},
                routed_action="read_file",
                actual_action="read_file",
                outcome_type=OutcomeType.CORRECT,
            )
        )
    assert len(fb) == 2
    assert fb.get_outcomes()[0].state["goal"] == "goal-3"
    assert fb.get_outcomes()[1].state["goal"] == "goal-4"


def test_feedback_buffer_clear():
    """Test clearing the buffer."""
    fb = FeedbackBuffer(capacity=3)

    outcome = RoutingOutcome(
        state={"goal": "read file"},
        routed_action="read_file",
        actual_action="read_file",
        outcome_type=OutcomeType.CORRECT,
    )

    fb.record(outcome)
    fb.record(outcome)
    assert len(fb) == 2

    fb.clear()
    assert len(fb) == 0


def test_feedback_buffer_is_full():
    """Test is_full() method."""
    fb = FeedbackBuffer(capacity=2)

    outcome = RoutingOutcome(
        state={"goal": "read file"},
        routed_action="read_file",
        actual_action="read_file",
        outcome_type=OutcomeType.CORRECT,
    )

    assert not fb.is_full()

    fb.record(outcome)
    assert not fb.is_full()

    fb.record(outcome)
    assert fb.is_full()


def test_hivestack_with_feedback_buffer():
    """Test HiveStack with feedback buffer."""
    from hive.stack import HiveStack

    fb = FeedbackBuffer(capacity=5)
    telemetry = Telemetry()
    policy = MockPolicy()

    stack = HiveStack(
        busybee_policy=policy,
        telemetry=telemetry,
        feedback_buffer=fb,
    )

    assert stack.feedback_buffer is fb
    assert stack._policy_updater is not None
    assert isinstance(stack._policy_updater, PolicyUpdater)


def test_record_outcome():
    """Test recording an outcome."""
    from hive.stack import HiveStack

    fb = FeedbackBuffer(capacity=5)
    telemetry = Telemetry()
    policy = MockPolicy()

    stack = HiveStack(
        busybee_policy=policy,
        telemetry=telemetry,
        feedback_buffer=fb,
    )

    state = {"goal": "read file"}
    decision = stack.route(state)
    stack.record_outcome(decision, "read_file", OutcomeType.CORRECT)

    assert len(fb) == 1
    outcomes = fb.get_outcomes()
    assert len(outcomes) == 1
    assert outcomes[0].outcome_type == OutcomeType.CORRECT
    assert outcomes[0].routed_action == "read_file"
    assert outcomes[0].actual_action == "read_file"


def test_record_outcome_string():
    """Test recording an outcome with string action."""
    from hive.stack import HiveStack

    fb = FeedbackBuffer(capacity=5)
    telemetry = Telemetry()
    policy = MockPolicy()

    stack = HiveStack(
        busybee_policy=policy,
        telemetry=telemetry,
        feedback_buffer=fb,
    )

    state = {"goal": "read file"}
    decision = stack.route(state)
    stack.record_outcome(decision, "read_file", "correct")

    assert len(fb) == 1
    outcomes = fb.get_outcomes()
    assert outcomes[0].outcome_type == OutcomeType.CORRECT


def test_record_outcome_no_decision():
    """Test recording an outcome without a previous decision."""
    from hive.stack import HiveStack

    fb = FeedbackBuffer(capacity=5)
    telemetry = Telemetry()
    policy = MockPolicy()

    stack = HiveStack(
        busybee_policy=policy,
        telemetry=telemetry,
        feedback_buffer=fb,
    )

    # Don't call route() first
    stack.record_outcome(None, "read_file", OutcomeType.CORRECT)

    # Should not record
    assert len(fb) == 0


def test_record_outcome_ignores_stale_state_for_mismatched_decision():
    from hive.stack import HiveStack, RouteDecision

    fb = FeedbackBuffer(capacity=5)
    policy = MockPolicy()
    stack = HiveStack(busybee_policy=policy, feedback_buffer=fb)

    stack.route({"goal": "first"})
    other = RouteDecision(
        tool="apply_patch",
        args={},
        confidence=0.5,
        escalated=False,
        source="busybee",
    )
    stack.record_outcome(other, "apply_patch", OutcomeType.CORRECT)

    assert len(fb) == 1
    assert fb.get_outcomes()[0].state == {}


def test_record_outcome_no_buffer():
    """Test that recording without buffer doesn't error."""
    from hive.stack import HiveStack

    telemetry = Telemetry()
    policy = MockPolicy()

    stack = HiveStack(
        busybee_policy=policy,
        telemetry=telemetry,
    )

    state = {"goal": "read file"}
    decision = stack.route(state)

    # Should not error
    stack.record_outcome(decision, "read_file", OutcomeType.CORRECT)


def test_should_update_policy():
    """Test should_update_policy() method."""
    from hive.stack import HiveStack

    fb = FeedbackBuffer(capacity=3)
    telemetry = Telemetry()
    policy = MockPolicy()

    stack = HiveStack(
        busybee_policy=policy,
        telemetry=telemetry,
        feedback_buffer=fb,
    )

    assert not stack.should_update_policy()

    state = {"goal": "read file"}
    decision = stack.route(state)

    stack.record_outcome(decision, "read_file", OutcomeType.CORRECT)
    assert not stack.should_update_policy()

    stack.record_outcome(decision, "read_file", OutcomeType.CORRECT)
    assert not stack.should_update_policy()

    stack.record_outcome(decision, "read_file", OutcomeType.CORRECT)
    assert stack.should_update_policy()


def test_update_policy():
    """Test updating the policy."""
    from hive.stack import HiveStack

    fb = FeedbackBuffer(capacity=2)
    telemetry = Telemetry()
    policy = MockPolicy()

    stack = HiveStack(
        busybee_policy=policy,
        telemetry=telemetry,
        feedback_buffer=fb,
    )

    state = {"goal": "read file"}
    decision = stack.route(state)
    stack.record_outcome(decision, "read_file", OutcomeType.CORRECT)
    stack.record_outcome(decision, "read_file", OutcomeType.CORRECT)

    assert stack.should_update_policy()

    success = stack.update_policy()
    assert success is True
    assert policy.trained
    assert len(policy.examples) == 2


def test_update_policy_not_ready():
    """Test that update_policy() returns False when not ready."""
    from hive.stack import HiveStack

    fb = FeedbackBuffer(capacity=5)
    telemetry = Telemetry()
    policy = MockPolicy()

    stack = HiveStack(
        busybee_policy=policy,
        telemetry=telemetry,
        feedback_buffer=fb,
    )

    assert not stack.should_update_policy()
    success = stack.update_policy()
    assert success is False


def test_update_policy_no_policy():
    """Test that update_policy() returns False when no policy."""
    from hive.stack import HiveStack

    fb = FeedbackBuffer(capacity=2)
    telemetry = Telemetry()

    stack = HiveStack(
        busybee_policy=None,
        telemetry=telemetry,
        feedback_buffer=fb,
    )

    state = {"goal": "read file"}
    decision = stack.route(state)  # Should escalate
    stack.record_outcome(decision, None, OutcomeType.ESCALATED_CORRECTLY)
    stack.record_outcome(decision, None, OutcomeType.ESCALATED_CORRECTLY)

    assert stack.should_update_policy()
    success = stack.update_policy()
    assert success is False


def test_stats_with_feedback():
    """Test stats() includes feedback."""
    from hive.stack import HiveStack

    fb = FeedbackBuffer(capacity=5)
    telemetry = Telemetry()
    policy = MockPolicy()

    stack = HiveStack(
        busybee_policy=policy,
        telemetry=telemetry,
        feedback_buffer=fb,
    )

    stats = stack.stats()
    assert "feedback" in stats
    assert "summary" in stats["feedback"]
    assert stats["feedback"]["summary"]["total_outcomes"] == 0

    state = {"goal": "read file"}
    decision = stack.route(state)
    stack.record_outcome(decision, "read_file", OutcomeType.CORRECT)

    stats = stack.stats()
    assert stats["feedback"]["summary"]["total_outcomes"] == 1
    assert stats["feedback"]["summary"]["by_outcome"]["correct"] == 1


def test_feedback_clears_after_update():
    """Test that feedback clears after policy update."""
    from hive.stack import HiveStack

    fb = FeedbackBuffer(capacity=2)
    telemetry = Telemetry()
    policy = MockPolicy()

    stack = HiveStack(
        busybee_policy=policy,
        telemetry=telemetry,
        feedback_buffer=fb,
    )

    state = {"goal": "read file"}
    decision = stack.route(state)
    stack.record_outcome(decision, "read_file", OutcomeType.CORRECT)
    stack.record_outcome(decision, "read_file", OutcomeType.CORRECT)

    assert len(fb) == 2

    success = stack.update_policy()
    assert success is True
    assert len(fb) == 0
