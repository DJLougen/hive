"""latent_probe_extract — capture hidden states at tool-decision points.

The state-only trace eval (trace_bench) plateaus because deidentified state
lacks tool-output content. This script tests the alternative: replay real
session context through a local model and store, per tool call, the residual
stream at the position where the model emits the call. A CPU probe trained on
those vectors sees the semantic signal the state features cannot.

Privacy: raw session text is read locally and never persisted — the output is
per-layer activation vectors (float16) + canonical tool labels + salted-hash
trace ids. Nothing human-readable is written.

For each assistant message containing tool call(s):
  prefix = conversation messages up to (not including) that assistant turn
  -> apply_chat_template(add_generation_prompt=True)
  -> one forward pass; store last-token hidden state of every layer
  -> label = canonical tool of each call in that message (same prefix
     vector is reused for multiple calls in one message — the model chose
     them jointly)

Usage:
    python scripts/latent_probe_extract.py \
        --model mlx-community/Qwen3-4B-Instruct-2507-4bit \
        --out benchmarks/latent [--limit N] [--max-steps K]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

from extract_traces import _SALT, _canon_tool  # noqa: E402

_log = logging.getLogger("hive.latent_extract")


# ---------------------------------------------------------------------------
# Session -> message reconstruction (raw; lives only in memory)
# ---------------------------------------------------------------------------

def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text") or "")
                         for p in content
                         if isinstance(p, dict) and p.get("type") == "text")
    return ""


def _iter_messages(path: Path):
    """Yield (role, parts, tool_call_id) per message record, in order."""
    import ast
    with open(path, encoding="utf-8", errors="replace") as fh:
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
                try:
                    content = ast.literal_eval(content)
                except (ValueError, SyntaxError):
                    content = None
            yield m.get("role"), content or [], m


def session_events(path: Path):
    """Flatten a session into a chat-style message list plus the tool call
    positions. Returns (messages_for_context, calls) where calls is a list of
    (prefix_len, canonical_tool, raw_name)."""
    msgs: list[dict[str, Any]] = []
    calls: list[tuple[int, str, str]] = []
    pending_result: dict[str, str] = {}

    for role, content, raw in _iter_messages(path):
        if role == "user":
            text = _content_text(content)
            if text.strip():
                msgs.append({"role": "user", "content": text[:8000]})
        elif role == "assistant":
            parts = content if isinstance(content, list) else []
            tcs = [p for p in parts
                   if isinstance(p, dict)
                   and p.get("type") in ("toolCall", "tool_call",
                                         "toolUse", "tool_use")]
            text = _content_text(parts)
            if tcs:
                prefix_len = len(msgs)
                tool_calls = [{
                    "id": str(p.get("id") or f"c{k}"),
                    "type": "function",
                    "function": {
                        "name": str(p.get("name") or "?"),
                        "arguments": json.dumps(p.get("arguments") or {}),
                    },
                } for k, p in enumerate(tcs)]
                for p in tcs:
                    canon = _canon_tool(str(p.get("name") or "?"),
                                        p.get("arguments") or {})
                    calls.append((prefix_len, canon, str(p.get("name") or "?")))
                msg: dict[str, Any] = {"role": "assistant",
                                       "content": text or None,
                                       "tool_calls": tool_calls}
                msgs.append(msg)
            elif text.strip():
                msgs.append({"role": "assistant", "content": text[:8000]})
        elif role in ("toolResult", "tool_result", "tool"):
            cid = str(raw.get("toolCallId") or raw.get("tool_call_id") or "")
            text = _content_text(content)
            name = str(raw.get("toolName") or pending_result.get(cid, "tool"))
            msgs.append({"role": "tool", "tool_call_id": cid,
                         "name": name, "content": text[:4000]})
        elif role == "system":
            text = _content_text(content)
            if text.strip():
                msgs.append({"role": "system", "content": text[:4000]})
    return msgs, calls


# ---------------------------------------------------------------------------
# Hidden-state capture
# ---------------------------------------------------------------------------

def last_token_per_layer(model, ids) -> np.ndarray:
    """One forward pass -> (n_layers, hidden) fp16 at the last position."""
    import mlx.core as mx
    from mlx_lm.models.base import create_attention_mask

    inner = model.model
    h = inner.embed_tokens(ids)
    mask = create_attention_mask(h, None)
    outs = []
    for layer in inner.layers:
        h = layer(h, mask, None)
        outs.append(h[0, -1])
    return np.asarray(mx.stack(outs).astype(mx.float16))


def check_replication(model, ids) -> None:
    """Sanity: manual layer loop must reproduce model() logits."""
    import mlx.core as mx
    inner = model.model
    h = last_token_per_layer(model, ids)
    ref = model(ids)[0, -1]
    inner2 = inner.norm
    # last captured layer is pre-norm; apply norm + lm_head
    last = mx.array(h[-1].astype(np.float32))[None, None]
    mine = model.lm_head(inner2(last))[0, 0]
    if int(mx.argmax(mine)) != int(mx.argmax(ref)):
        raise RuntimeError("manual layer loop diverges from model() "
                           "— capture path is wrong for this architecture")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default="mlx-community/Qwen3-4B-Instruct-2507-4bit")
    ap.add_argument("--omp-dir", default=str(Path.home() / ".omp/agent/sessions"))
    ap.add_argument("--prime-dir",
                    default=str(Path.home() / ".prime/agent/sessions"))
    ap.add_argument("--out", default="benchmarks/latent")
    ap.add_argument("--traces", default="benchmarks/traces",
                    help="eval suite dir (for family labels)")
    ap.add_argument("--pool", default="benchmarks/traces-all",
                    help="pool dir; sessions not in it or the eval set are "
                         "skipped (keeps vectors joinable to trace data)")
    ap.add_argument("--max-ctx", type=int, default=3072,
                    help="cap prefix tokens (head+tail truncation)")
    ap.add_argument("--max-steps", type=int, default=40,
                    help="cap captured calls per session (uniform subsample)")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N sessions (smoke test)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import random

    from mlx_lm import load

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # trace-id membership: eval suite + pool (labels/family joinable)
    keep_ids: dict[str, dict[str, str]] = {}
    for tag, d in (("eval", Path(args.traces)), ("pool", Path(args.pool))):
        if not d.exists():
            continue
        for p in d.glob("*.json"):
            if p.name == "suite.json":
                continue
            tr = json.loads(p.read_text())
            keep_ids[tr["id"]] = {"split": tag,
                                  "family": tr.get("family", "?"),
                                  "source": tr.get("source", "?")}
    _log.info("%d known trace ids (eval+pool)", len(keep_ids))

    _log.info("loading %s", args.model)
    model, tokenizer = load(args.model)
    checked = False

    files: list[tuple[Path, str]] = []
    for source, root in (("prime", Path(args.prime_dir).expanduser()),
                         ("omp", Path(args.omp_dir).expanduser())):
        files += [(f, source) for f in sorted(root.rglob("*.jsonl"))]

    rng = random.Random(42)
    t0 = time.time()
    n_sess = n_steps = 0
    for fi, (path, source) in enumerate(files):
        if args.limit and n_sess >= args.limit:
            break
        sid = hashlib.sha256(f"{_SALT}:{path}".encode()).hexdigest()[:16]
        meta = keep_ids.get(sid)
        if meta is None:
            continue  # not in eval suite or pool — skip
        try:
            msgs, calls = session_events(path)
        except Exception:
            _log.debug("skip %s", path, exc_info=True)
            continue
        if len(calls) < 8:
            continue
        if len(calls) > args.max_steps:
            calls = sorted(rng.sample(list(enumerate(calls)), args.max_steps))
            calls = [calls[i][1] for i in range(len(calls))]
            # rng.sample returned (idx, call) pairs above
        n_sess += 1
        states, labels, raws, step_idx = [], [], [], []
        for ci, (plen, canon, raw_name) in enumerate(calls):
            prefix = msgs[:plen]
            if not prefix:
                continue
            try:
                ids = tokenizer.apply_chat_template(
                    prefix, add_generation_prompt=True,
                    return_tensors="np")
            except Exception:
                try:
                    text = tokenizer.apply_chat_template(
                        prefix, add_generation_prompt=True,
                        tokenize=False)
                    ids = tokenizer.encode(text)
                except Exception:
                    continue
            ids = np.asarray(ids).reshape(-1)
            if ids.size > args.max_ctx:
                head = args.max_ctx // 4
                ids = np.concatenate([ids[:head], ids[-(args.max_ctx - head):]])
            x = ids[None]
            if not checked:
                check_replication(model, __import__("mlx.core",
                                                  fromlist=["array"]).array(x))
                checked = True
                _log.info("layer-loop replication verified")
            states.append(last_token_per_layer(
                model, __import__("mlx.core", fromlist=["array"]).array(x)))
            labels.append(canon)
            raws.append(raw_name)
            step_idx.append(ci)
            n_steps += 1
            if n_steps % 200 == 0:
                el = time.time() - t0
                _log.info("%d steps, %d sessions, %.1fs/step",
                          n_steps, n_sess, el / max(n_steps, 1))
        if states:
            np.savez_compressed(
                out_dir / f"{sid}.npz",
                states=np.stack(states), labels=np.array(labels),
                raw_names=np.array(raws), step_idx=np.array(step_idx),
                split=meta["split"], family=meta["family"],
                source=meta["source"])
    el = time.time() - t0
    _log.info("done: %d steps across %d sessions in %.0fs (%.2fs/step)",
              n_steps, n_sess, el, el / max(n_steps, 1))
    _log.info("label dist: %s", Counter(
        str(x) for npz in out_dir.glob("*.npz")
        for x in np.load(npz)["labels"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
