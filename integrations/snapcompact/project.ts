// project.ts — reusable Hive history projection over @oh-my-pi/snapcompact.
//
// The engine is a structural subset of the real package's exports, injected
// so unit tests run without the pinned native renderer. cli.ts supplies the
// real module via a lazy dynamic import; tests supply a fake.
//
// What the package does NOT do (host responsibilities, enforced here):
//   - model image capability: gated on the request's explicit
//     `model.supports_images` boolean — the package never checks it;
//   - renderability preflight: scanRenderability over the probe text, like
//     OMP's session-maintenance preflight;
//   - per-request image count: providerFrameBudget(provider) and
//     maxFramesForDataBudget(maxFrameDataBytes) cap maxFrames;
//   - per-request payload bytes: historyBlocks(maxFrameDataBytes) bounds the
//     base64 attached per request — and any frame it would drop fails closed;
//   - archive integrity: detected serializer truncation or archive-budget
//     eviction (truncatedChars growth, dropped frames) triggers fallback to
//     the original history. The visual archive is a derived view; this
//     adapter makes no claim about native-renderer or model readback
//     fidelity;
//   - bytes are diagnostics only: projected JSON is usually larger than the
//     text it replaces (PNG base64), so byte size never gates application;
//   - original-history fallback: ANY error returns applied:false and the
//     caller's own message list is authoritative.
//
// The projected message list is:
//   original[0..start)  — verbatim (includes every unsafe message)
//   summary message     — role "user", content [text summary, ...historyBlocks]
//   original[cut..n)    — verbatim (latest user message + recent tail)
// The archive is a lossy derived view; the host's original history stays
// authoritative and is never mutated.

import { isRecord } from "./guards.ts";
import { chooseArchiveRange, normalizeMessages, toEngineMessage } from "./normalize.ts";
import type { ArchivePolicy, ArchiveRange, NormalizedMessage } from "./normalize.ts";

// ============================================================================
// Engine interface (structural subset of @oh-my-pi/snapcompact@18.2.6)
// ============================================================================

export interface EngineShape {
	frameSize: number;
	frameTokenEstimate: number;
	font: string;
	variant: string;
	columns?: number;
}

export interface EngineFrame {
	data: string;
	mimeType: string;
	cols: number;
	rows: number;
	chars: number;
	font?: string;
	variant?: string;
	lineRepeat?: number;
	columns?: number;
	stopwordDim?: boolean;
	detail?: string;
}

export interface EngineArchive {
	frames: EngineFrame[];
	totalChars: number;
	truncatedChars: number;
	text?: string;
	textHead?: string;
	textTail?: string;
}

export interface EngineCompactionResult {
	summary: string;
	shortSummary?: string;
	firstKeptEntryId: string;
	tokensBefore: number;
	details?: { readFiles: string[]; modifiedFiles: string[] };
	preserveData?: Record<string, unknown>;
}

export interface EngineBlock {
	type: string;
	text?: string;
	data?: string;
	mimeType?: string;
	detail?: string;
}

/** The package surface this adapter uses. Verified against
 *  @oh-my-pi/snapcompact@18.2.6 src/snapcompact.ts:
 *  serializeConversation (939), scanRenderability (1356),
 *  renderabilityProbeText (1770), resolveShapeForText (1378),
 *  providerFrameBudget (530), FRAME_DATA_BYTES_BUDGET (497),
 *  compact (2110), historyBlocks (1915). */
