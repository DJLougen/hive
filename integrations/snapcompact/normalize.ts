// normalize.ts — host-neutral message normalization and archive-range
// selection for the snapcompact projection adapter.
//
// This module is deliberately free of @oh-my-pi/* imports so `bun test`
// exercises every policy decision without the pinned native renderer
// installed. project.ts maps the normalized messages onto pi-ai `Message`
// objects only at the engine boundary.
//
// Wire schema (request `messages` elements):
//   { "role": "user"|"assistant"|"tool"|"tool_result"|"toolResult"|
//             "system"|"developer",
//     "content": string | InputBlock[],
//     "tool_call_id"?: string,   // toolResult only
//     "tool_name"?: string,      // toolResult only
//     "is_error"?: boolean,      // toolResult only
//     "useless"?: boolean,       // toolResult only
//     "provider_payload"?: unknown, // opaque host/provider replay data
//     "timestamp"?: number }
// InputBlock: { "type": "text", "text": string }
//           | { "type": "thinking", "thinking": string }
//           | { "type": "toolCall", "id": string, "name": string,
//               "arguments"?: object, "intent"?: string }
//           | { "type": "image", ... }            // never archivable
//           | any other block type                // never archivable
//
// A message is marked `unsafe` when the upstream serializer cannot represent
// it faithfully (serializeConversation only understands text/thinking/
// toolCall blocks and drops everything else silently — including images,
// redactedThinking, provider server-tool blocks, developer/system roles,
// malformed tool-call arguments, and any block or message field outside this
// schema).
// Unsafe messages are never archived: the archive range starts after the last
// unsafe message and they pass through to the projected output verbatim.

import { isRecord } from "./guards.ts";

/** One content block in the host-neutral wire schema. */
export interface InputBlock {
	type: string;
	text?: string;
	thinking?: string;
	id?: string;
	name?: string;
	arguments?: Record<string, unknown>;
	intent?: string;
	[key: string]: unknown;
}

export type NormalizedRole = "user" | "assistant" | "toolResult" | "developer" | "other";

/**
 * Internal normalized message. `content` is already pi-ai-shaped for safe
 * messages; `unsafe` messages carry best-effort content and are never handed
 * to the engine — the projection re-emits the ORIGINAL input object for them.
 */
export interface NormalizedMessage {
	/** Index into the request's messages array. */
	index: number;
	role: NormalizedRole;
	content: unknown;
	toolCallId?: string;
	toolName?: string;
	isError?: boolean;
	useless?: boolean;
	timestamp: number;
	/** True when this message must never enter the lossy archive. */
	unsafe: boolean;
	unsafeReason?: string;
}

const ROLE_MAP: Record<string, NormalizedRole> = {
	user: "user",
	assistant: "assistant",
	tool: "toolResult",
	tool_result: "toolResult",
	toolresult: "toolResult",
	toolResult: "toolResult",
	system: "developer",
	developer: "developer",
};

/** Known wire keys per archivable block type. A block carrying any other
 *  field holds metadata the serializer would silently drop, so its message
 *  must pass through verbatim instead of being silently altered. */
const BLOCK_KEYS: Record<string, Record<string, true>> = {
	text: { type: true, text: true },
	thinking: { type: true, thinking: true },
	toolCall: { type: true, id: true, name: true, arguments: true, intent: true },
};

function hasUnknownKeys(block: Record<string, unknown>, type: string): boolean {
	const known = BLOCK_KEYS[type];
	if (known === undefined) return false;
	for (const key of Object.keys(block)) {
		if (known[key] !== true) return true;
	}
	return false;
}

/** Known wire keys per normalized role. Archiving replaces the whole message
 *  with a rendered summary, so any other top-level field (ids, signatures,
 *  provider state, …) would be silently lost — the message passes through
 *  verbatim instead. `provider_payload` is a known key but still unsafe. */
const MESSAGE_KEYS: Record<NormalizedRole, Record<string, true>> = {
	user: { role: true, content: true, timestamp: true, provider_payload: true },
	assistant: { role: true, content: true, timestamp: true, provider_payload: true },
	toolResult: {
		role: true,
		content: true,
		tool_call_id: true,
		tool_name: true,
		is_error: true,
		useless: true,
		timestamp: true,
		provider_payload: true,
	},
	developer: { role: true, content: true, timestamp: true, provider_payload: true },
	other: { role: true, content: true, timestamp: true, provider_payload: true },
};

