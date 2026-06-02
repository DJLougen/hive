"""hive/hardware.py — hardware monitoring utilities for Hive.

Three capabilities, in increasing order of accuracy:

* :class:`NullMonitor` — no GPU available; returns zeros.
* :class:`PynvmlMonitor` — real NVML reading via ``pynvml``. Works on
  NVIDIA x86 + Jetson (where pynvml is the supported binding).
* :func:`read_nvml_power` / :func:`read_nvml_memory` — low-level helpers
  that the benchmark calls for sub-second sampling.

If pynvml is missing, every function degrades to a no-op. The benchmark
script logs which mode it picked at startup.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from typing import Iterator

_log = logging.getLogger("hive.hardware")

# Module-level capability flag — set by :func:`init`. Tests reset it.
_HAS_NVML = False
_NVML = None  # the imported pynvml module, if available
_DEVICE_HANDLE = None  # the handle for GPU 0
_DEVICE_NAME = ""
_DEVICE_COUNT = 0


def init() -> str:
    """Initialise the monitor.

    Returns a short human-readable description of what we ended up with,
    suitable for printing in benchmark headers.
    """
    global _HAS_NVML, _NVML, _DEVICE_HANDLE, _DEVICE_NAME, _DEVICE_COUNT
    if os.environ.get("HIVE_NO_NVML"):
        return "disabled (HIVE_NO_NVML)"
    try:
        import pynvml  # type: ignore[import-not-found]

        pynvml.nvmlInit()
        _NVML = pynvml
        _DEVICE_COUNT = pynvml.nvmlDeviceGetCount()
        if _DEVICE_COUNT:
            _DEVICE_HANDLE = pynvml.nvmlDeviceGetHandleByIndex(0)
            name = pynvml.nvmlDeviceGetName(_DEVICE_HANDLE)
            # Newer pynvml returns a str; older returns bytes.
            _DEVICE_NAME = (
                name.decode() if isinstance(name, (bytes, bytearray)) else str(name)
            )
            _HAS_NVML = True
        return f"pynvml ({_DEVICE_COUNT} GPU(s); gpu0={_DEVICE_NAME})"
    except Exception as exc:  # pragma: no cover - optional
        _log.info("pynvml not available: %s", exc)
        return "unavailable"


def shutdown() -> None:
    if _HAS_NVML and _NVML is not None:
        try:
            _NVML.nvmlShutdown()
        except Exception:  # pragma: no cover  # nosec B110 — shutdown cleanup
            pass


def device_name() -> str:
    return _DEVICE_NAME


def device_count() -> int:
    return _DEVICE_COUNT


def read_power_mw() -> int:
    """Current GPU power draw in milliwatts. 0 if NVML is unavailable."""
    if not _HAS_NVML or _NVML is None or _DEVICE_HANDLE is None:
        return 0
    try:
        return int(_NVML.nvmlDeviceGetPowerUsage(_DEVICE_HANDLE))
    except Exception:  # pragma: no cover
        return 0


def read_memory_used_mb() -> float:
    """Current GPU memory used in MiB. 0 if NVML is unavailable."""
    if not _HAS_NVML or _NVML is None or _DEVICE_HANDLE is None:
        return 0.0
    try:
        return _NVML.nvmlDeviceGetMemoryInfo(_DEVICE_HANDLE).used / (1024 * 1024)
    except Exception:  # pragma: no cover
        return 0.0


def read_util_pct() -> float:
    """Current SM utilisation %. 0 if NVML is unavailable."""
    if not _HAS_NVML or _NVML is None or _DEVICE_HANDLE is None:
        return 0.0
    try:
        return float(_NVML.nvmlDeviceGetUtilizationRates(_DEVICE_HANDLE).gpu)
    except Exception:  # pragma: no cover
        return 0.0


# ---------------------------------------------------------------------------
# Sampler — periodic power + memory sampling
# ---------------------------------------------------------------------------


class PowerSampler:
    """Sample GPU power and memory at a fixed cadence.

    Used by the benchmark to get a real energy number (sum of power
    samples × Δt) instead of a wall-clock × TDP estimate. The CPU side
    remains a wall-clock × TDP estimate because there is no portable
    way to read host power on a developer laptop.
    """

    def __init__(self, *, interval_s: float = 0.05) -> None:
        self.interval_s = max(0.005, interval_s)
        self.samples: list[tuple[float, int, float]] = []  # (t, power_mw, mem_mb)

    def sample(self) -> None:
        self.samples.append(
            (time.perf_counter(), read_power_mw(), read_memory_used_mb())
        )

    def energy_joules(self) -> float:
        """Trapezoidal integration of the power samples."""
        if len(self.samples) < 2:
            return 0.0
        total = 0.0
        for (t0, p0, _), (t1, p1, _) in zip(self.samples, self.samples[1:]):
            dt = t1 - t0
            avg_mw = (p0 + p1) / 2.0
            total += avg_mw * dt * 1e-3  # mW × s = mJ; /1000 → J
        return total

    def peak_memory_mb(self) -> float:
        return max((m for _, _, m in self.samples), default=0.0)

    def avg_power_w(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        total = 0.0
        for (t0, p0, _), (t1, p1, _) in zip(self.samples, self.samples[1:]):
            total += (p0 + p1) / 2.0
        dur = self.samples[-1][0] - self.samples[0][0]
        return (total / max(1, len(self.samples) - 1)) / 1000.0 if dur > 0 else 0.0

    def reset(self) -> None:
        self.samples.clear()


@contextmanager
def power_window(*, interval_s: float = 0.05) -> Iterator[PowerSampler]:
    """Context manager that samples GPU power for the duration of the block.

    Usage::

        with power_window() as sampler:
            do_expensive_thing()
        print("joules", sampler.energy_joules())
    """
    sampler = PowerSampler(interval_s=interval_s)
    # One sample at start.
    sampler.sample()
    try:
        yield sampler
    finally:
        sampler.sample()
