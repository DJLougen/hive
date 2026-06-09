"""LLM-in-the-loop fidelity eval: raw vs. compressed context, real model.

The substring benchmark (``fidelity_benchmark.py``) bounds the information
that survives compression. This eval measures the step after that: given a
real LLM and a question an agent would actually need answered ("which tests
failed?", "what was the exit code?"), does the model answer as well from the
**compressed** context as from the **raw** context?

Design:

* Same deterministic corpus generators, scaled down (``scale``) so raw
  contexts fit CPU inference budgets.
* One agent-realistic question per category; grading is automatic against
  the corpus ground-truth facts (normalized substring / numeric checks).
* Two conditions per sample: ``raw`` (original tool output) and
  ``compressed`` (rule_fast output via ``HiveStack.compress``).
* Backends: an OpenAI-compatible API if ``OPENAI_API_KEY`` is set
  (model via ``HIVE_EVAL_MODEL``, default ``gpt-4o-mini``), otherwise a
  local HuggingFace model on CPU (default ``Qwen/Qwen2.5-0.5B-Instruct``).

Usage:

    python3 scripts/llm_fidelity_eval.py \
        [--seed 7] [--per-category 6] [--scale 0.2] \
        [--model Qwen/Qwen2.5-0.5B-Instruct] [--max-new-tokens 192]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fidelity_corpus import Sample, build_corpus  # noqa: E402

from hive.stack import HiveStack  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

SYSTEM_PROMPT = (
    "You answer questions about tool output captured during a software "
    "agent session. Be concise and exact: quote names, paths, numbers and "
    "error text verbatim from the tool output."
)

_QUESTIONS = {
    "pytest_log": (
        "List the exact names of every test that FAILED. Then state how many "
        "tests failed and how many passed in total."
    ),
    "traceback": (
        "What exception type and message was raised? Also give the file path "
        "and line number of the deepest stack frame where it occurred."
    ),
    "file_read": (
        "Exactly one function in this file assigns a variable named `limit`. "
        "Give that function's name, the value of its `timeout` keyword "
        "default, and the numeric value assigned to `limit`."
    ),
    "search_results": (
        "Where is the constant {const} defined? Give the file path, the line "
        "number, and its numeric value."
    ),
    "command_output": (
        "Quote the error line from this command output, and state the exit code."
    ),
}


def question_for(sample: Sample) -> str:
    q = _QUESTIONS[sample.category]
    if sample.category == "search_results":
        snippet = next(f.needle for f in sample.facts if f.name == "answer snippet")
        q = q.format(const=snippet.split(" =")[0])
    return q


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


def _norm(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[`'\"\[\]()]", "", text)
    text = re.sub(r"\s*=\s*", "=", text)
    return re.sub(r"\s+", " ", text).strip()


def _contains(answer_norm: str, needle: str) -> bool:
    return _norm(needle) in answer_norm


def grade_fact(fact_name: str, needle: str, answer: str) -> bool:
    """Return True if the model's answer contains this ground-truth fact."""
    ans = _norm(answer)
    if fact_name == "failing test name":
        return _contains(ans, needle)
    if fact_name == "failure count summary":
        # "14 failed, 586 passed" -> both counts must appear.
        nums = re.findall(r"\d+", needle)
        return all(re.search(rf"\b{n}\b", ans) for n in nums)
    if fact_name == "exception line":
        exc_type, _, msg = needle.partition(":")
        return _contains(ans, exc_type) and _contains(ans, msg.strip())
    if fact_name == "deepest stack frame":
        m = re.search(r'File "([^"]+)", line (\d+)', needle)
        assert m is not None
        return _contains(ans, m.group(1)) and re.search(rf"\b{m.group(2)}\b", ans) is not None
    if fact_name == "target function signature":
        m = re.search(r"def (\w+)\(.*timeout=(\d+)", needle)
        assert m is not None
        return _contains(ans, m.group(1)) and _contains(ans, f"timeout={m.group(2)}")
    if fact_name in ("target constant", "answer snippet"):
        value = needle.split("=")[-1].strip()
        return re.search(rf"\b{value}\b", ans) is not None
    if fact_name == "answer location":
        path, _, lineno = needle.rpartition(":")
        return _contains(ans, path) and re.search(rf"\b{lineno}\b", ans) is not None
    if fact_name == "error line":
        m = re.search(r"failed to push image to (\S+)", needle)
        fragment = "failed to push image" if m else needle
        return _contains(ans, fragment)
    if fact_name == "exit code":
        code = needle.split()[-1]
        return re.search(rf"exit\s*(code)?\s*(was|is|:)?\s*{code}\b", ans) is not None or (
            len(ans) < 40 and re.search(rf"\b{code}\b", ans) is not None
        )
    raise ValueError(f"no grader for fact {fact_name!r}")


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class OpenAIBackend:
    def __init__(self, model: str) -> None:
        import urllib.request  # noqa: F401 - validated lazily in ask()

        self.model = model
        self.api_key = os.environ["OPENAI_API_KEY"]
        self.base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.name = f"api:{model}"

    def ask(self, system: str, user: str, max_new_tokens: int) -> tuple[str, int]:
        import urllib.request

        body = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max_new_tokens,
                "temperature": 0,
            }
        ).encode()
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=120) as resp:  # nosec B310
            data = json.loads(resp.read())
        return (
            data["choices"][0]["message"]["content"],
            int(data["usage"]["prompt_tokens"]),
        )


