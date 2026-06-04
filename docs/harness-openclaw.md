# Using Hive with OpenClaw

**OpenClaw** is an agent harness framework that wraps an LLM with memory, tools, triggers, and messaging interfaces (WhatsApp, Telegram, etc.). It runs as a "personal OS" inside your chat app.

Hive plugs into OpenClaw as a **memory + compression + routing layer** — replacing OpenClaw's native context compaction and session store with Hive's graph memory and honey-comb compression.

---

## What Hive Adds to OpenClaw

| OpenClaw Native | + Hive | Result |
|-----------------|--------|--------|
| Built-in context compaction | honey-comb 5-label compression | Deterministic, audited compression |
| Session-based memory | rust-brain graph memory | Causal edges, timestamp protection |
| Generic tool routing | busybee-cpu policy routing | 2M routes/sec, CPU-only |
| Plugin tool registry | JWT auth + rate limiting | Enterprise-grade access control |

---

## Quick Start

```python
from hive import HiveStack
from hive.rust_brain import RustBrain

# One stack per OpenClaw session
stack = HiveStack(
    tenant_id=session.id,
    config=HiveConfig(validate_inputs=True),
)

# Inside OpenClaw's agent turn:
def openclaw_turn(prompt, tools):
    # 1. Route the tool call locally (skip LLM for mechanical stuff)
    state = {"goal": prompt.intent, "available_tools": tools}
    decision = stack.route(state)

    if not decision.escalated:
        return decision.tool, decision.args

    # 2. Escalated → compress context before LLM
    compressed = stack.compress("user", prompt.raw_text)

    # 3. Remember the outcome
    stack.remember("turn_result", {
        "prompt": compressed.content,
        "tool_used": decision.tool,
    })

    return "llm", {"compressed_context": compressed}
```

---

## Replacing OpenClaw's Context Compaction

OpenClaw has built-in compaction. Hive replaces it with auditable labels:

```python
# Before (OpenClaw native):
# - Prunes messages heuristically
# - No visibility into what was dropped

# After (Hive):
compressed = stack.compress("user", long_message)
# - label: "core" | "distill" | "compact" | "drop" | "stale" | "escalate"
# - Every decision is logged and reversible
```

---

## Session Persistence

OpenClaw sessions persist across conversations. Hive stores them with causal edges:

```python
# When OpenClaw starts a new session
stack.remember("session_start", {"channel": "whatsapp", "user": user_id})

# When a tool is used
stack.remember("tool_call", {"tool": "calendar", "args": args},
               edges={"caused_by": ["session_start"]})

# Later: walk the chain
stack.brain.neighbours("session_start", kind="caused_by")
```

---

## Plugin Integration

OpenClaw plugins can use Hive as a backend:

```python
# In your OpenClaw plugin:
from hive import HiveStack

class HivePlugin:
    def __init__(self, config):
        self.stack = HiveStack(
            tenant_id=config["tenant_id"],
            rate_limiter=RateLimiter(default_capacity=100),
        )

    def on_message(self, msg):
        # Compress before OpenClaw processes it
        c = self.stack.compress(msg.role, msg.content)
        return c
```

---

## Multi-Channel Isolation

OpenClaw runs across WhatsApp, Telegram, etc. Hive tenants keep them separate:

```python
channels = {
    "whatsapp": HiveStack(tenant_id="openclaw_whatsapp"),
    "telegram": HiveStack(tenant_id="openclaw_telegram"),
    "slack":    HiveStack(tenant_id="openclaw_slack"),
}
```

---

## See Also

- [Hive Usage Guide](USAGE.md)
- [docs/harness-hermes.md](harness-hermes.md)
