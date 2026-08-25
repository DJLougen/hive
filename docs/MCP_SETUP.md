# MCP setup — Cursor, Claude Desktop, and Codex

Hive ships an MCP server that exposes five tools: `hive_route`, `hive_compress`,
`hive_remember`, `hive_recall`, and `hive_search`.

## 1. Install

```bash
pip install "hive-agent-memory[agents]"
```

This installs the `hive-mcp` console command (stdio server) and the optional
FastAPI server extra.

Verify:

```bash
hive-mcp --help
# or
python -c "from hive.mcp_server import HIVE_MCP_TOOLS; print(HIVE_MCP_TOOLS)"
```

## 2. Quick install (all clients)

```bash
python -m hive.mcp_install --all
```

| Flag | Writes to |
|------|-----------|
| `--cursor` | `~/.cursor/mcp.json` (or `.cursor/mcp.json` with `--project`) |
| `--claude` | Claude Desktop config (macOS / Linux paths) |
| `--codex` | `~/.codex/config.toml` (or `.codex/config.toml` with `--project`) |
| `--all` | All of the above |

Restart the client after installing.

## 3. Manual setup

### Cursor

This repo includes a project config at [`.cursor/mcp.json`](../.cursor/mcp.json).
Open the repo in Cursor and reload the window — Hive should appear under MCP.

Global install (all projects):

```json
{
  "mcpServers": {
    "hive": {
      "type": "stdio",
      "command": "hive-mcp",
      "args": []
    }
  }
}
```

Path: `~/.cursor/mcp.json` (macOS/Linux) or `%USERPROFILE%\.cursor\mcp.json` (Windows).

If `hive-mcp` is not on PATH, use:

```json
{
  "mcpServers": {
    "hive": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "hive.mcp_server"]
    }
  }
}
```

### Claude Desktop

Merge [integrations/mcp/claude_desktop_config.snippet.json](../integrations/mcp/claude_desktop_config.snippet.json)
into your Claude Desktop config:

| OS | Path |
|----|------|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

Restart Claude Desktop.

### Codex (CLI / IDE extension / desktop app)

Append [integrations/mcp/codex.config.snippet.toml](../integrations/mcp/codex.config.snippet.toml)
to `~/.codex/config.toml`, or create `.codex/config.toml` in a trusted project.

```toml
[mcp_servers.hive]
command = "hive-mcp"
args = []
```

Verify with `codex mcp list` (Codex CLI) or `/mcp` inside a Codex session.

## 4. SSE mode (optional)

For HTTP clients that expect SSE instead of stdio:

```bash
python scripts/hive_mcp_server.py --transport sse --port 8080
```

Point remote MCP entries at `http://127.0.0.1:8080/sse`.

## 5. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `hive-mcp: command not found` | Re-run `pip install "hive-agent-memory[agents]"` or use `python -m hive.mcp_server` in config |
| Server shows red in Cursor | Reload window; check MCP logs in client settings |
| Claude does not list tools | Confirm JSON is valid and Claude was fully restarted |
| Codex ignores config | Use `[mcp_servers.hive]` (underscore), not `mcp.servers` |

## 6. Tools reference

| Tool | Purpose |
|------|---------|
| `hive_route` | CPU routing decision for a goal + available tools |
| `hive_compress` | Compress a bloated message before sending to the LLM |
| `hive_remember` | Write to causal memory graph |
| `hive_recall` | Read a memory key |
| `hive_search` | Search memories by tag |

## See also

- [HARNESS_SETUP.md](HARNESS_SETUP.md) — Hermes, OpenClaw, SWE-bench, and harness + MCP bridge
