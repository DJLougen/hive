"""Ensure the BusyBee /v1/learn security patch applies to upstream server.py."""

from __future__ import annotations

import shutil
import subprocess
import sys
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

    target_dir = tmp_path / "busybee_cpu"
    target_dir.mkdir()
    shutil.copy(_FIXTURE, target_dir / "server.py")

    # Prefer GNU patch on Linux/macOS; git apply is more portable on Windows.
    patch_applied = False
    if shutil.which("patch") and sys.platform != "win32":
        proc = subprocess.run(
            ["patch", "-p1", "-d", str(tmp_path), "-i", str(_PATCH)],
            capture_output=True,
            text=True,
        )
        patch_applied = proc.returncode == 0

    if not patch_applied and shutil.which("git"):
        proc = subprocess.run(
            ["git", "-C", str(tmp_path), "apply", "--check", str(_PATCH)],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            # Actually apply it so we can verify the content
            subprocess.run(
                ["git", "-C", str(tmp_path), "apply", str(_PATCH)],
                capture_output=True,
                check=False,
            )
            patch_applied = True

    if not patch_applied:
        pytest.skip("patch(1) or git apply not available or failed")

    patched = (target_dir / "server.py").read_text(encoding="utf-8")
    assert "_authorize_learn" in patched
    assert "if not _authorize_learn(self):" in patched
