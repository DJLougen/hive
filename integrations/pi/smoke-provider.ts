// smoke-provider.ts — synthetic provider + deterministic bash fixture for the
// Hive Pi smoke test (integrations/pi/smoke.mjs).
//
// This file is NEVER auto-loaded: it lives outside every Pi discovery root and
// is only passed via an explicit `-e` flag by the smoke runner. It registers:
//   - a credential-free native provider ("hive-smoke") built on the published
//     pi-ai faux provider, whose scripted responses emit bash tool calls and
//     whose response factories capture the exact model-visible Context on
//     every LLM call;
//   - an override of the built-in "bash" tool that returns deterministic
//     synthetic output — no real shell is ever spawned.
//
// Result snapshots are written to $HIVE_PI_SMOKE_RESULT as JSON. All fixture
// text is synthetic; nothing here touches credentials, the network, or the
// filesystem outside that one result file.

import { fauxAssistantMessage, fauxProvider, fauxToolCall, Type } from "@earendil-works/pi-ai";
import type { Context, Message } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { writeFileSync } from "node:fs";

const PROVIDER = "hive-smoke";
const MODEL_ID = "smoke-1";
const CANARY = "smoke-canary";

// --- deterministic fixtures -------------------------------------------------
// pass_* : successful test-run logs — valid compression candidates.
// fail_* : failing run — content-based exclusion must keep it verbatim.
// patch_*: diff-like output — source/patch exclusion must keep it verbatim.
function passLog(tag: string, cases: number): string {
  const lines = [
    `============================= ${tag} test session starts ==============================`,
    `platform darwin -- Python 3.13.1, pytest-8.3.4 ${CANARY}-${tag}`,
    `collected ${cases} items`,
    "",
  ];
  for (let i = 1; i <= cases; i++) {
    lines.push(`tests/test_alpha.py::test_case_${String(i).padStart(3, "0")} PASSED`);
  }
  lines.push("", `============================= ${cases} passed in 4.21s ==============================`);
  return lines.join("\n");
}

function failLog(tag: string): string {
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
  for (let i = 0; i < 40; i++) {
    lines.push(`E       assert compute(${i}) == ${i + 1}`);
  }
  lines.push("tests/test_beta.py:42: AssertionError");
  lines.push(`========================= 2 failed, 38 passed in 1.02s =========================`);
  return lines.join("\n");
}

function patchLog(tag: string): string {
  const lines = [
    `diff --git a/src/widget.c b/src/widget.c ${CANARY}-${tag}`,
    "--- a/src/widget.c",
    "+++ b/src/widget.c",
    "@@ -10,6 +10,9 @@ static int widget_init(void)",
  ];
  for (let i = 0; i < 60; i++) {
    lines.push(`+    entry_${i} = allocate_slot(${i});`);
  }
  return lines.join("\n");
}

const FIXTURES: Record<string, string> = {
  pass_a: passLog("pass_a", 300),
  fail_b: failLog("fail_b"),
  patch_c: patchLog("patch_c"),
  pass_d: passLog("pass_d", 300),
};

// --- context capture ---------------------------------------------------------
interface ToolResultSnapshot {
  index: number;
  toolCallId: string;
  toolName: string;
  isError: boolean;
  text: string;
}

interface CallSnapshot {
  callIndex: number;
  messageCount: number;
  roles: string[];
  toolResults: ToolResultSnapshot[];
}

const calls: CallSnapshot[] = [];

function textOf(message: Message): string {
  const content = (message as { content?: unknown }).content;
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((b): b is { type: string; text: string } => b?.type === "text")
    .map((b) => b.text)
    .join("\n");
}

function snapshot(context: Context): void {
  const toolResults: ToolResultSnapshot[] = [];
  let index = 0;
  for (const m of context.messages) {
    if (m.role === "toolResult") {
      toolResults.push({
        index: index++,
        toolCallId: m.toolCallId,
        toolName: m.toolName,
        isError: m.isError,
        text: textOf(m),
      });
    }
  }
  calls.push({
    callIndex: calls.length,
    messageCount: context.messages.length,
    roles: context.messages.map((m) => m.role),
    toolResults,
  });
}

function writeResult(): void {
  const out = process.env.HIVE_PI_SMOKE_RESULT;
  if (!out) return;
  try {
    writeFileSync(
      out,
      JSON.stringify(
        {
          provider: PROVIDER,
          model: MODEL_ID,
          fixtureKeys: Object.keys(FIXTURES),
          fixtureLengths: Object.fromEntries(
            Object.entries(FIXTURES).map(([k, v]) => [k, v.length]),
          ),
          callCount: calls.length,
          calls,
        },
        null,
        2,
      ),
    );
  } catch {
    // best effort; the runner reports a missing file as a failure
  }
}

// --- extension ---------------------------------------------------------------
export default function (pi: ExtensionAPI) {
  const faux = fauxProvider({
    provider: PROVIDER,
    api: "hive-smoke-api",
    models: [
      {
        id: MODEL_ID,
        name: "Hive Smoke Model",
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 128000,
        maxTokens: 16384,
      },
    ],
  });

  faux.setResponses([
    (context) => {
      snapshot(context);
      return fauxAssistantMessage(
        [fauxToolCall("bash", { command: "pass_a" }), fauxToolCall("bash", { command: "fail_b" }), fauxToolCall("bash", { command: "patch_c" })],
        { stopReason: "toolUse" },
      );
    },
    (context) => {
      snapshot(context);
      return fauxAssistantMessage([fauxToolCall("bash", { command: "pass_d" })], { stopReason: "toolUse" });
    },
    (context) => {
      snapshot(context);
      return fauxAssistantMessage("smoke done", { stopReason: "stop" });
    },
  ]);

  pi.registerProvider(faux.provider);

  // Overrides the built-in bash tool (extension registrations replace builtins
  // in the tool registry). Deterministic output; no shell is spawned.
  pi.registerTool({
    name: "bash",
    label: "Bash (smoke fixture)",
    description: "Run a shell command (smoke fixture: returns canned output)",
    parameters: Type.Object({
      command: Type.String({ description: "Command to run" }),
    }),
    async execute(_toolCallId, params) {
      const key = (params as { command?: string }).command ?? "";
      const text = FIXTURES[key] ?? `unknown fixture: ${key}`;
      return { content: [{ type: "text", text }], details: {} };
    },
  });

  pi.on("agent_end", async () => writeResult());
  pi.on("session_shutdown", async () => writeResult());
  process.on("exit", writeResult);
}