class LocalBackend:
    """Greedy decoding with a local HuggingFace model.

    Uses CUDA in fp16 when available (a 7B model fits comfortably on an
    RTX 3090), otherwise fp32 on CPU.
    """

    def __init__(self, model: str) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        if self.device == "cpu":
            torch.set_num_threads(os.cpu_count() or 4)
        self.tokenizer = AutoTokenizer.from_pretrained(model)
        self.model = AutoModelForCausalLM.from_pretrained(
            model,
            torch_dtype=torch.float16 if self.device == "cuda" else torch.float32,
        ).to(self.device)
        self.model.eval()
        self.name = f"local:{model} ({self.device})"

    def ask(self, system: str, user: str, max_new_tokens: int) -> tuple[str, int]:
        import torch

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        inputs = self.tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, return_tensors="pt"
        )
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        input_len = int(inputs["input_ids"].shape[1])
        with torch.no_grad():
            out = self.model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                pad_token_id=self.tokenizer.eos_token_id,
            )
        answer = self.tokenizer.decode(out[0][input_len:], skip_special_tokens=True)
        return answer, input_len


def make_backend(model: str | None) -> Any:
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAIBackend(model or os.environ.get("HIVE_EVAL_MODEL", "gpt-4o-mini"))
    return LocalBackend(model or "Qwen/Qwen2.5-0.5B-Instruct")


# ---------------------------------------------------------------------------
# Eval loop
# ---------------------------------------------------------------------------


