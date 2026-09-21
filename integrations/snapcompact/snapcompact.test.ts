// snapcompact.test.ts — synthetic bun:test coverage for the projection
// adapter. All fixtures are fabricated; no real transcripts, no network, no
// model calls, no native renderer. The engine is a structural fake matching
// the verified @oh-my-pi/snapcompact@18.2.6 surface; upstream rendering is
// NOT exercised here (that needs `bun install` + a smoke run).
// Run: bun test integrations/snapcompact/snapcompact.test.ts

import { describe, expect, test } from "bun:test";

import { chooseArchiveRange, normalizeMessages } from "./normalize.ts";
import { projectHistory, type EngineArchive, type EngineBlock, type EngineFrame, type EngineShape, type SnapcompactEngine } from "./project.ts";

// --- fake engine -----------------------------------------------------------

const FRAME = { data: "AAAA", mimeType: "image/png", cols: 100, rows: 40, chars: 4000 };

interface FakeCall {
	serializedInput?: unknown[];
	compactPreparation?: Record<string, unknown>;
	compactOptions?: Record<string, unknown>;
}

function fakeEngine(overrides: Partial<SnapcompactEngine> = {}, calls: FakeCall = {}): SnapcompactEngine {
	return {
		serializeConversation(messages, _options) {
			calls.serializedInput = messages;
			return messages.map(() => "x".repeat(100)).join("\n");
		},
		scanRenderability: () => ({ isSafe: true, unrenderableRatio: 0 }),
		renderabilityProbeText: serialized => serialized,
		resolveShapeForText: () => ({
			frameSize: 1568,
			frameTokenEstimate: 2881,
			font: "8x13",
			variant: "bw",
		}),
		providerFrameBudget: () => 17,
		maxFramesForDataBudget: () => 17,
		FRAME_DATA_BYTES_BUDGET: 3_000_000,
		async compact(preparation, options) {
			calls.compactPreparation = preparation as Record<string, unknown>;
			calls.compactOptions = options as Record<string, unknown>;
			const archive: EngineArchive = {
				frames: [FRAME],
				totalChars: 4000,
				truncatedChars: 0,
				text: "kept source",
				textHead: "head",
				textTail: "tail",
			};
			return {
				summary: "Reading guide.",
				firstKeptEntryId: preparation.firstKeptEntryId,
				tokensBefore: preparation.tokensBefore,
				preserveData: { snapcompact: archive },
			};
		},
		historyBlocks: archive => [
			{ type: "text", text: archive.textHead ?? "" },
			...archive.frames.map(f => ({ type: "image", data: f.data, mimeType: f.mimeType })),
			{ type: "text", text: archive.textTail ?? "" },
		],
		...overrides,
	};
}

// --- fixtures ---------------------------------------------------------------

function user(text: string | unknown[], extra: Record<string, unknown> = {}) {
	return { role: "user", content: text, timestamp: 1, ...extra };
}
function assistant(text: string, extra: Record<string, unknown> = {}) {
	return { role: "assistant", content: [{ type: "text", text }], timestamp: 1, ...extra };
}
function call(id: string, name = "bash") {
	return {
		role: "assistant",
		content: [{ type: "toolCall", id, name, arguments: { command: "ls" } }],
		timestamp: 1,
	};
}
function result(id: string, text = "ok") {
	return { role: "tool", tool_call_id: id, tool_name: "bash", content: text, timestamp: 1 };
}

/** user + N (call,result) pairs + final user — a realistic tool transcript. */
function transcript(pairs: number) {
	const msgs: unknown[] = [user("start")];
	for (let i = 0; i < pairs; i++) {
		msgs.push(call(`c${i}`), result(`c${i}`, `output ${i}`));
	}
	msgs.push(user("latest request"));
	return msgs;
}

const VISION_MODEL = { api: "openai-completions", id: "gpt-5.5", provider: "openai", supports_images: true };

function request(messages: unknown[], extra: Record<string, unknown> = {}) {
	return { version: 1, action: "project", model: VISION_MODEL, messages, ...extra };
}

// --- normalizeMessages -------------------------------------------------------

