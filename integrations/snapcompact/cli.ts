// cli.ts — JSON stdin/stdout wrapper for the snapcompact history projection.
//
// Protocol version 1 (one request per process):
//   stdin  : one JSON object, UTF-8, at most MAX_INPUT_BYTES (16 MiB):
//            { "version": 1, "action": "project",
//              "model": {"api"?: str, "id"?: str, "provider"?: str,
//                        "supports_images": bool},
//              "messages": [InputMessage, ...],
//              "policy"?: {...}, "previous_archive"?: {...},
//              "previous_summary"?: str, "first_kept_entry_id"?: str,
//              "tokens_before"?: int, "file_ops"?: {...} }
//   stdout : one JSON object:
//            { "version": 1, "ok": bool, "applied": bool, "reason": str,
//              "messages": [...], "archive"?: {...}, "stats"?: {...},
//              "error"?: str }
//   exit   : always 0 once the response is written — the response itself
//            carries the failure. Hosts treat any non-JSON output or nonzero
//            exit as "unchanged" and keep their original history.
//
// Fail-open contract: on ANY error (bad input, missing package, renderer
// failure, ineligible model, truncation/omission) the response is applied:false
// with the input messages echoed verbatim. The host's own history stays
// authoritative; this process never mutates anything.
//
// The engine is imported lazily so a missing/broken install still produces a
// clean applied:false response instead of a crash.

import { isRecord } from "./guards.ts";
import { parseRequest, projectHistory } from "./project.ts";
import type { ProjectResponse, SnapcompactEngine } from "./project.ts";
const MAX_INPUT_BYTES = 16 * 1024 * 1024;

function respond(response: ProjectResponse): void {
	process.stdout.write(`${JSON.stringify(response)}\n`);
}

function failOpen(messages: unknown[], reason: string, error?: unknown): void {
	respond({
		version: 1,
		ok: !(reason === "invalid_request" || reason === "input_too_large" || reason === "invalid_json"),
		applied: false,
		reason,
		messages,
		...(error !== undefined ? { error: error instanceof Error ? error.message : String(error) } : {}),
	});
}

async function readStdin(): Promise<{ text: string } | { reason: string }> {
	const chunks: Buffer[] = [];
	let total = 0;
	for await (const chunk of process.stdin) {
		const buf = chunk as Buffer;
		total += buf.length;
		if (total > MAX_INPUT_BYTES) return { reason: "input_too_large" };
		chunks.push(buf);
	}
	return { text: Buffer.concat(chunks).toString("utf8") };
}

async function loadEngine(): Promise<SnapcompactEngine> {
	// Dynamic import: the pinned package pulls in @oh-my-pi/pi-natives (a
	// ~157 MB native renderer). A missing install must degrade, not crash.
	const mod = (await import("@oh-my-pi/snapcompact")) as unknown as SnapcompactEngine;
	return mod;
}

async function main(): Promise<void> {
	const input = await readStdin();
	if ("reason" in input) {
		failOpen([], input.reason);
		return;
	}
	let raw: unknown;
	try {
		raw = JSON.parse(input.text);
	} catch (error) {
		failOpen([], "invalid_json", error);
		return;
	}
	const parsed = parseRequest(raw);
	if ("reason" in parsed) {
		failOpen(isRecord(raw) && Array.isArray(raw.messages) ? raw.messages : [], parsed.reason);
		return;
	}
	let engine: SnapcompactEngine;
	try {
		engine = await loadEngine();
	} catch (error) {
		failOpen(Array.isArray(parsed.request.messages) ? parsed.request.messages : [], "engine_unavailable", error);
		return;
	}
	try {
		respond(await projectHistory(parsed.request, engine));
	} catch (error) {
		failOpen(Array.isArray(parsed.request.messages) ? parsed.request.messages : [], "engine_error", error);
	}
}

await main();
