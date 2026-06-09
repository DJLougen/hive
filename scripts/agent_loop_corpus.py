"""Multi-step debugging episodes for the agent-loop fidelity eval.

Each episode is a short, realistic agent trajectory: run tests → read file
→ apply patch → re-run tests. At every step the agent must pick the next
tool from {read_file, run_tests, apply_patch, escalate} given the
(compressed or raw) transcript so far.

Ground truth is the tool name only — argument resolution is busybee's job.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Step:
    tool_output: str
    expected_tool: str


@dataclass(slots=True)
class Episode:
    id: str
    goal: str
    steps: list[Step]


EPISODES: list[Episode] = [
    Episode(
        id="auth_token_expiry",
        goal="Fix the failing auth token tests.",
        steps=[
            Step(
                tool_output=(
                    "============================= test session starts ==============================\n"
                    "collected 42 items\n"
                    "tests/test_auth.py::test_token_refresh_expired FAILED\n"
                    "tests/test_auth.py::test_token_refresh_valid PASSED\n"
                    "tests/test_auth.py::test_login_ok PASSED\n"
                    "=================================== FAILURES ===================================\n"
                    "________________________ test_token_refresh_expired ________________________\n"
                    "E       AssertionError: expected 200, got 401\n"
                    "tests/test_auth.py:88: AssertionError\n"
                    "========================= 1 failed, 41 passed in 2.31s ========================="
                ),
                expected_tool="read_file",
            ),
            Step(
                tool_output=(
                    '"""Token handling."""\n\n'
                    "TOKEN_TTL_SECONDS = 3600\n\n"
                    "def refresh_token(payload, *, timeout=30):\n"
                    "    ttl = TOKEN_TTL_SECONDS\n"
                    "    if payload.get('issued_at') + ttl < now():\n"
                    "        raise AuthError('expired')\n"
                    "    return issue(payload)\n"
                ),
                expected_tool="apply_patch",
            ),
            Step(
                tool_output=(
                    "patch applied to src/auth/token.py (+3 -1)\n"
                    "diff --git a/src/auth/token.py b/src/auth/token.py\n"
                    "@@ -4,7 +4,7 @@ TOKEN_TTL_SECONDS = 3600\n"
                    "-    if payload.get('issued_at') + ttl < now():\n"
                    "+    if payload.get('issued_at') + ttl <= now():\n"
                ),
                expected_tool="run_tests",
            ),
            Step(
                tool_output=(
                    "============================= test session starts ==============================\n"
                    "collected 42 items\n"
                    "tests/test_auth.py::test_token_refresh_expired PASSED\n"
                    "tests/test_auth.py::test_token_refresh_valid PASSED\n"
                    "========================= 42 passed in 2.18s ========================="
                ),
                expected_tool="escalate",
            ),
        ],
    ),
    Episode(
        id="billing_rounding",
        goal="Fix invoice rounding errors reported by QA.",
        steps=[
            Step(
                tool_output=(
                    "collected 18 items\n"
                    "tests/test_billing.py::test_invoice_rounding FAILED\n"
                    "tests/test_billing.py::test_invoice_total PASSED\n"
                    "=================================== FAILURES ===================================\n"
                    "E       AssertionError: expected Decimal('10.00'), got Decimal('9.999')\n"
                    "tests/test_billing.py:44: AssertionError\n"
                    "========================= 1 failed, 17 passed in 1.02s ========================="
                ),
                expected_tool="read_file",
            ),
            Step(
                tool_output=(
                    "from decimal import Decimal, ROUND_HALF_UP\n\n"
                    "def round_invoice(amount):\n"
                    "    return amount.quantize(Decimal('0.01'))\n"
                ),
                expected_tool="apply_patch",
            ),
            Step(
                tool_output="patch applied to src/billing/invoice.py",
                expected_tool="run_tests",
            ),
            Step(
                tool_output="18 passed in 0.98s",
                expected_tool="escalate",
            ),
        ],
    ),
    Episode(
        id="db_connection_pool",
        goal="Investigate intermittent database connection failures in staging.",
        steps=[
            Step(
                tool_output=(
                    "ERROR: connection pool exhausted (max=20)\n"
                    "Traceback (most recent call last):\n"
                    '  File "src/db/pool.py", line 88, in checkout\n'
                    "    raise PoolExhausted('max connections reached')\n"
                    "PoolExhausted: max connections reached\n"
                    "exit 1"
                ),
                expected_tool="read_file",
            ),
            Step(
                tool_output=(
                    "DEFAULT_POOL_SIZE = 20\n"
                    "DEFAULT_TIMEOUT = 5\n\n"
                    "def checkout():\n"
                    "    if len(_pool) >= DEFAULT_POOL_SIZE:\n"
                    "        raise PoolExhausted('max connections reached')\n"
                ),
                expected_tool="apply_patch",
            ),
            Step(
                tool_output="patch applied: DEFAULT_POOL_SIZE = 50",
                expected_tool="run_tests",
            ),
            Step(
                tool_output="tests/test_db_pool.py::test_checkout_under_load PASSED\n12 passed",
                expected_tool="escalate",
            ),
        ],
    ),
    Episode(
        id="search_unicode",
        goal="Fix unicode handling in the search API.",
        steps=[
            Step(
                tool_output=(
                    "tests/test_search.py::test_unicode_query FAILED\n"
                    "E       UnicodeEncodeError: 'ascii' codec can't encode character '\\u0103'\n"
                    "src/search/query.py:52: UnicodeEncodeError\n"
                    "1 failed, 24 passed"
                ),
                expected_tool="read_file",
            ),
            Step(
                tool_output=(
                    "def normalize_query(text: str) -> str:\n"
                    "    return text.encode('ascii', errors='ignore').decode('ascii')\n"
                ),
                expected_tool="apply_patch",
            ),
            Step(
                tool_output="patch applied to src/search/query.py",
                expected_tool="run_tests",
            ),
            Step(
                tool_output="25 passed in 1.4s",
                expected_tool="escalate",
            ),
        ],
    ),
    Episode(
        id="cache_eviction",
        goal="Debug LRU cache evicting hot keys too aggressively.",
        steps=[
            Step(
                tool_output=(
                    "tests/test_cache.py::test_eviction_lru FAILED\n"
                    "E       AssertionError: 'user:42' not in cache after second access\n"
                    "1 failed, 9 passed"
                ),
                expected_tool="read_file",
            ),
            Step(
                tool_output=(
                    "MAX_ENTRIES = 100\n\n"
                    "def touch(key):\n"
                    "    _order.append(key)  # bug: should move to end, not append duplicate\n"
                ),
                expected_tool="apply_patch",
            ),
            Step(
                tool_output="patch applied to src/cache/lru.py",
                expected_tool="run_tests",
            ),
            Step(
                tool_output="10 passed in 0.6s",
                expected_tool="escalate",
            ),
        ],
    ),
    Episode(
        id="api_rate_limit",
        goal="Users report 429 errors on the public API endpoint.",
        steps=[
            Step(
                tool_output=(
                    "grep -rn 'RATE_LIMIT' src/api/\n"
                    "src/api/middleware.py:14: RATE_LIMIT_PER_MIN = 60\n"
                    "src/api/handlers.py:88: if count > RATE_LIMIT_PER_MIN: return 429\n"
                    "src/api/config.py:3: DEFAULT_RATE = 60"
                ),
                expected_tool="read_file",
            ),
            Step(
                tool_output=(
                    "RATE_LIMIT_PER_MIN = 60\n\n"
                    "def check_rate(user_id):\n"
                    "    return _counts[user_id] < RATE_LIMIT_PER_MIN\n"
                ),
                expected_tool="apply_patch",
            ),
            Step(
                tool_output="patch applied: RATE_LIMIT_PER_MIN = 120",
                expected_tool="run_tests",
            ),
            Step(
                tool_output="tests/test_api_rate.py::test_burst_allowed PASSED\n8 passed",
                expected_tool="escalate",
            ),
        ],
    ),
]
