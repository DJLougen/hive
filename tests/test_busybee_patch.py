"""Ensure the BusyBee /v1/learn security patch applies to upstream server.py.

``busybee_cpu`` is not installed in this environment, so the patched module
cannot be imported wholesale. Instead the patch's added ``_authorize_learn``
function — which depends only on stdlib ``os``/``secrets`` — is extracted from
the patched source via ``ast`` and executed, so the test exercises the real
authorization behaviour rather than only matching text.
"""

from __future__ import annotations

import ast
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_PATCH = Path(__file__).resolve().parents[1] / "patches" / "busybee-secure-learn.patch"
_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "busybee_cpu_server_pre_patch.py"


def _apply_patch(tmp_path: Path) -> Path:
    """Apply the patch to the fixture; return the patched server.py path."""
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
    return target_dir / "server.py"


def _load_authorize_learn(patched_source: str):
    """Extract and exec the patch-added ``_authorize_learn`` function."""
    tree = ast.parse(patched_source)
    wanted = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_LEARN_API_KEY_ENV"
            for t in node.targets
        ):
            wanted.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == "_authorize_learn":
            wanted.append(node)
    names = {
        n.name if isinstance(n, ast.FunctionDef) else "_LEARN_API_KEY_ENV"
        for n in wanted
    }
    assert names == {"_LEARN_API_KEY_ENV", "_authorize_learn"}, (
        f"patched source missing patch additions: {names}"
    )
    module = ast.Module(body=wanted, type_ignores=[])
    ns: dict = {"os": os, "secrets": secrets, "BaseHTTPRequestHandler": object}
    exec(compile(module, "<patched server.py>", "exec"), ns)
    return ns["_authorize_learn"]


class _FakeHandler:
    def __init__(self, authorization: str | None = None):
        self.headers = {} if authorization is None else {"Authorization": authorization}


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

    patched_path = _apply_patch(tmp_path)
    patched = patched_path.read_text(encoding="utf-8")
    assert "_authorize_learn" in patched
    assert "if not _authorize_learn(self):" in patched


def test_busybee_secure_learn_authorize_learn_behaviour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The patched auth gate denies by default and honours the bearer key."""
    if not _FIXTURE.is_file():
        pytest.skip("fixture missing")

    patched_path = _apply_patch(tmp_path)
    authorize = _load_authorize_learn(patched_path.read_text(encoding="utf-8"))

    monkeypatch.delenv("BUSYBEE_LEARN_API_KEY", raising=False)
    monkeypatch.delenv("BUSYBEE_LEARN_ALLOW_INSECURE", raising=False)

    # Secure by default: no key configured -> every request denied.
    assert authorize(_FakeHandler()) is False
    assert authorize(_FakeHandler("Bearer anything")) is False

    # With a key configured, only the matching bearer token passes.
    monkeypatch.setenv("BUSYBEE_LEARN_API_KEY", "s3cret")
    assert authorize(_FakeHandler()) is False
    assert authorize(_FakeHandler("Bearer wrong")) is False
    assert authorize(_FakeHandler("Bearer s3cret")) is True

    # Explicit opt-out restores the old unauthenticated behaviour.
    monkeypatch.setenv("BUSYBEE_LEARN_ALLOW_INSECURE", "1")
    assert authorize(_FakeHandler()) is True
