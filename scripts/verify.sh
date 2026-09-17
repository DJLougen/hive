#!/usr/bin/env bash
# One-command verification for Hive. Everything CI enforces, in the order that
# fails fastest. Run from the repo root:  bash scripts/verify.sh
#
#   python  — ruff, mypy, the full suite with the coverage floor, the claim gate,
#             the pentest gate (unaccepted critical/high findings block)
#   rust    — cargo test, then the native adapter tests against a freshly built
#             extension (skipped with a notice when cargo/maturin are absent)
#
# Exit code is non-zero if anything fails. Nothing here touches the network.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 2
PY="${PY:-.venv/bin/python}"
[[ -x "$PY" ]] || PY=python3

fail=0
run() {
  local label="$1"; shift
  printf '\n=== %s\n' "$label"
  if "$@"; then
    printf -- '--- %s: OK\n' "$label"
  else
    printf -- '--- %s: FAILED\n' "$label"
    fail=1
  fi
}

run "ruff (hive, tests, scripts)" "$PY" -m ruff check hive/ tests/ scripts/
run "mypy (hive)" "$PY" -m mypy hive/ --ignore-missing-imports
run "pytest + coverage floor" "$PY" -m pytest -q --cov=hive --cov-report=term-missing --cov-fail-under=80
run "claim gate (README/docs vs committed artifacts)" "$PY" scripts/check_claims.py
run "pentest (hive module)" "$PY" scripts/hive_pentest.py --module hive

if command -v cargo >/dev/null 2>&1 && [[ -d hive-cpp ]]; then
  run "cargo test (hive-cpp)" cargo test --manifest-path hive-cpp/Cargo.toml
  if "$PY" -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('hive_cpp') else 1)"; then
    run "native adapter tests (extension installed)" env HIVE_REQUIRE_NATIVE=1 "$PY" -m pytest tests/test_native_backend.py -q
  else
    printf '\n=== native adapter tests: SKIPPED (hive_cpp not installed; pip install dist/*.whl)\n'
  fi
else
  printf '\n=== cargo test: SKIPPED (cargo or hive-cpp absent)\n'
fi

printf '\n%s\n' "----------------------------------------"
if [[ "$fail" -eq 0 ]]; then
  printf 'verify: all gates passed\n'
else
  printf 'verify: FAILURES above\n'
fi
exit "$fail"