function hasUnknownMessageKeys(msg: Record<string, unknown>, role: NormalizedRole): boolean {
	const known = MESSAGE_KEYS[role];
	for (const key of Object.keys(msg)) {
		if (known[key] !== true) return true;
	}
	return false;
}

/** Normalize a user/developer content field: string, or an all-text block
 *  array. Returns an unsafeReason when the content carries anything else. */
function textContent(content: unknown): { content: string | { type: "text"; text: string }[] } | { unsafeReason: string } {
	if (typeof content === "string") return { content };
	if (!Array.isArray(content)) return { unsafeReason: "non_text_content" };
	const blocks: { type: "text"; text: string }[] = [];
	for (const block of content) {
		if (!isRecord(block) || block.type !== "text" || typeof block.text !== "string") {
			return { unsafeReason: "non_text_content" };
		}
		if (hasUnknownKeys(block, "text")) return { unsafeReason: "unsupported_block_metadata" };
		blocks.push({ type: "text", text: block.text });
	}
	return { content: blocks };
}

/** Normalize an assistant content field. Only text/thinking/toolCall blocks
 *  are representable in the archive; anything else makes the whole message
 *  unsafe (the serializer would silently drop it). */
function assistantContent(content: unknown): { blocks: Record<string, unknown>[] } | { unsafeReason: string } {
	const rawBlocks: unknown[] = typeof content === "string" ? [{ type: "text", text: content }] : Array.isArray(content) ? content : [];
	if (rawBlocks.length === 0 && typeof content !== "string" && !Array.isArray(content)) {
		return { unsafeReason: "malformed_content" };
	}
	const blocks: Record<string, unknown>[] = [];
	for (const block of rawBlocks) {
		if (!isRecord(block) || typeof block.type !== "string") return { unsafeReason: "malformed_content" };
		if (block.type === "text") {
			if (typeof block.text !== "string") return { unsafeReason: "malformed_content" };
			if (hasUnknownKeys(block, "text")) return { unsafeReason: "unsupported_block_metadata" };
			blocks.push({ type: "text", text: block.text });
		} else if (block.type === "thinking") {
			if (typeof block.thinking !== "string") return { unsafeReason: "malformed_content" };
			if (hasUnknownKeys(block, "thinking")) return { unsafeReason: "unsupported_block_metadata" };
			blocks.push({ type: "thinking", thinking: block.thinking });
		} else if (block.type === "toolCall") {
			if (typeof block.id !== "string" || typeof block.name !== "string") {
				return { unsafeReason: "malformed_tool_call" };
			}
			// Malformed arguments are refused, never invented: the archive must
			// not fabricate an empty argument object the source did not contain.
			if (block.arguments !== undefined && !isRecord(block.arguments)) {
				return { unsafeReason: "malformed_tool_call" };
			}
			if (block.intent !== undefined && typeof block.intent !== "string") {
				return { unsafeReason: "malformed_tool_call" };
			}
			if (hasUnknownKeys(block, "toolCall")) return { unsafeReason: "unsupported_block_metadata" };
			blocks.push({
				type: "toolCall",
				id: block.id,
				name: block.name,
				arguments: block.arguments ?? {},
				...(block.intent !== undefined ? { intent: block.intent } : {}),
			});
		} else {
			// image, redactedThinking, fallback, anthropicServerTool, … — the
			// serializer drops these silently, so the message is not archivable.
			return { unsafeReason: "non_text_content" };
		}
	}
	return { blocks };
}

/** Normalize a toolResult content field: string, or an all-text block array
 *  (image results are not archivable). */
function toolResultContent(content: unknown): { blocks: { type: "text"; text: string }[] } | { unsafeReason: string } {
	if (typeof content === "string") return { blocks: [{ type: "text", text: content }] };
	if (!Array.isArray(content)) return { unsafeReason: "malformed_content" };
	const blocks: { type: "text"; text: string }[] = [];
	for (const block of content) {
		if (!isRecord(block) || block.type !== "text" || typeof block.text !== "string") {
			return { unsafeReason: "non_text_content" };
		}
		if (hasUnknownKeys(block, "text")) return { unsafeReason: "unsupported_block_metadata" };
		blocks.push({ type: "text", text: block.text });
	}
	return { blocks };
}

