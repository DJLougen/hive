// hive.test.js — synthetic Bun tests for the OpenCode observe-only plugin.
// All fixtures are fabricated; no real sessions, no network, no SDK client.
// Run: bun test integrations/opencode/hive.test.js

import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { HivePlugin } from "./hive.js";

let tmpDir;
let savedEnv;

beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "hive-opencode-test-"));
  savedEnv = {
    HIVE_OPENCODE_DATA_DIR: process.env.HIVE_OPENCODE_DATA_DIR,
    HIVE_OPENCODE_DISABLED: process.env.HIVE_OPENCODE_DISABLED,
  };
  process.env.HIVE_OPENCODE_DATA_DIR = tmpDir;
  delete process.env.HIVE_OPENCODE_DISABLED;
});

afterEach(() => {
  for (const [k, v] of Object.entries(savedEnv)) {
    if (v === undefined) delete process.env[k];
    else process.env[k] = v;
  }
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

function throwingClient() {
  return new Proxy(
    {},
    {
      get() {
        throw new Error("client access forbidden");
      },
    },
  );
}

function fakeInput(overrides = {}) {
  return {
    client: throwingClient(),
    project: { id: "proj-raw-1" },
    directory: "/raw/synthetic/dir",
    worktree: "/raw/synthetic/dir",
    serverUrl: new URL("http://127.0.0.1:1"),
    $: () => {
      throw new Error("shell forbidden");
    },
    ...overrides,
  };
}

function jsonlFiles() {
  return fs.readdirSync(tmpDir).filter((f) => f.endsWith(".jsonl"));
}

function readRecords(file) {
  return fs
    .readFileSync(path.join(tmpDir, file), "utf8")
    .split("\n")
    .filter(Boolean)
    .map((l) => JSON.parse(l));
}

async function collect(pluginInput, drive) {
  const hooks = await HivePlugin(pluginInput ?? fakeInput());
  await drive(hooks);
  await hooks.dispose?.();
  const files = jsonlFiles();
  expect(files.length).toBe(1);
  return { lines: readRecords(files[0]), file: files[0] };
}

function deepFreeze(o) {
  if (o && typeof o === "object" && !Object.isFrozen(o)) {
    for (const v of Object.values(o)) deepFreeze(v);
    Object.freeze(o);
  }
  return o;
}

const SENTINEL = "S3CR3T-SENTINEL-9f4b2c";

describe("lifecycle", () => {
  test("writes collector_started and collector_stopped, unique file per instance", async () => {
    const { lines, file } = await collect(undefined, async () => {});
    expect(file).toMatch(/^r_[0-9a-z]+_[0-9a-f]{16}\.jsonl$/);
    expect(lines[0].event_type).toBe("collector_started");
    expect(lines.at(-1).event_type).toBe("collector_stopped");
    // stats.written counts the collector_stopped record itself.
    expect(lines.at(-1).stats.written).toBeGreaterThanOrEqual(2);
    for (const rec of lines) {
      expect(rec.schema_version).toBe(1);
      expect(rec.mode).toBe("observe");
      expect(rec.source).toBe("opencode");
      expect(typeof rec.run_id).toBe("string");
      expect(typeof rec.project_id).toBe("string");
      expect(rec.project_id).not.toContain("proj-raw-1");
      expect(typeof rec.event_id).toBe("string");
      expect(typeof rec.timestamp_ms).toBe("number");
    }
  });

  test("two instances get distinct run files", async () => {
    const a = await HivePlugin(fakeInput());
    const b = await HivePlugin(fakeInput());
    await a.dispose();
    await b.dispose();
    expect(jsonlFiles().length).toBe(2);
  });

  test("HIVE_OPENCODE_DISABLED=1 registers no hooks and writes nothing", async () => {
    process.env.HIVE_OPENCODE_DISABLED = "1";
    const hooks = await HivePlugin(fakeInput());
    expect(hooks).toEqual({});
    expect(jsonlFiles().length).toBe(0);
  });

  test("unwritable data dir makes plugin inert, not throwing", async () => {
    const blocker = path.join(tmpDir, "blocker");
    fs.writeFileSync(blocker, "x"); // file where a dir is required
    process.env.HIVE_OPENCODE_DATA_DIR = path.join(blocker, "sub");
    const hooks = await HivePlugin(fakeInput());
    expect(hooks).toEqual({});
  });

  test("missing data dir is created on first run", async () => {
    process.env.HIVE_OPENCODE_DATA_DIR = path.join(tmpDir, "fresh", "nested");
    const hooks = await HivePlugin(fakeInput());
    expect(typeof hooks.event).toBe("function");
    await hooks.event({ event: { type: "session.idle", properties: { sessionID: "s1" } } });
    await hooks.dispose();
    const dir = process.env.HIVE_OPENCODE_DATA_DIR;
    expect(fs.readdirSync(dir).filter((f) => f.endsWith(".jsonl")).length).toBe(1);
    expect(fs.existsSync(path.join(dir, "salt"))).toBe(true);
  });
});

describe("event projection", () => {
  test("session_started pseudonymizes ids, drops title/directory", async () => {
    const { lines } = await collect(undefined, async (h) => {
      await h.event({
        event: {
          type: "session.created",
          properties: {
            sessionID: "sess-raw-1",
            info: {
              id: "sess-raw-1",
              parentID: "parent-raw-9",
              title: SENTINEL,
              directory: "/secret/path",
              metadata: { key: SENTINEL },
            },
          },
        },
      });
    });
    const rec = lines.find((l) => l.event_type === "session_started");
    expect(rec).toBeDefined();
    expect(rec.session_id).toMatch(/^[0-9a-f]{32}$/);
    expect(rec.parent_session_id).toMatch(/^[0-9a-f]{32}$/);
    expect(JSON.stringify(lines)).not.toContain(SENTINEL);
    expect(JSON.stringify(lines)).not.toContain("sess-raw-1");
    expect(JSON.stringify(lines)).not.toContain("/secret/path");
  });

  test("tool_state maps categories/status, dedups repeat snapshots", async () => {
    const mk = (status, start, end) => ({
      type: "message.part.updated",
      properties: {
        sessionID: "s1",
        part: {
          id: "p1",
          sessionID: "s1",
          messageID: "m1",
          type: "tool",
          callID: "c1",
          tool: "BaSh",
          state: {
            status,
            time: { start, end },
            input: { command: SENTINEL },
            output: SENTINEL,
            title: SENTINEL,
            error: SENTINEL,
            metadata: { x: SENTINEL },
          },
        },
      },
    });
    const { lines } = await collect(undefined, async (h) => {
      await h.event({ event: mk("running", 100, undefined) });
      await h.event({ event: mk("running", 100, undefined) }); // repeat snapshot
      await h.event({ event: mk("completed", 100, 250) });
      await h.event({ event: mk("completed", 100, 250) }); // repeat snapshot
    });
    const states = lines.filter((l) => l.event_type === "tool_state");
    expect(states.length).toBe(2);
    expect(states[0].status).toBe("running");
    expect(states[0].duration_ms).toBeNull();
    expect(states[1].status).toBe("completed");
    expect(states[1].duration_ms).toBe(150);
    expect(states[1].tool_category).toBe("bash");
    expect(states[1].call_id).toMatch(/^[0-9a-f]{32}$/);
    expect(JSON.stringify(lines)).not.toContain(SENTINEL);
  });

  test("unknown tool names map to other; unknown status skipped", async () => {
    const mk = (tool, status) => ({
      type: "message.part.updated",
      properties: {
        sessionID: "s1",
        part: {
          id: `p-${tool}-${status}`,
          sessionID: "s1",
          messageID: "m1",
          type: "tool",
          callID: `c-${tool}-${status}`,
          tool,
          state: { status, time: {} },
        },
      },
    });
    const { lines } = await collect(undefined, async (h) => {
      await h.event({ event: mk("mcp__weird_server__doxx", "completed") });
      await h.event({ event: mk("read", "mystery-status") });
    });
    const states = lines.filter((l) => l.event_type === "tool_state");
    expect(states.length).toBe(1);
    expect(states[0].tool_category).toBe("other");
    expect(JSON.stringify(lines)).not.toContain("mcp__weird_server__doxx");
  });

  test("step_usage dedup key stable, revision increments, tokens nullable", async () => {
    const mk = (input, output, cost) => ({
      type: "message.part.updated",
      properties: {
        sessionID: "s1",
        part: {
          id: "part-1",
          sessionID: "s1",
          messageID: "msg-1",
          type: "step-finish",
          reason: "stop",
          cost,
          tokens: { input, output, reasoning: 0, cache: { read: 5, write: 0 } },
          snapshot: SENTINEL,
        },
      },
    });
    const { lines } = await collect(undefined, async (h) => {
      await h.event({ event: mk(100, 50, 0.01) });
      await h.event({ event: mk(120, 60, 0.012) }); // revision of same step
    });
    const usage = lines.filter((l) => l.event_type === "step_usage");
    expect(usage.length).toBe(2);
    expect(usage[0].dedup_key).toBe(usage[1].dedup_key);
    expect(usage[0].revision).toBe(0);
    expect(usage[1].revision).toBe(1);
    expect(usage[1].tokens).toEqual({
      input: 120,
      output: 60,
      reasoning: 0,
      cache_read: 5,
      cache_write: 0,
    });
    expect(usage[1].cost_estimate).toBe(0.012);
    expect(JSON.stringify(lines)).not.toContain(SENTINEL);
    expect(JSON.stringify(lines)).not.toContain("msg-1");
  });

  test("missing usage stays null, not zero", async () => {
    const { lines } = await collect(undefined, async (h) => {
      await h.event({
        event: {
          type: "message.part.updated",
          properties: {
            sessionID: "s1",
            part: { id: "p", sessionID: "s1", messageID: "m", type: "step-finish" },
          },
        },
      });
    });
    const rec = lines.find((l) => l.event_type === "step_usage");
    expect(rec.tokens.input).toBeNull();
    expect(rec.tokens.cache_read).toBeNull();
    expect(rec.cost_estimate).toBeNull();
  });

  test("session_idle carries no outcome/success fields", async () => {
    const { lines } = await collect(undefined, async (h) => {
      await h.event({ event: { type: "session.idle", properties: { sessionID: "s1" } } });
    });
    const rec = lines.find((l) => l.event_type === "session_idle");
    expect(rec).toBeDefined();
    expect("outcome" in rec).toBe(false);
    expect("success" in rec).toBe(false);
  });

  test("session_error emits coarse kind only", async () => {
    const { lines } = await collect(undefined, async (h) => {
      await h.event({
        event: {
          type: "session.error",
          properties: {
            sessionID: "s1",
            error: { name: "ProviderAuthError", message: SENTINEL, data: { body: SENTINEL } },
          },
        },
      });
    });
    const rec = lines.find((l) => l.event_type === "session_error");
    expect(rec.error_kind).toBe("ProviderAuthError");
    expect(JSON.stringify(lines)).not.toContain(SENTINEL);
  });

  test("arbitrary error names map to allowlisted other", async () => {
    const { lines } = await collect(undefined, async (h) => {
      await h.event({
        event: {
          type: "session.error",
          properties: {
            sessionID: "s1",
            error: { name: `Vendor${SENTINEL}Error`, message: SENTINEL },
          },
        },
      });
      await h.event({
        event: {
          type: "session.error",
          properties: { sessionID: "s1", error: { name: "TypeError" } },
        },
      });
    });
    const errs = lines.filter((l) => l.event_type === "session_error");
    expect(errs[0].error_kind).toBe("other");
    expect(errs[1].error_kind).toBe("TypeError");
    expect(JSON.stringify(lines)).not.toContain(SENTINEL);
  });

  test("unknown events and content-bearing events ignored", async () => {
    const { lines } = await collect(undefined, async (h) => {
      await h.event({
        event: { type: "message.part.delta", properties: { sessionID: "s1", delta: SENTINEL } },
      });
      await h.event({
        event: { type: "session.diff", properties: { sessionID: "s1", diff: SENTINEL } },
      });
      await h.event({
        event: {
          type: "message.updated",
          properties: { sessionID: "s1", info: { summary: SENTINEL } },
        },
      });
      await h.event({ event: { type: "totally.unknown", properties: { x: SENTINEL } } });
      await h.event({ event: null });
      await h.event({});
    });
    expect(JSON.stringify(lines)).not.toContain(SENTINEL);
    expect(lines.every((l) => l.event_type.startsWith("collector_"))).toBe(true);
  });
});

describe("chat.params observation", () => {
  test("emits request_observed with scalar ids only", async () => {
    const { lines } = await collect(undefined, async (h) => {
      await h["chat.params"](
        {
          sessionID: "s1",
          agent: "build",
          model: { providerID: "anthropic", modelID: "claude-x" },
          provider: { options: { apiKey: SENTINEL } },
          message: { parts: [{ text: SENTINEL }] },
        },
        { temperature: 0.5, options: { key: SENTINEL } },
      );
    });
    const rec = lines.find((l) => l.event_type === "request_observed");
    expect(rec.agent).toBe("build");
    expect(rec.provider_id).toBe("anthropic");
    expect(rec.model_id).toBe("claude-x");
    expect(JSON.stringify(lines)).not.toContain(SENTINEL);
  });
});

describe("non-interference", () => {
  test("frozen inputs/outputs pass through unmutated", async () => {
    const input = deepFreeze({
      sessionID: "s1",
      agent: "build",
      model: { providerID: "p", modelID: "m" },
      provider: { options: {} },
      message: { parts: [] },
    });
    const output = deepFreeze({ temperature: 0.7, options: {} });
    const evt = deepFreeze({
      type: "session.idle",
      properties: { sessionID: "s1" },
    });
    const { lines } = await collect(undefined, async (h) => {
      await h["chat.params"](input, output);
      await h.event({ event: evt });
    });
    expect(output.temperature).toBe(0.7);
    expect(Object.keys(output.options).length).toBe(0);
    expect(lines.some((l) => l.event_type === "request_observed")).toBe(true);
  });

  test("malformed events and hook args never reject", async () => {
    const hooks = await HivePlugin(fakeInput());
    await expect(hooks.event()).resolves.toBeUndefined();
    await expect(hooks.event({})).resolves.toBeUndefined();
    await expect(
      hooks.event({ event: { type: "session.created", properties: "junk" } }),
    ).resolves.toBeUndefined();
    await expect(hooks["chat.params"]()).resolves.toBeUndefined();
    await expect(hooks["chat.params"](null, null)).resolves.toBeUndefined();
    await expect(hooks.dispose()).resolves.toBeUndefined();
  });

  test("no client, shell, or network access", async () => {
    const realFetch = globalThis.fetch;
    let fetchCalled = false;
    globalThis.fetch = () => {
      fetchCalled = true;
      throw new Error("network forbidden");
    };
    try {
      const { lines } = await collect(undefined, async (h) => {
        await h.event({ event: { type: "session.idle", properties: { sessionID: "s1" } } });
        await h["chat.params"]({ sessionID: "s1", model: {} }, {});
      });
      expect(fetchCalled).toBe(false);
      expect(lines.some((l) => l.event_type === "session_idle")).toBe(true);
    } finally {
      globalThis.fetch = realFetch;
    }
  });

  test("queue overflow drops are accounted, not thrown", async () => {
    const hooks = await HivePlugin(fakeInput());
    // No await inside the loop: handlers run synchronously, so the queue
    // actually fills past QUEUE_MAX before setImmediate drains can run.
    for (let i = 0; i < 5000; i++) {
      void hooks.event({
        event: { type: "session.idle", properties: { sessionID: `s${i}` } },
      });
    }
    await hooks.dispose();
    const files = jsonlFiles();
    expect(files.length).toBe(1);
    const lines = readRecords(files[0]);
    const stopped = lines.at(-1);
    // collector_stopped bypasses the bounded queue: always the last line.
    expect(stopped.event_type).toBe("collector_stopped");
    expect(stopped.stats.dropped).toBeGreaterThan(0);
    // stats.written counts the stopped record itself.
    expect(stopped.stats.written + stopped.stats.dropped).toBe(5002);
  });
});

describe("storage permissions", () => {
  test("salt and jsonl are 0600, dir is 0700", async () => {
    await collect(undefined, async () => {});
    const dirStat = fs.statSync(tmpDir);
    expect(dirStat.mode & 0o777).toBe(0o700);
    const saltStat = fs.statSync(path.join(tmpDir, "salt"));
    expect(saltStat.mode & 0o777).toBe(0o600);
    const jsonl = jsonlFiles()[0];
    expect(fs.statSync(path.join(tmpDir, jsonl)).mode & 0o777).toBe(0o600);
  });

  test("salt persists across instances (stable pseudonyms)", async () => {
    // Each instance writes its own run file; select the newly created one.
    const idleSessionId = async () => {
      const before = new Set(jsonlFiles());
      const hooks = await HivePlugin(fakeInput());
      await hooks.event({
        event: { type: "session.idle", properties: { sessionID: "same-session" } },
      });
      await hooks.dispose();
      const fresh = jsonlFiles().filter((f) => !before.has(f));
      expect(fresh.length).toBe(1);
      const lines = readRecords(fresh[0]);
      return lines.find((l) => l.event_type === "session_idle").session_id;
    };
    const a = await idleSessionId();
    const b = await idleSessionId();
    expect(a).toBe(b);
    expect(a).not.toContain("same-session");
  });
});