export interface SnapcompactEngine {
	serializeConversation(messages: unknown[], options?: Record<string, unknown>): string;
	scanRenderability(text: string, options?: { shape?: EngineShape }): { isSafe: boolean; unrenderableRatio: number };
	renderabilityProbeText(
		serialized: string,
		previousPreserveData?: Record<string, unknown>,
		previousSummary?: string,
	): string;
	resolveShapeForText(text: string, model?: { api?: string; id?: string }): EngineShape;
	providerFrameBudget(provider?: string): number;
	FRAME_DATA_BYTES_BUDGET: number;
	compact(
		preparation: {
			firstKeptEntryId: string;
			messagesToSummarize: unknown[];
			turnPrefixMessages: unknown[];
			tokensBefore: number;
			previousSummary?: string;
			previousPreserveData?: Record<string, unknown>;
			fileOps: { read: Set<string>; written: Set<string>; edited: Set<string> };
		},
		options?: Record<string, unknown>,
	): Promise<EngineCompactionResult>;
	historyBlocks(archive: EngineArchive, options?: { maxFrameDataBytes?: number }): EngineBlock[];
	/** Frame-count cap implied by a byte budget (upstream maxFramesForDataBudget). */
	maxFramesForDataBudget(maxFrameDataBytes?: number): number;
}

// ============================================================================
// Protocol types (version 1)
// ============================================================================

export interface ProjectRequest {
	version?: number;
	action?: string;
	model?: { api?: string; id?: string; provider?: string; supports_images?: boolean };
	messages?: unknown[];
	policy?: ArchivePolicy & {
		/** Per-request cap on archive frames; additionally clamped to
		 *  providerFrameBudget(provider). */
		maxFrames?: number;
		/** Per-request cap on base64 frame bytes attached to the rebuilt
		 *  request. Default FRAME_DATA_BYTES_BUDGET (3 MB). */
		maxFrameDataBytes?: number;
		/** Serialize assistant thinking into the archive. Default false —
		 *  archived reasoning replays as text on every later request and
		 *  trips Anthropic's reasoning_extraction classifier (upstream
		 *  issue #6093). */
		includeThinking?: boolean;
		/** Serializer caps (upstream defaults: 2000 / 500 / 2000). */
		toolResultMaxChars?: number;
		toolArgMaxChars?: number;
		toolCallMaxChars?: number;
	};
	/** Opaque archive returned by a previous applied response, for rolling
	 *  re-compaction. Forwarded as previousPreserveData. Rejected when the
	 *  prior archive is already truncated or lacks its kept source text —
	 *  extending it could not prove full coverage. */
	previous_archive?: Record<string, unknown>;
	/** Text summary from a previous non-snapcompact compaction, printed at the
	 *  archive head for continuity. */
	previous_summary?: string;
	/** Host-chosen id for the first retained entry (default "hive-cut-<n>"). */
	first_kept_entry_id?: string;
	/** Host-measured pre-compaction token count, echoed into the result. */
	tokens_before?: number;
	/** Host-extracted file operations for the summary's <files> section. */
	file_ops?: { read?: string[]; written?: string[]; edited?: string[] };
}

export interface ProjectStats {
	originalBytes: number;
	projectedBytes: number;
	archivedMessages: number;
	retainedMessages: number;
	frames: number;
	frameDataBytes: number;
	archiveTotalChars: number;
	archiveTruncatedChars: number;
	unrenderableRatio: number;
}

export interface ProjectResponse {
	version: 1;
	ok: boolean;
	applied: boolean;
	reason: string;
	/** Effective history: projected when applied, the input messages verbatim
	 *  otherwise. */
	messages: unknown[];
	archive?: Record<string, unknown>;
	stats?: ProjectStats;
	error?: string;
}

// ============================================================================
// Projection
// ============================================================================

function utf8Bytes(value: unknown): number {
	return Buffer.byteLength(JSON.stringify(value), "utf8");
}

function fail(messages: unknown[], reason: string, extra?: Partial<ProjectResponse>): ProjectResponse {
	return { version: 1, ok: true, applied: false, reason, messages, ...extra };
}

/** Conservative pre-check: when a serializer cap is finite, reject any
 *  archived message that would exceed it instead of letting upstream elide
 *  the middle. Mirrors upstream's own measurement (text length for results,
 *  JSON.stringify length per argument and per call). */
