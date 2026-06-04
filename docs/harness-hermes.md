# Using Hive with HermesAgent-20

**HermesAgent-20** is Nous Research's open-source, self-improving agent framework. It runs as a long-lived service with memory, tools, skills, and multi-platform I/O (Telegram, Discord, Slack, etc.).

Hive plugs into Hermes as a **memory backend** — replacing or augmenting Hermes' native memory with Hive's causal graph store, compression, and routing.

---

## What Hive Adds to Hermes

| Hermes Native | + Hive | Result |
|---------------|--------|--------|
| Plain text memory | Timestamped graph memory | Agents walk cause/effect chains |
| Unbounded context | honey-comb compression | 64% fewer tokens to LLM |
| Manual tool routing | busybee-cpu routing | 35% of calls never hit the GPU |
| Single-tenant | Multi-tenant RustBrain | Isolate agents by user/org |

---

## Quick Start

```python
from hive.rust_brain import HermesBackend, RustBrain

# One backend per Hermes agent instance
backend = HermesBackend(brain=RustBrain(tenant_id="hermes_agent_1"))

# Inside Hermes' memory event handler:
def on_memory_event(event):
    """Hermes fires this when it wants to store something."""
    backend.publish(event)

# Inside Hermes' prompt builder:
def build_prompt():
    """Pull recent, high-trust memories into the context window."""
    memories = backend.pull(max_keys=16, min_trust=0.7)
    return {"memory": memories, "messages": [...]}
```

---

## Event Mapping

Hermes fires `memory.*` events. Hive's `HermesBackend.publish()` accepts the exact shape:

```python
backend.publish({
    "key": "flight_booking_2024_06",   # stable ID
    "value": {"airline": "Delta", "pnr": "ABC123"},
    "trust": 0.95,                     # confidence
    "tags": ["travel", "booking"],
    "caused_by": ["user_request_42"], # causal chain
})
```

| Hermes Field | Hive Maps To |
|--------------|-------------|
| `key` | MemoryNode.key |
| `value` | MemoryNode.value |
| `trust` | MemoryNode.trust |
| `tags` | MemoryNode.tags |
| `caused_by` | EdgeKind.CAUSED_BY edge |

---

## Context Window Pull

```python
# Before every LLM call, pull relevant context
memories = backend.pull(max_keys=32, min_trust=0.5)

# Returns Hermes-compatible dicts:
# [
#   {
#     "id": "a1b2c3...",
#     "key": "flight_booking_2024_06",
#     "value": {"airline": "Delta", ...},
#     "ts_ns": 1704067200000000000,
#     "trust": 0.95,
#     "tags": ["travel", "booking"],
#     "edges": {"caused_by": ["user_request_42"]}
#   }
# ]
```

---

## Multi-Agent Isolation

Hermes can run multiple agents. Give each its own tenant:

```python
agents = {
    "telegram_bot": HermesBackend(RustBrain(tenant_id="telegram")),
    "discord_bot":  HermesBackend(RustBrain(tenant_id="discord")),
    "slack_bot":    HermesBackend(RustBrain(tenant_id="slack")),
}
```

No memory leaks between channels.

---

## Compression Before LLM

Hermes sends full conversation history to the LLM. Hive compresses it:

```python
from hive import HiveStack

stack = HiveStack(tenant_id="hermes")

# Compress a bloated tool output before Hermes builds the prompt
compressed = stack.compress("tool", "5000 lines of server logs...")
# → label="distill", only the summary goes to the LLM
```

---

## Full Integration Pattern

```python
class HiveHermesBridge:
    def __init__(self, agent_id):
        self.backend = HermesBackend(RustBrain(tenant_id=agent_id))
        self.stack = HiveStack(tenant_id=agent_id)

    def handle_turn(self, hermes_event, transcript):
        # 1. Store what Hermes learned
        if "memory" in hermes_event:
            self.backend.publish(hermes_event["memory"])

        # 2. Route mechanical decisions locally
        state = {"goal": hermes_event["intent"], "available_tools": [...]}
        decision = self.stack.route(state)

        # 3. If escalated, compress context before LLM
        if decision.escalated:
            compressed = self.stack.compress("user", transcript)

        # 4. Pull memory for context window
        context = self.backend.pull(max_keys=16)

        return decision, context
```

---

## See Also

- [Hive Usage Guide](USAGE.md)
- [docs/component-rust_brain.md](component-rust_brain.md)
