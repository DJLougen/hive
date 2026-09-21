// hive.ts — Hive collector + optional tool-output compression for Pi 0.85.1.
//
// Native Pi extension (default factory export). Modes:
//   off      — fully inert: no files, no events, no bridge calls.
//   observe  — metadata-only JSONL collector (default).
//   compress — observe + a context hook that rewrites eligible historical
//              bash tool-result text through the local Python bridge.
//
// Mode resolution: --hive-mode flag > HIVE_PI_MODE env > "observe".
// Unrecognized values fall back to "observe".
//
// Contract (shared with the Hive report pipeline — do not change
// independently):
//   - JSONL, one record per line. Envelope: {schema_version:1, source:'pi',
//     mode:'observe'|'compress', event_type, event_id, run_id, project_id,
//     session_id, timestamp_ms}. session_id is null when unavailable.
//   - run_id / event_id are random hex; project_id / session_id / call_id /
//     message_id are salted-HMAC pseudonyms (32 hex chars).
//   - event_type ∈ collector_started | session_started | collector_stopped |
//     model_usage | tool_result | context.
//   - collector_started carries stats:{queue_max}; collector_stopped
//     carries stats:{written,dropped}.
//   - model_usage: assistant message_end only; tokens
//     {input,output,cache_read,cache_write,total} each nullable, cost_estimate
//     nullable, stop_reason is the host stopReason verbatim when in
//     {pending,stop,length,toolUse,error,aborted,deferred} else null.
//     Missing or non-finite usage is null, never zero.
//   - tool_result: call_id, tool_category ∈ read|list|grep|bash|edit|write|
//     other, is_error.
//   - context: input_bytes/output_bytes count ALL candidate text payload
//     original/result bytes including unchanged blocks (not token savings;
//     repeated contexts are not unique saved bytes), changed_items,
//     bridge_status ∈ inactive|skipped|ok|error|timeout, and optional
//     nonnegative counters bridge_calls/cache_hits/cache_misses.
//   - Output: $HIVE_PI_DATA_DIR or ~/.local/share/hive/pi/events/
//     <run_id>.jsonl. Dirs 0700, files 0600, stable private salt.
//
// Compression bridge (compress mode only):
//   - $HIVE_PI_PYTHON (default python3) runs $HIVE_PI_BRIDGE (default
//     ../../scripts/hive_pi.py relative to this file) with argv
//     [script, "compress"]; no shell.
//   - stdin: {"schema_version":1,"items":[{"id","text"}]} capped at 128 items
//     and 1 MiB. stdout: {"schema_version":1,"items":[{"id","text","label"}]}
//     capped at 1 MiB. 3s timeout.
//   - Candidates: toolResult messages older than the latest toolResult,
//     toolName "bash", isError false, text-only content blocks (no images),
//     block text ≥ 2000 chars, no patch/source markers, no failure signals,
//     and a successful-test-summary signal. Structurally complete single-line
//     pytest progress records (`path::id PASSED|SKIPPED|XFAIL|XPASS [%]`) are
//     stripped before the failure-word check only — failure rows, tracebacks,
//     and body text are never exempted, and an independent success summary
//     (pytest `N passed` or unittest `Ran N tests` + `OK` footer) is still
//     required. Python re-validates; JS is only a pre-filter.
//   - Successful results are memoized in a per-session bounded LRU
//     (128 entries / 2 MiB of replacement text) keyed by exact source-text
//     fingerprint plus bridge interpreter/path identity. Errors, timeouts,
//     and aborts are never cached; the cache clears on session start,
//     session id switch, and shutdown. Cache lookup, bridge call, and
//     populate are serialized so concurrent identical hooks spawn one call.
//   - Any schema violation, bad/missing/duplicate id, nonzero exit, spawn
//     error, timeout, or abort → the original messages pass through
//     unchanged. Only cloned messages are ever returned; the persisted
//     transcript is never touched.
//
// Privacy: scalar allowlist only. Never serializes prompts, message text,
// tool args/output, paths, diffs, headers, provider credentials, or raw
// error objects. No network, no model calls, no routing. All hooks are
// fail-open: collection or compression failure never affects execution.
// Storage failure warns at most once via UI with a generic message.

import { spawn, type ChildProcess } from "node:child_process";
import { createHmac, randomBytes } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import type {
	ContextEvent,
	ExtensionAPI,
	ExtensionContext,
} from "@earendil-works/pi-coding-agent";

