"""Version metadata must agree across every shipping surface.

A release bump touches several files; missing one ships a package whose
``__version__``, lockfile, chart, or citation disagree. These tests fail
closed on that drift so a bump can't land half-done.

Policy (stated in docs/MIGRATION.md):

* the *working tree* version (``pyproject.toml``) is authoritative;
* artifacts that assert a *released* state — ``.github/citation.cff`` — track
  the last actual release, which lags the tree between releases and is
  corrected only when a tag is cut.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import tomllib

ROOT = Path(__file__).resolve().parent.parent


def _tree_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return data["project"]["version"]


def test_project_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", _tree_version()), _tree_version()


def test_hive_module_version_matches_tree() -> None:
    import hive

    assert hive.__version__ == _tree_version()


def test_hive_cpp_versions_match_tree() -> None:
    cargo = (ROOT / "hive-cpp" / "Cargo.toml").read_text()
    m = re.search(r'^version = "([^"]+)"', cargo, re.MULTILINE)
    assert m, "hive-cpp/Cargo.toml has no version"
    assert m.group(1) == _tree_version(), "hive-cpp/Cargo.toml drifted"

    py = (ROOT / "hive-cpp" / "pyproject.toml").read_text()
    m = re.search(r'^version = "([^"]+)"', py, re.MULTILINE)
    assert m, "hive-cpp/pyproject.toml has no version"
    assert m.group(1) == _tree_version(), "hive-cpp/pyproject.toml drifted"

    init = (ROOT / "hive-cpp" / "python" / "hive_cpp" / "__init__.py").read_text()
    m = re.search(r'^__version__ = "([^"]+)"', init, re.MULTILINE)
    assert m, "hive-cpp python __init__ has no version"
    assert m.group(1) == _tree_version(), "hive-cpp python __init__ drifted"


def test_uv_lock_root_version_matches_tree() -> None:
    lock = (ROOT / "uv.lock").read_text()
    m = re.search(
        r'\[\[package\]\]\nname = "hive-agent-memory"\nversion = "([^"]+)"', lock
    )
    assert m, "uv.lock has no hive-agent-memory entry"
    assert m.group(1) == _tree_version(), "uv.lock drifted (run `uv lock`)"


def test_helm_chart_matches_tree() -> None:
    chart = (ROOT / "deploy" / "helm" / "Chart.yaml").read_text()
    ver = re.search(r'^version: "?([\d.]+)"?', chart, re.MULTILINE)
    app = re.search(r'^appVersion: "?([\d.]+)"?', chart, re.MULTILINE)
    assert ver and app, "Chart.yaml missing version/appVersion"
    assert ver.group(1) == _tree_version(), "chart version drifted"
    assert app.group(1) == _tree_version(), "chart appVersion drifted"


def test_usage_guide_version_matches_tree() -> None:
    usage = (ROOT / "docs" / "USAGE.md").read_text()
    m = re.search(r"^\*\*Version\*\*: ([\d.]+)", usage, re.MULTILINE)
    assert m, "USAGE.md has no version stamp"
    assert m.group(1) == _tree_version(), "docs/USAGE.md drifted"


@pytest.mark.parametrize(
    "relpath",
    ["docs/compliance-checklist.md", "docs/incident-response-runbook.md"],
)
def test_attestation_docs_do_not_claim_future_version(relpath: str) -> None:
    """Compliance/runbook docs carry a date and describe an audited state.

    They must never claim a version newer than the last release without a
    matching re-validation — so they may not lead the tree.
    """
    text = (ROOT / relpath).read_text()
    m = re.search(r"^\*\*Version\*\*: ([\d.]+)", text, re.MULTILINE)
    assert m, f"{relpath} has no version stamp"
    stamped = tuple(int(x) for x in m.group(1).split("."))
    tree = tuple(int(x) for x in _tree_version().split("."))
    assert stamped <= tree, f"{relpath} claims {m.group(1)} > tree {_tree_version()}"
