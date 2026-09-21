# Using Hive with Pi

**Pi** (`@earendil-works/pi-coding-agent`) is a minimal terminal coding
harness. Hive ships a native Pi extension (`integrations/pi/hive.ts`)
that adds **metadata-only observation** and **opt-in context
compression** — nothing else. It does not route models or tools, does
not add memory to Pi, and makes no token-savings claims.

> **Checkout only.** The extension, launcher, bridge, and report are not
> packaged — `pip install` does not provide them. Run everything from a
> source checkout of this repository.

---

## What the integration does

| Mode | Behavior |
|------|----------|
| `off` | Fully inert: no files, no events, no bridge calls. |
| `observe` (default) | Writes a JSONL event log: lifecycle, per-message token/cost usage, tool-call categories, and context byte counters. Message content, prompts, paths, tool arguments, and error strings are never recorded. |
| `compress` | `observe` plus a context hook that rewrites *eligible* historical bash tool results — old, successful test-run logs only — through a local Python bridge (`scripts/hive_pi.py`, backed by `hive.rule_fast`). The persisted transcript is never modified; any bridge failure leaves context unchanged. |

All hooks are fail-open: a collector or bridge failure never affects the
Pi session.

## Requirements

- This repository checked out, with its `.venv` (Python ≥ 3.10) for the
  bridge/report.
- Node ≥ 22.19.
- Pi 0.85.1 installed into an isolated prefix:

  ```bash
  npm install --prefix ~/.local/share/hive/pi-runtime \
      @earendil-works/pi-coding-agent@0.85.1
  ```

## Running

```bash
# from the repo checkout
integrations/pi/hive-pi                  # interactive Pi, observe mode
integrations/pi/hive-pi -p "task"        # print mode; args forwarded verbatim
integrations/pi/hive-pi --hive-mode compress
integrations/pi/hive-pi --hive-mode off
```

`--hive-mode` overrides `HIVE_PI_MODE`; both accept `off | observe |
compress` and default to `observe`. Optionally symlink the launcher onto
your PATH (e.g. `~/.local/bin/hive-pi`); it resolves the repo through
its own symlink.

The launcher runs Pi with discovery disabled (`--no-extensions
--no-skills --no-prompt-templates --no-themes`) and `--no-approve`, loads
only the Hive extension, sets `PI_OFFLINE=1`/`PI_TELEMETRY=0`, and points
`PI_CODING_AGENT_DIR` at an isolated agent dir
(`~/.local/share/hive/pi-agent`). Credentials are never copied; log in
inside Pi with `/login`. `PI_OFFLINE=1` only disables startup network
work — it is not a sandbox.

## Evidence report

```bash
integrations/pi/hive-pi --hive-report [--json] [--strict] [--data-dir DIR]
```

Runs `scripts/hive_pi.py report` — Python only, no Node required. It
summarizes the local JSONL event log strictly observationally: usage
totals are `null` when incomplete (never zero-filled), context byte
counters are per-event payload observations — **not** token or monetary
savings — and malformed rows are dropped and counted. `--strict` exits
nonzero on dropped rows, duplicates, unreadable files, or flagged runs.

## Data locations

| What | Default | Override |
|------|---------|----------|
| Event log | `~/.local/share/hive/pi/events/<run_id>.jsonl` | `HIVE_PI_DATA_DIR` |
| Pi agent dir (auth, sessions) | `~/.local/share/hive/pi-agent` | `PI_CODING_AGENT_DIR` |
| Pi runtime prefix | `~/.local/share/hive/pi-runtime` | `HIVE_PI_RUNTIME` |
| Bridge script | `<repo>/scripts/hive_pi.py` | `HIVE_PI_BRIDGE` |
| Python for bridge/report | `<repo>/.venv/bin/python` | `HIVE_PI_PYTHON` |

Event dirs are `0700`, files `0600`; project/session/call/message ids
are salted-HMAC pseudonyms (salt stored locally, never committed).

## Tests and smoke

```bash
bun test integrations/pi/hive.test.ts        # unit: hooks, bridge, cache, privacy
pytest tests/test_hive_pi.py                 # bridge + report contract
node integrations/pi/smoke.mjs               # end-to-end against real Pi CLI
```

The smoke runner drives the actual pinned Pi CLI with an isolated HOME,
a synthetic provider, and a stubbed bash tool — no network, credentials,
or real shell.

## See Also

- [Hive Usage Guide](USAGE.md)
- [docs/harness-omp.md](harness-omp.md) — the batteries-included fork