// AgentMessage lives in pi-agent-core and is not re-exported; derive the
// element type from ContextEvent instead of importing the inner package.
type AgentMessage = ContextEvent["messages"][number];

const SCHEMA_VERSION = 1;
const QUEUE_MAX = 4096;
const SALT_FILE = "salt";

const MIN_CANDIDATE_CHARS = 2000;
const MAX_ITEMS = 128;
const MAX_PAYLOAD_BYTES = 1024 * 1024;
const MAX_STDOUT_BYTES = 1024 * 1024;
const BRIDGE_TIMEOUT_MS = 3000;
const CACHE_MAX_ENTRIES = 128;
const CACHE_MAX_BYTES = 2 * 1024 * 1024;

const MODES: Record<string, true> = { off: true, observe: true, compress: true };
const STOP_REASONS: Record<string, true> = {
	pending: true,
	stop: true,
	length: true,
	toolUse: true,
	error: true,
	aborted: true,
	deferred: true,
};

const TOOL_CATEGORY: Record<string, string> = {
	read: "read",
	glob: "list",
	find: "list",
	ls: "list",
	list: "list",
	grep: "grep",
	search: "grep",
	bash: "bash",
	shell: "bash",
	powershell: "bash",
	edit: "edit",
	patch: "edit",
	apply_patch: "edit",
	write: "write",
};

// Candidate pre-filter signals. Python is the authority; these only avoid
// spawning the bridge for text that is obviously ineligible. Source/patch
// markers reject outright — source-like output is never sent to the bridge.
const FAILURE_HINT =
	/\bnot ok\b|\bFAILED\b|\bFAIL:|\bTraceback \(most recent call last\)|\bAssertionError\b|\b[1-9]\d*\s+(?:failed|failures?|errors?)\b/i;
const SUCCESS_HINT =
	/\b\d+\s+(?:tests?\s+)?pass(?:ed)?\b|\bpassed\b|\bPASS\b|\bsuccessful\b|\bsucceeded\b|\bok\s+\S/i;
// Genuine unittest footer: both a `Ran N tests` line and a final
// `OK`/`OK (…)` line (uppercase, as unittest emits). Python decides
// structure; this only lets unittest logs reach the bridge.
const UNITTEST_RAN_HINT = /^Ran [1-9]\d* tests?\b/m;
const UNITTEST_OK_HINT = /^OK(?:\s*\([^)\n]*\))?\s*$/m;
// Structurally complete single-line pytest progress record, e.g.
// `tests/test_x.py::test_y[3 failed] PASSED [ 50%]`. Param ids may contain
// spaces and failure words; only lines ending in a pass-family status are
// stripped before the failure-word check — FAILED/ERROR rows, tracebacks,
// and body text are never exempted. Grammar shared with hive_pi.py.
const PYTEST_PROGRESS_LINE =
	/^\s*\S+::.*\s+(?:PASSED|SKIPPED|XFAIL|XPASS)\s*(?:\[\s*\d{1,3}%\])?\s*$/gm;
