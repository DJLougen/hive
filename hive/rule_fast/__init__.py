"""Lightweight rule-based context compressor used as Hive's high-perf fast path.

Why does this exist?
--------------------
The honey-comb package we ship with has an ML classifier on the hot path
(TF-IDF + voting ensemble). On x86_64 that delivers ~1.5k msg/s — fine for
production telemetry, too slow for a tight agent loop on edge devices.

The honey-comb readme also reports a 25k msg/s *high-performance* path
(``thread_safe=False, metrics_enabled=False``) and a 28k msg/s *rule-only*
path. The rule-only path is what this module re-implements in a small,
self-contained way: it does not depend on the honey-comb ML model, fits on
Jetson / Raspberry Pi, and is what the Hive Step 1 benchmark and
integration example use by default.

The compression *semantics* match honey-comb's published labels
(CORE / DISTILL / COMPACT / DROP / STALE / ESCALATE) so downstream agents
do not need to special-case anything. We just skip the ML classifier and
go straight to the deterministic rules.

The classes in this module are drop-in compatible with the
:class:`honeycomb.HoneyComb` interface (``process(message)``,
``get_stats()``); the benchmark + integration example import them when
``HIVE_HONEYCOMB_FAST=1`` or ``--honey-comb-mode fast`` is set.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "CompressedMessage",
    "ContentType",
    "Label",
    "Message",
    "RuleFastHoneyComb",
    "classify_label",
]


# ---------------------------------------------------------------------------
# Labels and content types (mirror honey-comb's public taxonomy)
# ---------------------------------------------------------------------------


class Label:
    """Same string values as :class:`honeycomb.labels.Label`."""

    CORE = "core"
    DISTILL = "distill"
    COMPACT = "compact"
    DROP = "drop"
    STALE = "stale"
    ESCALATE = "escalate"


class ContentType:
    SYSTEM = "system"
    USER_GOAL = "user_goal"
    AGENT_REASONING = "agent_reasoning"
    AGENT_PATCH = "agent_patch"
    TOOL_CALL = "tool_call"
    TOOL_RESULT_FILE = "tool_result_file"
    TOOL_RESULT_TEST = "tool_result_test"
    TOOL_RESULT_ERROR = "tool_result_error"
    TOOL_RESULT_SEARCH = "tool_result_search"
    TOOL_RESULT_COMMAND = "tool_result_command"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Heuristics — cheap, regex-based, content-type inference
# ---------------------------------------------------------------------------

_RE_PATCH = re.compile(r"^[-+]{3} |^diff --git|^@@ ", re.MULTILINE)
_RE_TOOL_CALL = re.compile(
    r'"name"\s*:\s*"(?:read_file|run_tests|apply_patch|search|run_command)"'
)
_RE_TEST_LINE = re.compile(r"\b\d+\s*(?:passed|failed|errors?)\b", re.IGNORECASE)
_RE_CODE = re.compile(
    r"^(class|def|import|from|export|function|pub |fn |struct |enum |impl )", re.MULTILINE
)
_RE_TRACEBACK = re.compile(
    r"Traceback \(most recent call last\)|^\w+(?:Error|Exception): ", re.MULTILINE
)
_RE_FILE_LINE = re.compile(r"^[^\s:]+:\d+:", re.MULTILINE)
_RE_EXIT = re.compile(r"exit[= ]+\d+", re.MULTILINE)


def _infer_content_type(role: str, content: str) -> str:
    role = role.lower()
    if role == "system":
        return ContentType.SYSTEM
    if role == "user":
        return ContentType.USER_GOAL
    if role == "assistant":
        if _RE_PATCH.search(content):
            return ContentType.AGENT_PATCH
        if _RE_TOOL_CALL.search(content):
            return ContentType.TOOL_CALL
        return ContentType.AGENT_REASONING
    # role == "tool"
    if _RE_PATCH.search(content):
        return ContentType.AGENT_PATCH
    if _RE_TEST_LINE.search(content):
        return ContentType.TOOL_RESULT_TEST
    # Traceback: only catch *real* tracebacks (with the magic line).
    if _RE_TRACEBACK.search(content):
        return ContentType.TOOL_RESULT_ERROR
    # Code structure wins over sparse search hits — files tend to look
    # like "src/foo.py:1: class Foo" at the head, but they also have
    # `def`/`class`/etc. several lines down.
    if _RE_CODE.search(content):
        return ContentType.TOOL_RESULT_FILE
    if _RE_FILE_LINE.search(content):
        return ContentType.TOOL_RESULT_SEARCH
    if _RE_EXIT.search(content):
        return ContentType.TOOL_RESULT_COMMAND
    return ContentType.UNKNOWN


def classify_label(role: str, content: str) -> str:
    """Label a message using the same taxonomy as the compressor.

    Public wrapper over the internal classifier so callers outside this module
    (e.g. the native hive-cpp compression branch in ``hive.stack``) can label
    already-compressed content with the project's own CORE/DISTILL/... taxonomy.
    """
    return _classify(role, _infer_content_type(role, content), content)


def _classify(role: str, content_type: str, content: str = "") -> str:
    if content_type in {ContentType.SYSTEM, ContentType.USER_GOAL}:
        return Label.CORE
    if content_type == ContentType.TOOL_CALL:
        return Label.DROP
    if content_type == ContentType.TOOL_RESULT_ERROR:
        # Keep recent errors verbatim, distill older ones. We only see
        # one message at a time so we keep everything CORE here; the
        # caller's cool loop handles staleness.
        return Label.CORE
    if content_type in {
        ContentType.TOOL_RESULT_TEST,
        ContentType.TOOL_RESULT_SEARCH,
        ContentType.TOOL_RESULT_COMMAND,
        ContentType.AGENT_PATCH,
    }:
        return Label.DISTILL
    if content_type == ContentType.TOOL_RESULT_FILE:
        # Small files are cheap and almost always load-bearing for the next
        # reasoning step — keep them verbatim. Only large reads compress.
        return Label.COMPACT if len(content) > 2400 else Label.CORE
    if content_type == ContentType.AGENT_REASONING:
        return Label.CORE if len(content) < 300 else Label.DISTILL
    return Label.DISTILL


# ---------------------------------------------------------------------------
# Compressors
# ---------------------------------------------------------------------------


_TEST_LINE_RE = re.compile(
    r"(?P<ok>\d+ passed|\d+ ok)|(?P<bad>\d+ failed|\d+ error)", re.IGNORECASE
)


_FAIL_DETAIL_RE = re.compile(
    r"^\s*(E\s|>\s|_{3,}|.*::|.*\bFAIL|.*ERROR|.*\.py:\d+:|assert|raise )"
)


def _compress_test_output(content: str) -> str:
    """Verbose test output → summary + the actual failure details.

    Keeps the pass/fail counts plus the failing test ids, assertion
    diffs (``E `` lines) and file:line references — the parts a model
    needs to diagnose — while dropping progress dots and noise.
    """
    lines = content.splitlines()
    summary = ""
    for ln in reversed(lines):
        if _TEST_LINE_RE.search(ln):
            summary = ln.strip()
            break
    details = [ln for ln in lines if _FAIL_DETAIL_RE.match(ln)][:30]
    result = f"[test] {summary or f'{len(lines)} lines'}\n" + "\n".join(details)
    return result.rstrip() if len(result) < len(content) else content


def _compress_search(content: str) -> str:
    lines = [line for line in content.splitlines() if line.strip()][:12]
    total = len(content.splitlines())
    return f"[search] {total} hits\n" + "\n".join(lines)


def _compress_command(content: str) -> str:
    head = "\n".join(content.splitlines()[:6])
    return f"[cmd] {head}\n... ({len(content)} chars)"


_CODE_SKEL_RE = re.compile(
    r"^\s*(@|class |def |async def |import |from |if __name__|[A-Z_]+ = )"
)


def _compact_file(content: str) -> str:
    """Large file → structural skeleton: imports, defs, classes, constants.

    Preserves everything a model needs to navigate the file (signatures,
    names, line count) instead of an arbitrary 3-line head.
    """
    lines = content.splitlines()
    if _RE_CODE.search(content):
        skeleton = [ln for ln in lines if _CODE_SKEL_RE.match(ln)]
        body = "\n".join(skeleton[:80])
        return f"[file] {len(lines)} lines, {len(content)} chars — skeleton:\n{body}"
    head = "\n".join(lines[:10])
    tail = "\n".join(lines[-5:])
    return f"[file] {len(lines)} lines, {len(content)} chars\n{head}\n...\n{tail}"


def _distill(content: str) -> str:
    """Best-effort distillation.

    Multi-line content (test output, file listings): keep first 3 and
    last 3 lines, drop the rest. Single-line but very long content
    (LLM reasoning, repeated boilerplate): keep first 60 and last 60
    words, drop the middle. Short content is passed through unchanged.
    """
    lines = content.splitlines()
    if len(lines) > 8:
        return "\n".join([*lines[:3], "...", *lines[-3:]])
    if len(content) > 800:
        words = content.split()
        if len(words) > 80:
            head = " ".join(words[:60])
            tail = " ".join(words[-60:])
            return f"{head}\n...\n{tail}"
    return content


def _compress(content: str, content_type: str, label: str) -> str:
    if label == Label.CORE:
        return content
    if content_type == ContentType.TOOL_RESULT_TEST:
        return _compress_test_output(content)
    if content_type == ContentType.TOOL_RESULT_SEARCH:
        return _compress_search(content)
    if content_type == ContentType.TOOL_RESULT_COMMAND:
        return _compress_command(content)
    if content_type == ContentType.TOOL_RESULT_FILE:
        return _compact_file(content)
    if label == Label.COMPACT:
        return _compact_file(content)
    if label == Label.DISTILL:
        return _distill(content)
    return content


# ---------------------------------------------------------------------------
# Data classes (mirror honey-comb's Message / CompressedMessage)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Message:
    role: str
    content: str
    content_type: str | None = None


@dataclass(slots=True)
class CompressedMessage:
    role: str
    content: str
    label: str
    content_type: str
    original_tokens: int
    compressed_tokens: int

    @property
    def ratio(self) -> float:
        if self.compressed_tokens == 0:
            return 0.0
        return self.original_tokens / self.compressed_tokens


# ---------------------------------------------------------------------------
# Estimator
# ---------------------------------------------------------------------------


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# The compressor itself
# ---------------------------------------------------------------------------


@dataclass
class RuleFastHoneyComb:
    """Drop-in replacement for :class:`honeycomb.HoneyComb` in high-perf mode.

    No thread safety, no metrics, no ML — just regex-based classification and
    deterministic compression. Targets ≥25k msg/s on a single x86_64 core,
    ≥10k msg/s on a Jetson Thor.

    The ``get_stats`` method is preserved so the orchestrator's
    ``HiveStack.stats()`` keeps working.
    """

    _turn: int = 0
    _total_in: int = 0
    _total_out: int = 0
    _entries: int = 0
    label_hist: Counter = field(default_factory=Counter)
    type_hist: Counter = field(default_factory=Counter)

    def process(self, message: Message) -> CompressedMessage:
        """Process a single message through the rule-based hot loop."""
        self._turn += 1
        content_type = message.content_type or _infer_content_type(
            message.role, message.content
        )
        label = _classify(message.role, content_type, message.content)
        compressed = _compress(message.content, content_type, label)
        in_tok = _estimate_tokens(message.content)
        out_tok = _estimate_tokens(compressed)
        self._total_in += in_tok
        self._total_out += out_tok
        self._entries += 1
        self.label_hist[label] += 1
        self.type_hist[content_type] += 1
        return CompressedMessage(
            role=message.role,
            content=compressed,
            label=label,
            content_type=content_type,
            original_tokens=in_tok,
            compressed_tokens=out_tok,
        )

    def get_stats(self) -> dict[str, Any]:
        return {
            "turn_count": self._turn,
            "total_entries": self._entries,
            "active_entries": self._entries,
            "total_tokens": self._total_out,
            "original_tokens": self._total_in,
            "compression_ratio": self._total_in / max(self._total_out, 1),
            "labels": dict(self.label_hist),
            "types": dict(self.type_hist),
            "mode": "fast-rule",
        }
