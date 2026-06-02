"""Test the CLI surface of :mod:`hive_benchmark` without touching a GPU."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "hive_benchmark.py")


def _run(args, env=None, cwd=None):
    full_env = os.environ.copy()
    ppath = full_env.get("PYTHONPATH", "")
    full_env["PYTHONPATH"] = ROOT + os.pathsep + ppath if ppath else ROOT
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        check=True,
        capture_output=True,
        text=True,
        env=full_env,
        cwd=cwd or ROOT,
    )


def test_benchmark_echo_smoke(tmp_path):
    out = tmp_path / "report.json"
    cp = _run(
        [
            "--transcript-turns",
            "20",
            "--brain-writes",
            "200",
            "--honey-comb-mode",
            "fast",
            "--inference-backend",
            "echo",
            "--quiet",
            "--output",
            str(out),
        ],
        env={"HIVE_NO_NVML": "1"},
    )
    assert "Hive Step 1 benchmark" in cp.stdout
    assert out.exists()
    payload = json.loads(out.read_text())
    assert "components" in payload
    assert "platform" in payload
    names = {c["name"] for c in payload["components"]}
    assert {"busybee_cpu", "honey_comb", "rust_brain", "inference"} <= names


def test_benchmark_unknown_honeycomb_mode_fails():
    with pytest.raises(subprocess.CalledProcessError):
        _run(["--honey-comb-mode", "wat", "--quiet"], env={"HIVE_NO_NVML": "1"})


def test_benchmark_with_real_honeycomb(tmp_path):
    pytest.importorskip("honeycomb")
    out = tmp_path / "report.json"
    _run(
        [
            "--transcript-turns",
            "20",
            "--brain-writes",
            "200",
            "--honey-comb-mode",
            "honeycomb",
            "--inference-backend",
            "echo",
            "--quiet",
            "--output",
            str(out),
        ],
        env={"HIVE_NO_NVML": "1"},
    )
    payload = json.loads(out.read_text())
    modes = {c["name"]: c["details"].get("mode") for c in payload["components"]}
    assert modes["honey_comb"] == "HoneyComb"
