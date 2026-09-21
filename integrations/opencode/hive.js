// hive.js — Hive observe-only data collector for OpenCode 1.18.31.
//
// Native server plugin. Exports exactly one plugin function (the OpenCode
// legacy loader iterates every exported value and expects each to be a
// plugin function, so nothing else may be exported).
//
// Contract (shared with the Hive report pipeline — do not change
// independently):
//   - JSONL, one record per line, schema_version=1, mode='observe',
//     source='opencode'.
//   - Envelope: run_id / project_id / session_id are salted-pseudonymous,
//     plus event_type, event_id, timestamp_ms.
//   - event_type ∈ session_started | session_idle | session_error |
//     tool_state | step_usage | request_observed | collector_started |
//     collector_stopped.
//   - tool_state: tool_category ∈ read|list|grep|bash|edit|write|other;
//     status ∈ pending|running|completed|error; optional duration_ms.
//   - session_error: error_kind is a fixed allowlist label (ERROR_KINDS
//     below, shared with scripts/opencode_report.py); arbitrary error
//     names map to "other".
//   - step_usage: hashed message_id/part_id, nullable tokens
//     {input,output,reasoning,cache_read,cache_write}, nullable
//     cost_estimate. Stable dedup key = session/message/part; revisions
//     re-emit with an incremented `revision` so consumers keep the latest
//     without double counting. Unknown usage is unknown, never "free".
//   - Output: $HIVE_OPENCODE_DATA_DIR or ~/.local/share/hive/opencode/
//     <run_id>.jsonl (unique per plugin instance). HIVE_OPENCODE_DISABLED=1
//     disables collection entirely.
//
// Privacy: scalar allowlist only. Never serializes prompts, message text,
// tool args/output/title/error/metadata, paths, diffs, headers, provider
// options, or raw event objects. Event hooks are fire-and-forget in the
// host, so every failure is contained inside this file. No SDK client
// calls, no network, no mutation of host objects.

import { createHmac, randomBytes } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const SCHEMA_VERSION = 1;
const COLLECTOR_VERSION = "hive-opencode-observe/1";
const QUEUE_MAX = 4096;
const SALT_FILE = "salt";

const TOOL_CATEGORY = new Map([
  ["read", "read"],
  ["glob", "list"],
  ["ls", "list"],
  ["list", "list"],
  ["grep", "grep"],
  ["search", "grep"],
  ["bash", "bash"],
  ["shell", "bash"],
  ["edit", "edit"],
  ["patch", "edit"],
  ["apply_patch", "edit"],
  ["write", "write"],
]);
const TOOL_STATUSES = new Set(["pending", "running", "completed", "error"]);

function dataDir() {
  const override = process.env.HIVE_OPENCODE_DATA_DIR;
  if (typeof override === "string" && override.length > 0) return override;
  return path.join(os.homedir(), ".local", "share", "hive", "opencode");
}

function loadOrCreateSalt(dir) {
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
    if (e && e.code === "EEXIST") {
      const raced = fs.readFileSync(file, "utf8").trim();
      if (/^[0-9a-f]{64}$/.test(raced)) return raced;
    }
    throw e;
  }
}

