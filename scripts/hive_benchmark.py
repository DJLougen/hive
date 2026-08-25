"""hive_benchmark.py — Step 1 end-to-end benchmark for the Hive stack.

This script exercises every component of Hive against a synthetic agent
transcript and reports the metrics the Step 1 acceptance criteria call for:

* **Peak host memory** (RSS) and **peak GPU memory** (NVML)
* **Context compression ratio** (honey-comb or rule_fast)
* **Tokens/sec** through the inline compression hot loop
* **Routing decisions per second** (busyBee-cpu)
* **Memory writes/sec** (rust-brain)
* **Energy** — *real* GPU joules via NVML power sampling; CPU estimated
  from package TDP × wall-clock. With NVML unavailable, GPU joules
  default to 0 and the GPU is skipped.

The script is also a small library: every measurement function is
importable so that a CI step can run the same code under a deterministic
seed without going through the CLI.

ARM64 / Jetson build notes
--------------------------
This file is pure Python and runs on:

* x86_64 + CUDA (RTX 3090, DGX Spark) — primary dev target, GPU enabled.
* aarch64 Linux (Jetson Thor, Grace, Orin) — falls back to CPU inference.
* Apple Silicon (MPS) and Raspberry Pi — CPU path.

For Jetson, install the system CUDA first (``nv-jetson-cuda``) and then::

    pip install --extra-index-url https://pypi.nvidia.com torch

See ``hive/docker/Dockerfile.aarch64`` for a turn-key image.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import random
import statistics
import string
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Optional deps — make the script degrade gracefully.
# ---------------------------------------------------------------------------

try:
    import psutil  # type: ignore[import-not-found]

    _HAS_PSUTIL = True
except Exception:  # pragma: no cover - import guard
    psutil = None  # type: ignore[assignment]
    _HAS_PSUTIL = False

try:
    import torch  # type: ignore[import-not-found]

    _HAS_TORCH = True
except Exception:  # pragma: no cover - import guard
    torch = None  # type: ignore[assignment]
    _HAS_TORCH = False

# Hive components
from hive import HiveStack, hardware
from hive import llm as llm_mod
from hive.rust_brain import RustBrain

_log = logging.getLogger("hive.benchmark")

# Initialise the hardware monitor. We do this at import time so any
# process that imports ``hive_benchmark`` (including pytest) gets a
# configured monitor.
hardware.init()


# ---------------------------------------------------------------------------
# Synthetic transcript
# ---------------------------------------------------------------------------


def _gibberish(n_words: int, *, rng: random.Random) -> str:
    """Produce realistic-looking test output / file content for the
    compression hot loop. We keep tokens heavy and lines long so honey-comb
    has something to compress."""
    n_words = max(8, n_words)
    if rng.random() < 0.5:
        return "\n".join(
            f"  test_{rng.randrange(1000):04d} ... {rng.choice(['ok', 'FAIL', 'ERROR'])}"
            for _ in range(max(1, n_words // 4))
        )
    return " ".join(
        "".join(rng.choices(string.ascii_letters, k=rng.randint(3, 8)))
        for _ in range(n_words)
    )


def make_transcript(num_turns: int, *, seed: int = 0) -> list[tuple[str, str]]:
    """Build a synthetic agent transcript of ``num_turns`` messages."""
    rng = random.Random(seed)
    roles = ["system", "user", "assistant", "tool"]
    out: list[tuple[str, str]] = []
    for i in range(num_turns):
        role = roles[i % len(roles)]
        n_words = rng.randint(120, 480)
        out.append((role, _gibberish(n_words, rng=rng)))
    return out


# ---------------------------------------------------------------------------
# Memory + energy tracking
# ---------------------------------------------------------------------------


@dataclass
class MemoryTracker:
    """Sample peak host + GPU memory and integrate real GPU energy."""

    start_rss_mb: float = 0.0
    peak_rss_mb: float = 0.0
    peak_gpu_mb: float = 0.0
    samples: int = 0

    def __post_init__(self) -> None:
        if _HAS_PSUTIL:
            proc = psutil.Process(os.getpid())
            self.start_rss_mb = proc.memory_info().rss / (1024 * 1024)
            self.peak_rss_mb = self.start_rss_mb
        if _HAS_TORCH and torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
        self._sampler = hardware.PowerSampler()

    def sample(self) -> None:
        self.samples += 1
        if _HAS_PSUTIL:
            rss = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
            self.peak_rss_mb = max(self.peak_rss_mb, rss)
        if _HAS_TORCH and torch.cuda.is_available():
            mb = torch.cuda.max_memory_allocated() / (1024 * 1024)
            self.peak_gpu_mb = max(self.peak_gpu_mb, mb)
        self._sampler.sample()

    def gpu_peak_mb(self) -> float:
        """Real NVML-resident peak memory (MiB)."""
        return max(self.peak_gpu_mb, self._sampler.peak_memory_mb())

    def gpu_energy_joules(self) -> float:
        return self._sampler.energy_joules()

    def gpu_avg_power_w(self) -> float:
        return self._sampler.avg_power_w()

    def finalise(self) -> MemoryTracker:
        self.sample()
        return self


# ---------------------------------------------------------------------------
# Energy estimation
# ---------------------------------------------------------------------------


def estimate_cpu_energy_joules(elapsed_s: float, *, package_tdp_w: float | None = None) -> float:
    """Rough CPU energy estimate. Multiplied by 0.4 because idle desktops
    draw ~40% of TDP on average; laptops and Jetson class ~25%."""
    if package_tdp_w is None:
        package_tdp_w = _guess_tdp()
    return elapsed_s * package_tdp_w * 0.4


def _guess_tdp() -> float:
    if _HAS_TORCH and torch.cuda.is_available():
        name = (torch.cuda.get_device_name(0) or "").lower()
        if "3090" in name:
            return 350.0
        if "thor" in name or "orin" in name:
            return 40.0
        if "spark" in name or "gb10" in name:
            return 150.0
        return 200.0
    if platform.machine().lower() in {"aarch64", "arm64"}:
        return 15.0
    return 65.0


# ---------------------------------------------------------------------------
# Component runners
# ---------------------------------------------------------------------------


@dataclass
class ComponentStats:
    name: str
    duration_s: float
    items: int
    items_per_s: float
    details: dict[str, Any] = field(default_factory=dict)


def run_rust_brain(num_writes: int, *, seed: int = 0) -> ComponentStats:
    """Smoke the rust-brain store with ``num_writes`` writes + 1 scan."""
    rng = random.Random(seed)
    brain = RustBrain()
    t0 = time.perf_counter()
    for i in range(num_writes):
        brain.remember(
            key=f"k{i:04d}",
            value={"i": i, "rand": rng.random(), "label": "x" * rng.randint(8, 32)},
            trust=round(rng.random(), 3),
        )
    for i in range(0, num_writes, max(1, num_writes // 100)):
        brain.recall(f"k{i:04d}")
    elapsed = time.perf_counter() - t0
    return ComponentStats(
        name="rust_brain",
        duration_s=elapsed,
        items=num_writes,
        items_per_s=num_writes / max(elapsed, 1e-9),
        details={"node_count": len(brain), "stats": brain.stats()},
    )


def run_honeycomb(stack: HiveStack, transcript: Sequence[tuple[str, str]]) -> ComponentStats:
    """Run every turn through the honey-comb hot loop."""
    t0 = time.perf_counter()
    total_in = total_out = 0
    label_hist: dict[str, int] = {}
    for role, content in transcript:
        out = stack.compress(role, content)
        total_in += out.original_tokens
        total_out += out.compressed_tokens
        label_hist[out.label] = label_hist.get(out.label, 0) + 1
    elapsed = time.perf_counter() - t0
    items = len(transcript)
    return ComponentStats(
        name="honey_comb",
        duration_s=elapsed,
        items=items,
        items_per_s=items / max(elapsed, 1e-9),
        details={
            "tokens_in": total_in,
            "tokens_out": total_out,
            "compression_ratio": total_in / max(total_out, 1),
            "labels": label_hist,
            "mode": type(stack.comb).__name__,
        },
    )


def run_busybee(stack: HiveStack, num_turns: int, *, seed: int = 0) -> ComponentStats:
    """Drive the busyBee policy with a synthetic state stream."""
    rng = random.Random(seed + 1)
    states = [
        {
            "goal": "ship the hive step 1 release",
            "state": {
                "current_step": i,
                "last_tool": rng.choice(["read_file", "run_tests", "apply_patch", "escalate"]),
                "recent_observations": [f"obs-{i}-{j}" for j in range(rng.randint(1, 4))],
            },
            "available_tools": [
                {"name": "read_file"},
                {"name": "run_tests"},
                {"name": "apply_patch"},
                {"name": "escalate"},
            ],
        }
        for i in range(num_turns)
    ]
    t0 = time.perf_counter()
    decisions: list[str] = []
    for s in states:
        decision = stack.route(s)
        decisions.append(decision.tool)
    elapsed = time.perf_counter() - t0
    return ComponentStats(
        name="busybee_cpu",
        duration_s=elapsed,
        items=num_turns,
        items_per_s=num_turns / max(elapsed, 1e-9),
        details={
            "decision_hist": {d: decisions.count(d) for d in sorted(set(decisions))},
            "policy_loaded": stack.busybee is not None,
        },
    )


def run_inference(backend: Any, *, prompt_tokens: int, max_tokens: int) -> ComponentStats:
    """Drive the LLM backend with a fixed prompt.

    Computes tokens/sec from the server-reported ``completion_tokens`` so
    the number is comparable across backends. The prompt is a small
    message list; the real per-turn cost will be dominated by the
    LLM call rather than by Hive.
    """
    # Build a prompt whose size is roughly ``prompt_tokens`` * 4 chars.
    chars = max(64, prompt_tokens * 4)
    prompt = "x" * chars
    messages = [
        {"role": "system", "content": "You are a careful coding agent."},
        {"role": "user", "content": prompt},
    ]
    t0 = time.perf_counter()
    try:
        resp = backend.chat(messages, max_tokens=max_tokens)
    except RuntimeError as exc:
        return ComponentStats(
            name="inference",
            duration_s=time.perf_counter() - t0,
            items=0,
            items_per_s=0.0,
            details={"error": str(exc), "model": getattr(backend, "model_name", "?")},
        )
    elapsed = time.perf_counter() - t0
    toks = resp.completion_tokens or 0
    return ComponentStats(
        name="inference",
        duration_s=elapsed,
        items=toks,
        items_per_s=toks / max(elapsed, 1e-9),
        details={
            "model": resp.model,
            "prompt_tokens": resp.prompt_tokens,
            "completion_tokens": toks,
            "finish_reason": resp.finish_reason,
            "backend": type(backend).__name__,
        },
    )


# ---------------------------------------------------------------------------
# Top-level benchmark driver
# ---------------------------------------------------------------------------


@dataclass
class BenchmarkReport:
    platform: dict[str, Any]
    components: list[ComponentStats]
    peak_rss_mb: float
    peak_gpu_mb: float
    elapsed_s: float
    cpu_energy_joules: float
    gpu_energy_joules: float
    gpu_avg_power_w: float
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "components": [asdict(c) for c in self.components],
            "peak_rss_mb": self.peak_rss_mb,
            "peak_gpu_mb": self.peak_gpu_mb,
            "elapsed_s": self.elapsed_s,
            "cpu_energy_joules": self.cpu_energy_joules,
            "gpu_energy_joules": self.gpu_energy_joules,
            "gpu_avg_power_w": self.gpu_avg_power_w,
            **self.extras,
        }


def _platform_info() -> dict[str, Any]:
    info: dict[str, Any] = {
        "python": sys.version.split()[0],
        "machine": platform.machine(),
        "system": platform.system(),
        "release": platform.release(),
        "hive_version": HiveStack.__module__,
        "nvml": hardware.device_name() or "unavailable",
    }
    if _HAS_TORCH and torch.cuda.is_available():
        info["cuda"] = torch.version.cuda
        info["gpu"] = torch.cuda.get_device_name(0)
        info["gpu_count"] = torch.cuda.device_count()
    return info


def run_benchmark(
    *,
    transcript_turns: int = 200,
    brain_writes: int = 5_000,
    inference_prompt_tokens: int = 256,
    inference_max_tokens: int = 64,
    busybee_model: str | None = None,
    honey_comb_mode: str = "auto",
    inference_backend: str = "echo",
    inference_endpoint: str | None = None,
    inference_model: str = "hive-default",
    package_tdp_w: float | None = None,
    seed: int = 0,
) -> BenchmarkReport:
    """Run the full Step 1 benchmark and return a structured report.

    Parameters
    ----------
    honey_comb_mode:
        ``"auto"`` (use honey-comb if installed, else rule_fast), ``"fast"``
        (always rule_fast), or ``"honeycomb"`` (require honey-comb).
    inference_backend:
        ``"echo"``, ``"vllm"`` or ``"llama.cpp"``. ``echo`` always works
        (returns a stub response); the others require ``inference_endpoint``.
    """
    busybee = None
    if busybee_model and os.path.exists(busybee_model):
        try:
            from busybee_cpu import CpuActionPolicy  # type: ignore[import-not-found]

            busybee = CpuActionPolicy.load(busybee_model)
        except Exception as exc:  # pragma: no cover - optional dep
            _log.warning("could not load busybee model %s: %s", busybee_model, exc)

    comb = _resolve_honey_comb(honey_comb_mode)
    stack = HiveStack(busybee_policy=busybee, honey_comb=comb)
    backend = _resolve_inference(inference_backend, inference_endpoint, inference_model)

    transcript = make_transcript(transcript_turns, seed=seed)
    mem = MemoryTracker()

    t_start = time.perf_counter()
    busybee_stats = run_busybee(stack, transcript_turns, seed=seed)
    mem.sample()
    comb_stats = run_honeycomb(stack, transcript)
    mem.sample()
    brain_stats = run_rust_brain(brain_writes, seed=seed)
    mem.sample()
    inf_stats = run_inference(
        backend, prompt_tokens=inference_prompt_tokens, max_tokens=inference_max_tokens
    )
    mem.sample()
    elapsed = time.perf_counter() - t_start
    mem.finalise()

    return BenchmarkReport(
        platform=_platform_info(),
        components=[busybee_stats, comb_stats, brain_stats, inf_stats],
        peak_rss_mb=mem.peak_rss_mb,
        peak_gpu_mb=mem.gpu_peak_mb(),
        elapsed_s=elapsed,
        cpu_energy_joules=estimate_cpu_energy_joules(elapsed, package_tdp_w=package_tdp_w),
        gpu_energy_joules=mem.gpu_energy_joules(),
        gpu_avg_power_w=mem.gpu_avg_power_w(),
        extras={
            "transcript_turns": transcript_turns,
            "brain_writes": brain_writes,
            "honey_comb_mode": type(comb).__name__,
            "inference_backend": type(backend).__name__,
            "inference_endpoint": inference_endpoint or "(echo)",
            "package_tdp_w_estimate": _guess_tdp(),
        },
    )


def _resolve_honey_comb(mode: str) -> Any:
    if mode == "fast":
        from hive.rule_fast import RuleFastHoneyComb

        return RuleFastHoneyComb()
    if mode == "honeycomb":
        from honeycomb import HoneyComb  # type: ignore[import-not-found]

        return HoneyComb(thread_safe=True, metrics_enabled=True)
    # auto: prefer honey-comb; fall back to rule_fast
    try:
        from honeycomb import HoneyComb  # type: ignore[import-not-found]

        return HoneyComb(thread_safe=True, metrics_enabled=True)
    except Exception:
        from hive.rule_fast import RuleFastHoneyComb

        return RuleFastHoneyComb()


def _resolve_inference(name: str, endpoint: str | None, model: str) -> Any:
    if name in ("vllm", "llama.cpp") and endpoint:
        try:
            llm_mod.probe_endpoint(endpoint, timeout=1.0)
        except RuntimeError as exc:
            _log.warning("inference endpoint unreachable, falling back to echo: %s", exc)
            return llm_mod.EchoBackend()
    return llm_mod.make_backend(name, endpoint=endpoint, model=model)


# ---------------------------------------------------------------------------
# Statistical envelope
# ---------------------------------------------------------------------------


def run_with_envelope(
    *,
    runs: int = 3,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run the benchmark ``runs`` times and return mean ± stddev per metric.

    Useful for CI: a single run is too noisy on shared hardware.
    """
    reports = [run_benchmark(**kwargs) for _ in range(runs)]
    keys = ["elapsed_s", "peak_rss_mb", "peak_gpu_mb", "cpu_energy_joules", "gpu_energy_joules"]
    aggregate: dict[str, dict[str, float]] = {}
    for key in keys:
        values = [getattr(r, key) for r in reports]
        aggregate[key] = {
            "mean": statistics.fmean(values),
            "stdev": statistics.pstdev(values) if len(values) > 1 else 0.0,
            "min": min(values),
            "max": max(values),
        }
    # Per-component rates
    component_rates: dict[str, list[float]] = {}
    for r in reports:
        for c in r.components:
            component_rates.setdefault(c.name, []).append(c.items_per_s)
    aggregate["component_rates"] = {
        name: {
            "mean": statistics.fmean(v),
            "stdev": statistics.pstdev(v) if len(v) > 1 else 0.0,
        }
        for name, v in component_rates.items()
    }
    return {"runs": runs, "aggregate": aggregate, "reports": [r.to_dict() for r in reports]}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_table(report: BenchmarkReport) -> None:
    print("\n=== Hive Step 1 benchmark ===")
    print(f"  platform    : {report.platform.get('machine')} / {report.platform.get('system')}")
    print(f"  nvml        : {report.platform.get('nvml')}")
    print(f"  peak RSS    : {report.peak_rss_mb:8.1f} MB")
    if report.peak_gpu_mb:
        print(f"  peak GPU    : {report.peak_gpu_mb:8.1f} MB")
    print(f"  elapsed     : {report.elapsed_s:8.3f} s")
    print(f"  CPU energy  : {report.cpu_energy_joules:8.1f} J (est. from TDP)")
    if report.gpu_energy_joules:
        print(f"  GPU energy  : {report.gpu_energy_joules:8.1f} J (NVML)")
        print(f"  GPU avg pwr : {report.gpu_avg_power_w:8.1f} W")
    print()
    print(f"  {'component':<16} {'items':>8} {'time(s)':>10} {'rate':>14}")
    print(f"  {'-'*16} {'-'*8} {'-'*10} {'-'*14}")
    for c in report.components:
        rate = f"{c.items_per_s:.1f}/s" if c.items_per_s else "n/a"
        print(f"  {c.name:<16} {c.items:>8d} {c.duration_s:>10.4f} {rate:>14}")
    print()
    for c in report.components:
        print(f"  {c.name}:")
        for k, v in c.details.items():
            print(f"    {k}: {v}")
    print()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Step 1 benchmark for the Hive stack (busyBee-cpu + honey-comb + rust-brain).",
    )
    p.add_argument("--transcript-turns", type=int, default=200)
    p.add_argument("--brain-writes", type=int, default=5_000)
    p.add_argument("--inference-prompt-tokens", type=int, default=256)
    p.add_argument("--inference-max-tokens", type=int, default=64)
    p.add_argument("--busybee-model", default=None, help="Path to a trained busybee_cpu CpuActionPolicy .joblib")
    p.add_argument(
        "--honey-comb-mode",
        choices=("auto", "fast", "honeycomb"),
        default="auto",
        help="'fast' = in-repo rule_fast, 'honeycomb' = the sibling package, 'auto' = prefer honeycomb.",
    )
    p.add_argument(
        "--inference-backend",
        choices=("echo", "vllm", "llama.cpp"),
        default="echo",
    )
    p.add_argument("--inference-endpoint", default=None, help="URL of the model server, e.g. http://127.0.0.1:8000")
    p.add_argument("--inference-model", default="hive-default")
    p.add_argument("--package-tdp-w", type=float, default=None, help="Override TDP estimate (watts).")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--runs", type=int, default=1, help="Repeat the benchmark N times for an envelope.")
    p.add_argument("--output", default=None, help="Optional path to write the JSON report.")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    common = dict(
        transcript_turns=args.transcript_turns,
        brain_writes=args.brain_writes,
        inference_prompt_tokens=args.inference_prompt_tokens,
        inference_max_tokens=args.inference_max_tokens,
        busybee_model=args.busybee_model,
        honey_comb_mode=args.honey_comb_mode,
        inference_backend=args.inference_backend,
        inference_endpoint=args.inference_endpoint,
        inference_model=args.inference_model,
        package_tdp_w=args.package_tdp_w,
        seed=args.seed,
    )
    if args.runs <= 1:
        report = run_benchmark(**common)
        _print_table(report)
        if args.output:
            out_path = os.path.abspath(args.output)
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(report.to_dict(), fh, indent=2)
            print(f"wrote {out_path}")
    else:
        envelope = run_with_envelope(runs=args.runs, **common)
        if args.output:
            out_path = os.path.abspath(args.output)
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(envelope, fh, indent=2)
            print(f"wrote {out_path}")
        else:
            print(json.dumps(envelope["aggregate"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