function unsafeMessage(
	index: number,
	role: NormalizedRole,
	reason: string,
	timestamp: number,
	extra?: Partial<NormalizedMessage>,
): NormalizedMessage {
	return { index, role, content: "", timestamp, unsafe: true, unsafeReason: reason, ...extra };
}

/**
 * Normalize the request message array. Never fails on individual messages:
 * unrepresentable messages are marked `unsafe` and pass through verbatim.
 * Returns an error only when `raw` is not an array of objects.
 */
export function normalizeMessages(raw: unknown): { messages: NormalizedMessage[] } | { error: string } {
	if (!Array.isArray(raw)) return { error: "messages_not_array" };
	const messages: NormalizedMessage[] = [];
	for (let index = 0; index < raw.length; index++) {
		const msg = raw[index];
		if (!isRecord(msg)) {
			messages.push(unsafeMessage(index, "other", "malformed_message", 0));
			continue;
		}
		const role = typeof msg.role === "string" ? ROLE_MAP[msg.role] : undefined;
		const timestamp = typeof msg.timestamp === "number" && Number.isFinite(msg.timestamp) ? msg.timestamp : 0;
		const payloadSuffix = msg.provider_payload !== undefined && msg.provider_payload !== null ? "+provider_payload" : "";

		if (role === undefined) {
			messages.push(unsafeMessage(index, "other", "unsupported_role", timestamp));
			continue;
		}
		if (role === "developer") {
			// serializeConversation has no developer/system scope — it would drop
			// the message entirely. Always retained verbatim instead.
			messages.push(unsafeMessage(index, role, `system_or_developer_message${payloadSuffix}`, timestamp));
			continue;
		}
		if (role === "user") {
			const result = textContent(msg.content);
			const unsafeReason =
				("unsafeReason" in result ? result.unsafeReason : undefined) ??
				(hasUnknownMessageKeys(msg, role) ? "unsupported_message_metadata" : undefined) ??
				(payloadSuffix ? "provider_payload" : undefined);
			messages.push({
				index,
				role,
				content: "content" in result ? result.content : "",
				timestamp,
				unsafe: unsafeReason !== undefined,
				unsafeReason,
			});
			continue;
		}
		if (role === "assistant") {
			const result = assistantContent(msg.content);
			const blocks = "blocks" in result ? result.blocks : [];
			const unsafeReason =
				("unsafeReason" in result ? result.unsafeReason : undefined) ??
				(hasUnknownMessageKeys(msg, role) ? "unsupported_message_metadata" : undefined) ??
				(payloadSuffix ? "provider_payload" : undefined);
			messages.push({
				index,
				role,
				content: blocks,
				timestamp,
				unsafe: unsafeReason !== undefined,
				unsafeReason,
			});
			continue;
		}
		// toolResult
		const toolCallId = typeof msg.tool_call_id === "string" && msg.tool_call_id.length > 0 ? msg.tool_call_id : undefined;
		const result = toolResultContent(msg.content);
		const blocks = "blocks" in result ? result.blocks : [];
		const unsafeReason =
			("unsafeReason" in result ? result.unsafeReason : undefined) ??
			(hasUnknownMessageKeys(msg, role) ? "unsupported_message_metadata" : undefined) ??
			(toolCallId === undefined ? "missing_tool_call_id" : payloadSuffix ? "provider_payload" : undefined);
		messages.push({
			index,
			role,
			content: blocks,
			toolCallId,
			toolName: typeof msg.tool_name === "string" ? msg.tool_name : undefined,
			isError: msg.is_error === true,
			useless: msg.useless === true,
			timestamp,
			unsafe: unsafeReason !== undefined,
			unsafeReason,
		});
	}
	return { messages };
}

/** Map a normalized SAFE message onto the pi-ai `Message` shape the package
 *  serializer reads (role/content/toolCallId/isError/useless). Extra pi-ai
 *  fields are filled with neutral placeholders; the serializer never reads
 *  them. Callers cast the result — this stays engine-agnostic on purpose. */
