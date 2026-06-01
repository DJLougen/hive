"""hive_llama_integration.py — minimal Hive + GPU model integration example.

This script wires Hive (busyBee-cpu + honey-comb + rust-brain) in front of a
GPU model. Two inference backends are supported:

* **vllm** — preferred when CUDA is available. Uses the OpenAI-compatible
  HTTP server that vLLM exposes.
* **llama.cpp** — preferred on Jetson / aarch64 / macOS. Talks to the
  llama-server binary over HTTP.
* **echo** — last-resort stub so this script remains runnable on a fresh
  checkout with no model server running.

Typical use on RTX 3090 / DGX Spark::

    vllm serve meta-llama/Llama-3.2-3B-Instruct --port 8000 &
    python examples/hive_llama_integration.py \\
        --inference-backend vllm --inference-endpoint http://127.0.0.1:8000

Typical use on Jetson Thor (llama.cpp)::

    llama-server -m model.gguf --port 8080 &
    python examples/hive_llama_integration.py \\
        --inference-backend llama.cpp --inference-endpoint http://127.0.0.1:8080
"""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any, Iterable

from hive import HiveStack
from hive import hardware, llm as llm_mod
from hive.rust_brain import EdgeKind

_log = logging.getLogger("hive.integration")

# Initialise NVML once at import. No-op on non-NVIDIA systems.
hardware.init()


# ---------------------------------------------------------------------------
# The agent loop
# ---------------------------------------------------------------------------


def build_messages(
    user_goal: str,
    transcript: list[tuple[str, str]],
    *,
    extra_memory: Iterable[dict[str, Any]] = (),
) -> list[dict[str, str]]:
    """Build the OpenAI-style message list fed to the LLM.

    Honey-Comb already compressed the transcript; we pass the compressed
    text plus the most relevant memory nodes from rust-brain.
    """
    messages: list[dict[str, str]] = [
        {"role": "system", "content": "You are a careful coding agent."}
    ]
    if extra_memory:
        mem = "\n".join(f"- {m['key']}: {m['value']}" for m in extra_memory)
        messages.append({"role": "system", "content": f"Relevant memory:\n{mem}"})
    messages.append({"role": "user", "content": user_goal})
    messages.extend({"role": role, "content": content} for role, content in transcript)
    return messages


def run_agent(
    stack: HiveStack,
    client: Any,
    *,
    user_goal: str,
    transcript: list[tuple[str, str]],
) -> dict[str, Any]:
    """One end-to-end Hive turn: route → compress → LLM → write memory.

    Returns a dict with the routing decision, compression stats, the
    model's response, and the current memory footprint.
    """
    # 1. busyBee-cpu decides the next mechanical action (or escalates).
    decision = stack.route(
        {
            "goal": user_goal,
            "state": {"current_step": 0, "last_tool": None},
            "available_tools": [
                {"name": "read_file"},
                {"name": "run_tests"},
                {"name": "apply_patch"},
                {"name": "escalate"},
            ],
        }
    )
    _log.info(
        "busybee → %s (conf=%.2f, escalated=%s)",
        decision.tool,
        decision.confidence,
        decision.escalated,
    )

    # 2. honey-comb compresses the transcript before the LLM sees it.
    compressed = stack.compress_many(transcript)
    compressed_msgs = [(c.role, c.content) for c in compressed]

    # 3. rust-brain contributes the most relevant prior memories.
    memory = stack.brain.search(min_trust=0.5)[:8]
    memory_dump = [n.to_dict() for n in memory]

    messages = build_messages(user_goal, compressed_msgs, extra_memory=memory_dump)
    response = client.chat(messages)

    # 4. Persist the model's response so future turns can recall it.
    stack.brain.remember(
        key="turn:0:response",
        value=response.text,
        trust=0.8,
        tags=("agent", "response"),
        edges={EdgeKind.CAUSED_BY: ["endpoint"]} if stack.brain.get("endpoint") else None,
    )

    return {
        "decision": {
            "tool": decision.tool,
            "args": decision.args,
            "confidence": decision.confidence,
            "escalated": decision.escalated,
            "source": decision.source,
        },
        "compression": {
            "turns": len(compressed),
            "tokens_in": sum(c.original_tokens for c in compressed),
            "tokens_out": sum(c.compressed_tokens for c in compressed),
            "ratio": (
                sum(c.original_tokens for c in compressed)
                / max(1, sum(c.compressed_tokens for c in compressed))
            ),
        },
        "response": {
            "text": response.text,
            "model": response.model,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
            "duration_s": response.duration_s,
            "finish_reason": response.finish_reason,
        },
        "memory_nodes": len(memory_dump),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Hive + GPU LLM integration example.")
    p.add_argument(
        "--inference-backend",
        choices=("vllm", "llama.cpp", "echo"),
        default="echo",
    )
    p.add_argument("--inference-endpoint", default="http://127.0.0.1:8000")
    p.add_argument(
        "--inference-model", default="meta-llama/Llama-3.2-3B-Instruct"
    )
    p.add_argument(
        "--goal",
        default="Refactor the auth module to use dependency injection.",
    )
    p.add_argument("--busybee-model", default=None, help="Optional path to a trained .joblib")
    p.add_argument(
        "--honey-comb-mode",
        choices=("auto", "fast", "honeycomb"),
        default="auto",
    )
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # Build the stack. busyBee is optional.
    busybee = None
    if args.busybee_model:
        try:
            from busybee_cpu import CpuActionPolicy  # type: ignore[import-not-found]

            busybee = CpuActionPolicy.load(args.busybee_model)
        except Exception as exc:  # pragma: no cover
            _log.warning("could not load busybee model: %s", exc)

    comb = _resolve_comb(args.honey_comb_mode)
    stack = HiveStack(busybee_policy=busybee, honey_comb=comb)
    stack.brain.remember(
        "endpoint", args.inference_endpoint, trust=0.95, tags=("config",)
    )

    # Build a tiny synthetic transcript so the demo is self-contained.
    transcript: list[tuple[str, str]] = [
        ("user", "Read the auth module first."),
        (
            "assistant",
            "Reading src/auth.py — it's 312 lines, uses a global Session singleton.",
        ),
        ("tool", "file: src/auth.py\n" + ("x" * 1500)),
        ("tool", "tests: 12 passed, 1 failed (test_session_invalidation)"),
    ]

    if args.inference_backend in ("vllm", "llama.cpp"):
        try:
            llm_mod.probe_endpoint(args.inference_endpoint, timeout=1.0)
        except RuntimeError as exc:
            _log.warning("model server unreachable (%s); falling back to echo", exc)
            args.inference_backend = "echo"
    client = llm_mod.make_backend(
        args.inference_backend,
        endpoint=args.inference_endpoint,
        model=args.inference_model,
    )

    with hardware.power_window() as sampler:
        result = run_agent(
            stack, client, user_goal=args.goal, transcript=transcript
        )
    result["power"] = {
        "avg_w": sampler.avg_power_w(),
        "energy_joules": sampler.energy_joules(),
        "peak_memory_mb": sampler.peak_memory_mb(),
    }
    print(json.dumps(result, indent=2, default=str))
    return 0


def _resolve_comb(mode: str) -> Any:
    if mode == "fast":
        from hive.rule_fast import RuleFastHoneyComb

        return RuleFastHoneyComb()
    if mode == "honeycomb":
        from honeycomb import HoneyComb  # type: ignore[import-not-found]

        return HoneyComb(thread_safe=True, metrics_enabled=True)
    return None  # auto: pick at construction time


if __name__ == "__main__":
    raise SystemExit(main())
