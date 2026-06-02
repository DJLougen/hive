"""Tests for :mod:`hive.hardware` and :mod:`hive.llm`.

We don't require NVML or a running model server for these tests. The
behaviour we test is the graceful-degradation contract.
"""

from __future__ import annotations


import pytest

from hive import hardware, llm as llm_mod


def test_hardware_init_returns_string(monkeypatch):
    monkeypatch.setenv("HIVE_NO_NVML", "1")
    # The init module-level already ran; we re-init to be sure.
    desc = hardware.init()
    assert isinstance(desc, str)
    assert "disabled" in desc or "unavailable" in desc or "pynvml" in desc


def test_read_power_returns_int_when_nvml_missing(monkeypatch):
    monkeypatch.setenv("HIVE_NO_NVML", "1")
    hardware.init()
    assert hardware.read_power_mw() == 0
    assert hardware.read_memory_used_mb() == 0.0
    assert hardware.read_util_pct() == 0.0


def test_power_sampler_trapezoid():
    sampler = hardware.PowerSampler()
    # Two-sample synthetic trace
    sampler.samples = [
        (0.0, 100_000, 0.0),  # 100 W
        (1.0, 200_000, 0.0),  # 200 W
    ]
    # Trapezoid: avg 150 W × 1 s = 150 J
    assert sampler.energy_joules() == pytest.approx(150.0, abs=0.5)


def test_power_sampler_empty_returns_zero():
    sampler = hardware.PowerSampler()
    assert sampler.energy_joules() == 0.0
    assert sampler.peak_memory_mb() == 0.0
    assert sampler.avg_power_w() == 0.0


def test_power_window_records_two_samples():
    sampler_cm = hardware.power_window()
    with sampler_cm as s:
        pass
    # Two samples: one on enter, one on exit.
    assert len(s.samples) == 2


def test_echo_backend_is_deterministic():
    backend = llm_mod.EchoBackend()
    r1 = backend.chat([{"role": "user", "content": "hello world"}])
    r2 = backend.chat([{"role": "user", "content": "hello world"}])
    assert r1.text == r2.text
    assert r1.model == "echo"
    assert r1.finish_reason == "stop"


def test_make_backend_echo_no_endpoint():
    backend = llm_mod.make_backend("echo")
    assert isinstance(backend, llm_mod.EchoBackend)


def test_make_backend_unknown_raises():
    with pytest.raises(ValueError):
        llm_mod.make_backend("nope")


def test_make_backend_http_requires_endpoint():
    with pytest.raises(ValueError):
        llm_mod.make_backend("vllm")


def test_probe_endpoint_unreachable_raises():
    with pytest.raises(RuntimeError):
        # Port 1 is reserved and not bound on any sane machine.
        llm_mod.probe_endpoint("http://127.0.0.1:1", timeout=0.1)
