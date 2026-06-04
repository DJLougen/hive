# Using Hive with Oh My Pi (OMP)

**Oh My Pi (OMP)** is a "batteries-included" fork of the Pi terminal coding harness. It runs an AI coding agent with Python, Bun/TypeScript, LSP, browser, subagents, and more — all in your terminal.

Hive plugs into OMP as a **compression + memory layer** — replacing or augmenting OMP's native context handling with Hive's causal graph memory and honey-comb compression.

---

## What Hive Adds to Oh My Pi

| Oh My Pi Native | + Hive | Result |
|-----------------|--------|--------|
| Terminal context window | honey-comb compression | 803x compression on test logs |
| File-based memory | rust-brain graph memory | Cause/effect tracking across sessions |
| Bash tool loop | busybee-cpu routing | Mechanical commands skip LLM |
| Bun/Python kernel | Multi-tenant isolation | Sandboxed subagent memory |

---

## Quick Start

Add Hive to your OMP environment:

```bash
pip install hive-agent-memory
```

In your OMP project workspace:

```python
from hive import HiveStack

# One stack per OMP project
stack = HiveStack(tenant_id="my_project")

# Compress tool output before sending to LLM
tool_output = "5000 lines of compiler errors..."
compressed = stack.compress("tool", tool_output)
# → label="distill", content=summary, ratio=803x

# Remember what you fixed
stack.remember("fix_attempt_1", {
    "file": "auth.py",
    "change": "added null check",
    "trust": 0.9,
})

# Recall later
fix = stack.recall("fix_attempt_1")
```

---

## Terminal Workflow

```python
# Inside an OMP turn (Python kernel):
def omp_tool_hook(tool_name, tool_output):
    """OMP calls this after every tool execution."""

    # 1. Compress bloated output
    if len(tool_output) > 1000:
        compressed = stack.compress("tool", tool_output)
        tool_output = compressed.content

    # 2. Route mechanical fixes locally
    if tool_name in ["run_tests", "read_file", "apply_patch"]:
        state = {"goal": tool_name, "available_tools": [tool_name]}
        decision = stack.route(state)
        if not decision.escalated:
            return decision.tool  # skip LLM

    # 3. Remember the outcome
    stack.remember(f"tool_{tool_name}", {
        "output": tool_output,
        "success": True,
    }, tags={tool_name})

    return tool_output
```

---

## Cross-Session Memory

OMP sessions are ephemeral. Hive persists across restarts:

```python
# Before closing OMP:
brain = stack.brain
brain.snapshot_to_file("/workspace/.omp_memory.gz")

# On next OMP start:
brain.restore_from_file("/workspace/.omp_memory.gz")
```

---

## Subagent Isolation

OMP supports subagents. Hive tenants keep them isolated:

```python
subagents = {
    "frontend": HiveStack(tenant_id="omp_frontend"),
    "backend":  HiveStack(tenant_id="omp_backend"),
    "tests":    HiveStack(tenant_id="omp_tests"),
}
```

---

## LSP Integration

OMP uses LSP for language-aware edits. Hive compresses LSP responses:

```python
lsp_output = "200 diagnostic messages..."
compressed = stack.compress("lsp", lsp_output)
# → label="distill", only critical diagnostics kept
```

---

## See Also

- [Hive Usage Guide](USAGE.md)
- [docs/harness-pi.md](harness-pi.md) — the minimal Pi harness