describe("normalizeMessages", () => {
	test("maps role aliases and fills defaults", () => {
		const out = normalizeMessages([
			user("hi"),
			{ role: "tool", tool_call_id: "c1", content: "done" },
			{ role: "tool_result", tool_call_id: "c2", content: [{ type: "text", text: "t" }] },
			{ role: "toolResult", tool_call_id: "c3", content: "camel" },
		]);
		if (!("messages" in out)) throw new Error("expected messages");
		expect(out.messages.length).toBe(4);
		expect(out.messages[1].role).toBe("toolResult");
		expect(out.messages[1].toolCallId).toBe("c1");
		expect(out.messages[2].role).toBe("toolResult");
		expect(out.messages[3].role).toBe("toolResult");
		expect(out.messages[3].toolCallId).toBe("c3");
		expect(out.messages.every(m => !m.unsafe)).toBe(true);
	});

	test("marks image blocks unsafe", () => {
		const out = normalizeMessages([user([{ type: "image", data: "AAAA", mimeType: "image/png" }])]);
		if (!("messages" in out)) throw new Error("expected messages");
		expect(out.messages[0].unsafe).toBe(true);
		expect(out.messages[0].unsafeReason).toBe("non_text_content");
	});

	test("marks system/developer unsafe (serializer would drop them)", () => {
		const out = normalizeMessages([{ role: "system", content: "rules" }, { role: "developer", content: "dev" }]);
		if (!("messages" in out)) throw new Error("expected messages");
		expect(out.messages[0].unsafeReason).toBe("system_or_developer_message");
		expect(out.messages[1].unsafe).toBe(true);
	});

	test("marks provider_payload and malformed messages unsafe", () => {
		const out = normalizeMessages([
			user("x", { provider_payload: { type: "openaiResponsesHistory" } }),
			"not-an-object",
			{ role: "assistant", content: [{ type: "toolCall", name: "no-id" }] },
			{ role: "tool", content: "orphan without id" },
		]);
		if (!("messages" in out)) throw new Error("expected messages");
		expect(out.messages.map(m => m.unsafeReason)).toEqual([
			"provider_payload",
			"malformed_message",
			"malformed_tool_call",
			"missing_tool_call_id",
		]);
	});

	test("rejects non-array input", () => {
		expect("error" in normalizeMessages("nope")).toBe(true);
	});
});

// --- chooseArchiveRange ------------------------------------------------------

describe("chooseArchiveRange", () => {
	test("retains latest user and recent tail verbatim", () => {
		const raw = transcript(5);
		const norm = normalizeMessages(raw);
		if (!("messages" in norm)) throw new Error("expected messages");
		const range = chooseArchiveRange(norm.messages, { retainLast: 4 });
		if (!range.ok) throw new Error("expected range");
		// n-4 lands on the toolResult at index 8; cut backs off to the call at
		// 7. The last user message (index 10) stays inside the retained suffix.
		expect(norm.messages[range.cut].role).not.toBe("toolResult");
		expect(range.cut).toBeLessThanOrEqual(raw.length - 1);
		expect(raw.slice(range.cut).at(-1)).toEqual(raw.at(-1));
	});

	test("never starts the retained suffix on a toolResult", () => {
		// retainLast=2 → n-2 lands on the result; cut must back off to the
		// call so the pair stays together in the retained suffix.
		const raw = [user("a"), user("b"), call("c1"), result("c1"), user("c")];
		const norm = normalizeMessages(raw);
		if (!("messages" in norm)) throw new Error("expected messages");
		const range = chooseArchiveRange(norm.messages, { retainLast: 2 });
		if (!range.ok) throw new Error("expected range");
		expect(norm.messages[range.cut].role).not.toBe("toolResult");
		expect(range.cut).toBe(2);
	});

	test("start advances past unsafe messages", () => {
		const raw = [user("a"), user([{ type: "image", data: "x", mimeType: "image/png" }]), assistant("b"), user("c")];
		const norm = normalizeMessages(raw);
		if (!("messages" in norm)) throw new Error("expected messages");
		const range = chooseArchiveRange(norm.messages, { retainLast: 1, minArchiveMessages: 1 });
		if (!range.ok) throw new Error("expected range");
		expect(range.start).toBe(2); // image message stays verbatim
	});

	test("fails open when history is too small", () => {
		const norm = normalizeMessages([user("only")]);
		if (!("messages" in norm)) throw new Error("expected messages");
		const range = chooseArchiveRange(norm.messages);
		expect(range.ok).toBe(false);
	});

	test("fails open with no user message", () => {
		const norm = normalizeMessages([assistant("a"), assistant("b")]);
		if (!("messages" in norm)) throw new Error("expected messages");
		expect(chooseArchiveRange(norm.messages).ok).toBe(false);
	});

	test("bounds archive size with maxArchiveMessages", () => {
		const raw = transcript(20);
		const norm = normalizeMessages(raw);
		if (!("messages" in norm)) throw new Error("expected messages");
		const range = chooseArchiveRange(norm.messages, { retainLast: 1, maxArchiveMessages: 5 });
		if (!range.ok) throw new Error("expected range");
		expect(range.cut - range.start).toBe(5);
	});
});

