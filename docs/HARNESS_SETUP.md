# Harness setup — Hermes, OpenClaw, SWE-bench, and MCP

Hive plugs into agent **harnesses** (long-running agent frameworks and eval loops) as a
memory + compression + CPU-routing layer. This guide ties together the per-harness docs,
the SWE-bench eval script, and the MCP server so you can use Hive from Cursor, Claude
Desktop, Codex, or inside a harness loop.

## Architecture

```
┌─────────────────┐     MCP (stdio)      ┌──────────────┐
│ Cursor / Claude │ ◄──────────────────► │  hive-mcp    │
│ Codex           │                      │  (5 tools)   │
└─────────────────┘                      └──────┬───────┘
                                                │
┌─────────────────┐   HiveStack.route()         │
│ Hermes /        │ ◄───────────────────────────┤
│ OpenClaw /      │   HiveStack.compress()      │
│ SWE-bench eval  │   HiveStack.remember()      │
└─────────────────┘                             │
                                                ▼
                                    ┌───────────────────────┐
                                    │ busyBee-cpu (routing) │
                                    │ honey-comb (compress) │
                                    │ rust-brain (memory)   │
                                    └───────────────────────┘
```

**Two integration paths:**

| Path | When to use |
|------|-------------|
| **In-process** (`HiveStack`) | Harness runs Python; call `stack.route()` / `stack.compress()` directly |
| **MCP** (`hive-mcp`) | Harness or IDE is MCP-capable; connect via stdio config ([MCP_SETUP.md](MCP_SETUP.md)) |

Both paths hit the same five tools: `hive_route`, `hive_compress`, `hive_remember`,
`hive_recall`, `hive_search`.

---

## 1. Install

```bash
pip install "hive-agent-memory[agents]"
# optional: trained CPU router + ML compressor
pip install "hive-agent-memory[full]"
```

For MCP client wiring see [MCP_SETUP.md](MCP_SETUP.md).

---

## 2. Per-harness guides

| Harness | Doc | Hive role |
|---------|-----|-----------|
| **HermesAgent-20** | [harness-hermes.md](harness-hermes.md) | `HermesBackend` graph memory + compression |
| **OpenClaw** | [harness-openclaw.md](harness-openclaw.md) | Session memory, compression, routing |
| **Open Model Protocol (OMP)** | [harness-omp.md](harness-omp.md) | Tool registry + routing hooks |
| **Pi agent** | [harness-pi.md](harness-pi.md) | Lightweight agent loop integration |

Each doc shows the in-process `HiveStack` pattern. Add MCP when the harness or your IDE
should call Hive as an external tool server instead of importing it.

---

## 3. SWE-bench eval harness

`scripts/hive_swebench_eval.py` runs SWE-bench-lite instances with and without Hive in
the loop. It uses `hive.harness.load_routing_policy()` to pick a policy:

1. **Trained busyBee** — when `--busybee-model path/to/model.joblib` is passed and
   `busybee-cpu` is installed
2. **Rule-based fallback** — keyword routing for read/test/patch/install steps (no model
   file required)

```bash
# Baseline vs Hive (rule-based routing)
python scripts/hive_swebench_eval.py --instances 10 --seed 42

# With a trained busyBee model
python scripts/hive_swebench_eval.py --instances 10 --busybee-model models/busybee.joblib
```

Reports land in `docs/benchmarks/swebench-lite/`.

---

## 4. Routing policy loader

```python
from hive.harness import load_routing_policy, policy_label
from hive import HiveStack

policy = load_routing_policy(model_path="models/busybee.joblib")  # or None
print(policy_label(policy))  # "CpuActionPolicy" or "rule-based"

stack = HiveStack(busybee_policy=policy)
decision = stack.route({"goal": "read file src/main.py", "available_tools": ["read_file"]})
```

Use this in custom harnesses instead of duplicating the rule-based stub.

---

## 5. MCP inside a harness loop

If your harness is MCP-native (or you develop in Cursor/Codex with Hive MCP enabled),
map harness steps to MCP tools:

| Harness step | MCP tool | In-process equivalent |
|--------------|----------|----------------------|
| Pick next tool | `hive_route` | `stack.route(state)` |
| Shrink context | `hive_compress` | `stack.compress(role, content)` |
| Store finding | `hive_remember` | `stack.remember(key, value)` |
| Load finding | `hive_recall` | `stack.recall(key)` |
| Search by tag | `hive_search` | `stack.brain.search(tag=...)` |

Example MCP `hive_route` call (from any connected client):

```json
{
  "goal": "run tests for the auth module",
  "available_tools": ["read_file", "run_tests", "apply_patch"]
}
```

---

## 6. Hermes + busyBee adapter (sibling repo)

For HermesAgent-20 with a trained busyBee policy, see the adapter patch in the busyBee-cpu
repo:

- `integrations/hermesagent20/busybee-cpu-adapter.patch`
- `docs/HERMES_HARNESS_SETUP.md` (busyBee-cpu)

Hive's `HermesBackend` handles memory shape; busyBee handles routing. PFN-based training
mode is tracked separately on the Hive roadmap.

---

## 7. Troubleshooting

| Symptom | Fix |
|---------|-----|
| Every `route()` escalates | Load a busyBee model or install `[full]`; without a policy Hive escalates by design |
| MCP tools missing in IDE | Run `python -m hive.mcp_install --all` and restart the client ([MCP_SETUP.md](MCP_SETUP.md)) |
| SWE-bench eval uses mock data | `pip install datasets` for real SWE-bench-lite instances |
| Harness doc vs MCP mismatch | In-process = `HiveStack`; external = `hive-mcp` — same semantics, different transport |

---

## See also

- [MCP_SETUP.md](MCP_SETUP.md) — Cursor, Claude Desktop, Codex
- [USAGE.md](USAGE.md) — core HiveStack API
- [WHATS_NEW.md](WHATS_NEW.md) — August 2026 modernization summary
