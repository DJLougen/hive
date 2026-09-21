#!/usr/bin/env node
// smoke.mjs — end-to-end smoke test for the Hive Pi integration.
//
// Drives the REAL installed Pi CLI (dist/bundle/cli.js) as a child process in
// --mode json with a fully isolated HOME / agent dir / data dir / project dir.
// A synthetic provider extension (smoke-provider.ts, explicit -e only, never
// discovered) emits scripted bash tool calls; the built-in bash tool is
// overridden by a deterministic fixture so no real shell, network, or
// credentials are involved.
//
// Arms:
//   observe          context reaches the provider byte-identical
//   compress         older successful test-output logs are compressed in the
//                    model-visible context; failure/patch/latest stay verbatim
//   bridge-fail      compress mode with a broken bridge: context unchanged
//   off              no event file written
//   compress-session compress mode with a real session dir: the persisted
//                    transcript keeps the original uncompressed tool results
//
// Exit 0 on success, 1 on failure. All fixture content is synthetic.

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..");
const PINNED_PI_VERSION = "0.85.1";
const RUNTIME = process.env.HIVE_PI_RUNTIME ?? path.join(os.homedir(), ".local/share/hive/pi-runtime");
const PI_PKG = path.join(RUNTIME, "node_modules", "@earendil-works", "pi-coding-agent");
const PI_CLI = path.join(PI_PKG, "dist", "bundle", "cli.js");
const HIVE_EXT = path.join(HERE, "hive.ts");
const SMOKE_EXT = path.join(HERE, "smoke-provider.ts");
const BRIDGE = process.env.HIVE_PI_BRIDGE ?? path.join(REPO, "scripts", "hive_pi.py");
const PYTHON = process.env.HIVE_PI_PYTHON ?? path.join(REPO, ".venv", "bin", "python");

const PROVIDER = "hive-smoke";
const MODEL = "smoke-1";
const TIMEOUT_MS = 120_000;

const failures = [];
const notes = [];
function check(cond, label, detail = "") {
  if (cond) {
    console.log(`  ok    ${label}`);
  } else {
    failures.push(`${label}${detail ? ` — ${detail}` : ""}`);
    console.log(`  FAIL  ${label}${detail ? ` — ${detail}` : ""}`);
  }
}

// --- preflight ---------------------------------------------------------------
function die(msg) {
  console.error(`smoke: ${msg}`);
  process.exit(2);
}
const nodeVer = process.versions.node.split(".").map(Number);
if (nodeVer[0] < 22 || (nodeVer[0] === 22 && nodeVer[1] < 19)) {
  die(`node ${process.versions.node} too old; Pi ${PINNED_PI_VERSION} requires >= 22.19`);
}
if (!fs.existsSync(PI_CLI)) die(`Pi CLI not found at ${PI_CLI} (set HIVE_PI_RUNTIME)`);
const pkgVersion = JSON.parse(fs.readFileSync(path.join(PI_PKG, "package.json"), "utf8")).version;
if (pkgVersion !== PINNED_PI_VERSION) die(`installed Pi is ${pkgVersion}, expected ${PINNED_PI_VERSION}`);
for (const f of [HIVE_EXT, SMOKE_EXT, BRIDGE]) {
  if (!fs.existsSync(f)) die(`required file missing: ${f}`);
}
if (!fs.existsSync(PYTHON)) die(`HIVE_PI_PYTHON not found: ${PYTHON}`);

// --- fixture keys (must match smoke-provider.ts) ------------------------------
const FIXTURE_KEYS = ["pass_a", "fail_b", "patch_c", "pass_d"];
const CANARY = "smoke-canary";

