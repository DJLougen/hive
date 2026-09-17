"""Mine HuggingFace agent-trajectory datasets into deidentified traces.

Normalizes external trajectory datasets into the same schema
``scripts/extract_traces.py`` produces for local omp/prime sessions —
canonical tool name, ok/error flag, step index, per-tool histogram, arg
key names/classes only. No message text, paths, or argument values are
persisted.

Supported today (add a normalizer per schema):

* ``ThreeSixNine/hermes-agent-reasoning-traces`` (configs ``kimi``,
  ``glm-5.1``) — real tool execution, ``conversations`` list with
  ``<tool_call>{...}</tool_call>`` blocks in ``gpt`` turns and
  ``<tool_response>`` JSON in ``tool`` turns.

Usage:
    python scripts/fetch_hf_traces.py --dataset hermes-kimi \
        --out benchmarks/traces-hf --n 150
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_scripts = str(_REPO_ROOT / "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

from extract_traces import _arg_classes, _canon_tool, family_of

_log = logging.getLogger("hive.fetch_hf")

_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
_TOOL_RESP_RE = re.compile(r"<tool_response>\s*(\{.*?\})\s*</tool_response>", re.S)


def _hermes_steps(row: dict[str, Any]) -> list[dict[str, Any]]:
    """conversation [{'from','value'}] -> deidentified steps."""
    steps: list[dict[str, Any]] = []
    hist: Counter[str] = Counter()
    n_err = 0
    last_tool: str | None = None
    last_ok = True
    pending_err: list[bool] = []

    convs = row["conversations"]
    for i, m in enumerate(convs):
        role, val = m.get("from"), m.get("value") or ""
        if role == "tool":
            for block in _TOOL_RESP_RE.findall(val):
                try:
                    content = json.loads(block).get("content") or {}
                    ok = bool(content.get("success", True)) and not content.get("error")
                except (json.JSONDecodeError, AttributeError):
                    ok = True
                pending_err.append(not ok)
        elif role == "gpt":
            calls = _TOOL_CALL_RE.findall(val)
            for tc in calls:
                try:
                    call = json.loads(tc)
                except json.JSONDecodeError:
                    continue
                name = str(call.get("name") or "?")
                args = call.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                canon = _canon_tool(name, args if isinstance(args, dict) else {})
                keys, has_path, has_pat, has_cmd = _arg_classes(
                    args if isinstance(args, dict) else {})
                # tool_result follows the gpt turn; attribute by position
                is_err = (pending_err.pop(0)
                          if pending_err and i + 1 < len(convs)
                          and convs[i + 1].get("from") == "tool"
                          else False)
                steps.append({
                    "t": len(steps), "last_tool": last_tool,
                    "last_ok": last_ok, "n_err": n_err,
                    "hist": dict(hist), "n_args": len(keys),
                    "has_path": has_path, "has_pattern": has_pat,
                    "has_cmd": has_cmd, "tool": canon,
                })
                hist[canon] += 1
                last_tool = canon
                last_ok = not is_err
                if is_err:
                    n_err += 1
    if len(steps) < 8 or len(hist) < 2:
        return []
    steps.append({
        "t": len(steps), "last_tool": last_tool, "last_ok": last_ok,
        "n_err": n_err, "hist": dict(hist), "n_args": 0,
        "has_path": False, "has_pattern": False, "has_cmd": False,
        "tool": "finish",
    })
    return steps


_DATASETS = {
    "hermes-kimi": ("ThreeSixNine/hermes-agent-reasoning-traces", "kimi"),
    "hermes-glm": ("ThreeSixNine/hermes-agent-reasoning-traces", "glm-5.1"),
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", choices=[*_DATASETS, "all"], required=True)
    ap.add_argument("--out", default="benchmarks/traces-hf")
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--max-scan", type=int, default=3000,
                    help="dataset rows to stream while collecting")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from datasets import load_dataset

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    names = list(_DATASETS) if args.dataset == "all" else [args.dataset]
    traces: list[dict[str, Any]] = []
    for name in names:
        repo, config = _DATASETS[name]
        ds = load_dataset(repo, config, split="train", streaming=True)
        got = 0
        for i, row in enumerate(ds):
            if i >= args.max_scan or got >= args.n:
                break
            steps = _hermes_steps(row)
            if not steps:
                continue
            sid = hashlib.sha256(f"hf:{name}:{row.get('id', i)}".encode())
            tr = {
                "id": sid.hexdigest()[:16],
                "source": f"hf:{name}",
                "hf_category": row.get("category"),
                "n_steps": len(steps),
                "steps": steps,
            }
            tr["family"] = family_of(tr)
            if tr["family"] in ("shell-ops", "test-only"):
                tr["family"] = "mixed"
            traces.append(tr)
            got += 1
        _log.info("%s: %d traces", name, got)

    fam_counts = Counter(t["family"] for t in traces)
    for t in traces:
        (out_dir / f"{t['id']}.json").write_text(json.dumps(t) + "\n")
    suite = {
        "name": "hive-trace-bench-hf",
        "version": 1,
        "unit": "trace-coverage",
        "description": (
            "Deidentified traces mined from public HuggingFace agent-"
            "trajectory datasets. Same schema as benchmarks/traces/; "
            "hf_category preserves the dataset's own task taxonomy."
        ),
        "families": dict(sorted(fam_counts.items())),
        "tasks": [t["id"] for t in traces],
    }
    (out_dir / "suite.json").write_text(json.dumps(suite, indent=2) + "\n")
    _log.info("wrote %d traces -> %s; families=%s", len(traces), out_dir,
              dict(fam_counts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
