"""Capture *real* tool output as fixtures for the fidelity benchmark.

Writes a small throwaway test suite with known failing tests to a temp
directory, runs genuine ``pytest`` against it, and stores the verbatim
output plus a ground-truth facts file under ``tests/fixtures/fidelity/``.

Run once and commit the result:

    python3 scripts/capture_real_fixtures.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from fidelity_corpus import FIXTURE_DIR

_FAILING_TESTS = [
    "test_auth_token_refresh_expired",
    "test_billing_invoice_rounding",
    "test_search_unicode_query",
]
_PASSING_TESTS = [
    "test_auth_login_ok",
    "test_auth_logout_ok",
    "test_billing_invoice_total",
    "test_search_basic_query",
    "test_cache_eviction_lru",
]

_TEST_TEMPLATE = '''"""Throwaway suite used to capture genuine pytest output."""


def _status(route):
    return 500 if "fail" in route else 200

{failing}

{passing}
'''


def _make_suite(tmp: Path) -> None:
    failing = "\n".join(
        f'def {name}():\n    assert _status("fail/{name}") == 200, "expected 200"\n'
        for name in _FAILING_TESTS
    )
    passing = "\n".join(
        f'def {name}():\n    assert _status("ok/{name}") == 200\n'
        for name in _PASSING_TESTS
    )
    (tmp / "test_capture_demo.py").write_text(
        _TEST_TEMPLATE.format(failing=failing, passing=passing)
    )


def main() -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        _make_suite(tmp)
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-v", "-p", "no:cacheprovider", str(tmp)],
            capture_output=True,
            text=True,
        )
        output = proc.stdout

    (FIXTURE_DIR / "pytest_real_run.txt").write_text(output)
    facts = {
        "category": "pytest_log",
        "role": "tool",
        "facts": [
            {"name": "failing test name", "needle": name} for name in _FAILING_TESTS
        ]
        + [
            {
                "name": "failure count summary",
                "needle": f"{len(_FAILING_TESTS)} failed, {len(_PASSING_TESTS)} passed",
            }
        ],
    }
    (FIXTURE_DIR / "pytest_real_run.facts.json").write_text(
        json.dumps(facts, indent=2) + "\n"
    )
    print(f"captured {len(output)} chars of real pytest output -> {FIXTURE_DIR}")


if __name__ == "__main__":
    main()
