# What's new — August 2026 modernization

Branch: [`cursor/modernize-all-tiers-fda8`](https://github.com/DJLougen/hive/pull/62) · Release track: v0.7 (unreleased)

## Headline outcomes

- **200 tests passing** — full `pytest` suite green after modernization
- **HLC bug fixed** — snapshot restore and gossip replay preserve causal timestamps (`hlc` / `ts_ns`)
- **MCP one-liner** — `pip install "hive-agent-memory[agents]"` exposes Hive to Cursor, Claude Desktop, and Codex
- **Long-context eval** — `scripts/hive_long_context_eval.py --smoke` shows up to **153×** compression on 50k+ char logs
- **`HIVE_BACKEND`** — switch route/compress between Python and native hive-cpp (`python` | `native` | `auto`)
- **LinUCB** — contextual bandit routing without sklearn
- **httpx async LLM** — `_OpenAICompatBackend.achat` via `[http]` extra

## Four tiers (what changed)

### Tier 1 — Tooling + HLC correctness

- `uv.lock`, `.pre-commit-config.yaml`, Dependabot
- Ruff lint config; CI matrix Python 3.10–3.13
- HLC preservation in `restore_from_file` and `gossip.receive`
- New tests: `tests/test_hlc_snapshot_gossip.py`

### Tier 2 — Eval credibility + MCP/API extras

- Optional extras: `server`, `mcp`, `agents`, `http`
- `scripts/hive_long_context_eval.py` for compression evidence on long transcripts
- CI smoke: MCP import, long-context eval

### Tier 3 — Product wiring

- `hive/backend.py` + `HIVE_BACKEND` env var wired into `HiveStack`
- LinUCB policy in `hive/policy_updater.py`
- Async LLM client via httpx
- New tests: `test_backend.py`, `test_linucb_policy.py`, `test_mcp_smoke.py`

### Tier 4 — Ops + docs

- `pip-audit`, SBOM generation job, nightly GPU/Jetson smoke (continue-on-error until runners registered)
- Docker aarch64 → L4T r36.4.0; numpy 2.x allowed
- Refreshed `docs/enterprise-roadmap.md`, `hive-improvement-plan.md`, README

## Twitter-ready copy

**Short (≤280 chars)**

```
Hive update: fixes memory ordering when agents sync across machines, 200 tests green, plug into Cursor, Claude, or Codex via MCP, better compression on long logs, faster native backend option. github.com/DJLougen/hive/pull/62
```

**Plain English (thread-friendly)**

```
We updated Hive — the layer that lets AI agents handle boring steps on the CPU instead of burning LLM calls.

What's new:
• Memory keeps the right order when saving/restoring or syncing between servers
• 200 automated tests passing
• One install line to hook Hive into Cursor, Claude Desktop, or Codex
• Long chat logs compress much harder (tested on 50k+ character dumps)
• Optional faster Rust backend when you need speed

PR: github.com/DJLougen/hive/pull/62
```

**Technical (for dev audience)**

```
Hive August 2026 refresh: HLC snapshot fix, 200 tests, MCP one-liner (pip install hive-agent-memory[agents]), 153× long-context compression eval, HIVE_BACKEND native/python, LinUCB + httpx async LLM. PR: github.com/DJLougen/hive/pull/62
```

**Longer (technical)**

```
Shipped a four-tier modernization of Hive — the CPU orchestration layer that routes mechanical agent work off the LLM.

✅ HLC fix: causal timestamps survive snapshot restore + gossip replay
✅ 200 tests passing (pytest)
✅ MCP: pip install "hive-agent-memory[agents]" → Cursor / Claude Desktop / Codex tools
✅ Long-context eval: up to 153× compression on 50k+ char logs
✅ HIVE_BACKEND=python|native|auto for hive-cpp hot paths
✅ LinUCB contextual bandit + httpx async LLM
✅ uv.lock, pre-commit, Dependabot, ruff, pip-audit + SBOM CI

PR #62: https://github.com/DJLougen/hive/pull/62

PFN / busyBee training mode is next — not in this PR.
```

## Explicitly out of scope

- **PFN / busyBee-cpu training-mode integration** — inference + campaign retrain from `FeedbackBuffer`; tracked on the [README roadmap](../README.md#roadmap)

## Reproduce validation

```bash
pip install -e ".[dev]"
pytest                    # 200 passed
ruff check hive/ tests/
python scripts/hive_long_context_eval.py --smoke
```