// --- projectHistory ----------------------------------------------------------

describe("projectHistory", () => {
	test("happy path: prefix + summary + verbatim suffix", async () => {
		const raw = transcript(6);
		const res = await projectHistory(request(raw), fakeEngine());
		expect(res.applied).toBe(true);
		expect(res.reason).toBe("projected");
		// projected = summary message + retained suffix (nothing before start)
		const summary = res.messages[0] as { role: string; content: { type: string }[] };
		expect(summary.role).toBe("user");
		expect(summary.content[0].type).toBe("text");
		expect(summary.content.some(b => b.type === "image")).toBe(true);
		// retained suffix is the ORIGINAL objects, verbatim — cut backs off the
		// toolResult at index 10 to the call at 9, so 5 messages are retained.
		expect(res.messages.slice(1)).toEqual(raw.slice(-5));
		expect(res.stats?.frames).toBe(1);
		expect(res.archive?.["snapcompact"]).toBeDefined();
	});

	test("unsafe messages stay verbatim and out of the archive", async () => {
		const raw = [
			{ role: "system", content: "rules" },
			user("a"),
			user([{ type: "image", data: "x", mimeType: "image/png" }]),
			call("c1"),
			result("c1"),
			assistant("done"),
			user("latest"),
		];
		const calls: FakeCall = {};
		const res = await projectHistory(request(raw, { policy: { retainLast: 1, minArchiveMessages: 1 } }), fakeEngine({}, calls));
		expect(res.applied).toBe(true);
		// system + image messages are verbatim at the head of the output
		expect(res.messages[0]).toEqual(raw[0]);
		expect(res.messages[1]).toEqual(raw[1]);
		expect(res.messages[2]).toEqual(raw[2]);
		// and were never handed to the serializer
		expect((calls.serializedInput as unknown[]).length).toBe(3); // call,result,assistant
	});

	test("fails open without supports_images", async () => {
		const raw = transcript(6);
		const res = await projectHistory(
			request(raw, { model: { ...VISION_MODEL, supports_images: false } }),
			fakeEngine(),
		);
		expect(res.applied).toBe(false);
		expect(res.reason).toBe("model_images_unsupported");
		expect(res.messages).toEqual(raw);
	});

	test("fails open on unrenderable text", async () => {
		const engine = fakeEngine({ scanRenderability: () => ({ isSafe: false, unrenderableRatio: 0.4 }) });
		const res = await projectHistory(request(transcript(6)), engine);
		expect(res.applied).toBe(false);
		expect(res.reason).toBe("unrenderable_text");
	});

	test("byte growth does not gate application (bytes are diagnostic only)", async () => {
		// A huge frame payload makes projected JSON larger than the original —
		// still applied: PNG base64 size is not the savings metric.
		const big = { ...FRAME, data: "A".repeat(1_000_000) };
		const engine = fakeEngine({
			async compact(preparation) {
				return {
					summary: "s",
					firstKeptEntryId: preparation.firstKeptEntryId,
					tokensBefore: 0,
					preserveData: { snapcompact: { frames: [big], totalChars: 1, truncatedChars: 0, textHead: "h" } },
				};
			},
		});
		const res = await projectHistory(request(transcript(3)), engine);
		expect(res.applied).toBe(true);
		expect(res.stats?.projectedBytes).toBeGreaterThan(res.stats?.originalBytes ?? 0);
	});

	test("fails closed when the archive evicts source text", async () => {
		const engine = fakeEngine({
			async compact(preparation) {
				return {
					summary: "s",
					firstKeptEntryId: preparation.firstKeptEntryId,
					tokensBefore: 0,
					preserveData: { snapcompact: { frames: [FRAME], totalChars: 1, truncatedChars: 500, textHead: "h" } },
				};
			},
		});
		const res = await projectHistory(request(transcript(6)), engine);
		expect(res.applied).toBe(false);
		expect(res.reason).toBe("archive_truncated");
		expect(res.messages).toEqual(transcript(6));
	});

	test("fails closed when historyBlocks omits a frame", async () => {
		const engine = fakeEngine({
			historyBlocks: () => [{ type: "text", text: "omitted marker" }],
		});
		const res = await projectHistory(request(transcript(6)), engine);
		expect(res.applied).toBe(false);
		expect(res.reason).toBe("frames_omitted");
	});

	test("rejects oversize content when a finite cap is set", async () => {
		const raw = [user("a"), call("c1"), result("c1", "x".repeat(5000)), assistant("b"), user("c")];
		const res = await projectHistory(
			request(raw, { policy: { retainLast: 1, minArchiveMessages: 1, toolResultMaxChars: 2000 } }),
			fakeEngine(),
		);
		expect(res.applied).toBe(false);
		expect(res.reason).toBe("content_exceeds_cap:tool_result");
	});

	test("rejects a truncated or incomplete previous archive", async () => {
		const truncated = { snapcompact: { frames: [FRAME], totalChars: 1, truncatedChars: 10, text: "old" } };
		const res = await projectHistory(request(transcript(6), { previous_archive: truncated }), fakeEngine());
		expect(res.applied).toBe(false);
		expect(res.reason).toBe("previous_archive_truncated");

		const frameOnly = { snapcompact: { frames: [FRAME], totalChars: 1, truncatedChars: 0 } };
		const res2 = await projectHistory(request(transcript(6), { previous_archive: frameOnly }), fakeEngine());
		expect(res2.applied).toBe(false);
		expect(res2.reason).toBe("previous_archive_incomplete");
	});

	test("clamps maxFrames to provider and data budgets", async () => {
		const calls: FakeCall = {};
		const engine = fakeEngine({ providerFrameBudget: () => 5, maxFramesForDataBudget: () => 3 }, calls);
		await projectHistory(request(transcript(6), { policy: { maxFrames: 50 } }), engine);
		expect(calls.compactOptions?.["maxFrames"]).toBe(3);
	});

	test("forwards previous archive and file ops", async () => {
		const calls: FakeCall = {};
		const prev = { snapcompact: { frames: [], totalChars: 1, truncatedChars: 0, text: "old" } };
		await projectHistory(
			request(transcript(6), {
				previous_archive: prev,
				previous_summary: "earlier",
				file_ops: { read: ["a.ts"], edited: ["b.ts"] },
				tokens_before: 1234,
			}),
			fakeEngine({}, calls),
		);
		expect(calls.compactPreparation?.["previousPreserveData"]).toEqual(prev);
		expect(calls.compactPreparation?.["previousSummary"]).toBe("earlier");
		expect(calls.compactPreparation?.["tokensBefore"]).toBe(1234);
		const fileOps = calls.compactPreparation?.["fileOps"] as { read: Set<string>; edited: Set<string> };
		expect(fileOps.read.has("a.ts")).toBe(true);
		expect(fileOps.edited.has("b.ts")).toBe(true);
	});

	test("engine throw fails open with original messages", async () => {
		const engine = fakeEngine({
			compact: () => Promise.reject(new Error("native boom")),
		});
		const res = await projectHistory(request(transcript(6)), engine);
		expect(res.applied).toBe(false);
		expect(res.reason).toBe("engine_error");
		expect(res.messages).toEqual(transcript(6));
	});

	test("rejects wrong action and version", async () => {
		const res = await projectHistory({ action: "nope", messages: transcript(3) }, fakeEngine());
		expect(res.applied).toBe(false);
		expect(res.reason).toBe("unsupported_action");
		const res2 = await projectHistory({ version: 2, action: "project", messages: transcript(3) }, fakeEngine());
		expect(res2.applied).toBe(false);
		expect(res2.reason).toBe("unsupported_version");
	});
});