export function toEngineMessage(msg: NormalizedMessage): Record<string, unknown> {
	switch (msg.role) {
		case "user":
			return { role: "user", content: msg.content, timestamp: msg.timestamp };
		case "developer":
			return { role: "developer", content: msg.content, timestamp: msg.timestamp };
		case "assistant":
			return {
				role: "assistant",
				content: msg.content,
				api: "",
				provider: "",
				model: "",
				usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0 },
				stopReason: "stop",
				timestamp: msg.timestamp,
			};
		case "toolResult":
			return {
				role: "toolResult",
				toolCallId: msg.toolCallId,
				toolName: msg.toolName ?? "tool",
				content: msg.content,
				isError: msg.isError === true,
				...(msg.useless === true ? { useless: true } : {}),
				timestamp: msg.timestamp,
			};
		default:
			return { role: "user", content: "", timestamp: msg.timestamp };
	}
}

// ============================================================================
// Archive-range selection
// ============================================================================

export interface ArchivePolicy {
	/** Messages kept verbatim at the newest edge. Default 4. */
	retainLast?: number;
	/** Minimum messages in the archived prefix; below this the projection is
	 *  not worth an image round-trip and fails open. Default 2. */
	minArchiveMessages?: number;
	/** Upper bound on messages archived in one pass. Older messages stay
	 *  verbatim in the projected output (nothing is dropped). Default 256. */
	maxArchiveMessages?: number;
}

export const DEFAULT_RETAIN_LAST = 4;
export const DEFAULT_MIN_ARCHIVE_MESSAGES = 2;
export const DEFAULT_MAX_ARCHIVE_MESSAGES = 256;

export type ArchiveRange = { ok: true; start: number; cut: number } | { ok: false; reason: string };

/**
 * Choose the archived prefix `[start, cut)` of `messages`.
 *
 * Invariants enforced here (the upstream serializer does NOT enforce them):
 * - the latest user message is always retained verbatim (cut <= lastUser);
 * - at least `retainLast` messages are retained verbatim;
 * - the retained suffix never starts on a toolResult, so no retained result
 *   is orphaned from its call (the archive tolerates orphans; the live
 *   suffix must not create them);
 * - no `unsafe` message is ever archived: `start` advances past the last
 *   unsafe message inside the candidate range, and those messages remain in
 *   the projected output verbatim;
 * - the archive covers at most `maxArchiveMessages` messages.
 */
export function chooseArchiveRange(messages: NormalizedMessage[], policy: ArchivePolicy = {}): ArchiveRange {
	// Policy fields are used as indices/bounds below: anything that is not a
	// finite nonnegative integer (fractions, negatives, NaN, Infinity,
	// non-numbers, a non-object policy) is refused rather than coerced —
	// a fractional retainLast would index messages[cut] out of bounds.
	if (!isRecord(policy)) return { ok: false, reason: "invalid_policy" };
	for (const field of ["retainLast", "minArchiveMessages", "maxArchiveMessages"] as const) {
		const value = policy[field];
		if (value !== undefined && (!Number.isInteger(value) || value < 0)) {
			return { ok: false, reason: `invalid_policy:${field}` };
		}
	}
	const retainLast = policy.retainLast ?? DEFAULT_RETAIN_LAST;
	const minArchive = Math.max(1, policy.minArchiveMessages ?? DEFAULT_MIN_ARCHIVE_MESSAGES);
	const maxArchive = Math.max(1, policy.maxArchiveMessages ?? DEFAULT_MAX_ARCHIVE_MESSAGES);
	const n = messages.length;

	let lastUser = -1;
	for (let i = n - 1; i >= 0; i--) {
		if (messages[i].role === "user") {
			lastUser = i;
			break;
		}
	}
	if (lastUser < 0) return { ok: false, reason: "no_user_message" };

	// Retained suffix must include the latest user message AND at least
	// retainLast messages.
	let cut = Math.min(lastUser, n - retainLast);
	// Never start the retained suffix on a toolResult: its call would be
	// archived while the result stays live, orphaning the pair.
	while (cut > 0 && messages[cut].role === "toolResult") cut--;
	if (cut <= 0) return { ok: false, reason: "insufficient_history" };

	// Unsafe messages are never archived: start after the last unsafe message
	// inside the candidate prefix. They stay verbatim in the output.
	let start = 0;
	for (let i = 0; i < cut; i++) {
		if (messages[i].unsafe) start = i + 1;
	}
	// Bound the archive size; messages before `start` remain verbatim.
	if (cut - start > maxArchive) start = cut - maxArchive;

	if (cut - start < minArchive) return { ok: false, reason: "insufficient_history" };
	return { ok: true, start, cut };
}
