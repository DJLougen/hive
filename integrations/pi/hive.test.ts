// hive.test.ts — synthetic bun:test coverage for the Pi extension.
// All fixtures are fabricated; no real sessions, no network, no model calls.
// The compression bridge is exercised through fake python scripts written
// into the temp dir (HIVE_PI_BRIDGE / HIVE_PI_PYTHON), never the real one.
// Run: bun test integrations/pi/hive.test.ts

import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import hiveExtension from "./hive.ts";

const ENV_KEYS = [
	"HIVE_PI_DATA_DIR",
	"HIVE_PI_MODE",
	"HIVE_PI_PYTHON",
	"HIVE_PI_BRIDGE",
	"COUNT_FILE",
];

let tmpDir: string;
let savedEnv: Record<string, string | undefined>;
let notifications: { message: string; type?: string }[];

beforeEach(() => {
	tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "hive-pi-test-"));
	savedEnv = {};
	for (const k of ENV_KEYS) {
		savedEnv[k] = process.env[k];
		delete process.env[k];
	}
	process.env.HIVE_PI_DATA_DIR = tmpDir;
	notifications = [];
});

afterEach(() => {
	for (const k of ENV_KEYS) {
		if (savedEnv[k] === undefined) delete process.env[k];
		else process.env[k] = savedEnv[k];
	}
	fs.rmSync(tmpDir, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// Fakes

type Handler = (event: unknown, ctx: unknown) => unknown;

function fakePi(flagValues: Record<string, unknown> = {}) {
	const handlers = new Map<string, Handler[]>();
	const commands = new Map<string, { description?: string; handler: Handler }>();
	const flags = new Map<string, { type: string }>();
	const pi = {
		on(event: string, handler: Handler) {
			const list = handlers.get(event) ?? [];
			list.push(handler);
			handlers.set(event, list);
		},
		registerCommand(name: string, options: { description?: string; handler: Handler }) {
			commands.set(name, options);
		},
		registerFlag(name: string, options: { type: string }) {
			flags.set(name, options);
		},
		getFlag(name: string) {
			return flags.has(name) ? flagValues[name] : undefined;
		},
		async emit(event: string, payload: unknown, ctx: unknown) {
			const results = [];
			for (const h of handlers.get(event) ?? []) results.push(await h(payload, ctx));
			return results;
		},
		async runCommand(name: string, args: string, ctx: unknown) {
			const cmd = commands.get(name);
			if (!cmd) throw new Error(`unknown command ${name}`);
			return cmd.handler(args, ctx);
		},
	};
	return { pi, handlers, commands, flags };
}

function fakeCtx(overrides: Record<string, unknown> = {}) {
	return {
		cwd: "/raw/synthetic/cwd",
		mode: "tui",
		hasUI: true,
		signal: undefined,
		ui: {
			notify(message: string, type?: string) {
				notifications.push({ message, type });
			},
			setStatus() {},
		},
		sessionManager: {
			getSessionId: () => "raw-session-id-1",
			getSessionFile: () => "/raw/synthetic/session.jsonl",
		},
		...overrides,
	};
}

function jsonlFiles() {
	return fs.existsSync(tmpDir)
		? fs.readdirSync(tmpDir).filter((f) => f.endsWith(".jsonl"))
		: [];
}

function readRecords(file: string) {
	return fs
		.readFileSync(path.join(tmpDir, file), "utf8")
		.split("\n")
		.filter(Boolean)
		.map((l) => JSON.parse(l));
}

function rawLog() {
	return jsonlFiles()
		.map((f) => fs.readFileSync(path.join(tmpDir, f), "utf8"))
		.join("");
}

// Drive a full session lifecycle and return the parsed records.
async function collect(
	pi: ReturnType<typeof fakePi>["pi"],
	ctx: unknown,
	drive: () => Promise<void>,
) {
	await pi.emit("session_start", { type: "session_start", reason: "startup" }, ctx);
	await drive();
	await pi.emit("session_shutdown", { type: "session_shutdown", reason: "quit" }, ctx);
	const files = jsonlFiles();
	expect(files.length).toBe(1);
	return readRecords(files[0]);
}

function deepFreeze<T>(o: T): T {
	if (o && typeof o === "object" && !Object.isFrozen(o)) {
		for (const v of Object.values(o)) deepFreeze(v);
		Object.freeze(o);
	}
	return o;
}

// ---------------------------------------------------------------------------
// Message fixtures

const SENTINEL = "S3CR3T-SENTINEL-9f4b2c";

function toolResultMsg(overrides: Record<string, unknown> = {}) {
	return {
		role: "toolResult",
		toolCallId: "call-raw-1",
		toolName: "bash",
		isError: false,
		content: [{ type: "text", text: "ok" }],
		timestamp: 1700000000000,
		...overrides,
	};
}

function assistantMsg(overrides: Record<string, unknown> = {}) {
	return {
		role: "assistant",
		content: [{ type: "text", text: "reply" }],
		responseId: "resp-raw-1",
		usage: {
			input: 10,
			output: 20,
			cacheRead: 3,
			cacheWrite: 4,
			totalTokens: 37,
			cost: { input: 0.1, output: 0.2, cacheRead: 0, cacheWrite: 0, total: 0.3 },
		},
		stopReason: "stop",
		timestamp: 1700000001000,
		...overrides,
	};
}

// Eligible compression candidate: ≥2000 chars, success signal, no failure
// or patch markers.
function testOutput(seed = "x") {
	const filler = (seed + " ok test_case passed\n").repeat(120); // ~2500 chars
	return `${filler}12 passed in 0.42s`;
}

function contextEvent(messages: unknown[]) {
	return { type: "context", messages };
}

// ---------------------------------------------------------------------------
// Fake bridges (python scripts written into tmpDir)

function writeBridge(name: string, body: string) {
	const file = path.join(tmpDir, name);
	fs.writeFileSync(file, body, { mode: 0o755 });
	return file;
}

const BRIDGE_OK = `import json,sys
d=json.load(sys.stdin)
print(json.dumps({"schema_version":1,"items":[{"id":i["id"],"text":"SHORT","label":"distill"} for i in d["items"]]}))`;

const BRIDGE_UNCHANGED = `import json,sys
d=json.load(sys.stdin)
print(json.dumps({"schema_version":1,"items":[{"id":i["id"],"text":i["text"],"label":"unchanged"} for i in d["items"]]}))`;

const BRIDGE_BAD_JSON = `print("this is not json")`;

const BRIDGE_EXIT = `import sys
sys.exit(3)`;

const BRIDGE_DUPE = `import json,sys
d=json.load(sys.stdin)
items=[{"id":d["items"][0]["id"],"text":"A","label":"distill"},{"id":d["items"][0]["id"],"text":"B","label":"distill"}]
print(json.dumps({"schema_version":1,"items":items}))`;

const BRIDGE_MISSING_ID = `import json,sys
d=json.load(sys.stdin)
print(json.dumps({"schema_version":1,"items":[{"id":d["items"][0]["id"],"text":"A","label":"distill"}][:-1]}))`;

const BRIDGE_SLEEP = `import time
time.sleep(10)`;

const BRIDGE_SPY = `import sys,os
data=sys.stdin.buffer.read()
open(os.environ["SPY_FILE"],"wb").write(data)
print("not json")`;

// Counts invocations by appending to COUNT_FILE; echoes items unchanged.
const BRIDGE_COUNT = `import json,sys,os
d=json.load(sys.stdin)
open(os.environ["COUNT_FILE"],"a").write("x")
print(json.dumps({"schema_version":1,"items":[{"id":i["id"],"text":i["text"],"label":"unchanged"} for i in d["items"]]}))`;

// Counts invocations, then exits nonzero — never cacheable.
const BRIDGE_COUNT_FAIL = `import sys,os
sys.stdin.buffer.read()
open(os.environ["COUNT_FILE"],"a").write("x")
sys.exit(3)`;

// Compresses only items whose text contains MARK; others pass through.
const BRIDGE_MIXED = `import json,sys
d=json.load(sys.stdin)
print(json.dumps({"schema_version":1,"items":[{"id":i["id"],"text":("SHORT" if "MARK" in i["text"] else i["text"]),"label":"distill"} for i in d["items"]]}))`;

const BRIDGE_EMPTY = `import json,sys,os
d=json.load(sys.stdin)
open(os.environ["COUNT_FILE"],"a").write("x")
print(json.dumps({"schema_version":1,"items":[{"id":i["id"],"text":"","label":"distill"} for i in d["items"]]}))`;

const BRIDGE_LONGER = `import json,sys,os
d=json.load(sys.stdin)
open(os.environ["COUNT_FILE"],"a").write("x")
print(json.dumps({"schema_version":1,"items":[{"id":i["id"],"text":i["text"]+"PAD","label":"distill"} for i in d["items"]]}))`;

// Replacement is input minus 1000 chars: large but strictly shorter.
const BRIDGE_BIG = `import json,sys,os
d=json.load(sys.stdin)
open(os.environ["COUNT_FILE"],"a").write("x")
print(json.dumps({"schema_version":1,"items":[{"id":i["id"],"text":i["text"][:-1000],"label":"distill"} for i in d["items"]]}))`;

function useBridge(script: string) {
	process.env.HIVE_PI_PYTHON = "python3";
	process.env.HIVE_PI_BRIDGE = writeBridge("bridge.py", script);
}

// ---------------------------------------------------------------------------

describe("lifecycle", () => {
	test("default mode is observe: collector_started/session_started/collector_stopped", async () => {
		const { pi } = fakePi();
		const ctx = fakeCtx();
		hiveExtension(pi as never);
		const lines = await collect(pi, ctx, async () => {});
		expect(lines[0].event_type).toBe("collector_started");
		expect(lines[1].event_type).toBe("session_started");
		expect(lines.at(-1).event_type).toBe("collector_stopped");
		expect(lines.at(-1).stats.written).toBeGreaterThanOrEqual(3);
		expect(lines.at(-1).stats.dropped).toBe(0);
		for (const rec of lines) {
			expect(rec.schema_version).toBe(1);
			expect(rec.source).toBe("pi");
			expect(rec.mode).toBe("observe");
			expect(rec.run_id).toMatch(/^r_[0-9a-f]{24}$/);
			expect(rec.event_id).toMatch(/^e_[0-9a-f]{16}$/);
			expect(rec.project_id).toMatch(/^[0-9a-f]{32}$/);
			expect(typeof rec.timestamp_ms).toBe("number");
		}
		// session_id is a salted pseudonym, never the raw id.
		expect(lines[1].session_id).toMatch(/^[0-9a-f]{32}$/);
		expect(rawLog()).not.toContain("raw-session-id-1");
		expect(rawLog()).not.toContain("/raw/synthetic/cwd");
	});

	test("off mode writes nothing and registers no activity", async () => {
		process.env.HIVE_PI_MODE = "off";
		const { pi } = fakePi();
		const ctx = fakeCtx();
		hiveExtension(pi as never);
		await pi.emit("session_start", { type: "session_start", reason: "startup" }, ctx);
		await pi.emit("message_end", { type: "message_end", message: assistantMsg() }, ctx);
		await pi.emit("context", contextEvent([toolResultMsg()]), ctx);
		await pi.emit("session_shutdown", { type: "session_shutdown", reason: "quit" }, ctx);
		expect(jsonlFiles().length).toBe(0);
	});

	test("--hive-mode flag overrides env", async () => {
		process.env.HIVE_PI_MODE = "off";
		const { pi } = fakePi({ "hive-mode": "observe" });
		const ctx = fakeCtx();
		hiveExtension(pi as never);
		const lines = await collect(pi, ctx, async () => {});
		expect(lines[0].mode).toBe("observe");
	});

	test("invalid mode value falls back to observe", async () => {
		process.env.HIVE_PI_MODE = "bogus";
		const { pi } = fakePi();
		const ctx = fakeCtx();
		hiveExtension(pi as never);
		const lines = await collect(pi, ctx, async () => {});
		expect(lines[0].mode).toBe("observe");
	});

	test("unwritable data dir: collector inert, warns once, never throws", async () => {
		const blocker = path.join(tmpDir, "blocker");
		fs.writeFileSync(blocker, "x");
		process.env.HIVE_PI_DATA_DIR = path.join(blocker, "sub");
		const { pi } = fakePi();
		const ctx = fakeCtx();
		hiveExtension(pi as never);
		await pi.emit("session_start", { type: "session_start", reason: "startup" }, ctx);
		await pi.emit("session_start", { type: "session_start", reason: "reload" }, ctx);
		const warns = notifications.filter((n) => n.type === "warning");
		expect(warns.length).toBe(1);
		// Warning must not leak raw error text or paths.
		expect(warns[0].message).not.toContain("blocker");
		expect(warns[0].message).not.toContain("EEXIST");
		expect(warns[0].message).not.toContain("ENOTDIR");
	});

	test("/hive reports mode, logs path, bridge status, and no routing", async () => {
		const { pi } = fakePi();
		const ctx = fakeCtx();
		hiveExtension(pi as never);
		await pi.runCommand("hive", "", ctx);
		expect(notifications.length).toBe(1);
		const msg = notifications[0].message;
		expect(msg).toContain("observe");
		expect(msg).toContain(tmpDir);
		expect(msg).toContain("bridge");
		expect(msg).toContain("routing: none");
	});

	test("/hive works in off mode", async () => {
		process.env.HIVE_PI_MODE = "off";
		const { pi } = fakePi();
		const ctx = fakeCtx();
		hiveExtension(pi as never);
		await pi.runCommand("hive", "", ctx);
		expect(notifications[0].message).toContain("off");
	});
});

describe("model_usage", () => {
	test("assistant message_end emits usage with pseudonymized message_id", async () => {
		const { pi } = fakePi();
		const ctx = fakeCtx();
		hiveExtension(pi as never);
		const lines = await collect(pi, ctx, async () => {
			await pi.emit(
				"message_end",
				{ type: "message_end", message: assistantMsg() },
				ctx,
			);
		});
		const rec = lines.find((l) => l.event_type === "model_usage");
		expect(rec).toBeDefined();
		expect(rec.message_id).toMatch(/^[0-9a-f]{32}$/);
		expect(rec.tokens).toEqual({
			input: 10,
			output: 20,
			cache_read: 3,
			cache_write: 4,
			total: 37,
		});
		expect(rec.cost_estimate).toBe(0.3);
		expect(rec.stop_reason).toBe("stop");
		expect(rawLog()).not.toContain("resp-raw-1");
	});

	test("non-assistant message_end emits nothing", async () => {
		const { pi } = fakePi();
		const ctx = fakeCtx();
		hiveExtension(pi as never);
		const lines = await collect(pi, ctx, async () => {
			await pi.emit(
				"message_end",
				{ type: "message_end", message: { role: "user", content: "hi" } },
				ctx,
			);
			await pi.emit(
				"message_end",
				{ type: "message_end", message: toolResultMsg() },
				ctx,
			);
		});
		expect(lines.filter((l) => l.event_type === "model_usage").length).toBe(0);
	});

	test("missing/non-finite usage stays null, never zero", async () => {
		const { pi } = fakePi();
		const ctx = fakeCtx();
		hiveExtension(pi as never);
		const lines = await collect(pi, ctx, async () => {
			await pi.emit(
				"message_end",
				{
					type: "message_end",
					message: assistantMsg({
						responseId: "resp-raw-2",
						usage: {
							input: Number.NaN,
							output: Number.POSITIVE_INFINITY,
							cacheRead: -5,
							cacheWrite: 1.5,
							totalTokens: -1,
							cost: { total: -0.01 },
						},
						stopReason: "weird-provider-value",
					}),
				},
				ctx,
			);
		});
		const rec = lines.find((l) => l.event_type === "model_usage");
		expect(rec.tokens).toEqual({
			input: null,
			output: null,
			cache_read: null,
			cache_write: null,
			total: null,
		});
		expect(rec.cost_estimate).toBeNull();
		expect(rec.stop_reason).toBeNull();
	});

	test("dedup: responseId+timestamp key — same both replays, different ts differs", async () => {
		const { pi } = fakePi();
		const ctx = fakeCtx();
		hiveExtension(pi as never);
		const lines = await collect(pi, ctx, async () => {
			// Same responseId, different timestamps → distinct pseudonyms
			// (providers reuse small ids like chatcmpl-NNN within a session).
			await pi.emit(
				"message_end",
				{ type: "message_end", message: assistantMsg() },
				ctx,
			);
			await pi.emit(
				"message_end",
				{ type: "message_end", message: assistantMsg({ timestamp: 1700000002000 }) },
				ctx,
			);
			await pi.emit(
				"message_end",
				{ type: "message_end", message: assistantMsg({ responseId: "resp-raw-9" }) },
				ctx,
			);
			// Replay of the same responseId+timestamp → same pseudonym.
			await pi.emit(
				"message_end",
				{ type: "message_end", message: assistantMsg() },
				ctx,
			);
		});
		const ids = lines
			.filter((l) => l.event_type === "model_usage")
			.map((l) => l.message_id);
		expect(ids.length).toBe(4);
		expect(ids[0]).not.toBe(ids[1]); // same responseId, different ts
		expect(ids[0]).not.toBe(ids[2]); // different responseId
		expect(ids[0]).toBe(ids[3]); // exact replay → stable pseudonym
	});
});

describe("tool_result", () => {
	test("emits category and is_error with pseudonymized call_id", async () => {
		const { pi } = fakePi();
		const ctx = fakeCtx();
		hiveExtension(pi as never);
		const lines = await collect(pi, ctx, async () => {
			await pi.emit(
				"tool_result",
				{
					type: "tool_result",
					toolCallId: "call-raw-1",
					toolName: "bash",
					input: { command: SENTINEL },
					content: [{ type: "text", text: SENTINEL }],
					isError: false,
				},
				ctx,
			);
			await pi.emit(
				"tool_result",
				{
					type: "tool_result",
					toolCallId: "call-raw-2",
					toolName: "read",
					input: {},
					content: [],
					isError: true,
				},
				ctx,
			);
		});
		const recs = lines.filter((l) => l.event_type === "tool_result");
		expect(recs.length).toBe(2);
		expect(recs[0].tool_category).toBe("bash");
		expect(recs[0].is_error).toBe(false);
		expect(recs[1].tool_category).toBe("read");
		expect(recs[1].is_error).toBe(true);
		for (const r of recs) expect(r.call_id).toMatch(/^[0-9a-f]{32}$/);
		expect(rawLog()).not.toContain("call-raw-1");
		expect(rawLog()).not.toContain(SENTINEL);
	});
});

describe("context hook — observe mode", () => {
	test("emits context row with bridge_status inactive and never spawns bridge", async () => {
		const spyFile = path.join(tmpDir, "spy.stdin");
		process.env.SPY_FILE = spyFile;
		useBridge(BRIDGE_SPY);
		const { pi } = fakePi();
		const ctx = fakeCtx();
		hiveExtension(pi as never);
		const lines = await collect(pi, ctx, async () => {
			const res = await pi.emit(
				"context",
				contextEvent([
					toolResultMsg({ content: [{ type: "text", text: testOutput() }] }),
					toolResultMsg({ toolCallId: "call-raw-2" }),
				]),
				ctx,
			);
			expect(res).toEqual([undefined]); // no message replacement
		});
		const rec = lines.find((l) => l.event_type === "context");
		expect(rec.bridge_status).toBe("inactive");
		expect(rec.input_bytes).toBe(0);
		expect(rec.output_bytes).toBe(0);
		expect(rec.changed_items).toBe(0);
		expect(fs.existsSync(spyFile)).toBe(false);
		delete process.env.SPY_FILE;
	});
});

describe("context hook — compress mode", () => {
	function compressSetup(bridge: string) {
		process.env.HIVE_PI_MODE = "compress";
		useBridge(bridge);
		const { pi } = fakePi();
		const ctx = fakeCtx();
		hiveExtension(pi as never);
		return { pi, ctx };
	}

	function eligibleMessages() {
		return [
			toolResultMsg({
				toolCallId: "call-raw-old",
				content: [{ type: "text", text: testOutput() }],
			}),
			toolResultMsg({ toolCallId: "call-raw-latest" }), // latest: excluded
		];
	}

	test("eligible bash test output is compressed via bridge, cloned not mutated", async () => {
		const { pi, ctx } = compressSetup(BRIDGE_OK);
		const messages = eligibleMessages();
		const original = messages[0].content[0].text;
		let contextResult: unknown;
		const lines = await collect(pi, ctx, async () => {
			const res = await pi.emit("context", contextEvent(messages), ctx);
			contextResult = res[0];
		});
		const result = contextResult as { messages: { content: { text: string }[] }[] };
		expect(result.messages[0].content[0].text).toBe("SHORT");
		// Original array and message object untouched.
		expect(messages[0].content[0].text).toBe(original);
		expect(result.messages[0]).not.toBe(messages[0]);
		expect(result.messages[1]).toBe(messages[1]); // unchanged: same ref
		const rec = lines.find((l) => l.event_type === "context");
		expect(rec.bridge_status).toBe("ok");
		expect(rec.changed_items).toBe(1);
		expect(rec.input_bytes).toBeGreaterThan(2000);
		expect(rec.output_bytes).toBe(5); // "SHORT"
	});

	test("latest toolResult is never a candidate", async () => {
		const { pi, ctx } = compressSetup(BRIDGE_OK);
		const messages = [
			toolResultMsg({
				toolCallId: "call-raw-only",
				content: [{ type: "text", text: testOutput() }],
			}),
		];
		const lines = await collect(pi, ctx, async () => {
			const res = await pi.emit("context", contextEvent(messages), ctx);
			expect(res).toEqual([undefined]);
		});
		expect(lines.find((l) => l.event_type === "context").bridge_status).toBe("skipped");
	});

	test("no compression for error/undefined-isError, images, non-bash, short, failures, patches, source", async () => {
		const { pi, ctx } = compressSetup(BRIDGE_OK);
		const cases = [
			toolResultMsg({
				isError: true,
				content: [{ type: "text", text: testOutput() }],
			}),
			toolResultMsg({
				isError: undefined, // contract requires strictly false
				content: [{ type: "text", text: testOutput() }],
			}),
			toolResultMsg({
				content: [
					{ type: "text", text: testOutput() },
					{ type: "image", data: "AAAA", mimeType: "image/png" },
				],
			}),
			toolResultMsg({
				toolName: "read",
				content: [{ type: "text", text: testOutput() }],
			}),
			toolResultMsg({ content: [{ type: "text", text: "short passed" }] }),
			toolResultMsg({
				content: [{ type: "text", text: `${testOutput()}\n3 failed` }],
			}),
			toolResultMsg({
				content: [{ type: "text", text: `${testOutput()}\ndiff --git a/x b/x` }],
			}),
			toolResultMsg({
				content: [
					{ type: "text", text: `${testOutput()}\ndef helper(x):\n    return x` },
				],
			}),
		];
		const lines = await collect(pi, ctx, async () => {
			for (const msg of cases) {
				const res = await pi.emit(
					"context",
					contextEvent([msg, toolResultMsg({ toolCallId: "call-raw-latest" })]),
					ctx,
				);
				expect(res).toEqual([undefined]);
			}
		});
		const contexts = lines.filter((l) => l.event_type === "context");
		expect(contexts.length).toBe(cases.length);
		for (const rec of contexts) expect(rec.bridge_status).toBe("skipped");
	});

	test("bridge failure modes fall back to original messages", async () => {
		for (const script of [BRIDGE_BAD_JSON, BRIDGE_EXIT, BRIDGE_DUPE, BRIDGE_MISSING_ID]) {
			const { pi, ctx } = compressSetup(script);
			const messages = eligibleMessages();
			await pi.emit("session_start", { type: "session_start", reason: "startup" }, ctx);
			const res = await pi.emit("context", contextEvent(messages), ctx);
			expect(res).toEqual([undefined]);
			await pi.emit("session_shutdown", { type: "session_shutdown", reason: "quit" }, ctx);
		}
		// Each iteration is a fresh extension instance → one run file each.
		const contexts = jsonlFiles()
			.flatMap((f) => readRecords(f))
			.filter((l) => l.event_type === "context");
		expect(contexts.length).toBe(4);
		for (const rec of contexts) {
			expect(rec.bridge_status).toBe("error");
			expect(rec.changed_items).toBe(0);
		}
	});

	test("missing bridge script → error status, messages unchanged", async () => {
		process.env.HIVE_PI_MODE = "compress";
		process.env.HIVE_PI_PYTHON = "python3";
		process.env.HIVE_PI_BRIDGE = path.join(tmpDir, "does-not-exist.py");
		const { pi } = fakePi();
		const ctx = fakeCtx();
		hiveExtension(pi as never);
		const lines = await collect(pi, ctx, async () => {
			const res = await pi.emit("context", contextEvent(eligibleMessages()), ctx);
			expect(res).toEqual([undefined]);
		});
		expect(lines.find((l) => l.event_type === "context").bridge_status).toBe("error");
	});

	test("bridge timeout → timeout status, messages unchanged", async () => {
		const { pi, ctx } = compressSetup(BRIDGE_SLEEP);
		const lines = await collect(pi, ctx, async () => {
			const res = await pi.emit("context", contextEvent(eligibleMessages()), ctx);
			expect(res).toEqual([undefined]);
		});
		expect(lines.find((l) => l.event_type === "context").bridge_status).toBe(
			"timeout",
		);
	}, 15000);

	test("unchanged labels produce ok status with zero changed items", async () => {
		const { pi, ctx } = compressSetup(BRIDGE_UNCHANGED);
		const lines = await collect(pi, ctx, async () => {
			const res = await pi.emit("context", contextEvent(eligibleMessages()), ctx);
			expect(res).toEqual([undefined]); // nothing changed → no replacement
		});
		const rec = lines.find((l) => l.event_type === "context");
		expect(rec.bridge_status).toBe("ok");
		expect(rec.changed_items).toBe(0);
	});

	test("context hook never mutates frozen input messages", async () => {
		const { pi, ctx } = compressSetup(BRIDGE_OK);
		const messages = deepFreeze(eligibleMessages());
		const res = await pi.emit("context", contextEvent(messages), ctx);
		expect(res[0]).toBeDefined();
		expect(messages[0].content[0].text.length).toBeGreaterThan(2000);
	});

	test("compression works when storage is unavailable (fail-open)", async () => {
		const blocker = path.join(tmpDir, "blocker");
		fs.writeFileSync(blocker, "x");
		process.env.HIVE_PI_DATA_DIR = path.join(blocker, "sub");
		const { pi, ctx } = compressSetup(BRIDGE_OK);
		// session_start fails to open the sink → warn once, collector inert.
		await pi.emit("session_start", { type: "session_start", reason: "startup" }, ctx);
		expect(notifications.filter((n) => n.type === "warning").length).toBe(1);
		// The context hook still compresses: bridge path is not gated on sink.
		const res = await pi.emit("context", contextEvent(eligibleMessages()), ctx);
		expect((res[0] as { messages: { content: { text: string }[] }[] }).messages[0].content[0].text).toBe("SHORT");
		expect(jsonlFiles().length).toBe(0);
	});
});

describe("bridge result cache", () => {
	function cacheSetup(bridge: string) {
		process.env.HIVE_PI_MODE = "compress";
		process.env.COUNT_FILE = path.join(tmpDir, "calls.count");
		useBridge(bridge);
		const { pi } = fakePi();
		const ctx = fakeCtx();
		hiveExtension(pi as never);
		return { pi, ctx };
	}

	function callCount() {
		return fs.existsSync(process.env.COUNT_FILE as string)
			? fs.readFileSync(process.env.COUNT_FILE as string, "utf8").length
			: 0;
	}

	function msgs(text: string) {
		return [
			toolResultMsg({
				toolCallId: "call-raw-old",
				content: [{ type: "text", text }],
			}),
			toolResultMsg({ toolCallId: "call-raw-latest" }),
		];
	}

	test("identical source invokes the bridge once; second emit is a cache hit", async () => {
		const { pi, ctx } = cacheSetup(BRIDGE_COUNT);
		const lines = await collect(pi, ctx, async () => {
			await pi.emit("context", contextEvent(msgs(testOutput("a"))), ctx);
			await pi.emit("context", contextEvent(msgs(testOutput("a"))), ctx);
		});
		expect(callCount()).toBe(1);
		const recs = lines.filter((l) => l.event_type === "context");
		expect(recs[0].bridge_calls).toBe(1);
		expect(recs[0].cache_misses).toBe(1);
		expect(recs[0].cache_hits).toBe(0);
		expect(recs[1].bridge_calls).toBe(0);
		expect(recs[1].cache_hits).toBe(1);
		expect(recs[1].cache_misses).toBe(0);
		expect(recs[1].bridge_status).toBe("ok");
	});

	test("changed source text at the same message id misses the cache", async () => {
		const { pi, ctx } = cacheSetup(BRIDGE_COUNT);
		await collect(pi, ctx, async () => {
			await pi.emit("context", contextEvent(msgs(testOutput("a"))), ctx);
			await pi.emit("context", contextEvent(msgs(testOutput("b"))), ctx);
		});
		expect(callCount()).toBe(2);
	});

	test("cache is per-session: a different session id misses", async () => {
		const { pi, ctx } = cacheSetup(BRIDGE_COUNT);
		const ctx2 = fakeCtx({
			sessionManager: {
				getSessionId: () => "raw-session-id-2",
				getSessionFile: () => "/raw/synthetic/session2.jsonl",
			},
		});
		await collect(pi, ctx, async () => {
			await pi.emit("context", contextEvent(msgs(testOutput("a"))), ctx);
			await pi.emit("context", contextEvent(msgs(testOutput("a"))), ctx2);
		});
		expect(callCount()).toBe(2);
	});

	test("session_start clears the cache", async () => {
		const { pi, ctx } = cacheSetup(BRIDGE_COUNT);
		await pi.emit("session_start", { type: "session_start", reason: "startup" }, ctx);
		await pi.emit("context", contextEvent(msgs(testOutput("a"))), ctx);
		await pi.emit("session_start", { type: "session_start", reason: "resume" }, ctx);
		await pi.emit("context", contextEvent(msgs(testOutput("a"))), ctx);
		expect(callCount()).toBe(2);
	});

	test("in-flight results cannot repopulate a reset session cache", async () => {
		// Real subprocess integration: JS fake timers cannot advance Python.
		// Poll its explicit invocation marker before resetting the session.
		const delayed = BRIDGE_COUNT
			.replace('"text":i["text"]', '"text":"SHORT"')
			.replace("print(json", "import time\ntime.sleep(0.15)\nprint(json");
		const { pi, ctx } = cacheSetup(delayed);
		await pi.emit("session_start", { type: "session_start", reason: "startup" }, ctx);
		const pending = pi.emit("context", contextEvent(msgs(testOutput("a"))), ctx);
		for (let i = 0; callCount() === 0 && i < 200; i++) {
			const { promise, resolve } = Promise.withResolvers<void>();
			setTimeout(resolve, 5);
			await promise;
		}
		expect(callCount()).toBe(1);
		await pi.emit("session_start", { type: "session_start", reason: "resume" }, ctx);
		expect(await pending).toEqual([undefined]);
		await pi.emit("context", contextEvent(msgs(testOutput("a"))), ctx);
		expect(callCount()).toBe(2);
	});

	test("bridge failures are never cached: identical source retries", async () => {
		const { pi, ctx } = cacheSetup(BRIDGE_COUNT_FAIL);
		const lines = await collect(pi, ctx, async () => {
			await pi.emit("context", contextEvent(msgs(testOutput("a"))), ctx);
			await pi.emit("context", contextEvent(msgs(testOutput("a"))), ctx);
		});
		expect(callCount()).toBe(2);
		const recs = lines.filter((l) => l.event_type === "context");
		for (const rec of recs) {
			expect(rec.bridge_status).toBe("error");
			expect(rec.bridge_calls).toBe(1);
			expect(rec.output_bytes).toBe(rec.input_bytes);
		}
	});

	test("concurrent identical hooks spawn one bridge call", async () => {
		const { pi, ctx } = cacheSetup(BRIDGE_COUNT);
		await pi.emit("session_start", { type: "session_start", reason: "startup" }, ctx);
		const [r1, r2] = await Promise.all([
			pi.emit("context", contextEvent(msgs(testOutput("a"))), ctx),
			pi.emit("context", contextEvent(msgs(testOutput("a"))), ctx),
		]);
		expect(callCount()).toBe(1);
		expect(r1).toEqual([undefined]);
		expect(r2).toEqual([undefined]);
	});

	test("unchanged results are cached, not re-bridged", async () => {
		const { pi, ctx } = cacheSetup(BRIDGE_COUNT);
		const lines = await collect(pi, ctx, async () => {
			for (let i = 0; i < 3; i++) {
				const res = await pi.emit(
					"context",
					contextEvent(msgs(testOutput("a"))),
					ctx,
				);
				expect(res).toEqual([undefined]);
			}
		});
		expect(callCount()).toBe(1);
		const recs = lines.filter((l) => l.event_type === "context");
		expect(recs[2].cache_hits).toBe(1);
		expect(recs[2].changed_items).toBe(0);
	});

	test("entry bound: oldest entries evict past 128", async () => {
		const { pi, ctx } = cacheSetup(BRIDGE_COUNT);
		await pi.emit("session_start", { type: "session_start", reason: "startup" }, ctx);
		for (let i = 0; i < 130; i++) {
			await pi.emit("context", contextEvent(msgs(testOutput(`s${i}`))), ctx);
		}
		expect(callCount()).toBe(130);
		// s0 was evicted by the 129th insert → re-emitting it misses.
		await pi.emit("context", contextEvent(msgs(testOutput("s0"))), ctx);
		expect(callCount()).toBe(131);
		// s129 is still resident → hit.
		await pi.emit("context", contextEvent(msgs(testOutput("s129"))), ctx);
		expect(callCount()).toBe(131);
	}, 120000);

	test("byte bound: large replacements evict past 2 MiB", async () => {
		const { pi, ctx } = cacheSetup(BRIDGE_BIG);
		const big = (seed: string) =>
			`${(seed + " ok test_case passed\n").repeat(45000)}12 passed in 0.42s`;
		await pi.emit("session_start", { type: "session_start", reason: "startup" }, ctx);
		await pi.emit("context", contextEvent(msgs(big("a"))), ctx);
		await pi.emit("context", contextEvent(msgs(big("b"))), ctx);
		await pi.emit("context", contextEvent(msgs(big("c"))), ctx);
		expect(callCount()).toBe(3);
		// ~3 × 0.95 MiB cached > 2 MiB → "a" evicted, "c" resident.
		await pi.emit("context", contextEvent(msgs(big("a"))), ctx);
		expect(callCount()).toBe(4);
		await pi.emit("context", contextEvent(msgs(big("c"))), ctx);
		expect(callCount()).toBe(4);
	}, 60000);

	test("empty and longer bridge output never replace originals", async () => {
		for (const script of [BRIDGE_EMPTY, BRIDGE_LONGER]) {
			const { pi, ctx } = cacheSetup(script);
			fs.rmSync(process.env.COUNT_FILE as string, { force: true });
			const messages = msgs(testOutput("a"));
			await pi.emit("session_start", { type: "session_start", reason: "startup" }, ctx);
			const res = await pi.emit("context", contextEvent(messages), ctx);
			expect(res).toEqual([undefined]);
			// The keep-original outcome is cached: no second bridge call.
			await pi.emit("context", contextEvent(msgs(testOutput("a"))), ctx);
			expect(callCount()).toBe(1);
			await pi.emit("session_shutdown", { type: "session_shutdown", reason: "quit" }, ctx);
		}
	});

	test("output_bytes counts all candidate result bytes incl. unchanged", async () => {
		const { pi, ctx } = cacheSetup(BRIDGE_MIXED);
		const unchangedText = testOutput("keep");
		const messages = [
			toolResultMsg({
				toolCallId: "call-raw-1",
				content: [{ type: "text", text: `${testOutput("m")}MARK` }],
			}),
			toolResultMsg({
				toolCallId: "call-raw-2",
				content: [{ type: "text", text: unchangedText }],
			}),
			toolResultMsg({ toolCallId: "call-raw-latest" }),
		];
		const lines = await collect(pi, ctx, async () => {
			const res = await pi.emit("context", contextEvent(messages), ctx);
			const out = (res[0] as { messages: { content: { text: string }[] }[] })
				.messages;
			expect(out[0].content[0].text).toBe("SHORT");
			expect(out[1].content[0].text).toBe(unchangedText);
		});
		const rec = lines.find((l) => l.event_type === "context");
		expect(rec.changed_items).toBe(1);
		expect(rec.output_bytes).toBe(
			5 + Buffer.byteLength(unchangedText, "utf8"),
		);
		expect(rec.input_bytes).toBeGreaterThan(rec.output_bytes);
	});

	test("/hive status reports calls/hits/misses", async () => {
		const { pi, ctx } = cacheSetup(BRIDGE_COUNT);
		await pi.emit("session_start", { type: "session_start", reason: "startup" }, ctx);
		await pi.emit("context", contextEvent(msgs(testOutput("a"))), ctx);
		await pi.emit("context", contextEvent(msgs(testOutput("a"))), ctx);
		await pi.runCommand("hive", "", ctx);
		const note = notifications.find((n) => n.message.includes("bridge status"));
		expect(note?.message).toContain("calls=1");
		expect(note?.message).toContain("hits=1");
		expect(note?.message).toContain("misses=1");
	});
});

describe("prefilter — pytest progress ids and unittest", () => {
	function prefilterSetup() {
		process.env.HIVE_PI_MODE = "compress";
		process.env.COUNT_FILE = path.join(tmpDir, "calls.count");
		useBridge(BRIDGE_COUNT);
		const { pi } = fakePi();
		const ctx = fakeCtx();
		hiveExtension(pi as never);
		return { pi, ctx };
	}

	function callCount() {
		return fs.existsSync(process.env.COUNT_FILE as string)
			? fs.readFileSync(process.env.COUNT_FILE as string, "utf8").length
			: 0;
	}

	function msgs(text: string) {
		return [
			toolResultMsg({
				toolCallId: "call-raw-old",
				content: [{ type: "text", text }],
			}),
			toolResultMsg({ toolCallId: "call-raw-latest" }),
		];
	}

	const filler = (seed: string) => (seed + " ok test_case passed\n").repeat(120);

	test("pytest ids with failure words still reach the bridge", async () => {
		const { pi, ctx } = prefilterSetup();
		const log =
			filler("x") +
			"tests/test_x.py::test_login[3 failed attempts] PASSED [ 50%]\n" +
			"tests/test_x.py::test_error_path FAILED PASSED\n" + // ends PASSED → stripped despite FAILED in id
			"tests/test_x.py::test_y SKIPPED [ 75%]\n" +
			"======================= 62 passed in 1.04s =======================";
		await collect(pi, ctx, async () => {
			await pi.emit("context", contextEvent(msgs(log)), ctx);
		});
		expect(callCount()).toBe(1);
	});

	test("real failure rows and tracebacks are never exempted", async () => {
		const { pi, ctx } = prefilterSetup();
		const bad = [
			`${filler("x")}tests/test_x.py::test_y FAILED [ 50%]\n62 passed in 1.04s`,
			`${filler("x")}FAILED tests/test_x.py::test_y - assert 1 == 2\n62 passed`,
			`${filler("x")}Traceback (most recent call last):\n  File "x.py"\n62 passed`,
			`${filler("x")}tests/test_x.py::test_y PASSED [ 50%]\n2 failed, 60 passed`,
		];
		await collect(pi, ctx, async () => {
			for (const log of bad) {
				const res = await pi.emit("context", contextEvent(msgs(log)), ctx);
				expect(res).toEqual([undefined]);
			}
		});
		expect(callCount()).toBe(0);
	});

	test("genuine unittest footer reaches the bridge", async () => {
		const { pi, ctx } = prefilterSetup();
		const log = `${"test case output line\n".repeat(150)}............\n----------------------------------------------------------------------\nRan 12 tests in 0.01s\n\nOK\n`;
		await collect(pi, ctx, async () => {
			await pi.emit("context", contextEvent(msgs(log)), ctx);
		});
		expect(callCount()).toBe(1);
	});

	test("unittest failure footer is still rejected", async () => {
		const { pi, ctx } = prefilterSetup();
		const log = `${"test case output line\n".repeat(150)}............\n----------------------------------------------------------------------\nRan 12 tests in 0.01s\n\nFAILED (failures=1)\n`;
		await collect(pi, ctx, async () => {
			const res = await pi.emit("context", contextEvent(msgs(log)), ctx);
			expect(res).toEqual([undefined]);
		});
		expect(callCount()).toBe(0);
	});
});

describe("storage permissions", () => {
	test("data dir 0700, salt and jsonl 0600, salt stable across instances", async () => {
		const { pi } = fakePi();
		const ctx = fakeCtx();
		hiveExtension(pi as never);
		await collect(pi, ctx, async () => {});
		const dirStat = fs.statSync(tmpDir);
		expect(dirStat.mode & 0o777).toBe(0o700);
		const saltFile = path.join(tmpDir, "salt");
		expect(fs.statSync(saltFile).mode & 0o777).toBe(0o600);
		const salt1 = fs.readFileSync(saltFile, "utf8").trim();
		expect(salt1).toMatch(/^[0-9a-f]{64}$/);
		for (const f of jsonlFiles()) {
			expect(fs.statSync(path.join(tmpDir, f)).mode & 0o777).toBe(0o600);
		}
		// Second instance reuses the same salt → stable pseudonyms.
		const { pi: pi2 } = fakePi();
		hiveExtension(pi2 as never);
		await pi2.emit("session_start", { type: "session_start", reason: "startup" }, ctx);
		await pi2.emit("session_shutdown", { type: "session_shutdown", reason: "quit" }, ctx);
		expect(fs.readFileSync(saltFile, "utf8").trim()).toBe(salt1);
		expect(jsonlFiles().length).toBe(2);
	});
});