def run_eval(
    samples: list[Sample], backend: Any, max_new_tokens: int
) -> list[dict[str, Any]]:
    stack = HiveStack()
    rows: list[dict[str, Any]] = []
    for i, s in enumerate(samples):
        question = question_for(s)
        compressed = stack.compress(s.role, s.content).content
        for condition, context in (("raw", s.content), ("compressed", compressed)):
            user = f"<tool_output>\n{context}\n</tool_output>\n\n{question}"
            t0 = time.perf_counter()
            answer, prompt_tokens = backend.ask(SYSTEM_PROMPT, user, max_new_tokens)
            latency_s = time.perf_counter() - t0
            graded = {
                f"{f.name}::{f.needle[:60]}": grade_fact(f.name, f.needle, answer)
                for f in s.facts
            }
            rows.append(
                {
                    "id": s.id,
                    "category": s.category,
                    "source": s.source,
                    "condition": condition,
                    "prompt_tokens": prompt_tokens,
                    "latency_s": round(latency_s, 2),
                    "answer": answer.strip(),
                    "graded": graded,
                    "correct": sum(graded.values()),
                    "total": len(graded),
                }
            )
            print(
                f"[{i + 1}/{len(samples)}] {s.id:24s} {condition:10s} "
                f"{rows[-1]['correct']}/{rows[-1]['total']} "
                f"({prompt_tokens} tok, {latency_s:.1f}s)",
                flush=True,
            )
    return rows


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def _agg(subset: list[dict[str, Any]]) -> dict[str, Any]:
        correct = sum(r["correct"] for r in subset)
        total = sum(r["total"] for r in subset)
        all_ok = sum(1 for r in subset if r["correct"] == r["total"])
        return {
            "messages": len(subset),
            "qa_accuracy_pct": 100.0 * correct / total if total else 0.0,
            "all_facts_answered_pct": 100.0 * all_ok / len(subset) if subset else 0.0,
            "avg_prompt_tokens": (
                sum(r["prompt_tokens"] for r in subset) / len(subset) if subset else 0.0
            ),
        }

    out: dict[str, Any] = {}
    for condition in ("raw", "compressed"):
        sub = [r for r in rows if r["condition"] == condition]
        out[condition] = _agg(sub)
        out[condition]["by_category"] = {
            cat: _agg([r for r in sub if r["category"] == cat])
            for cat in sorted({r["category"] for r in sub})
        }
    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def render_report(result: dict[str, Any]) -> str:
    raw = result["aggregate"]["raw"]
    comp = result["aggregate"]["compressed"]
    saved = 100.0 * (1 - comp["avg_prompt_tokens"] / raw["avg_prompt_tokens"])
    lines = [
        "# LLM-in-the-Loop Fidelity Eval",
        "",
        "Given a real model and an agent-realistic question per message, does",
        "the model answer as well from **compressed** context as from **raw**?",
        "Grading is automatic against corpus ground truth.",
        "",
        f"- Model: `{result['backend']}` (greedy decoding)",
        f"- Corpus: {result['messages']} messages "
        f"(seed={result['seed']}, {result['per_category']}/category + real fixture, scale={result['scale']})",
        f"- Compressor: rule_fast via `HiveStack.compress`",
        f"- Machine: {result['machine']}",
        f"- Commit: `{result['commit']}` — {result['timestamp']}",
        "",
        "## Overall",
        "",
        "| Metric | Raw context | Compressed context |",
        "|---|---|---|",
        f"| QA accuracy (graded facts) | {raw['qa_accuracy_pct']:.1f}% | **{comp['qa_accuracy_pct']:.1f}%** |",
        f"| Messages fully answered | {raw['all_facts_answered_pct']:.1f}% | {comp['all_facts_answered_pct']:.1f}% |",
        f"| Avg prompt tokens | {raw['avg_prompt_tokens']:.0f} | {comp['avg_prompt_tokens']:.0f} (**-{saved:.1f}%**) |",
        "",
        "## By category (QA accuracy)",
        "",
        "| Category | Raw | Compressed | Raw tokens | Compressed tokens |",
        "|---|---|---|---|---|",
    ]
    for cat in raw["by_category"]:
        r = raw["by_category"][cat]
        c = comp["by_category"][cat]
        lines.append(
            f"| {cat} | {r['qa_accuracy_pct']:.1f}% | {c['qa_accuracy_pct']:.1f}% "
            f"| {r['avg_prompt_tokens']:.0f} | {c['avg_prompt_tokens']:.0f} |"
        )
    lines += [
        "",
        "## How to read this",
        "",
        "- The raw-context column is the model's ceiling on this corpus; the",
        "  compressed column shows what compression costs (or saves) on top.",
        "- Categories where compressed ≥ raw mean compression removed",
        "  distraction, not signal. Categories where compressed < raw are the",
        "  measured price of the token savings.",
        "",
        "## Caveats",
        "",
        "- A small CPU model is a *lower bound* on answer quality; the",
        "  raw-vs-compressed comparison is the meaningful signal, not the",
        "  absolute accuracy. Rerun with `OPENAI_API_KEY` set for a frontier",
        "  model (`HIVE_EVAL_MODEL` to choose).",
        "- Single question per message; does not measure multi-step task",
        "  success (SWE-bench-style runs remain the gold standard).",
        "",
        "## Reproduce",
        "",
        "```bash",
        "pip install -e \".[dev]\" torch transformers",
        "python3 scripts/llm_fidelity_eval.py",
        "```",
        "",
    ]
    return "\n".join(lines)


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        ).stdout.strip()
    except Exception:
        return "unknown"


def main(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--per-category", type=int, default=6)
    parser.add_argument("--scale", type=float, default=0.2)
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-new-tokens", type=int, default=192)
    parser.add_argument(
        "--json", type=Path, default=REPO_ROOT / "results" / "llm_fidelity_eval.json"
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPO_ROOT / "docs" / "benchmarks" / "llm-fidelity.md",
    )
    args = parser.parse_args(argv)

    samples = build_corpus(
        seed=args.seed, per_category=args.per_category, scale=args.scale
    )
    backend = make_backend(args.model)
    print(f"backend: {backend.name}; {len(samples)} messages x 2 conditions")

    import platform

    rows = run_eval(samples, backend, args.max_new_tokens)
    result: dict[str, Any] = {
        "backend": backend.name,
        "messages": len(samples),
        "seed": args.seed,
        "per_category": args.per_category,
        "scale": args.scale,
        "max_new_tokens": args.max_new_tokens,
        "machine": f"{platform.machine()} / {platform.system()} / {os.cpu_count()} cores",
        "commit": _git_commit(),
        "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "aggregate": aggregate(rows),
        "rows": rows,
    }

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(result, indent=2) + "\n")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_report(result))

    raw = result["aggregate"]["raw"]
    comp = result["aggregate"]["compressed"]
    print(f"\nQA accuracy raw:        {raw['qa_accuracy_pct']:.1f}%")
    print(f"QA accuracy compressed: {comp['qa_accuracy_pct']:.1f}%")
    print(
        f"prompt tokens:          {raw['avg_prompt_tokens']:.0f} -> "
        f"{comp['avg_prompt_tokens']:.0f}"
    )
    print(f"json:                   {args.json}")
    print(f"report:                 {args.report}")
    return result


if __name__ == "__main__":
    main()
