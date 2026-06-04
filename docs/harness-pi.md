# Using Hive with Pi

**Pi** is a minimal terminal coding harness. Philosophy: "Bash is all you need." It intentionally skips complex agent patterns — just read, write, edit, bash, repeat.

Hive plugs into Pi as a **lightweight memory + compression layer** — keeping Pi minimal while adding causal memory and context compression.

---

## What Hive Adds to Pi

| Pi Native | + Hive | Result |
|-----------|--------|--------|
| No memory | rust-brain graph memory | Cross-session file relationships |
| Full context to LLM | honey-comb compression | 64% fewer tokens |
| Manual tool selection | busybee-cpu routing | Mechanical edits skip LLM |
| Single-file edits | Multi-file causal tracking | "Changed X, broke Y" is remembered |

---

## Quick Start

```bash
# In your Pi project:
pip install hive-agent-memory
```

```python
from hive import HiveStack

stack = HiveStack()  # defaults are fine for Pi

# After reading a file:
stack.remember("read_file", {"path": "auth.py", "content": snippet})

# After editing:
stack.remember("edit_file", {"path": "auth.py", "change": "+null_check"},
               edges={"caused_by": ["read_file"]})

# Before LLM call — compress context
compressed = stack.compress("user", "5000 lines of test output...")
# → Only the summary reaches the LLM
```

---

## Pi-Style Workflow

```python
# In Pi's turn loop:
def pi_turn(files_read, bash_output, user_prompt):
    # 1. Remember what Pi read
    for f in files_read:
        stack.remember(f"file_{f}", {"path": f})

    # 2. Compress bash output if bloated
    if len(bash_output) > 2000:
        bash_output = stack.compress("tool", bash_output).content

    # 3. Route mechanical edits
    if "apply_patch" in user_prompt:
        decision = stack.route({
            "goal": "apply patch",
            "available_tools": ["apply_patch"],
        })
        if not decision.escalated:
            return "apply_patch", decision.args

    # 4. Build context with causal memory
    memories = stack.brain.search(tag="file")[:8]
    context = [m.to_dict() for m in memories]

    return "llm", {"context": context, "prompt": user_prompt}
```

---

## Minimal Config

Pi values minimalism. Hive respects that:

```python
# No extras needed
stack = HiveStack(
    honey_comb=RuleFastHoneyComb(),  # no ML model
    validate=False,                   # no Pydantic overhead
    config=HiveConfig(
        tenant_isolation=False,       # single user
        default_ttl_s=3600,           # 1h TTL — Pi sessions are short
    ),
)
```

---

## Session Snapshot

Pi sessions are short-lived. Save memory before exit:

```python
# .pi_exit_hook
stack.brain.snapshot_to_file(".pi_memory.gz")

# .pi_startup_hook
stack.brain.restore_from_file(".pi_memory.gz")
```

---

## See Also

- [Hive Usage Guide](USAGE.md)
- [docs/harness-omp.md](harness-omp.md) — the batteries-included fork