// --- arm runner ---------------------------------------------------------------
function runArm(name, { mode, session = false, bridgePath = BRIDGE }) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), `hive-pi-smoke-${name}-`));
  const dirs = {};
  for (const d of ["home", "agent", "data", "project", "sessions"]) {
    dirs[d] = path.join(root, d);
    fs.mkdirSync(dirs[d], { recursive: true });
  }
  const resultPath = path.join(root, "result.json");

  const env = {
    PATH: process.env.PATH ?? "/usr/bin:/bin",
    HOME: dirs.home,
    USER: process.env.USER ?? "smoke",
    LANG: "en_US.UTF-8",
    TERM: "dumb",
    NO_COLOR: "1",
    PI_CODING_AGENT_DIR: dirs.agent,
    PI_CODING_AGENT_SESSION_DIR: dirs.sessions,
    PI_OFFLINE: "1",
    PI_TELEMETRY: "0",
    HIVE_PI_MODE: mode,
    HIVE_PI_DATA_DIR: dirs.data,
    HIVE_PI_PYTHON: PYTHON,
    HIVE_PI_BRIDGE: bridgePath,
    HIVE_PI_SMOKE_RESULT: resultPath,
  };

  const args = [
    PI_CLI,
    "--mode", "json",
    "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes",
    "--no-context-files", "--no-approve",
    "--tools", "bash",
    "--provider", PROVIDER, "--model", MODEL,
    "--thinking", "off",
    "-e", HIVE_EXT,
    "-e", SMOKE_EXT,
    "--hive-mode", mode,
  ];
  if (session) args.push("--session-dir", dirs.sessions);
  else args.push("--no-session");
  args.push("smoke run");

  const proc = spawnSync(process.execPath, args, {
    cwd: dirs.project,
    env,
    timeout: TIMEOUT_MS,
    maxBuffer: 64 * 1024 * 1024,
    encoding: "utf8",
  });

  const eventFiles = fs.existsSync(dirs.data)
    ? fs.readdirSync(dirs.data).filter((f) => f.endsWith(".jsonl")).map((f) => path.join(dirs.data, f))
    : [];
  const events = [];
  for (const f of eventFiles) {
    for (const line of fs.readFileSync(f, "utf8").split("\n")) {
      if (!line.trim()) continue;
      try { events.push(JSON.parse(line)); } catch { /* malformed row: reporter's problem, count it */ }
    }
  }
  const rawEvents = eventFiles.map((f) => fs.readFileSync(f, "utf8")).join("");

  let result = null;
  if (fs.existsSync(resultPath)) {
    try { result = JSON.parse(fs.readFileSync(resultPath, "utf8")); } catch { /* leave null */ }
  }

  let sessionTexts = null;
  if (session) {
    sessionTexts = [];
    const sessionFiles = [];
    const walk = (dir) => {
      for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
        const p = path.join(dir, e.name);
        if (e.isDirectory()) walk(p);
        else if (e.name.endsWith(".jsonl")) sessionFiles.push(p);
      }
    };
    if (fs.existsSync(dirs.sessions)) walk(dirs.sessions);
    for (const f of sessionFiles) {
      for (const line of fs.readFileSync(f, "utf8").split("\n")) {
        if (!line.trim()) continue;
        let row;
        try { row = JSON.parse(line); } catch { continue; }
        const m = row?.message;
        if (row?.type === "message" && m?.role === "toolResult") {
          const text = (m.content ?? []).filter((b) => b?.type === "text").map((b) => b.text).join("\n");
          sessionTexts.push(text);
        }
      }
    }
  }

  return { name, proc, result, events, rawEvents, sessionTexts, root };
}

// --- shared assertions --------------------------------------------------------
function assertBasics(arm) {
  check(arm.proc.status === 0 && !arm.proc.error, `${arm.name}: pi exits 0`,
    arm.proc.error ? String(arm.proc.error) : `status=${arm.proc.status} stderr=${(arm.proc.stderr ?? "").slice(0, 400)}`);
  check(arm.result !== null, `${arm.name}: provider wrote result.json`);
  if (!arm.result) return false;
  check(arm.result.callCount === 3, `${arm.name}: exactly 3 LLM calls`, `got ${arm.result.callCount}`);
  return arm.result.callCount === 3;
}