function num(v) {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function shortString(v, max = 128) {
  return typeof v === "string" && v.length > 0 ? v.slice(0, max) : null;
}

function toolCategory(name) {
  if (typeof name !== "string") return "other";
  return TOOL_CATEGORY.get(name.toLowerCase()) ?? "other";
}

// Coarse error-kind classification only; never the message or data.
// Fixed allowlist shared with scripts/opencode_report.py — anything
// outside it maps to "other" so arbitrary error names are never emitted.
const ERROR_KINDS = new Set([
  "unknown", "other",
  "Error", "TypeError", "RangeError", "SyntaxError", "ReferenceError",
  "EvalError", "URIError", "AggregateError",
  "AbortError", "TimeoutError", "NetworkError", "ProviderAuthError",
  "RateLimitError", "QuotaExceededError", "ContextOverflowError",
  "ModelNotFoundError", "APIError",
]);
function errorKind(err) {
  const name = err && typeof err === "object" ? err.name : null;
  if (typeof name !== "string" || name.length === 0) return "unknown";
  const cleaned = name.replace(/[^A-Za-z0-9_]/g, "").slice(0, 32);
  if (cleaned.length === 0) return "unknown";
  return ERROR_KINDS.has(cleaned) ? cleaned : "other";
}


function createSink(dir, runId) {
  const file = path.join(dir, `${runId}.jsonl`);
  const fd = fs.openSync(file, "a", 0o600);

  const queue = [];
  let scheduled = false;
  let closed = false;
  let written = 0;
  let dropped = 0;
  let writeErrors = 0;

  function drain() {
    scheduled = false;
    while (queue.length > 0 && !closed) {
      const line = queue.shift();
      try {
        fs.writeSync(fd, line);
        written += 1;
      } catch {
        writeErrors += 1;
      }
    }
  }

  function enqueue(record) {
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

  // Terminal path: flush the queued records, then write the final record
  // directly so collector_stopped is never dropped at QUEUE_MAX.
  // stats.written counts this final record itself.
  function finish(finalRecord) {
    drain();
    closed = true;
    try {
      finalRecord.stats = {
        written: written + 1,
        dropped,
        write_errors: writeErrors,
      };
      fs.writeSync(fd, JSON.stringify(finalRecord) + "\n");
      written += 1;
    } catch {
      writeErrors += 1;
    }
    try {
      fs.closeSync(fd);
    } catch {
      // already closed
    }
  }

  return { enqueue, finish, file };
}

export const HivePlugin = async (input) => {
  if (process.env.HIVE_OPENCODE_DISABLED === "1") return {};

  const runId = `r_${Date.now().toString(36)}_${randomBytes(8).toString("hex")}`;

  let sink;
  let hmacKey;
  try {
    const dir = dataDir();
    fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
    try {
      fs.chmodSync(dir, 0o700);
    } catch {
      // best effort on filesystems that support chmod
    }
    hmacKey = loadOrCreateSalt(dir);
    sink = createSink(dir, runId);
  } catch {
    // No usable data dir → collector is inert; register nothing rather
    // than risk touching the host.
    return {};
  }

  const pseud = (kind, raw) =>
    createHmac("sha256", hmacKey)
      .update(`${kind}:${String(raw)}`)
      .digest("hex")
      .slice(0, 32);

  const projectRaw =
    (input && input.project && input.project.id) ||
    (input && input.directory) ||
    "unknown";
  const projectId = pseud("project", projectRaw);

  let seq = 0;
  const stepRevisions = new Map(); // dedupKey -> revision count
  const toolLast = new Map(); // callID -> last emitted "status|duration"

  function envelope(eventType, sessionId) {
    seq += 1;
    return {
      schema_version: SCHEMA_VERSION,
      mode: "observe",
      source: "opencode",
      collector_version: COLLECTOR_VERSION,
      run_id: runId,
      project_id: projectId,
      session_id: sessionId ? pseud("session", sessionId) : null,
      event_type: eventType,
      event_id: `e_${seq}`,
      timestamp_ms: Date.now(),
    };
  }

  function emit(record) {
    try {
      sink.enqueue(record);
    } catch {
      // contained: collection failure must never reach the host
    }
  }

  function onSessionCreated(props) {
    if (!props || typeof props !== "object") return;
    const info = props.info && typeof props.info === "object" ? props.info : {};
    const rec = envelope("session_started", props.sessionID ?? info.id);
    rec.parent_session_id = info.parentID ? pseud("session", info.parentID) : null;
    emit(rec);
  }

  function onSessionIdle(props) {
    if (!props || typeof props !== "object") return;
    // Idle is a turn boundary, NOT session success or completion.
    emit(envelope("session_idle", props.sessionID));
  }

  function onSessionError(props) {
    if (!props || typeof props !== "object") return;
    const rec = envelope("session_error", props.sessionID);
    rec.error_kind = errorKind(props.error);
    emit(rec);
  }

  function onToolPart(part) {
    const state = part.state && typeof part.state === "object" ? part.state : {};
    const status = typeof state.status === "string" ? state.status : null;
    if (!status || !TOOL_STATUSES.has(status)) return; // never fabricate
    const time = state.time && typeof state.time === "object" ? state.time : {};
    const start = num(time.start);
    const end = num(time.end);
    const duration = start !== null && end !== null ? Math.max(0, end - start) : null;

    const callKey = String(part.callID ?? part.id);
    const fingerprint = `${status}|${duration}`;
    if (toolLast.get(callKey) === fingerprint) return; // repeat snapshot
    toolLast.set(callKey, fingerprint);

    const rec = envelope("tool_state", part.sessionID);
    rec.call_id = pseud("call", callKey);
    rec.tool_category = toolCategory(part.tool);
    rec.status = status;
    rec.duration_ms = duration;
    emit(rec);
  }

  function onStepFinish(part) {
    const t = part.tokens && typeof part.tokens === "object" ? part.tokens : {};
    const dedupKey = `${part.sessionID}/${part.messageID}/${part.id}`;
    const revision = stepRevisions.get(dedupKey) ?? 0;
    stepRevisions.set(dedupKey, revision + 1);

    const rec = envelope("step_usage", part.sessionID);
    rec.message_id = pseud("message", part.messageID ?? "");
    rec.part_id = pseud("part", part.id ?? "");
    rec.dedup_key = pseud("step", dedupKey);
    rec.revision = revision;
    // Nullable: absent usage stays null — unknown is not zero/free.
    rec.tokens = {
      input: num(t.input),
      output: num(t.output),
      reasoning: num(t.reasoning),
      cache_read: num(t.cache && t.cache.read),
      cache_write: num(t.cache && t.cache.write),
    };
    // OpenCode-reported estimate, not authoritative billing.
    rec.cost_estimate = num(part.cost);
    emit(rec);
  }

  function onPartUpdated(props) {
    const part = props && typeof props === "object" ? props.part : null;
    if (!part || typeof part !== "object") return;
    if (part.type === "tool") onToolPart(part);
    else if (part.type === "step-finish") onStepFinish(part);
    // message.part.delta and all other part types carry raw content: ignore.
  }

  const hooks = {
    // Fire-and-forget in the host (void, unawaited): contain everything.
    event: async ({ event } = {}) => {
      try {
        const type = event && event.type;
        const props = event && typeof event === "object" ? event.properties : null;
        switch (type) {
          case "session.created":
            onSessionCreated(props);
            break;
          case "session.idle":
            onSessionIdle(props);
            break;
          case "session.error":
            onSessionError(props);
            break;
          case "message.part.updated":
            onPartUpdated(props);
            break;
          default:
            break; // session.status/diff/deleted, message.updated, etc.: not collected
        }
      } catch {
        // contained
      }
    },

    // Read-only decision-boundary observation. Mutating `output` here would
    // change real sampling — we only read scalar IDs from input.
    "chat.params": async (input) => {
      try {
        const model = input && typeof input.model === "object" ? input.model : {};
        const rec = envelope("request_observed", input && input.sessionID);
        rec.agent = shortString(input && input.agent, 64);
        rec.provider_id = shortString(model.providerID, 64);
        rec.model_id = shortString(model.modelID, 128);
        emit(rec);
      } catch {
        // contained
      }
    },

    dispose: async () => {
      try {
        sink.finish(envelope("collector_stopped", null));
      } catch {
        // contained
      }
    },
  };

  try {
    const started = envelope("collector_started", null);
    started.stats = { queue_max: QUEUE_MAX };
    sink.enqueue(started);
  } catch {
    // contained
  }

  return hooks;
};