function oversizeReason(
	archived: NormalizedMessage[],
	caps: { toolResultMaxChars: number; toolArgMaxChars: number; toolCallMaxChars: number },
): string | undefined {
	for (const msg of archived) {
		if (msg.role === "toolResult" && Array.isArray(msg.content)) {
			const text = (msg.content as { type: string; text?: string }[])
				.filter(block => block.type === "text")
				.map(block => block.text ?? "")
				.join("");
			if (text.length > caps.toolResultMaxChars) return "content_exceeds_cap:tool_result";
		}
		if (msg.role === "assistant" && Array.isArray(msg.content)) {
			for (const block of msg.content as Record<string, unknown>[]) {
				if (block.type !== "toolCall" || !isRecord(block.arguments)) continue;
				let total = 0;
				for (const value of Object.values(block.arguments)) {
					// Unserializable values (circular, bigint) fail closed:
					// over every finite cap instead of throwing.
					let len = Number.POSITIVE_INFINITY;
					try {
						len = (JSON.stringify(value) ?? "undefined").length;
					} catch {
						// fail closed
					}
					if (len > caps.toolArgMaxChars) return "content_exceeds_cap:tool_arg";
					total += len;
				}
				if (total > caps.toolCallMaxChars) return "content_exceeds_cap:tool_call";
			}
		}
	}
	return undefined;
}



/**
 * Project `request.messages` through the snapcompact engine. Pure with
 * respect to the host: the input array and its messages are never mutated,
 * and every failure path returns the input verbatim with applied:false.
 */
