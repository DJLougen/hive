"""Ensure the BusyBee /v1/learn security patch applies to upstream server.py."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_PATCH = Path(__file__).resolve().parents[1] / "patches" / "busybee-secure-learn.patch"
_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "busybee_cpu_server_pre_patch.py"


def test_busybee_secure_learn_patch_has_valid_unified_diff_lines() -> None:
    text = _PATCH.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line:
            continue
        if line.startswith(("---", "+++", "@@")):
            continue
        assert line[0] in " +-", f"line {lineno}: malformed diff prefix: {line!r}"


def test_busybee_secure_learn_patch_applies_to_upstream_fixture(tmp_path: Path) -> None:
  if not _FIXTURE.is_file():
    pytest.skip("fixture missing")
  if shutil.which("patch") is None:
    pytest.skip("patch(1) not available")

  target_dir = tmp_path / "busybee_cpu"
  target_dir.mkdir()
  shutil.copy(_FIXTURE, target_dir / "server.py")

  proc = subprocess.run(
    ["patch", "-p1", "-d", str(tmp_path), "-i", str(_PATCH)],
    capture_output=True,
    text=True,
  )
  assert proc.returncode == 0, proc.stderr or proc.stdout

  patched = (target_dir / "server.py").read_text(encoding="utf-8")
  assert "_authorize_learn" in patched
  assert "if not _authorize_learn(self):" in patched
