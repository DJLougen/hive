# Hive for Normal People

You don't need to know what a "causal graph memory store" is. You just want your AI agent to stop sending the entire internet to the LLM every time it asks a question.

Hive does three things:

1. **Routes dumb decisions locally** — "read this file" doesn't need a $0.03 LLM call
2. **Compresses bloated context** — 5000 lines of logs become 30 words
3. **Remembers what happened** — so it doesn't ask the same thing twice

---

## Install

```bash
pip install hive-agent-memory
```

That's it. No GPU needed. No API keys needed for the local parts.

---

## The One-Minute Version

```python
from hive import HiveStack

stack = HiveStack()

# Your agent gets a request:
request = "Fix the login bug"

# 1. Hive routes it locally if it's obvious
state = {"goal": request, "available_tools": ["read_file", "run_tests", "edit_file"]}
decision = stack.route(state)

# If decision.tool == "read_file", you just saved an LLM call.
# If decision.tool == "escalate", it wasn't obvious — send to LLM.

# 2. Compress huge outputs before the LLM sees them
logs = "5000 lines of server logs..."
compressed = stack.compress("user", logs)
# compressed.content is ~30 words. The LLM only sees that.

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

If the answer is "read a file," "run tests," or "look at the logs" — Hive handles it locally. Only confusing stuff goes to the LLM.

**Result:** 35% fewer LLM calls. Saves money.

### `stack.compress(role, content)` — "Make this shorter"

Your agent wants to paste 5000 lines of logs into the LLM prompt. Hive compresses it to ~30 words. The LLM still gets the point, but you pay for 30 tokens instead of 5000.

**Result:** ~64% fewer tokens per LLM call. Saves money.

### `stack.remember(key, value)` / `recall(key)` — "Don't forget"

Your agent fixed the login bug. Next session it asks "why is login broken?" again. If you remembered it, you can just recall the fix.

**Result:** Stops repeated mistakes. Saves time.

---

## Example: Building a Chatbot

```python
from hive import HiveStack

stack = HiveStack()

class MyChatbot:
    def handle_message(self, user_msg):
        # Compress if the user pasted a wall of text
        if len(user_msg) > 1000:
            user_msg = stack.compress("user", user_msg).content

        # Route: is this a mechanical request?
        decision = stack.route({
            "goal": user_msg,
            "available_tools": ["search", "summarize", "escalate"],
        })

        if decision.tool == "search":
            result = self.search(user_msg)
            stack.remember(f"search_{user_msg}", result)
            return result

        if decision.tool == "summarize":
            return self.summarize(user_msg)

        # Not mechanical — ask the LLM
        return self.ask_llm(user_msg)
```

---

## Example: Code Agent

```python
from hive import HiveStack

stack = HiveStack()

class CodeAgent:
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
| Every decision → LLM ($0.03) | Obvious decisions → CPU ($0) | `route()` |
| Full logs → LLM (5000 tokens) | Summary → LLM (30 tokens) | `compress()` |
| "Fix login again" → LLM | "Already fixed: ..." → local | `remember()` / `recall()` |

**Bottom line:** Your LLM bill drops by ~80% for mechanical agent tasks.

---

## What You Don't Need to Know

- **Pydantic** — schema validation. Skip it. Set `validate=False`.
- **JWT** — authentication. Skip it unless you're running a public API.
- **Prometheus** — metrics. Skip it unless you have a dashboard.
- **Rust backend** — extra speed. Skip it. The Python version is fast enough.
- **Multi-tenancy** — user isolation. Skip it unless you have multiple users.

Just use `HiveStack()` with defaults. It works.

---

## Troubleshooting

### "It's not compressing enough"

```python
stack = HiveStack(honey_comb=RuleFastHoneyComb())
# RuleFast is more aggressive than the ML model.
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

You need a trained policy. Without one, everything escalates to the LLM:

```python
# This is expected — busybee needs training data
stack = HiveStack()  # no policy loaded → everything escalates
```

Training a policy is advanced. For now, just use `compress()` and `remember()`.

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
