"""Version metadata must agree inside each artifact that is mechanically coupled.

A release bump touches several files within one shippable artifact; missing one
ships a component whose metadata disagrees with itself. These tests check only
*mechanical* coupling — files that are part of the same package and cannot
legitimately disagree:

* the root package: ``pyproject.toml`` ↔ ``hive.__version__`` ↔ ``uv.lock``
* hive-cpp:       ``Cargo.toml`` ↔ ``pyproject.toml`` ↔ ``hive_cpp.__version__``

Deliberately NOT checked here: whether hive-cpp equals the root version, and
whether the Helm chart's ``version`` equals its ``appVersion``. Those are
policy/design choices, not mechanical invariants — this repo has legitimately
let hive-cpp lag the root (at tag v0.6.0 hive-cpp was 0.5.0 while the root was
0.6.0), and Helm's ``version`` (chart packaging) and ``appVersion`` (deployed
application) are distinct fields that may differ. A unit test must not freeze
either; cross-artifact alignment is a review item.
"""

from __future__ import annotations

import re
from pathlib import Path

try:  # tomllib is 3.11+; this project supports 3.10
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on 3.10 only
    import tomli as tomllib  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parent.parent


def _root_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return data["project"]["version"]


def _first_version(path: Path) -> str:
    m = re.search(r'^version = "([^"]+)"', path.read_text(), re.MULTILINE)
    assert m, f"{path} has no 'version = \"...\"' line"
    return m.group(1)


def _dunder_version(path: Path) -> str:
    m = re.search(r'^__version__ = "([^"]+)"', path.read_text(), re.MULTILINE)
    assert m, f"{path} has no __version__"
    return m.group(1)


def test_root_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", _root_version()), _root_version()


def test_root_module_version_matches_pyproject() -> None:
    import hive

    assert hive.__version__ == _root_version()


def test_uv_lock_root_version_matches_pyproject() -> None:
    lock = (ROOT / "uv.lock").read_text()
    m = re.search(
        r'\[\[package\]\]\nname = "hive-agent-memory"\nversion = "([^"]+)"', lock
    )
    assert m, "uv.lock has no hive-agent-memory entry"
    assert m.group(1) == _root_version(), "uv.lock drifted (run `uv lock`)"


def test_hive_cpp_metadata_is_internally_consistent() -> None:
    cargo = _first_version(ROOT / "hive-cpp" / "Cargo.toml")
    pyproject = _first_version(ROOT / "hive-cpp" / "pyproject.toml")
    module = _dunder_version(ROOT / "hive-cpp" / "python" / "hive_cpp" / "__init__.py")
    assert cargo == pyproject == module, (
        f"hive-cpp disagrees with itself: Cargo.toml={cargo} "
        f"pyproject.toml={pyproject} __init__={module}"
    )