// --- malformed input containment ---------------------------------------------

describe("malformed input containment", () => {
	test("chooseArchiveRange refuses non-integer or negative policy fields", () => {
		const norm = normalizeMessages(transcript(6));
		if (!("messages" in norm)) throw new Error("expected messages");
		for (const policy of [
			{ retainLast: 1.5 },
			{ retainLast: -1 },
			{ retainLast: Number.NaN },
			{ retainLast: Number.POSITIVE_INFINITY },
			{ minArchiveMessages: 0.5 },
			{ maxArchiveMessages: -3 },
		]) {
			const range = chooseArchiveRange(norm.messages, policy);
			expect(range.ok).toBe(false);
			if (!range.ok) expect(range.reason).toMatch(/^invalid_policy/);
		}
	});

	test("projectHistory refuses malformed policy without throwing", async () => {
		const raw = transcript(6);
		for (const policy of [
			{ retainLast: 1.5 },
			{ maxFrames: 2.5 },
			{ maxFrameDataBytes: -1 },
			{ toolResultMaxChars: Number.NaN },
			{ includeThinking: "yes" },
			"not-an-object",
			null,
		]) {
			const res = await projectHistory(request(raw, { policy }), fakeEngine());
			expect(res.applied).toBe(false);
			expect(res.reason).toMatch(/^invalid_policy/);
			expect(res.messages).toEqual(raw);
		}
	});

	test("refuses malformed previous_archive shapes", async () => {
		const raw = transcript(6);
		for (const previous_archive of [
			5,
			null,
			"archive",
			{ snapcompact: "nope" },
			{ snapcompact: { frames: "not-array", totalChars: 1, truncatedChars: 0, text: "t" } },
			{ snapcompact: { frames: [null], totalChars: 1, truncatedChars: 0, text: "t" } },
			{ snapcompact: { frames: [{ data: "AAAA" }], totalChars: 1, truncatedChars: 0, text: "t" } },
			{ snapcompact: { text: "old" } },
			{ snapcompact: { frames: [], truncatedChars: 0, text: "t" } },
			{ snapcompact: { frames: [], totalChars: 1, truncatedChars: -1, text: "t" } },
			{ snapcompact: { frames: [], totalChars: 1, truncatedChars: 0, text: 42 } },
		]) {
			const res = await projectHistory(request(raw, { previous_archive }), fakeEngine());
			expect(res.applied).toBe(false);
			expect(res.reason).toBe("previous_archive_invalid");
			expect(res.messages).toEqual(raw);
		}
	});

	test("refuses malformed file_ops and scalar fields", async () => {
		const raw = transcript(6);
		for (const extra of [
			{ file_ops: "read a.ts" },
			{ file_ops: { read: "a.ts" } },
			{ file_ops: { read: ["ok", 7] } },
			{ previous_summary: 42 },
			{ first_kept_entry_id: 7 },
			{ tokens_before: -5 },
			{ tokens_before: Number.NaN },
		]) {
			const res = await projectHistory(request(raw, extra), fakeEngine());
			expect(res.applied).toBe(false);
			expect(res.reason).toMatch(/^invalid_request/);
			expect(res.messages).toEqual(raw);
		}
	});

	test("malformed tool-call arguments stay verbatim, never serialized", async () => {
		const badCall = {
			role: "assistant",
			content: [{ type: "toolCall", id: "c0", name: "bash", arguments: "not-an-object" }],
			timestamp: 1,
		};
		const raw = [user("start"), badCall, result("c0"), user("latest")];
		const calls: FakeCall = {};
		const res = await projectHistory(
			request(raw, { policy: { retainLast: 1, minArchiveMessages: 1 } }),
			fakeEngine({}, calls),
		);
		expect(res.applied).toBe(true);
		// verbatim in the output prefix, excluded from the serialized archive
		expect(res.messages[1]).toEqual(badCall);
		expect((calls.serializedInput as unknown[]).length).toBe(1);
	});

	test("unknown block fields stay verbatim, never serialized", async () => {
		const badCall = {
			role: "assistant",
			content: [{ type: "toolCall", id: "c0", name: "bash", arguments: {}, signature: "sig" }],
			timestamp: 1,
		};
		const raw = [user("start"), badCall, result("c0"), user("latest")];
		const calls: FakeCall = {};
		const res = await projectHistory(
			request(raw, { policy: { retainLast: 1, minArchiveMessages: 1 } }),
			fakeEngine({}, calls),
		);
		expect(res.applied).toBe(true);
		expect(res.messages[1]).toEqual(badCall);
		expect((calls.serializedInput as unknown[]).length).toBe(1);
	});
	test("unknown message-level fields stay verbatim, never serialized", async () => {
		const metaCall = { ...call("c0"), provider_state: { cursor: "abc" } };
		const raw = [user("start"), metaCall, result("c0"), user("latest")];
		const calls: FakeCall = {};
		const res = await projectHistory(
			request(raw, { policy: { retainLast: 1, minArchiveMessages: 1 } }),
			fakeEngine({}, calls),
		);
		expect(res.applied).toBe(true);
		expect(res.messages[1]).toEqual(metaCall);
		expect((calls.serializedInput as unknown[]).length).toBe(1);
	});

	test("circular tool-call arguments fail closed under a finite cap", async () => {
		const circular: Record<string, unknown> = {};
		circular["self"] = circular;
		const badCall = {
			role: "assistant",
			content: [{ type: "toolCall", id: "c0", name: "bash", arguments: circular }],
			timestamp: 1,
		};
		const raw = [user("start"), badCall, result("c0"), user("latest")];
		const res = await projectHistory(
			request(raw, { policy: { retainLast: 1, minArchiveMessages: 1, toolArgMaxChars: 100 } }),
			fakeEngine(),
		);
		expect(res.applied).toBe(false);
		expect(res.reason).toBe("content_exceeds_cap:tool_arg");
		expect(res.messages).toEqual(raw);
	});
});