const PATCH_HINT =
	/(^|\n)(diff --git |@@ -\d|Index: |--- a\/|\+\+\+ b\/|#!\/)/;
const SOURCE_HINT =
	/(^|\n)(```|def [A-Za-z_]\w*\s*\(|class [A-Za-z_]\w*|function [A-Za-z_]\w*\s*\(|#include\s*[<"]|package\s+[a-z][\w.]*\s*;|public\s+(?:class|static)|import\s+[a-zA-Z_][\w.]*|from\s+\w[\w.]*\s+import\b)/;

function dataDir(): string {
	const override = process.env.HIVE_PI_DATA_DIR;
	if (typeof override === "string" && override.length > 0) return override;
	return path.join(os.homedir(), ".local", "share", "hive", "pi", "events");
}

function pythonBin(): string {
	const v = process.env.HIVE_PI_PYTHON;
	return typeof v === "string" && v.length > 0 ? v : "python3";
}

function bridgePath(): string {
	const v = process.env.HIVE_PI_BRIDGE;
	if (typeof v === "string" && v.length > 0) return v;
	try {
		const here = path.dirname(fileURLToPath(import.meta.url));
		return path.resolve(here, "..", "..", "scripts", "hive_pi.py");
	} catch {
		return "scripts/hive_pi.py";
	}
}

function loadOrCreateSalt(dir: string): string {
	const file = path.join(dir, SALT_FILE);
	try {
		const existing = fs.readFileSync(file, "utf8").trim();
		if (/^[0-9a-f]{64}$/.test(existing)) return existing;
	} catch {
		// absent or unreadable — create below
	}
	const salt = randomBytes(32).toString("hex");
	try {
		fs.writeFileSync(file, salt + "\n", { mode: 0o600, flag: "wx" });
		return salt;
	} catch (e) {
		// Another instance won the create race: use its salt.
		if (e && (e as NodeJS.ErrnoException).code === "EEXIST") {
			const raced = fs.readFileSync(file, "utf8").trim();
			if (/^[0-9a-f]{64}$/.test(raced)) return raced;
		}
		throw e;
	}
}

function num(v: unknown): number | null {
	return typeof v === "number" && Number.isFinite(v) ? v : null;
}

// Token counters: nonnegative safe integers only — negative or fractional
// usage is malformed and must read as null, never zero.
function numTokens(v: unknown): number | null {
	return typeof v === "number" && Number.isSafeInteger(v) && v >= 0 ? v : null;
}

// Cost estimate: nonnegative finite number only.
function numCost(v: unknown): number | null {
	return typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : null;
}

function toolCategory(name: unknown): string {
	return typeof name === "string"
		? (TOOL_CATEGORY[name.toLowerCase()] ?? "other")
		: "other";
}

interface Sink {
	enqueue(record: Record<string, unknown>): void;
	finish(finalRecord: Record<string, unknown>): void;
	file: string;
}

function createSink(dir: string, runId: string): Sink {
	const file = path.join(dir, `${runId}.jsonl`);
	const fd = fs.openSync(file, "a", 0o600);

	const queue: string[] = [];
	let scheduled = false;
	let closed = false;
	let written = 0;
	let dropped = 0;

	function drain() {
		scheduled = false;
		while (queue.length > 0 && !closed) {
			const line = queue.shift() as string;
			try {
				fs.writeSync(fd, line);
				written += 1;
			} catch {
				// contained: a failed write drops the record
				dropped += 1;
			}
		}
	}

	function enqueue(record: Record<string, unknown>) {
		if (closed || queue.length >= QUEUE_MAX) {
			dropped += 1;
			return;
		}
		queue.push(JSON.stringify(record) + "\n");
		if (!scheduled) {
			scheduled = true;
			setImmediate(drain);
		}
	}

	// Terminal path: flush queued records, then write the final record
	// directly so collector_stopped is never dropped at QUEUE_MAX.
	// stats.written counts this final record itself.
	function finish(finalRecord: Record<string, unknown>) {
		drain();
		closed = true;
		try {
			finalRecord.stats = { written: written + 1, dropped };
			fs.writeSync(fd, JSON.stringify(finalRecord) + "\n");
			written += 1;
		} catch {
			// contained
		}
		try {
			fs.closeSync(fd);
		} catch {
			// already closed
		}
	}

	return { enqueue, finish, file };
}

// Eligible: toolResult messages strictly older than the latest toolResult,
// bash only, not errors, text-only blocks (any image block disqualifies the
// message), block text ≥ MIN_CANDIDATE_CHARS, no patch/source markers, no
// failure signals, and a successful-test-summary signal. Pytest progress
// records are stripped before the failure/success checks so test ids with
// failure words don't disqualify a clean run; the success signal must come
// from the remaining text (or a genuine unittest footer).
interface Candidate {
	id: string;
	msgIdx: number;
	blockIdx: number;
	text: string;
	bytes: number;
}

function collectCandidates(messages: AgentMessage[]): Candidate[] {
	let lastToolResult = -1;
	for (let i = messages.length - 1; i >= 0; i--) {
		const m = messages[i] as { role?: string } | undefined;
		if (m && m.role === "toolResult") {
			lastToolResult = i;
			break;
		}
	}

	const items: Candidate[] = [];
	let payloadBytes = 0;
	for (let i = 0; i < messages.length; i++) {
		if (i === lastToolResult) continue;
		const m = messages[i] as
			| {
					role?: string;
					toolName?: string;
					isError?: boolean;
					content?: unknown;
			  }
			| undefined;
		if (!m || m.role !== "toolResult") continue;
		if (m.toolName !== "bash" || m.isError !== false) continue;
		const content = m.content;
		if (!Array.isArray(content) || content.length === 0) continue;
		if (
			!content.every(
				(b) =>
					b &&
					typeof b === "object" &&
					(b as { type?: string }).type === "text" &&
					typeof (b as { text?: unknown }).text === "string",
			)
		) {
			continue; // images or non-text blocks: never compress
		}
		for (let j = 0; j < content.length; j++) {
			const text = (content[j] as { text: string }).text;
			if (text.length < MIN_CANDIDATE_CHARS) continue;
			if (PATCH_HINT.test(text) || SOURCE_HINT.test(text)) continue;
			// Strip structurally complete pytest progress records so test
			// ids containing failure words (e.g. `test_x[3 failed] PASSED`)
			// don't disqualify a clean run. Failure rows, tracebacks, and
			// body text never match the progress grammar and still trip
			// FAILURE_HINT below.
			const stripped = text.replace(PYTEST_PROGRESS_LINE, "");
			if (FAILURE_HINT.test(stripped)) continue;
			if (
				!SUCCESS_HINT.test(stripped) &&
				!(UNITTEST_RAN_HINT.test(text) && UNITTEST_OK_HINT.test(text))
			) {
				continue;
			}
			const bytes = Buffer.byteLength(text, "utf8");
			if (items.length >= MAX_ITEMS || payloadBytes + bytes > MAX_PAYLOAD_BYTES) {
				return items;
			}
			items.push({ id: `m${i}b${j}`, msgIdx: i, blockIdx: j, text, bytes });
			payloadBytes += bytes;
		}
	}
	return items;
}

type BridgeStatus = "inactive" | "skipped" | "ok" | "error" | "timeout";

interface BridgeResult {
	status: BridgeStatus;
	texts?: Map<string, string>;
}

// Response is valid only when every sent id comes back exactly once with
// string text and label. Anything else → caller keeps original messages.
function validateBridgeResponse(
	parsed: unknown,
	sentIds: Set<string>,
): Map<string, string> | null {
	if (!parsed || typeof parsed !== "object") return null;
	const obj = parsed as { schema_version?: unknown; items?: unknown };
	if (obj.schema_version !== 1 || !Array.isArray(obj.items)) return null;
	if (obj.items.length !== sentIds.size) return null;
	const texts = new Map<string, string>();
	for (const item of obj.items) {
		if (!item || typeof item !== "object") return null;
		const { id, text, label } = item as {
			id?: unknown;
			text?: unknown;
			label?: unknown;
		};
		if (typeof id !== "string" || typeof text !== "string" || typeof label !== "string") {
			return null;
		}
		if (!sentIds.has(id) || texts.has(id)) return null;
		texts.set(id, text);
	}
	return texts;
}

// Exact argv, no shell. Bounded: 3s timeout, 1MiB stdout cap, abort-aware.
function runBridge(
	body: string,
	sentIds: Set<string>,
	signal: AbortSignal | undefined,
): Promise<BridgeResult> {
	const { promise, resolve } = Promise.withResolvers<BridgeResult>();
	let child: ChildProcess;
	try {
		child = spawn(pythonBin(), [bridgePath(), "compress"], {
			stdio: ["pipe", "pipe", "pipe"],
		});
	} catch {
		resolve({ status: "error" });
		return promise;
	}

	const chunks: Buffer[] = [];
	let outLen = 0;
	let done = false;

	const finish = (status: BridgeStatus, texts?: Map<string, string>) => {
		if (done) return;
		done = true;
		clearTimeout(timer);
		if (signal) signal.removeEventListener("abort", onAbort);
		try {
			child.kill("SIGKILL");
		} catch {
			// already exited
		}
		resolve({ status, texts });
	};
	const onAbort = () => finish("error");
	const timer = setTimeout(() => finish("timeout"), BRIDGE_TIMEOUT_MS);

	child.on("error", () => finish("error"));
	child.stdout?.on("data", (c: Buffer) => {
		outLen += c.length;
		if (outLen > MAX_STDOUT_BYTES) {
			finish("error");
			return;
		}
		chunks.push(c);
	});
	child.stdout?.on("error", () => {});
	child.stderr?.on("data", () => {}); // drain so the child never blocks
	child.stderr?.on("error", () => {});
	child.stdin?.on("error", () => {});
	child.on("close", (code) => {
		if (done) return;
		if (code !== 0) return finish("error");
		try {
			const parsed = JSON.parse(Buffer.concat(chunks).toString("utf8"));
			const texts = validateBridgeResponse(parsed, sentIds);
			if (!texts) return finish("error");
			finish("ok", texts);
		} catch {
			finish("error");
		}
	});

	if (signal) {
		if (signal.aborted) {
			finish("error");
			return promise;
		}
		signal.addEventListener("abort", onAbort, { once: true });
	}
	try {
		child.stdin?.write(body);
		child.stdin?.end();
	} catch {
		finish("error");
	}
	return promise;
}

export default function hiveExtension(pi: ExtensionAPI) {
	pi.registerFlag("hive-mode", {
		description: "Hive mode: off | observe | compress (default: observe)",
		type: "string",
	});

	// Flag values are only populated after extension load, so resolve lazily.
	function resolveMode(): { mode: string; source: string } {
		let flag: unknown;
		try {
			flag = pi.getFlag("hive-mode");
		} catch {
			flag = undefined;
		}
		if (typeof flag === "string" && flag.length > 0) {
			const v = flag.trim().toLowerCase();
			return MODES[v]
				? { mode: v, source: "flag" }
				: { mode: "observe", source: "flag (invalid, defaulted)" };
		}
		const env = process.env.HIVE_PI_MODE;
		if (typeof env === "string" && env.length > 0) {
			const v = env.trim().toLowerCase();
			return MODES[v]
				? { mode: v, source: "env" }
				: { mode: "observe", source: "env (invalid, defaulted)" };
		}
		return { mode: "observe", source: "default" };
	}

	const runId = `r_${randomBytes(12).toString("hex")}`;
	let sink: Sink | null = null;
	let hmacKey: string | null = null;
	let projectId: string | null = null;
	let stopped = false;
	let storageWarned = false;
	let autoSeq = 0;
	const bridgeStats = {
		last: "none" as string,
		ok: 0,
		error: 0,
		timeout: 0,
		calls: 0,
		hits: 0,
		misses: 0,
	};
	let bridgeChain: Promise<void> = Promise.resolve();
	// Per-session LRU of applied bridge outcomes keyed by exact source-text
	// fingerprint plus bridge interpreter/path identity. Values hold only
	// the replacement text (null = "keep original"); raw input text is
	// never retained. Bounded by CACHE_MAX_ENTRIES / CACHE_MAX_BYTES.
	const bridgeCache = new Map<string, { text: string | null; bytes: number }>();
	let bridgeCacheBytes = 0;
	let cacheSessionId: string | null = null;
	let cacheGeneration = 0;

	function pseud(kind: string, raw: unknown): string {
		return createHmac("sha256", hmacKey as string)
			.update(`${kind}:${String(raw)}`)
			.digest("hex")
			.slice(0, 32);
	}

	function sessionPseud(ctx: ExtensionContext): string | null {
		try {
			const id = ctx.sessionManager.getSessionId();
			if (typeof id === "string" && id.length > 0) return pseud("session", id);
		} catch {
			// unavailable → null
		}
		return null;
	}

	function envelope(eventType: string, sessionId: string | null) {
		return {
			schema_version: SCHEMA_VERSION,
			source: "pi",
			mode: resolveMode().mode,
			event_type: eventType,
			event_id: `e_${randomBytes(8).toString("hex")}`,
			run_id: runId,
			project_id: projectId,
			session_id: sessionId,
			timestamp_ms: Date.now(),
		};
	}

	function emit(record: Record<string, unknown>) {
		try {
			sink?.enqueue(record);
		} catch {
			// contained: collection failure must never reach the host
		}
	}

	function emitEvent(
		eventType: string,
		ctx: ExtensionContext,
		fields: Record<string, unknown>,
	) {
		if (!sink || stopped) return;
		const rec = envelope(eventType, sessionPseud(ctx));
		Object.assign(rec, fields);
		emit(rec);
	}

	function warnStorageOnce(ctx: ExtensionContext) {
		if (storageWarned) return;
		storageWarned = true;
		try {
			// Generic message only: never the raw error, path, or cause.
			ctx.ui.notify(
				"hive: event log unavailable; metadata collection disabled",
				"warning",
			);
		} catch {
			// contained
		}
	}

	// Lazily create the sink on first use. Returns false when storage is
	// unavailable — the collector is then inert but the host is unaffected.
	function ensureCollector(ctx: ExtensionContext): boolean {
		if (sink && !stopped) return true;
		if (stopped) return false;
		try {
			const dir = dataDir();
			fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
			try {
				fs.chmodSync(dir, 0o700);
			} catch {
				// best effort
			}
			hmacKey = loadOrCreateSalt(dir);
			projectId = pseud("project", ctx.cwd ?? "unknown");
			sink = createSink(dir, runId);
			const started = envelope("collector_started", null);
			started.stats = { queue_max: QUEUE_MAX };
			sink.enqueue(started);
			return true;
		} catch {
			warnStorageOnce(ctx);
			return false;
		}
	}

	function recordBridge(status: BridgeStatus) {
		bridgeStats.last = status;
		if (status === "ok") bridgeStats.ok += 1;
		else if (status === "error") bridgeStats.error += 1;
		else if (status === "timeout") bridgeStats.timeout += 1;
	}

	function emitContext(
		ctx: ExtensionContext,
		status: BridgeStatus,
		inputBytes: number,
		outputBytes: number,
		changedItems: number,
		counters: { calls: number; hits: number; misses: number },
	) {
		emitEvent("context", ctx, {
			input_bytes: inputBytes,
			output_bytes: outputBytes,
			changed_items: changedItems,
			bridge_status: status,
			bridge_calls: counters.calls,
			cache_hits: counters.hits,
			cache_misses: counters.misses,
		});
	}

	function clearBridgeCache() {
		cacheGeneration += 1;
		bridgeCache.clear();
		bridgeCacheBytes = 0;
	}

	// Clear on session start/switch/shutdown: the cache is per-session.
	function syncCacheSession(ctx: ExtensionContext) {
		let id: string | null = null;
		try {
			const raw = ctx.sessionManager.getSessionId();
			if (typeof raw === "string" && raw.length > 0) id = raw;
		} catch {
			// unavailable → null
		}
		if (id !== cacheSessionId) {
			clearBridgeCache();
			cacheSessionId = id;
		}
	}

	function cacheKey(text: string): string {
		const fp = createHmac("sha256", "hive-pi-cache")
			.update(text)
			.digest("hex");
		return JSON.stringify([pythonBin(), bridgePath(), fp]);
	}

	function cacheGet(key: string): { hit: boolean; text: string | null } {
		const e = bridgeCache.get(key);
		if (e === undefined) return { hit: false, text: null };
		// LRU refresh: re-insert moves the entry to the newest position.
		bridgeCache.delete(key);
		bridgeCache.set(key, e);
		return { hit: true, text: e.text };
	}

	function cacheSet(key: string, text: string | null) {
		const old = bridgeCache.get(key);
		if (old !== undefined) {
			bridgeCacheBytes -= old.bytes;
			bridgeCache.delete(key);
		}
		const bytes = text === null ? 0 : Buffer.byteLength(text, "utf8");
		// An oversized replacement is never cached.
		if (bytes <= CACHE_MAX_BYTES) {
			bridgeCache.set(key, { text, bytes });
			bridgeCacheBytes += bytes;
		}
		while (
			bridgeCache.size > CACHE_MAX_ENTRIES ||
			bridgeCacheBytes > CACHE_MAX_BYTES
		) {
			const oldest = bridgeCache.keys().next();
			if (oldest.done) break;
			const e = bridgeCache.get(oldest.value);
			if (e) bridgeCacheBytes -= e.bytes;
			bridgeCache.delete(oldest.value);
		}
	}

	interface ResolveResult {
		status: BridgeStatus;
		texts?: Map<string, string | null>;
		calls: number;
		hits: number;
		misses: number;
	}

	// Serialized lookup → bridge → populate. Concurrent identical hooks
	// queue behind the in-flight resolve and hit the populated cache, so
	// duplicate bridge spawns cannot happen. Errors/timeouts/aborts are
	// never cached; only the applied outcome (shorter non-empty
	// replacement, or null for keep-original) is stored.
	function resolveInner(
		candidates: Candidate[],
		signal: AbortSignal | undefined,
		generation: number,
	): Promise<ResolveResult> {
		const texts = new Map<string, string | null>();
		const misses: { c: Candidate; key: string }[] = [];
		let hits = 0;
		for (const c of candidates) {
			const key = cacheKey(c.text);
			const e = cacheGet(key);
			if (e.hit) {
				texts.set(c.id, e.text);
				hits += 1;
			} else {
				misses.push({ c, key });
			}
		}
		if (misses.length === 0) {
			return Promise.resolve({
				status: "ok",
				texts,
				calls: 0,
				hits,
				misses: 0,
			});
		}
		if (signal?.aborted) {
			return Promise.resolve({
				status: "error",
				calls: 0,
				hits,
				misses: misses.length,
			});
		}
		const body = JSON.stringify({
			schema_version: 1,
			items: misses.map((m) => ({ id: m.c.id, text: m.c.text })),
		});
		const sentIds = new Set(misses.map((m) => m.c.id));
		return runBridge(body, sentIds, signal).then((res) => {
			if (signal?.aborted || generation !== cacheGeneration) {
				return { status: "error", calls: 1, hits, misses: misses.length };
			}
			if (res.status !== "ok" || !res.texts) {
				return {
					status: res.status,
					calls: 1,
					hits,
					misses: misses.length,
				};
			}
			for (const m of misses) {
				const next = res.texts.get(m.c.id);
				// Tightened application: empty, non-shorter, or missing
				// bridge output never replaces the original and is cached
				// as keep-original so it is not retried every turn.
				const applied =
					typeof next === "string" &&
					next !== m.c.text &&
					next.length > 0 &&
					Buffer.byteLength(next, "utf8") < m.c.bytes
						? next
						: null;
				texts.set(m.c.id, applied);
				cacheSet(m.key, applied);
			}
			return { status: "ok", texts, calls: 1, hits, misses: misses.length };
		});
	}

	function resolveCandidates(
		candidates: Candidate[],
		signal: AbortSignal | undefined,
	): Promise<ResolveResult> {
		const generation = cacheGeneration;
		const p: Promise<ResolveResult> = bridgeChain.then(() => {
			if (signal?.aborted || generation !== cacheGeneration) {
				return { status: "error", calls: 0, hits: 0, misses: 0 };
			}
			return resolveInner(candidates, signal, generation);
		});
		bridgeChain = p.then(
			() => {},
			() => {},
		);
		return p;
	}
	pi.on("session_start", async (_event, ctx) => {
		try {
			clearBridgeCache();
			cacheSessionId = null;
			if (resolveMode().mode === "off") return;
			if (!ensureCollector(ctx)) return;
			emitEvent("session_started", ctx, {});
		} catch {
			// contained
		}
	});

	pi.on("session_shutdown", async (_event, ctx) => {
		try {
			clearBridgeCache();
			cacheSessionId = null;
			if (!sink || stopped) return;
			stopped = true;
			sink.finish(envelope("collector_stopped", sessionPseud(ctx)));
		} catch {
			// contained
		}
	});

	pi.on("message_end", (event, ctx) => {
		try {
			if (resolveMode().mode === "off" || !sink || stopped) return;
			const m = event?.message as
				| {
						role?: string;
						responseId?: string;
						timestamp?: number;
						stopReason?: string;
						usage?: {
							input?: number;
							output?: number;
							cacheRead?: number;
							cacheWrite?: number;
							totalTokens?: number;
							cost?: { total?: number };
						};
				  }
				| undefined;
			if (!m || m.role !== "assistant") return;
			const u = m.usage && typeof m.usage === "object" ? m.usage : {};
			const cost = u.cost && typeof u.cost === "object" ? u.cost : {};
			// responseId alone can collide (providers reuse small ids like
			// chatcmpl-NNN across a session) — pair it with the timestamp when
			// both are present so the dedup key stays unique per message.
			const ts = num(m.timestamp);
			const rawId =
				typeof m.responseId === "string" && m.responseId.length > 0
					? ts !== null
						? `${m.responseId}@${ts}`
						: m.responseId
					: ts !== null
						? `ts:${m.timestamp}`
						: `auto:${++autoSeq}`;
			emitEvent("model_usage", ctx, {
				message_id: pseud("message", rawId),
				tokens: {
					input: numTokens(u.input),
					output: numTokens(u.output),
					cache_read: numTokens(u.cacheRead),
					cache_write: numTokens(u.cacheWrite),
					total: numTokens(u.totalTokens),
				},
				cost_estimate: numCost(cost.total),
				stop_reason:
					typeof m.stopReason === "string" && STOP_REASONS[m.stopReason]
						? m.stopReason
						: null,
			});
		} catch {
			// contained
		}
	});

	pi.on("tool_result", (event, ctx) => {
		try {
			if (resolveMode().mode === "off" || !sink || stopped) return;
			emitEvent("tool_result", ctx, {
				call_id: pseud("call", String(event?.toolCallId ?? "")),
				tool_category: toolCategory(event?.toolName),
				is_error: event?.isError === true,
			});
		} catch {
			// contained
		}
	});

	pi.on("context", async (event, ctx) => {
		try {
			const { mode } = resolveMode();
			// Compression is independent of collector storage: a missing sink
			// only suppresses event emits, never the bridge path.
			if (mode === "off") return;
			const zero = { calls: 0, hits: 0, misses: 0 };
			if (mode !== "compress") {
				emitContext(ctx, "inactive", 0, 0, 0, zero);
				return;
			}
			const messages = event?.messages;
			if (!Array.isArray(messages)) {
				emitContext(ctx, "skipped", 0, 0, 0, zero);
				return;
			}
			const candidates = collectCandidates(messages);
			if (candidates.length === 0) {
				emitContext(ctx, "skipped", 0, 0, 0, zero);
				return;
			}
			const inputBytes = candidates.reduce((a, c) => a + c.bytes, 0);
			syncCacheSession(ctx);
			const generation = cacheGeneration;
			const res = await resolveCandidates(candidates, ctx.signal);
			bridgeStats.calls += res.calls;
			bridgeStats.hits += res.hits;
			bridgeStats.misses += res.misses;
			if (res.status !== "ok" || !res.texts || ctx.signal?.aborted
				|| generation !== cacheGeneration) {
				const status = res.status === "ok" ? "error" : res.status;
				recordBridge(status);
				// Result bytes = originals: nothing was replaced.
				emitContext(ctx, status, inputBytes, inputBytes, 0, {
					calls: res.calls,
					hits: res.hits,
					misses: res.misses,
				});
				return; // fail-open: original messages unchanged
			}
			// Apply replacements to clones only; event.messages is never
			// mutated. res.texts already encodes the applied outcome: a
			// string is a validated shorter non-empty replacement, null
			// keeps the original. output_bytes counts every candidate's
			// result bytes including unchanged blocks.
			const out = messages.slice();
			let changed = 0;
			let outputBytes = 0;
			for (const c of candidates) {
				const next = res.texts.get(c.id);
				if (typeof next !== "string") {
					outputBytes += c.bytes;
					continue;
				}
				const msg = out[c.msgIdx] as {
					content: { type: string; text: string }[];
				};
				out[c.msgIdx] = {
					...msg,
					content: msg.content.map((b, j) =>
						j === c.blockIdx ? { ...b, text: next } : b,
					),
				} as AgentMessage;
				changed += 1;
				outputBytes += Buffer.byteLength(next, "utf8");
			}
			recordBridge("ok");
			emitContext(ctx, "ok", inputBytes, outputBytes, changed, {
				calls: res.calls,
				hits: res.hits,
				misses: res.misses,
			});
			// Abort after the bridge resolved: never apply stale results.
			if (ctx.signal?.aborted || changed === 0) return;
			return { messages: out };
		} catch {
			return; // fail-open
		}
	});

	pi.registerCommand("hive", {
		description: "Show Hive collector status",
		handler: async (_args, ctx) => {
			try {
				const { mode, source } = resolveMode();
				const lines = [
					`hive mode: ${mode} (${source})`,
					`logs: ${dataDir()}`,
					`collector: ${sink && !stopped ? "active" : "inactive"}`,
					`bridge: ${bridgePath()}`,
					`bridge status: last=${bridgeStats.last} ok=${bridgeStats.ok} error=${bridgeStats.error} timeout=${bridgeStats.timeout} calls=${bridgeStats.calls} hits=${bridgeStats.hits} misses=${bridgeStats.misses}`,
					"routing: none — metadata collection only, no model or traffic routing",
				];
				ctx.ui.notify(lines.join("\n"), "info");
			} catch {
				// contained
			}
		},
	});
}
