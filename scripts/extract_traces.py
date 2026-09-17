"""Extract deidentified tool-call traces from agent session logs.

Reads omp-style and prime-style session JSONL files (both use ``message``
records whose assistant content holds ``toolCall`` parts and whose
``toolResult`` records carry ``isError``) and emits one JSON file per trace
under ``benchmarks/traces/``.

Deidentification contract — nothing identifying ever leaves this script:

* user/assistant *text* is never read into the output
* tool *argument values* are never persisted — only argument key names and
  a coarse class (path-like / pattern-like / command-like)
* shell/code tool *content* is inspected in-memory only to classify the
  call (test run vs write vs read vs generic command), then discarded
* session ids are salted SHA-256 hashes; no timestamps, paths, models, or
  project names are kept

What is kept per step: position in the session, the canonical tool
category, whether the previous tool errored, per-tool call counts so far,
argument key names and their coarse classes. Enough to train and evaluate
a tool-choice policy — nothing more.

Usage:
    python scripts/extract_traces.py \
        --omp-dir ~/.omp/agent/sessions \
        --prime-dir ~/.prime/agent/sessions \
        --out benchmarks/traces --n 100
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import logging
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_log = logging.getLogger("hive.extract_traces")

_SALT = "hive-trace-deid-v1"  # stable hash salt; raw ids never persisted

_NAME_MAP = {
    "glob": "list_files", "list": "list_files", "ls": "list_files",
    "find": "list_files", "list_dir": "list_files", "tree": "list_files",
    "read": "read_file", "read_file": "read_file", "cat": "read_file",
    "view": "read_file", "open_file": "read_file",
    "grep": "grep", "rg": "grep", "search": "grep", "find_in_files": "grep",
    "write": "write_file", "write_file": "write_file", "edit": "write_file",
    "edit_file": "write_file", "str_replace": "write_file",
    "apply_patch": "write_file", "notebook_edit": "write_file",
    "web_search": "web", "web_fetch": "web", "fetch": "web",
    "search_web": "web", "browser": "web", "websearch": "web",
    "patch": "write_file", "edit_file_tool": "write_file",
    "search_files": "list_files", "list_files": "list_files",
    "search_text": "grep", "search_content": "grep", "find_text": "grep",
}

_EXEC_TOOLS = {"bash", "eval", "ipython", "shell", "run", "exec", "python",
               "terminal", "command", "console", "execute_code",
               "run_command", "code_execution", "shell_exec"}

_TEST_RE = re.compile(
    r"pytest|py\.test|npm\s+test|pnpm\s+test|yarn\s+test|jest|vitest|"
    r"cargo\s+test|go\s+test|unittest|ctest|make\s+test|tox\b|rspec|"
    r"phpunit|dotnet\s+test|mvn\s+test|gradle\s+test|mix\s+test")
_WRITE_RE = re.compile(
    r"sed\s+-i|apply_patch|\bpatch\b|\btee\b|>\s*[^\s|]+|>>\s*[^\s|]+|"
    r"cat\s*<<|open\([^)]*['\"][wa]|\.write\(|write_text|str_replace")
_LIST_RE = re.compile(r"(^|[;&|]\s*)(ls|find|fd|tree|du|wc\s+-l)\b|\.glob\(|listdir|scandir")
_GREP_RE = re.compile(r"(^|[;&|]\s*)(grep|rg|ag|ack)\b|re\.search|re\.findall")
_READ_RE = re.compile(
    r"(^|[;&|]\s*)(cat|head|tail|less|more|bat|sed\s+-n)\b|\.read\(|read_text|"
    r"open\([^)]*['\"]r['\"]")
_WEB_RE = re.compile(r"websearch|web_search|serper|google\.|duckduckgo|"
                     r"requests\.get\(['\"]http|urllib|httpx\.get")


def _classify_exec(content: str) -> str:
    """Bucket a shell/code string into a canonical tool. Content is
    inspected here and never persisted."""
    if _TEST_RE.search(content):
        return "run_tests"
    if _WEB_RE.search(content):
        return "web"
    if _WRITE_RE.search(content):
        return "write_file"
    if _GREP_RE.search(content):
        return "grep"
    if _READ_RE.search(content):
        return "read_file"
    if _LIST_RE.search(content):
        return "list_files"
    return "run_command"


def _canon_tool(name: str, args: dict[str, Any]) -> str:
    n = name.lower().split("__")[-1]  # mcp__server__tool -> tool
    if n in _EXEC_TOOLS:
        content = " ".join(str(v) for v in args.values())
        return _classify_exec(content)
    return _NAME_MAP.get(n, "other")


def _arg_classes(args: dict[str, Any]) -> tuple[list[str], bool, bool, bool]:
    """Argument key names + coarse value classes. Values never persist."""
    keys = sorted(str(k) for k in args)
    has_path = has_pat = has_cmd = False
    for k, v in args.items():
        kl, vs = str(k).lower(), str(v)
        if kl in ("path", "file", "filename", "file_path", "dir", "cwd") or "/" in vs[:200]:
            has_path = True
        if kl in ("pattern", "regex", "query", "i"):
            has_pat = True
        if kl in ("command", "cmd", "code", "script"):
            has_cmd = True
    return keys, has_path, has_pat, has_cmd


def _iter_calls(session_file: Path):
    """Yield (tool_name, args, is_error_or_None) in call order.

    Assistant ``toolCall`` parts are paired with ``toolResult`` records by
    ``toolCallId`` when available, else by order."""
    pending: dict[str, tuple[str, dict]] = {}
    order: list[str] = []
    errors: dict[str, bool] = {}
    with open(session_file, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            try:
                d = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if d.get("type") != "message":
                continue
            m = d.get("message") or {}
            content = m.get("content")
            if isinstance(content, str) and content.startswith("["):
                # prime serializes content as a python-repr string
                try:
                    content = ast.literal_eval(content)
                except (ValueError, SyntaxError):
                    content = None
            if m.get("role") == "assistant" and isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") in (
                            "toolCall", "tool_call", "toolUse", "tool_use"):
                        cid = str(part.get("id") or len(order))
                        pending[cid] = (str(part.get("name") or "?"),
                                        part.get("arguments") or {})
                        order.append(cid)
            elif m.get("role") == "toolResult":
                cid = str(m.get("toolCallId") or "")
                errors[cid] = bool(m.get("isError"))
    for cid in order:
        name, args = pending[cid]
        yield name, args, errors.get(cid)


def extract_session(path: Path, source: str) -> dict[str, Any] | None:
    """One deidentified trace: a list of (state, tool) steps."""
    steps: list[dict[str, Any]] = []
    hist: Counter[str] = Counter()
    n_err = 0
    last_tool: str | None = None
    last_ok = True
    for name, args, is_err in _iter_calls(path):
        canon = _canon_tool(name, args)
        keys, has_path, has_pat, has_cmd = _arg_classes(args)
        steps.append({
            "t": len(steps),
            "last_tool": last_tool,
            "last_ok": last_ok,
            "n_err": n_err,
            "hist": dict(hist),
            "n_args": len(keys),
            "has_path": has_path,
            "has_pattern": has_pat,
            "has_cmd": has_cmd,
            "tool": canon,
        })
        hist[canon] += 1
        last_tool = canon
        if is_err is not None:
            last_ok = not is_err
            if is_err:
                n_err += 1
    if len(steps) < 8 or len(hist) < 2:
        return None
    # Session end: the decision to stop calling tools is itself a decision.
    steps.append({
        "t": len(steps), "last_tool": last_tool, "last_ok": last_ok,
        "n_err": n_err, "hist": dict(hist), "n_args": 0,
        "has_path": False, "has_pattern": False, "has_cmd": False,
        "tool": "finish",
    })
    sid = hashlib.sha256(f"{_SALT}:{path}".encode()).hexdigest()[:16]
    return {"id": sid, "source": source, "n_steps": len(steps), "steps": steps}


def family_of(trace: dict[str, Any]) -> str:
    """Workflow-signature family label, derived from the tool histogram."""
    hist: Counter[str] = Counter()
    for s in trace["steps"]:
        hist[s["tool"]] += 1
    n = max(hist.total() - hist.get("finish", 0), 1)
    wrote = hist.get("write_file", 0) > 0
    tested = hist.get("run_tests", 0) > 0
    if wrote and tested:
        return "edit-verify"
    if wrote:
        return "edit-only"
    if hist.get("web", 0) >= 2:
        return "web-research"
    if hist.get("run_command", 0) / n >= 0.5:
        return "shell-ops"
    if (hist.get("list_files", 0) + hist.get("read_file", 0)
            + hist.get("grep", 0)) / n >= 0.6:
        return "explore"
    if tested:
        return "test-only"
    return "mixed"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--omp-dir", default=str(Path.home() / ".omp/agent/sessions"))
    ap.add_argument("--prime-dir", default=str(Path.home() / ".prime/agent/sessions"))
    ap.add_argument("--out", default="benchmarks/traces")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    traces: list[dict[str, Any]] = []
    for source, root in (("prime", Path(args.prime_dir).expanduser()),
                         ("omp", Path(args.omp_dir).expanduser())):
        files = sorted(root.rglob("*.jsonl"))
        n_ok = 0
        for f in files:
            try:
                tr = extract_session(f, source)
            except Exception:
                _log.debug("skip %s", f, exc_info=True)
                continue
            if tr:
                fam = family_of(tr)
                # Rare signatures fold into "mixed" — a family needs enough
                # members for its stats to mean anything.
                tr["family"] = "mixed" if fam in ("shell-ops", "test-only") else fam
                traces.append(tr)
                n_ok += 1
        _log.info("%s: %d usable traces from %d files", source, n_ok, len(files))

    # Stratified sample: equal draw per family so each family's metrics have
    # a usable n; leftovers fill any family that came up short.
    rng = random.Random(args.seed)
    by_fam: dict[str, list[dict[str, Any]]] = {}
    for t in traces:
        by_fam.setdefault(t["family"], []).append(t)
    for v in by_fam.values():
        rng.shuffle(v)
    per = max(args.n // max(len(by_fam), 1), 1)
    take: list[dict[str, Any]] = []
    leftovers: list[dict[str, Any]] = []
    for _fam, pool in sorted(by_fam.items()):
        take.extend(pool[:per])
        leftovers.extend(pool[per:])
    rng.shuffle(leftovers)
    take.extend(leftovers[: max(args.n - len(take), 0)])
    rng.shuffle(take)
    take = take[: args.n]

    fam_counts = Counter(t["family"] for t in take)
    for t in take:
        (out_dir / f"{t['id']}.json").write_text(json.dumps(t) + "\n")

    suite = {
        "name": "hive-trace-bench",
        "version": 1,
        "unit": "trace-coverage",
        "description": (
            "Deidentified real-agent tool-call traces (omp + prime session "
            "logs). Each task replays a real decision sequence: the policy is "
            "scored on coverage (fraction of steps routed without escalation) "
            "and fidelity (routed tool == the tool the agent actually used). "
            "No user text, paths, or argument values are stored."
        ),
        "families": dict(sorted(fam_counts.items())),
        "tasks": [t["id"] for t in take],
    }
    (out_dir / "suite.json").write_text(json.dumps(suite, indent=2) + "\n")
    _log.info("wrote %d traces -> %s; families=%s", len(take), out_dir,
              dict(fam_counts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