// --- engine boundary containment ----------------------------------------------

describe("engine boundary containment", () => {
	test("budget calls outside compact fail open with original messages", async () => {
		const raw = transcript(6);
		for (const overrides of [
			{ providerFrameBudget: () => { throw new Error("provider boom"); } },
			{ maxFramesForDataBudget: () => { throw new Error("budget boom"); } },
		] satisfies Partial<SnapcompactEngine>[]) {
			const res = await projectHistory(request(raw), fakeEngine(overrides));
			expect(res.applied).toBe(false);
			expect(res.reason).toBe("engine_error");
			expect(res.messages).toEqual(raw);
		}
	});

	test("non-integer or negative engine budgets are invalid, not clamped", async () => {
		const raw = transcript(6);
		for (const overrides of [
			{ providerFrameBudget: () => Number.NaN },
			{ providerFrameBudget: () => -1 },
			{ providerFrameBudget: () => 2.5 },
			{ maxFramesForDataBudget: () => -4 },
		] satisfies Partial<SnapcompactEngine>[]) {
			const res = await projectHistory(request(raw), fakeEngine(overrides));
			expect(res.applied).toBe(false);
			expect(res.reason).toBe("engine_response_invalid");
			expect(res.messages).toEqual(raw);
		}
	});

	test("a zero frame budget is exhausted, not permission for one frame", async () => {
		const raw = transcript(6);
		for (const [extra, overrides] of [
			[{}, { providerFrameBudget: () => 0 }],
			[{}, { maxFramesForDataBudget: () => 0 }],
			[{ policy: { maxFrames: 0 } }, {}],
		] satisfies [Record<string, unknown>, Partial<SnapcompactEngine>][]) {
			const res = await projectHistory(request(raw, extra), fakeEngine(overrides));
			expect(res.applied).toBe(false);
			expect(res.reason).toBe("frame_budget_exhausted");
			expect(res.messages).toEqual(raw);
		}
	});

	test("contract-violating engine responses fail open", async () => {
		const raw = transcript(6);
		// Cast reason: fabricate a contract-violating frame payload on purpose.
		const badArchive: EngineArchive = {
			frames: [{ data: 5, mimeType: "image/png", cols: 1, rows: 1, chars: 1 } as unknown as EngineFrame],
			totalChars: 1,
			truncatedChars: 0,
			text: "t",
		};
		for (const overrides of [
			// non-string serialization
			// Cast reason: fabricate a contract-violating return on purpose.
			{ serializeConversation: () => 42 as unknown as string },
			// Cast reason: fabricate a contract-violating return on purpose.
			{ resolveShapeForText: () => "shape" as unknown as EngineShape },
			// non-finite renderability ratio
			{ scanRenderability: () => ({ isSafe: true, unrenderableRatio: Number.NaN }) },
			// missing summary string
			{
				// Cast reason: fabricate a contract-violating summary on purpose.
				compact: async (preparation: { firstKeptEntryId: string }) => ({
					summary: 42 as unknown as string,
					firstKeptEntryId: preparation.firstKeptEntryId,
					tokensBefore: 0,
				}),
			},
			// malformed archive frame payload
			{
				compact: async (preparation: { firstKeptEntryId: string }) => ({
					summary: "s",
					firstKeptEntryId: preparation.firstKeptEntryId,
					tokensBefore: 0,
					preserveData: { snapcompact: badArchive },
				}),
			},
			// Cast reason: fabricate a contract-violating return on purpose.
			{ historyBlocks: () => "blocks" as unknown as EngineBlock[] },
			// image block without a data payload
			{ historyBlocks: () => [{ type: "image" }] },
		] satisfies Partial<SnapcompactEngine>[]) {
			const res = await projectHistory(request(raw), fakeEngine(overrides));
			expect(res.applied).toBe(false);
			expect(res.reason).toBe("engine_response_invalid");
			expect(res.messages).toEqual(raw);
		}
	});
});
