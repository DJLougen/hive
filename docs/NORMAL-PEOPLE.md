# Hive for Normal People

You don't need to know what a "causal graph memory store" is. You just want your AI agent to stop sending the entire internet to the LLM every time it asks a question.

Hive does three things:

1. **Routes dumb decisions locally** — "read this file" doesn't need a $0.03 LLM call (needs a routing policy attached; see below)
2. **Compresses bloated context** — 5000 lines of logs become 30 words
3. **Remembers what happened** — so it doesn't ask the same thing twice

---

## Install

```bash
pip install -e /path/to/hive   # from source (not yet on PyPI)
```

That's it. No GPU needed. No API keys needed for the local parts.

---

## The One-Minute Version

```python
from hive import HiveStack
from hive.harness import load_routing_policy

# With no model_path, load_routing_policy() always returns the built-in
# rule-based policy (pass model_path=... to load a trained busyBee model —
# it falls back to the same rules if that fails). A bare HiveStack() has NO
# policy and escalates every route, so attach one to route anything locally.
stack = HiveStack(busybee_policy=load_routing_policy())

# 1. Route the mechanical steps locally
state = {
    "goal": "read file auth.py and check the traceback",
    "available_tools": ["read_file", "run_tests", "apply_patch", "escalate"],
}
decision = stack.route(state)
# decision.tool == "read_file", escalated == False → you just saved an LLM call.

# Anything the policy does not recognise escalates, and that is the point —
# deciding the fix is done is a judgment call, not a mechanical transition:
decision = stack.route({"goal": "Fix the login bug", "available_tools": state["available_tools"]})
# decision.tool == "escalate", escalated == True → send it to the LLM.

# 2. Compress huge outputs before the LLM sees them
logs = "5000 lines of server logs..."
compressed = stack.compress("user", logs)
# compressed.content is a compact summary. The LLM only sees that.

# 3. Remember the fix so you don't ask again
stack.remember("login_bug_fix", {
    "problem": "null pointer in auth.py",
    "solution": "added null check on line 42",
})

# Later:
fix = stack.recall("login_bug_fix")
# → {"problem": "null pointer in auth.py", "solution": "..."}
```

---

## What Each Part Does

### `stack.route(state)` — "Is this obvious?"

With a routing policy attached, "read a file", "run tests" and "look at the logs" are handled
locally, and only confusing stuff goes to the LLM. **With the default `HiveStack()` — no policy
attached — every call escalates** (`source="fallback"`); see "The route always says escalate".

**Measured result:** on the published hard-tier benchmark, 58% fewer LLM calls with resolve rates not separable from the LLM-everything baseline — see [`../benchmarks/README.md`](../benchmarks/README.md). Saves money.

### `stack.compress(role, content)` — "Make this shorter"

Your agent wants to paste 5000 lines of logs into the LLM prompt. Hive compresses it to ~30 words. The LLM still gets the point, but you pay for 30 tokens instead of 5000.

**Measured result:** ~45% fewer prompt tokens per episode on the hard tier (compression plus fewer calls compound). Saves money.

### `stack.remember(key, value)` / `recall(key)` — "Don't forget"

Your agent fixed the login bug. Next session it asks "why is login broken?" again. If you remembered it, you can just recall the fix.

**Result:** Stops repeated mistakes. Saves time.

---

## Example: Building a Chatbot

```python
from hive import HiveStack

stack = HiveStack()

class MyChatbot:
    # This example deliberately uses only the parts that work with plain
    # defaults. Routing needs a policy AND a mechanical goal; the policy
    # cannot emit domain tools like "search"/"summarize", so don't route
    # chat turns — keep asking the LLM and use Hive for context + memory.
    def handle_message(self, user_msg):
        # Compress if the user pasted a wall of text
        if len(user_msg) > 1000:
            user_msg = stack.compress("user", user_msg).content

        # Cheap dedupe: has this been asked before?
        previous = stack.recall(user_msg)
        if previous:
            return previous

        answer = self.ask_llm(user_msg)
        stack.remember(user_msg, answer)  # otherwise recall() never hits
        return answer
```

