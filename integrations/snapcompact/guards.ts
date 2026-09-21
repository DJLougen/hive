// guards.ts — the package's single canonical record guard. Wire input is
// untyped JSON; this narrows "is a plain object" only — field checks stay at
// the use site (typeof / Array.isArray / in).

export function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}
