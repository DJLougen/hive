# RLHF Pipeline for Hive Routing — Exploration Roadmap

**Status**: Spike / research phase  
**Owner**: TBD  
**Target**: v0.5.0 or later

---

## Problem Statement

`HiveStack.route()` uses a static `busybee_policy`. The `PolicyUpdater` exists as a stub but performs no actual learning. Feedback is collected in `FeedbackBuffer` but only converted to static training examples.

**Goal**: Turn feedback into continuous policy improvement so Hive routing gets better the longer it runs.

---

## Current State

```python
# hive/policy_updater.py — today
class PolicyUpdater:
    def update(self, policy, outcomes):
        training_samples = [self._convert_to_training_format(o) for o in outcomes]
        policy.train(training_samples)  # Just re-fits sklearn
        return True
```

This is batch retraining, not RL. It works but:
- Forgets everything before the current batch
- No exploration / exploitation tradeoff
- No reward shaping for partial successes

---

## Options Explored

### Option A: Lightweight Online Bandit (Thompson Sampling)

Replace the decision tree with a multi-armed bandit per feature bucket.

**Pros**: O(1) update, no replay buffer, proven in production  
**Cons**: Doesn't use state features, converges slower for high-dimensional state  
**Complexity**: Low (~2 weeks)

### Option B: Policy Gradient (REINFORCE)

Train a small neural net π(a|s) with Monte Carlo returns.

**Pros**: Natural fit for stochastic routing, uses full state  
**Cons**: High variance, needs many episodes, adds torch dependency  
**Complexity**: Medium (~1 month)

### Option C: Offline RL (DPO / IPO on logged feedback)

Treat `FeedbackBuffer` as an offline dataset. Train with Direct Preference Optimization.

**Pros**: Uses existing data, no environment simulator needed  
**Cons**: Needs preference pairs (not just scalar outcomes)  
**Complexity**: High (~2 months)

### Option D: Contextual Bandit with LinUCB

Linear upper-confidence-bound per action, updated online.

**Pros**: Theoretical regret bounds, simple implementation  
**Cons**: Assumes linear reward model  
**Complexity**: Low (~2 weeks)

---

## Recommendation

**Start with Option D (LinUCB)** for v0.5.0:
1. Proven in recommender systems at scale
2. Fits the current `busybee_cpu.CpuActionPolicy` architecture
3. No new dependencies (pure numpy)
4. Can warm-start from existing decision tree policy

**Migrate to Option C (DPO)** for v0.6.0 if feedback volume justifies it.

---

## Open Questions

1. **Reward signal**: Is `OutcomeType.CORRECT = +1`, `ESCALATED_CORRECTLY = +0.5`, `INCORRECT = -1` granular enough?
2. **Exploration budget**: How many "wrong" routes can we afford per 100 calls?
3. **Safety**: How do we prevent a corrupted policy from making bad routing decisions in production?
4. **Feature engineering**: Should `AgentState` include embedding-based features, or stick to tabular?

---

## Spike Tasks

- [ ] Implement LinUCB policy as `hive.policy.LinUCBPolicy`
- [ ] Add `FeedbackBuffer.to_linucb_matrix()` converter
- [ ] Benchmark: LinUCB vs static decision tree on 10k synthetic episodes
- [ ] Write policy rollback mechanism (keep last N checkpoints)
- [ ] A/B test harness: route 10% traffic through learned policy