---

## Example: Code Agent

```python
from hive import HiveStack
from hive.harness import load_routing_policy

import time  # used by CodeAgent.edit_file

stack = HiveStack(busybee_policy=load_routing_policy())
# The rule-based fallback routes list_files / run_tests / read_file /
# apply_patch and escalates anything that needs judgment.

class CodeAgent:
    # `self.ask_llm(...)` / `self.search(...)` are your own model calls — Hive
    # only decides when to skip them.
    def edit_file(self, filepath, instruction):
        # Remember what we did
        stack.remember(filepath, {
            "last_edit": instruction,
            "timestamp": time.time(),
        })

    def read_logs(self, raw_logs):
        # Compress before showing to LLM
        return stack.compress("tool", raw_logs).content

    def fix_bug(self, bug_description):
        # Check if we already fixed this
        previous = stack.recall(bug_description)
        if previous:
            return f"Already fixed: {previous}"

        # Route: is this a mechanical fix?
        decision = stack.route({
            "goal": bug_description,
            "available_tools": ["apply_patch", "run_tests", "escalate"],
        })

        if not decision.escalated:
            return f"Applying {decision.tool}"

        # Escalate to LLM
        return self.ask_llm(bug_description)
```

---

## The Money Shot

| Before Hive | After Hive | What Changed |
|-------------|-----------|--------------|
| Every decision → LLM ($0.03) | Obvious decisions → CPU ($0) | `route()` — with a policy attached |
| Full logs → LLM (5000 tokens) | Summary → LLM (30 tokens) | `compress()` |
| "Fix login again" → LLM | "Already fixed: ..." → local | `remember()` / `recall()` |

**Bottom line:** on the published hard-tier benchmark the routing arm used 58% fewer LLM calls at
resolve rates that were **not separable** from the LLM-everything baseline; compression cut prompt
tokens ~45% per episode. `compress()` and `remember()`/`recall()` work with plain defaults —
routing needs a policy.

---

## What You Don't Need to Know

- **Pydantic** — schema validation. Skip it. Set `validate=False`.
- **JWT** — authentication. Skip it unless you're running a public API.
- **Prometheus** — metrics. Skip it unless you have a dashboard.
- **Rust backend** — extra speed. Skip it. The Python version is fast enough.
- **Multi-tenancy** — user isolation. Skip it unless you have multiple users.

Just use `HiveStack()` with defaults for compression and memory. It works. Routing stays in
"escalate everything" until you attach a policy — that default is deliberate, not a bug.

---

## Troubleshooting

### "It's not compressing enough"

```python
from hive import HiveStack
from hive.rule_fast import RuleFastHoneyComb

stack = HiveStack(honey_comb=RuleFastHoneyComb())
# RuleFast is the in-repo rule-based compressor (what you get by default when
# honey-comb is not installed); pass a honeycomb model here to use the ML one.
```

### "It forgot everything"

```python
# Hive memory is in-process. When your program restarts, it's gone.
# Save it:
stack.brain.snapshot_to_file("memory_backup.gz")

# Load it on startup:
stack.brain.restore_from_file("memory_backup.gz")
```

### "The route always says escalate"

That is the default: a stack with no policy attached escalates everything.

```python
from hive import HiveStack
from hive.harness import load_routing_policy

stack = HiveStack()                                    # escalates every route
stack = HiveStack(busybee_policy=load_routing_policy())  # routes the mechanical steps
```

The built-in rule-based policy needs no training and routes `read_file`,
`run_tests`, `apply_patch` and friends for goals it recognises; a trained
busyBee model (see `benchmarks/README.md`) replaces it when you have data.
Anything unrecognised still escalates — including "is this fix complete?".

---

## One-Liners

```python
from hive import HiveStack
stack = HiveStack()

# Save money on tokens
short = stack.compress("user", wall_of_text).content

# Stop repeating yourself
stack.remember("fix", solution)

# Look it up later
stack.recall("fix")
```

That's Hive.