// The provider records snapshots; fixture bodies are rebuilt here identically
// so assertions compare exact bytes.
function passLog(tag, cases) {
  const lines = [
    `============================= ${tag} test session starts ==============================`,
    `platform darwin -- Python 3.13.1, pytest-8.3.4 ${CANARY}-${tag}`,
    `collected ${cases} items`,
    "",
  ];
  for (let i = 1; i <= cases; i++) lines.push(`tests/test_alpha.py::test_case_${String(i).padStart(3, "0")} PASSED`);
  lines.push("", `============================= ${cases} passed in 4.21s ==============================`);
  return lines.join("\n");
}
function failLog(tag) {
  const lines = [
    `============================= ${tag} test session starts ==============================`,
    `collected 40 items ${CANARY}-${tag}`,
    "",
    "tests/test_beta.py test_case_001 FAILED",
    "tests/test_beta.py test_case_002 FAILED",
    "",
    "=================================== FAILURES ====================================",
    "_____________________________ test_case_001 _____________________________________",
  ];
  for (let i = 0; i < 40; i++) lines.push(`E       assert compute(${i}) == ${i + 1}`);
  lines.push("tests/test_beta.py:42: AssertionError");
  lines.push(`========================= 2 failed, 38 passed in 1.02s =========================`);
  return lines.join("\n");
}
function patchLog(tag) {
  const lines = [
    `diff --git a/src/widget.c b/src/widget.c ${CANARY}-${tag}`,
    "--- a/src/widget.c",
    "+++ b/src/widget.c",
    "@@ -10,6 +10,9 @@ static int widget_init(void)",
  ];
  for (let i = 0; i < 60; i++) lines.push(`+    entry_${i} = allocate_slot(${i});`);
  return lines.join("\n");
}
const EXPECTED = {
  pass_a: passLog("pass_a", 300),
  fail_b: failLog("fail_b"),
  patch_c: patchLog("patch_c"),
  pass_d: passLog("pass_d", 300),
};

// Map a recorded tool result (by order) to its fixture key.
const ORDER = ["pass_a", "fail_b", "patch_c", "pass_d"];

function assertUnchanged(arm, callIdx, label) {
  const call = arm.result.calls[callIdx];
  const trs = call.toolResults;
  check(trs.length === 4, `${arm.name} ${label}: 4 tool results visible`, `got ${trs.length}`);
  let allExact = true;
  for (let i = 0; i < Math.min(4, trs.length); i++) {
    if (trs[i].text !== EXPECTED[ORDER[i]]) allExact = false;
    if (trs[i].toolName !== "bash" || trs[i].isError !== false) allExact = false;
  }
  check(allExact, `${arm.name} ${label}: all tool results byte-identical to fixtures`);
}

function assertEventsPrivacy(arm) {
  check(!arm.rawEvents.includes(CANARY) && !arm.rawEvents.includes("test_case_001"),
    `${arm.name}: no raw fixture text in event log`);
  for (const e of arm.events) {
    if (e.schema_version !== 1 || e.source !== "pi") {
      check(false, `${arm.name}: event schema_version=1 source=pi`, JSON.stringify(e).slice(0, 200));
      return;
    }
  }
  check(true, `${arm.name}: all events schema_version=1 source=pi`);
}

// --- run arms ------------------------------------------------------------------
console.log("hive-pi smoke — real Pi CLI, synthetic provider, no network/credentials");
console.log(`pi: ${PI_CLI} (v${pkgVersion})`);

const arms = {};

console.log("\n[observe]");
arms.observe = runArm("observe", { mode: "observe" });
if (assertBasics(arms.observe)) {
  assertUnchanged(arms.observe, 2, "final-call context");
}
assertEventsPrivacy(arms.observe);
{
  const types = new Set(arms.observe.events.map((e) => e.event_type));
  check(types.has("collector_started"), "observe: collector_started event");
  check(types.has("session_started"), "observe: session_started event");
  check(types.has("tool_result"), "observe: tool_result events");
  const trEvents = arms.observe.events.filter((e) => e.event_type === "tool_result");
  check(trEvents.length >= 4 && trEvents.every((e) => e.tool_category === "bash" && e.is_error === false),
    "observe: >=4 bash tool_result events, is_error=false", `got ${trEvents.length}`);
  check(types.has("collector_stopped"), "observe: collector_stopped event");
}