export async function projectHistory(request: ProjectRequest, engine: SnapcompactEngine): Promise<ProjectResponse> {
	if (!isRecord(request)) return fail([], "invalid_request");
	const echo = Array.isArray(request.messages) ? request.messages : [];
	if (request.action !== "project") return fail(echo, "unsupported_action");
	if (request.version !== undefined && request.version !== 1) return fail(echo, "unsupported_version");
	const rawMessages = request.messages;
	if (!Array.isArray(rawMessages) || rawMessages.length === 0) return fail([], "empty_messages");

	// Image eligibility is explicit: the host declares the ACTIVE model can
	// consume images. The package never checks this itself.
	if (request.model?.supports_images !== true) return fail(rawMessages, "model_images_unsupported");

	const normalized = normalizeMessages(rawMessages);
	if ("error" in normalized) return fail(rawMessages, normalized.error);
	const messages = normalized.messages;

	// chooseArchiveRange validates the range fields (retainLast,
	// min/maxArchiveMessages) and rejects a non-object policy.
	const range: ArchiveRange = chooseArchiveRange(messages, request.policy);
	if (!range.ok) return fail(rawMessages, range.reason);
	const { start, cut } = range;

	// The remaining policy fields are validated the same way: finite
	// nonnegative integers (or a boolean for includeThinking), never coerced.
	const policy = request.policy ?? {};
	for (const field of [
		"maxFrames",
		"maxFrameDataBytes",
		"toolResultMaxChars",
		"toolArgMaxChars",
		"toolCallMaxChars",
	] as const) {
		const value = policy[field];
		if (value !== undefined && (!Number.isInteger(value) || value < 0)) {
			return fail(rawMessages, `invalid_policy:${field}`);
		}
	}
	if (policy.includeThinking !== undefined && typeof policy.includeThinking !== "boolean") {
		return fail(rawMessages, "invalid_policy:includeThinking");
	}

	// Scalar request fields are refused when malformed rather than coerced —
	// a fabricated tokensBefore or summary would silently alter the archive.
	if (request.previous_summary !== undefined && typeof request.previous_summary !== "string") {
		return fail(rawMessages, "invalid_request:previous_summary");
	}
	if (request.first_kept_entry_id !== undefined && typeof request.first_kept_entry_id !== "string") {
		return fail(rawMessages, "invalid_request:first_kept_entry_id");
	}
	if (
		request.tokens_before !== undefined &&
		(typeof request.tokens_before !== "number" || !Number.isFinite(request.tokens_before) || request.tokens_before < 0)
	) {
		return fail(rawMessages, "invalid_request:tokens_before");
	}

	// Engine messages for the archived slice only. Unsafe messages are
	// excluded by construction (start is past the last unsafe index).
	const archived = messages.slice(start, cut);
	const engineMessages = archived.map(toEngineMessage);

	// Serializer caps default to UNLIMITED: upstream accepts Infinity to
	// disable each cap, and this prototype fails closed rather than silently
	// eliding task facts. A finite cap rejects oversize eligible content
	// BEFORE serialization — truncation is never silently applied.
	const caps = {
		toolResultMaxChars: policy.toolResultMaxChars ?? Number.POSITIVE_INFINITY,
		toolArgMaxChars: policy.toolArgMaxChars ?? Number.POSITIVE_INFINITY,
		toolCallMaxChars: policy.toolCallMaxChars ?? Number.POSITIVE_INFINITY,
	};
	const oversize = oversizeReason(archived, caps);
	if (oversize !== undefined) return fail(rawMessages, oversize);
	const serializeOptions: Record<string, unknown> = {
		includeThinking: policy.includeThinking === true,
		...caps,
	};

	// Rolling compaction is only allowed when the prior archive can prove
	// full coverage: a truncated archive, or a legacy frame-only archive with
	// no kept source text, would silently lose history on re-render. The
	// archive's own shape is validated first — a malformed archive is
	// refused, never probed field by field.
	let previousTruncated = 0;
	if (request.previous_archive !== undefined) {
		if (!isRecord(request.previous_archive)) return fail(rawMessages, "previous_archive_invalid");
		const prev = request.previous_archive["snapcompact"];
		if (!isRecord(prev)) return fail(rawMessages, "previous_archive_invalid");
		// The archive must carry the full EngineArchive contract — every
		// frame and counter is validated before it is forwarded to the
		// engine as previousPreserveData.
		if (!Array.isArray(prev.frames)) return fail(rawMessages, "previous_archive_invalid");
		for (const frame of prev.frames) {
			if (
				!isRecord(frame) ||
				typeof frame.data !== "string" ||
				typeof frame.mimeType !== "string" ||
				typeof frame.cols !== "number" ||
				!Number.isFinite(frame.cols) ||
				frame.cols < 0 ||
				typeof frame.rows !== "number" ||
				!Number.isFinite(frame.rows) ||
				frame.rows < 0 ||
				typeof frame.chars !== "number" ||
				!Number.isFinite(frame.chars) ||
				frame.chars < 0
			) {
				return fail(rawMessages, "previous_archive_invalid");
			}
		}
		if (typeof prev.totalChars !== "number" || !Number.isFinite(prev.totalChars) || prev.totalChars < 0) {
			return fail(rawMessages, "previous_archive_invalid");
		}
		if (
			typeof prev.truncatedChars !== "number" ||
			!Number.isFinite(prev.truncatedChars) ||
			prev.truncatedChars < 0
		) {
			return fail(rawMessages, "previous_archive_invalid");
		}
		for (const field of ["text", "textHead", "textTail"] as const) {
			if (prev[field] !== undefined && typeof prev[field] !== "string") {
				return fail(rawMessages, "previous_archive_invalid");
			}
		}
		previousTruncated = prev.truncatedChars;
		if (previousTruncated > 0) return fail(rawMessages, "previous_archive_truncated");
		const hasSource =
			(typeof prev.text === "string" && prev.text.length > 0) ||
			(typeof prev.textHead === "string" && prev.textHead.length > 0) ||
			(typeof prev.textTail === "string" && prev.textTail.length > 0);
		if (prev.frames.length > 0 && !hasSource) {
			return fail(rawMessages, "previous_archive_incomplete");
		}
	}
	if (request.file_ops !== undefined) {
		if (!isRecord(request.file_ops)) return fail(rawMessages, "invalid_request:file_ops");
		for (const field of ["read", "written", "edited"] as const) {
			const list = request.file_ops[field];
			if (list !== undefined && (!Array.isArray(list) || !list.every(entry => typeof entry === "string"))) {
				return fail(rawMessages, "invalid_request:file_ops");
			}
		}
	}
	const fileOps = {
		read: new Set(request.file_ops?.read ?? []),
		written: new Set(request.file_ops?.written ?? []),
		edited: new Set(request.file_ops?.edited ?? []),
	};

	// The ENTIRE reusable engine boundary is contained: budget reads,
	// serialization, renderability, compaction, history blocks, output
	// assembly and stats. Any throw or contract-violating response fails
	// open with the original history — never a partial or fabricated
	// projection.
	try {
		const dataBudget = policy.maxFrameDataBytes ?? engine.FRAME_DATA_BYTES_BUDGET;
		if (typeof dataBudget !== "number" || !Number.isFinite(dataBudget) || dataBudget < 0) {
			return fail(rawMessages, "engine_response_invalid");
		}
		const maxFrameDataBytes = Math.floor(dataBudget);
		// Frame count: caller's cap, never above the provider's per-request
		// image budget or the frame-count cap implied by the byte budget
		// (compact itself only clamps to its internal 80-frame default).
		const providerBudget = engine.providerFrameBudget(request.model?.provider);
		const dataBudgetFrames = engine.maxFramesForDataBudget(maxFrameDataBytes);
		if (
			!Number.isInteger(providerBudget) ||
			providerBudget < 0 ||
			!Number.isInteger(dataBudgetFrames) ||
			dataBudgetFrames < 0
		) {
			return fail(rawMessages, "engine_response_invalid");
		}
		let maxFrames = Math.min(providerBudget, dataBudgetFrames);
		if (policy.maxFrames !== undefined) maxFrames = Math.min(maxFrames, policy.maxFrames);
		// A zero budget is exhausted, not permission for one frame — fail open
		// rather than clamping up to 1.
		if (maxFrames <= 0) return fail(rawMessages, "frame_budget_exhausted");
		const serialized = engine.serializeConversation(engineMessages, serializeOptions);
		if (typeof serialized !== "string") return fail(rawMessages, "engine_response_invalid");
		const probeText = engine.renderabilityProbeText(serialized, request.previous_archive, request.previous_summary);
		if (typeof probeText !== "string") return fail(rawMessages, "engine_response_invalid");
		const shape = engine.resolveShapeForText(probeText, { api: request.model?.api, id: request.model?.id });
		if (!isRecord(shape)) return fail(rawMessages, "engine_response_invalid");
		const renderability = engine.scanRenderability(probeText, { shape });
		if (!isRecord(renderability) || renderability.isSafe !== true) {
			return fail(rawMessages, "unrenderable_text");
		}
		if (typeof renderability.unrenderableRatio !== "number" || !Number.isFinite(renderability.unrenderableRatio)) {
			return fail(rawMessages, "engine_response_invalid");
		}

		const result = await engine.compact(
			{
				firstKeptEntryId: request.first_kept_entry_id ?? `hive-cut-${cut}`,
				messagesToSummarize: engineMessages,
				turnPrefixMessages: [],
				tokensBefore: request.tokens_before ?? 0,
				...(request.previous_summary !== undefined ? { previousSummary: request.previous_summary } : {}),
				...(request.previous_archive !== undefined ? { previousPreserveData: request.previous_archive } : {}),
				fileOps,
			},
			{
				model: { api: request.model?.api, id: request.model?.id },
				maxFrames,
				...serializeOptions,
			},
		);

		if (!isRecord(result) || typeof result.summary !== "string") {
			return fail(rawMessages, "engine_response_invalid");
		}
		const preserveData = isRecord(result.preserveData) ? result.preserveData : undefined;
		const candidate = preserveData?.["snapcompact"];
		if (!isRecord(candidate)) return fail(rawMessages, "empty_archive");
		// Shape-check the archive before trusting it: malformed frames or
		// counters are an engine contract violation, not an empty archive.
		if (!Array.isArray(candidate.frames)) return fail(rawMessages, "engine_response_invalid");
		let frameDataBytes = 0;
		for (const frame of candidate.frames) {
			if (!isRecord(frame) || typeof frame.data !== "string") {
				return fail(rawMessages, "engine_response_invalid");
			}
			frameDataBytes += frame.data.length;
		}
		for (const field of ["text", "textHead", "textTail"] as const) {
			if (candidate[field] !== undefined && typeof candidate[field] !== "string") {
				return fail(rawMessages, "engine_response_invalid");
			}
		}
		let archiveTotalChars = 0;
		let archiveTruncatedChars = 0;
		for (const field of ["totalChars", "truncatedChars"] as const) {
			const value = candidate[field];
			if (typeof value !== "number" || !Number.isFinite(value)) {
				return fail(rawMessages, "engine_response_invalid");
			}
			if (field === "totalChars") archiveTotalChars = value;
			else archiveTruncatedChars = value;
		}
		if (candidate.frames.length === 0 && !candidate.text && !candidate.textHead && !candidate.textTail) {
			return fail(rawMessages, "empty_archive");
		}
		// Fail closed on ANY new archive-budget eviction: truncatedChars is
		// cumulative, so growth past the prior archive's count means this pass
		// dropped source text.
		if (archiveTruncatedChars > previousTruncated) return fail(rawMessages, "archive_truncated");
		const archive = candidate as EngineArchive;
		const emitted = engine.historyBlocks(archive, { maxFrameDataBytes });
		if (!Array.isArray(emitted)) return fail(rawMessages, "engine_response_invalid");
		let imageBlocks = 0;
		for (const block of emitted) {
			if (!isRecord(block) || typeof block.type !== "string") {
				return fail(rawMessages, "engine_response_invalid");
			}
			if (block.type === "image") {
				if (typeof block.data !== "string") return fail(rawMessages, "engine_response_invalid");
				imageBlocks++;
			}
		}
		// Fail closed when the byte budget or a missing payload drops a frame:
		// historyBlocks inserts a marker, but the prototype must not silently
		// lose archived content.
		if (imageBlocks < candidate.frames.length) {
			return fail(rawMessages, "frames_omitted");
		}

		// The summary message mirrors how OMP re-attaches archives: the
		// reading-guide text plus the ordered text/image history blocks on one
		// user-role message. Hosts map the blocks onto their provider's
		// content format.
		const summaryMessage = {
			role: "user",
			content: [{ type: "text", text: result.summary }, ...emitted],
			synthetic: true,
			timestamp: messages[cut].timestamp,
		};

		const projected: unknown[] = [
			...rawMessages.slice(0, start),
			summaryMessage,
			...rawMessages.slice(cut),
		];

		// Bytes are measured diagnostics only — PNG base64 is usually larger
		// than the text it replaces, so byte size is NOT the savings metric
		// and never gates application. The win is vision-token/context
		// accounting, which the evaluation harness measures at the provider.
		const stats: ProjectStats = {
			originalBytes: utf8Bytes(rawMessages),
			projectedBytes: utf8Bytes(projected),
			archivedMessages: cut - start,
			retainedMessages: rawMessages.length - (cut - start),
			frames: candidate.frames.length,
			frameDataBytes,
			archiveTotalChars,
			archiveTruncatedChars,
			unrenderableRatio: renderability.unrenderableRatio,
		};

		return {
			version: 1,
			ok: true,
			applied: true,
			reason: "projected",
			messages: projected,
			archive: preserveData,
			stats,
		};
	} catch (error) {
		return fail(rawMessages, "engine_error", {
			error: error instanceof Error ? error.message : String(error),
		});
	}
}

/** Validate the top-level request envelope. Returns the request or a reason. */
export function parseRequest(raw: unknown): { request: ProjectRequest } | { reason: string } {
	if (!isRecord(raw)) return { reason: "invalid_request" };
	if (raw.action !== "project") return { reason: "unsupported_action" };
	if (raw.version !== undefined && raw.version !== 1) return { reason: "unsupported_version" };
	return { request: raw as ProjectRequest };
}