console.log("\n[compress]");
arms.compress = runArm("compress", { mode: "compress" });
if (assertBasics(arms.compress)) {
  const final = arms.compress.result.calls[2];
  const trs = final.toolResults;
  check(trs.length === 4, "compress: final call sees 4 tool results", `got ${trs.length}`);
  if (trs.length === 4) {
    const [a, b, c, d] = trs;
    check(a.text !== EXPECTED.pass_a && a.text.startsWith("[test]") && a.text.includes("300 passed"),
      "compress: oldest pass log replaced by compressed form",
      `len ${EXPECTED.pass_a.length} -> ${a.text.length}`);
    check(a.text.length < EXPECTED.pass_a.length, "compress: compressed text is shorter");
    check(b.text === EXPECTED.fail_b, "compress: failing output kept verbatim");
    check(c.text === EXPECTED.patch_c, "compress: patch-like output kept verbatim");
    check(d.text === EXPECTED.pass_d, "compress: latest tool result kept verbatim");
    notes.push(`compress: pass_a ${EXPECTED.pass_a.length}B -> ${a.text.length}B in model-visible context`);
  }
  // earlier call (index 1): pass_a/fail_b/patch_c visible; patch_c is latest there
  const mid = arms.compress.result.calls[1];
  if (mid && mid.toolResults.length === 3) {
    check(mid.toolResults[0].text !== EXPECTED.pass_a, "compress: pass_a already compressed at call 2");
    check(mid.toolResults[1].text === EXPECTED.fail_b, "compress: fail_b verbatim at call 2");
    check(mid.toolResults[2].text === EXPECTED.patch_c, "compress: patch_c verbatim at call 2 (latest)");
  }
}
assertEventsPrivacy(arms.compress);
{
  const ctxEvents = arms.compress.events.filter((e) => e.event_type === "context");
  check(ctxEvents.length >= 1, "compress: context events emitted", `got ${ctxEvents.length}`);
  const okEvents = ctxEvents.filter((e) => e.bridge_status === "ok");
  check(okEvents.length >= 1, "compress: bridge_status=ok context event",
    `statuses=${ctxEvents.map((e) => e.bridge_status).join(",")}`);
  const changed = okEvents.reduce((n, e) => n + (e.changed_items ?? 0), 0);
  check(changed >= 1, "compress: changed_items >= 1 across ok context events", `got ${changed}`);
  const bridgeCalls = ctxEvents.reduce((n, e) => n + (e.bridge_calls ?? 0), 0);
  const cacheHits = ctxEvents.reduce((n, e) => n + (e.cache_hits ?? 0), 0);
  check(bridgeCalls === 1, "compress: repeated historical output invokes bridge once",
    `bridge_calls=${bridgeCalls}`);
  check(cacheHits >= 1, "compress: later context reuses cached compression",
    `cache_hits=${cacheHits}`);
  notes.push(`compress: context events=${ctxEvents.length} changed_items=${changed}`);
}

console.log("\n[bridge-fail]");
arms.bridgeFail = runArm("bridge-fail", {
  mode: "compress",
  bridgePath: path.join(os.tmpdir(), "hive-pi-smoke-no-such-bridge.py"),
});
if (assertBasics(arms.bridgeFail)) {
  assertUnchanged(arms.bridgeFail, 2, "final-call context");
}
assertEventsPrivacy(arms.bridgeFail);
{
  const ctxEvents = arms.bridgeFail.events.filter((e) => e.event_type === "context");
  const badOk = ctxEvents.filter((e) => e.bridge_status === "ok");
  check(badOk.length === 0, "bridge-fail: no context event reports bridge_status=ok",
    `statuses=${ctxEvents.map((e) => e.bridge_status).join(",") || "none"}`);
}

console.log("\n[off]");
arms.off = runArm("off", { mode: "off" });
if (assertBasics(arms.off)) {
  assertUnchanged(arms.off, 2, "final-call context");
}
check(arms.off.events.length === 0, "off: no event rows written", `got ${arms.off.events.length}`);

console.log("\n[compress-session]");
arms.session = runArm("compress-session", { mode: "compress", session: true });
if (assertBasics(arms.session)) {
  check(arms.session.sessionTexts !== null && arms.session.sessionTexts.length === 4,
    "compress-session: transcript has 4 tool results", `got ${arms.session.sessionTexts?.length}`);
  if (arms.session.sessionTexts?.length === 4) {
    let allOriginal = true;
    for (let i = 0; i < 4; i++) {
      if (arms.session.sessionTexts[i] !== EXPECTED[ORDER[i]]) allOriginal = false;
    }
    check(allOriginal, "compress-session: persisted transcript keeps original uncompressed text");
  }
}

// --- summary -------------------------------------------------------------------
console.log("\n--- measured (synthetic fixtures only; no benchmark claims) ---");
for (const n of notes) console.log(`  ${n}`);
console.log("  all tool outputs, prompts, and provider responses are deterministic synthetic fixtures");
if (failures.length) {
  console.log(`\nSMOKE FAILED: ${failures.length} assertion(s)`);
  for (const f of failures) console.log(`  - ${f}`);
  process.exit(1);
}
console.log("\nSMOKE PASSED");
process.exit(0);
